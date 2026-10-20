"""Conservative Go resolution over every ``.go`` file of one capture (docs/go.md).

A package is the files of one directory sharing a package clause, keyed
``module:go:<dir>/<package>`` (``module:go:<package>`` at the root). The ``module``
line of each captured ``go.mod`` maps import paths to directories; anything else
(the standard library, other modules, ``replace``, ``go.work``, vendoring) stays
unresolved. Build constraints are not evaluated: same-named declarations in
platform files make a reference ambiguous rather than choosing one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from oxide.core.go import Parsed
from oxide.core.python import Edge, Res, Use, edge_target, structural
from oxide.models import Entity, RelationKind, SourceRef

_MODULE = re.compile(rb"^\s*module\s+\"?([^\s\"]+?)\"?\s*(?://.*)?$", re.MULTILINE)
_TESTS = ("Test", "Benchmark", "Fuzz", "Example")

type Package = tuple[str, str]  # (directory, package clause)
type Site = tuple[int, int]  # (file, scope)


def module_key(package: Package) -> str:
    directory, name = package
    return f"module:go:{directory}/{name}" if directory else f"module:go:{name}"


def is_test(path: str, name: str) -> bool:
    """``go test`` discovery: Test, Benchmark, Fuzz and Example functions of a _test.go
    file, whose suffix does not start with a lower-case letter."""
    if not path.endswith("_test.go"):
        return False
    for prefix in _TESTS:
        if name.startswith(prefix):
            rest = name[len(prefix) :]
            return not rest or not rest[0].islower()
    return False


def _exported(name: str) -> bool:
    return name[:1].isupper()


def symbol_keys(path: str, parsed: Parsed) -> list[str]:
    """The file key, then ``symbol:<path>#<segment>`` per scope: the name, or
    ``Owner.Name`` for methods. Equal segments (``init``, platform duplicates, broken
    code) take ``~n`` in source order, so keys are unique within the file."""
    decls = [s.decl for s in parsed.scopes[1:] if s.decl is not None]
    segments = [f"{d.owner}.{d.name}" if d.owner else d.name for d in decls]
    named = [""] * len(decls)
    seen: dict[str, int] = {}
    for j in sorted(range(len(decls)), key=lambda j: decls[j].start):
        n = seen.get(segments[j], 0)
        seen[segments[j]] = n + 1
        named[j] = segments[j] + (f"~{n}" if n else "")
    return [f"file:{path}", *(f"symbol:{path}#{s}" for s in named)]


@dataclass(slots=True)
class _File:
    path: str
    digest: str
    parsed: Parsed
    keys: list[str]
    package: Package
    test: bool  # a _test.go file: visible only to test files of its own package


class Resolver:
    def __init__(self, files: list[tuple[str, str, Parsed]], mods: dict[str, bytes]) -> None:
        self.roots: dict[str, str] = {}  # directory of a go.mod -> its module path
        for path, data in mods.items():
            found = _MODULE.search(data)
            if found is not None:
                self.roots[path.rpartition("/")[0]] = found.group(1).decode("utf-8", "replace")
        self.files: list[_File] = []
        self.packages: dict[Package, list[int]] = {}
        # Per package: package-level names, and methods by (receiver type, name).
        self.tops: dict[Package, dict[str, list[Site]]] = {}
        self.methods: dict[Package, dict[tuple[str, str], list[Site]]] = {}
        self.tests: set[str] = set()
        self.call_targets: set[str] = set()
        for path, digest, parsed in files:
            directory = path.rpartition("/")[0]
            package = (directory, parsed.package) if parsed.package else (path, "")
            i, keys = len(self.files), symbol_keys(path, parsed)
            self.files.append(_File(path, digest, parsed, keys, package, path.endswith("_test.go")))
            self.packages.setdefault(package, []).append(i)
            tops = self.tops.setdefault(package, {})
            methods = self.methods.setdefault(package, {})
            for k, s in enumerate(parsed.scopes):
                if s.parent != 0 or s.decl is None:
                    continue
                if s.decl.kind != "method":
                    tops.setdefault(s.decl.name, []).append((i, k))
                    if s.decl.kind in ("function", "struct", "interface", "type"):
                        self.call_targets.add(keys[k])
                    if s.decl.kind == "function" and is_test(path, s.decl.name):
                        self.tests.add(keys[k])
                elif s.decl.owner:
                    self.call_targets.add(keys[k])
                    methods.setdefault((s.decl.owner, s.decl.name), []).append((i, k))

    def _visible(self, sites: list[Site], test: bool) -> list[str]:
        return sorted(self.files[i].keys[k] for i, k in sites if test or not self.files[i].test)

    def _top(self, package: Package, name: str, test: bool) -> list[str]:
        return self._visible(self.tops[package].get(name, []), test)

    def _lookup(self, i: int, k: int, name: str, imports: dict[str, list[Package]]) -> Res:
        """Candidates for ``name`` or ``q.name`` used in scope ``k`` of file ``i``."""
        file = self.files[i]
        scope = file.parsed.scopes[k]
        qualifier, _, member = name.rpartition(".")
        if not qualifier:
            if name in scope.bound or name == scope.receiver:
                return None
            found = self._top(file.package, name, file.test)
            if not found and _exported(name):  # dot imports cannot redeclare package names
                found = sorted(x for p in imports.get(".", []) for x in self._top(p, name, False))
            return found or None
        if qualifier in scope.bound:
            return None
        if qualifier == scope.receiver and scope.decl is not None and scope.decl.owner:
            sites = self.methods[file.package].get((scope.decl.owner, member), [])
            return self._visible(sites, file.test) or None  # promoted methods: unresolved
        if qualifier in imports and _exported(member):
            return (
                sorted(x for p in imports[qualifier] for x in self._top(p, member, False)) or None
            )
        return None

    def _owner(self, directory: str) -> str | None:
        """The directory of the nearest go.mod at or above ``directory``."""
        while directory not in self.roots:
            if not directory:
                return None
            directory = directory.rpartition("/")[0]
        return directory

    def _directory(self, path: str) -> str | None:
        """The captured directory of an import path, through the longest module path."""
        roots = [r for r, m in self.roots.items() if path == m or path.startswith(m + "/")]
        longest = max((len(self.roots[r]) for r in roots), default=0)
        roots = [r for r in roots if len(self.roots[r]) == longest]
        if len(roots) != 1:
            return None
        rest = [p for p in path[longest:].split("/") if p]
        if any(p in ("testdata", "vendor") or p[0] in "._" for p in rest):
            return None  # directories the go tool ignores; vendoring is unmodeled
        directory = "/".join(p for p in (roots[0], *rest) if p)
        return directory if self._owner(directory) == roots[0] else None

    def _imported(self, path: str) -> list[Package]:
        """Importable packages of the import path's directory: not ``main`` or an
        external test package, and with a non-test file. More than one is ambiguous."""
        directory = self._directory(path)
        if directory is None:
            return []
        return sorted(
            p
            for p, members in self.packages.items()
            if p[0] == directory
            and p[1] not in ("", "main")
            and not p[1].endswith("_test")
            and any(not self.files[i].test for i in members)
        )

    def derive(self, entities: list[Entity], edges: list[Edge]) -> None:
        for package in sorted(p for p in self.packages if p[1]):
            directory, name = package
            label, owner = directory or ".", self._owner(directory)
            if owner is not None:  # the import path
                rest = directory[len(owner) :].strip("/")
                label = "/".join(p for p in (self.roots[owner], rest) if p)
            label += "_test" if name.endswith("_test") else ""
            key = module_key(package)
            entities.append(Entity(key, "module", "module", label, None, None, False))
            edges.append(structural("contains", "repo", key, None))
            for i in self.packages[package]:
                file_key = f"file:{self.files[i].path}"
                edges.append(structural("contains_logical", key, file_key, None))
        for i in range(len(self.files)):
            self._emit(i, entities, edges)

    def _emit(self, i: int, entities: list[Entity], edges: list[Edge]) -> None:
        file = self.files[i]

        def evidence(start: int, end: int) -> SourceRef:
            return SourceRef(file.path, start, end, file.digest)

        def edge(kind: RelationKind, source: str, res: Res, use: Use) -> Edge:
            targets, resolution, basis = edge_target(res, kind == "calls")
            shown = None if resolution == "resolved" else use.name
            at = evidence(use.start, use.end)
            return Edge(kind, source, targets, resolution, basis, shown, at)

        imports: dict[str, list[Package]] = {}
        for item in file.parsed.imports:
            found = self._imported(item.path)
            use = Use(item.path, item.start, item.end)
            edges.append(edge("imports", file.keys[0], [module_key(p) for p in found] or None, use))
            for p in found if item.alias != "_" else []:
                imports.setdefault(item.alias or p[1], []).append(p)

        for k, scope in enumerate(file.parsed.scopes):
            key = file.keys[k]
            if scope.decl is not None and scope.parent is not None:
                d = scope.decl
                source = evidence(d.start, d.end)
                test = key in self.tests
                entities.append(Entity(key, "symbol", d.kind, d.name, d.signature, source, test))
                edges.append(structural("contains", file.keys[scope.parent], key, None))
                if scope.parent == 0 and file.package[1]:
                    edges.append(structural("defines", module_key(file.package), key, source))
                if scope.parent == 0 and d.kind == "method" and d.owner:
                    res = self._lookup(i, 0, d.owner, imports)
                    edges.append(edge("references", key, res, Use(d.owner, d.start, d.end)))
            for ref in scope.refs:
                edges.append(edge("references", key, self._lookup(i, k, ref.name, imports), ref))
            for call in scope.calls:
                targets = self._lookup(i, k, call.name, imports)
                if targets is not None:
                    targets = [
                        candidate for candidate in targets if candidate in self.call_targets
                    ] or None
                relation = edge("calls", key, targets, call)
                callee = relation.targets[0] if relation.resolution == "resolved" else None
                if key in self.tests and callee and callee not in self.tests:
                    at = evidence(call.start, call.end)
                    edges.append(
                        Edge("tested_by", callee, (key,), "resolved", "heuristic", None, at)
                    )
                edges.append(relation)
