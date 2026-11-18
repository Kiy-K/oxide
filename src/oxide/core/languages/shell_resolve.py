"""Conservative shell resolution over every captured shell file (docs/shell.md).

A name is only resolved against what the calling file can actually see: its own
functions and variables, and functions from files reached by a proven chain of
``source`` / ``.`` imports. Sourced scripts resolve only when their path contains
a slash and names a captured file relative to the sourcing file. Dynamic names
and slashless source paths are emitted unresolved with the written text; nothing
is executed and no PATH lookup is simulated.
"""

from __future__ import annotations

import posixpath

from oxide.core.languages.python_resolve import Edge, edge_target, structural
from oxide.core.languages.shell_model import Parsed, Use, symbol_key
from oxide.models import Entity, RelationKind, SourceRef

Found = tuple[tuple[str, ...], str]


def _target_path(source: str, written: str) -> str | None:
    """Resolve a static relative path inside the captured tree, or None."""
    base = source.rpartition("/")[0]
    joined = f"{base}/{written}" if base else written
    path = posixpath.normpath(joined)
    if path.startswith("/") or path == ".." or path.startswith("../"):
        return None
    return path


class Resolver:
    def __init__(self, sources: list[tuple[str, str, Parsed]], paths: set[str]) -> None:
        self.sources = sources
        self.paths = paths
        self.keys: dict[str, list[str]] = {}
        self.owner: dict[str, str] = {}
        self.by_function: dict[str, list[str]] = {}
        self.by_variable: dict[tuple[str, int | None, str], list[str]] = {}
        self.sourced: dict[str, set[str]] = {}
        self._reachable: dict[str, frozenset[str]] = {}
        for path, _digest, parsed in sources:
            keys = [symbol_key(path, d, parsed.decls) for d in parsed.decls]
            self.keys[path] = keys
            for key, decl in zip(keys, parsed.decls, strict=True):
                self.owner[key] = path
                if decl.kind == "function":
                    self.by_function.setdefault(decl.name, []).append(key)
                else:
                    self.by_variable.setdefault((path, decl.owner, decl.name), []).append(key)
            for use in parsed.uses:
                # Executing ``bash other.sh`` is a separate process: it does not
                # import that file's functions into this shell's namespace.
                if use.kind == "source" and (target := self._path(path, use)) in paths:
                    self.sourced.setdefault(path, set()).add(target)

    def _path(self, path: str, use: Use) -> str | None:
        return _target_path(path, use.path) if use.path else None

    def reachable(self, path: str) -> frozenset[str]:
        """The file and every file reached by a transitive, proven source chain."""
        if path not in self._reachable:
            seen: set[str] = set()
            stack = [path]
            while stack:
                current = stack.pop()
                if current in seen:
                    continue
                seen.add(current)
                stack.extend(sorted(self.sourced.get(current, ())))
            self._reachable[path] = frozenset(seen)
        return self._reachable[path]

    def _command(self, path: str, name: str) -> Found:
        visible = self.reachable(path)
        found = tuple(
            sorted(key for key in self.by_function.get(name, ()) if self.owner[key] in visible)
        )
        return (found, "resolved") if found else ((), "unresolved")

    def _variable(self, path: str, owner: int | None, name: str) -> Found:
        found = self.by_variable.get((path, owner, name), ())
        if not found and owner is not None:
            found = self.by_variable.get((path, None, name), ())
        return (tuple(sorted(found)), "resolved") if found else ((), "unresolved")

    def derive(self, entities: list[Entity], edges: list[Edge]) -> None:
        for path, digest, parsed in self.sources:
            keys = self.keys[path]

            def evidence(
                start: int, end: int, *, path: str = path, digest: str = digest
            ) -> SourceRef:
                return SourceRef(path, start, end, digest)

            def edge(
                kind: RelationKind,
                source: str,
                found: Found,
                name: str,
                start: int,
                end: int,
            ) -> Edge:
                candidates, _ = found
                targets, resolution, default = edge_target(list(candidates), False)
                shown = name if resolution != "resolved" else None
                return Edge(kind, source, targets, resolution, default, shown, evidence(start, end))

            for key, decl in zip(keys, parsed.decls, strict=True):
                entities.append(
                    Entity(
                        key,
                        "symbol",
                        decl.kind,
                        decl.name,
                        decl.signature,
                        evidence(decl.start, decl.end),
                        False,
                    )
                )
                parent = keys[decl.owner] if decl.owner is not None else f"file:{path}"
                edges.append(structural("contains", parent, key, None))

            for use in parsed.uses:
                source = keys[use.owner] if use.owner is not None else f"file:{path}"
                if use.kind == "source":
                    target = self._path(path, use)
                    found: Found = (
                        ((f"file:{target}",), "resolved")
                        if target in self.paths
                        else ((), "unresolved")
                    )
                    kind: RelationKind = "imports"
                elif use.kind == "script":
                    target = self._path(path, use)
                    found = (
                        ((f"file:{target}",), "resolved")
                        if target in self.paths
                        else ((), "unresolved")
                    )
                    kind = "calls"
                elif use.kind == "command":
                    found = self._command(path, use.name)
                    kind = "calls"
                elif use.kind == "variable":
                    found = self._variable(path, use.owner, use.name)
                    kind = "references"
                else:  # dynamic_command | dynamic_source | dynamic_script
                    found = ((), "unresolved")
                    kind = "imports" if use.kind == "dynamic_source" else "calls"
                edges.append(edge(kind, source, found, use.name, use.start, use.end))
