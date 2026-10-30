"""Conservative C resolution over every ``.c`` and ``.h`` file of one capture (docs/c.md).

There are no modules: ``#include`` edges name captured files. A quoted include is
looked up beside the including file, then (like an unknown ``-I`` path) as the one
captured file whose path ends with it; an angle include only by that suffix, and only
with a directory part, so ``<stdio.h>`` never binds a project file. A file sees its own
declarations and those of the files it includes, transitively. Conditional compilation
is not evaluated: same-named declarations in several branches make a use ambiguous.
"""

from __future__ import annotations

import posixpath
from dataclasses import dataclass

from oxide.core.languages.c import Parsed
from oxide.core.languages.python import Use
from oxide.core.languages.python_resolve import Edge, Res, edge_target, structural
from oxide.models import Entity, RelationKind, SourceRef

type Site = tuple[int, int]  # (file, scope)


def symbol_keys(path: str, parsed: Parsed) -> list[str]:
    """The file key, then ``symbol:<path>#<name>`` per scope. Equal names (a prototype
    and its definition, conditional branches, ``typedef struct S S``) take ``~n`` in
    source order, so keys are unique within the file."""
    keys = [f"file:{path}"]
    seen: dict[str, int] = {}
    for scope in parsed.scopes[1:]:
        assert scope.decl is not None
        n = seen.get(scope.decl.name, 0)
        seen[scope.decl.name] = n + 1
        keys.append(f"symbol:{path}#{scope.decl.name}" + (f"~{n}" if n else ""))
    return keys


@dataclass(slots=True)
class _File:
    path: str
    digest: str
    parsed: Parsed
    keys: list[str]
    includes: list[list[str]]  # candidate paths per include, in source order


class Resolver:
    def __init__(self, files: list[tuple[str, str, Parsed]], paths: set[str]) -> None:
        self.files: list[_File] = []
        self.at: dict[str, int] = {}
        self.names: dict[str, list[Site]] = {}  # by declared name, every kind
        suffixes: dict[str, list[str]] = {}
        for path in paths:
            parts = path.split("/")
            for n in range(len(parts)):
                suffixes.setdefault("/".join(parts[n:]), []).append(path)
        for path, digest, parsed in files:
            includes = []
            for item in parsed.includes:
                beside = posixpath.normpath(posixpath.join(posixpath.dirname(path), item.path))
                if not item.system and beside in paths:
                    includes.append([beside])
                elif not item.system or "/" in item.path:
                    includes.append(sorted(suffixes.get(posixpath.normpath(item.path), [])))
                else:
                    includes.append([])
            self.at[path] = len(self.files)
            self.files.append(_File(path, digest, parsed, symbol_keys(path, parsed), includes))
            for k, scope in enumerate(parsed.scopes[1:], 1):
                assert scope.decl is not None
                self.names.setdefault(scope.decl.name, []).append((self.at[path], k))
        self.visible: dict[int, set[int]] = {}

    def _sees(self, i: int) -> set[int]:
        """File ``i`` and the parsed files its resolved includes reach."""
        if i not in self.visible:
            seen, todo = {i}, [i]
            while todo:
                for found in self.files[todo.pop()].includes:
                    j = self.at.get(found[0], -1) if len(found) == 1 else -1
                    if j >= 0 and j not in seen:
                        seen.add(j)
                        todo.append(j)
            self.visible[i] = seen
        return self.visible[i]

    def _keys(self, sites: list[Site]) -> Res:
        return sorted(self.files[i].keys[k] for i, k in sites) or None

    def _decl(self, site: Site) -> tuple[str, bool, bool]:
        d = self.files[site[0]].parsed.scopes[site[1]].decl
        assert d is not None
        return d.kind, d.static, d.defines

    def _definitions(self, i: int, name: str) -> Res:
        """Function definitions of ``name`` that file ``i`` links to: the visible ones,
        else every external one in the capture."""
        sites = [s for s in self.names.get(name, []) if self._decl(s)[::2] == ("function", True)]
        sees = self._sees(i)
        return self._keys([s for s in sites if s[0] in sees]) or self._keys(
            [s for s in sites if not self._decl(s)[1]]
        )

    def _lookup(self, i: int, k: int, name: str, call: bool) -> Res:
        """Candidates for a call, a typedef name or a ``struct S`` tag in scope ``k``."""
        if name in self.files[i].parsed.scopes[k].bound:
            return None
        kind, _, tag = name.rpartition(" ")
        sees = self._sees(i)
        sites = [s for s in self.names.get(tag, []) if s[0] in sees]
        if kind:
            return self._keys([s for s in sites if self._decl(s)[0] == kind])
        macros = [s for s in sites if self._decl(s)[0] == "macro"]
        if macros or not call:
            return self._keys(macros or [s for s in sites if self._decl(s)[0] == "type"])
        return self._definitions(i, name) or self._keys(
            [s for s in sites if self._decl(s)[0] == "function"]
        )

    def derive(self, entities: list[Entity], edges: list[Edge]) -> None:
        for i in range(len(self.files)):
            self._emit(i, entities, edges)

    def _emit(self, i: int, entities: list[Entity], edges: list[Edge]) -> None:
        file = self.files[i]

        def evidence(start: int, end: int) -> SourceRef:
            return SourceRef(file.path, start, end, file.digest)

        def edge(kind: RelationKind, source: str, res: Res, use: Use) -> Edge:
            targets, resolution, basis = edge_target(res, kind == "calls")
            shown = None if resolution == "resolved" else use.name
            return Edge(
                kind, source, targets, resolution, basis, shown, evidence(use.start, use.end)
            )

        for item, found in zip(file.parsed.includes, file.includes, strict=True):
            res = [f"file:{path}" for path in found] or None
            edges.append(edge("imports", file.keys[0], res, Use(item.path, item.start, item.end)))
        for k, scope in enumerate(file.parsed.scopes):
            key = file.keys[k]
            if (d := scope.decl) is not None:
                source = evidence(d.start, d.end)
                entities.append(Entity(key, "symbol", d.kind, d.name, d.signature, source, False))
                edges.append(structural("contains", file.keys[0], key, None))
                if d.kind == "function" and not d.defines:  # a prototype: its definitions
                    wanted = ("function", d.static, True)
                    sites = [s for s in self.names[d.name] if self._decl(s) == wanted]
                    if d.static:
                        sites = [s for s in sites if s[0] in self._sees(i)]
                    if targets := self._keys(sites):
                        edges.append(edge("references", key, targets, Use(d.name, d.start, d.end)))
            for ref in scope.refs:
                edges.append(edge("references", key, self._lookup(i, k, ref.name, False), ref))
            for call in scope.calls:
                edges.append(edge("calls", key, self._lookup(i, k, call.name, True), call))
