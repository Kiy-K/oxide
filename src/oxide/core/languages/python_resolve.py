"""Python resolution and graph construction, plus the relation helpers every language slice
shares (``Edge``, ``edge_target``, ``structural``, ``symbol_keys``). Facts are in ``python``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from oxide.core.languages.python import (
    Import,
    Parsed,
    Use,
)
from oxide.models import Basis, Entity, RelationKind, Resolution, SourceRef


@dataclass(frozen=True, slots=True)
class Edge:
    """A derived relation before storage; ``targets`` holds every candidate endpoint."""

    kind: RelationKind
    source: str
    targets: tuple[str, ...]
    resolution: Resolution
    basis: Basis
    name: str | None
    evidence: SourceRef | None


# Resolution result: candidate entity keys, or None when the name is opaque.
type Res = list[str] | None


def module_name(path: str) -> str | None:
    if not path.endswith(".py"):
        return None
    parts = path[:-3].split("/")
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts) if parts and all(p.isidentifier() for p in parts) else None


def module_key(name: str) -> str:
    return f"module:python:{name}"


def import_root_names(paths: list[str]) -> dict[str, str]:
    """Module names of files inside a package whose import root is a nested directory
    (``src/pkg/a.py`` -> ``pkg.a``): the root is the first ancestor without an
    ``__init__.py``, as pytest's ``prepend`` mode puts on ``sys.path``. Files outside a
    package, and packages rooted at the repository, have no second name."""
    packages = {p.rpartition("/")[0] for p in paths if p.rpartition("/")[2] == "__init__.py"}
    names = {}
    for path in paths:
        directory = path.rpartition("/")[0]
        if directory not in packages:
            continue
        root = directory
        while root and root in packages:
            root = root.rpartition("/")[0]
        if root and (name := module_name(path[len(root) + 1 :])) is not None:
            names[path] = name
    return names


@dataclass(slots=True)
class _File:
    path: str
    digest: str
    parsed: Parsed
    keys: list[str]
    children: list[list[int]]
    module: str | None
    init: bool


def symbol_keys(path: str, decls: list[tuple[int, str, int]]) -> tuple[list[str], list[list[int]]]:
    """Keys of the file (index 0) and of each declaration ``(parent, name, start)``, in
    order (index k + 1, parents first), and each one's children (ADR-0010 § SymbolId)."""
    children: list[list[int]] = [[] for _ in range(len(decls) + 1)]
    siblings: dict[tuple[int, str], list[tuple[int, int]]] = {}
    for k, (parent, name, start) in enumerate(decls, start=1):
        children[parent].append(k)
        siblings.setdefault((parent, name), []).append((start, k))
    ordinals = [0] * (len(decls) + 1)
    for group in siblings.values():
        for ordinal, (_, k) in enumerate(sorted(group)):
            ordinals[k] = ordinal
    segments: list[str] = [""]
    for k, (parent, name, _) in enumerate(decls, start=1):
        own = name + (f"~{ordinals[k]}" if ordinals[k] else "")
        segments.append(f"{segments[parent]}.{own}" if segments[parent] else own)
    keys = [f"file:{path}"] + [f"symbol:{path}#{s}" for s in segments[1:]]
    return keys, children


def _decls(parsed: Parsed) -> list[tuple[int, str, int]]:
    out = []
    for scope in parsed.scopes[1:]:
        assert scope.parent is not None and scope.decl is not None
        out.append((scope.parent, scope.decl.name, scope.decl.start))
    return out


def _is_test(file: _File, k: int) -> bool:
    name = file.path.rsplit("/", 1)[-1]
    if not (name.startswith("test_") or name.endswith("_test.py")):
        return False
    scopes = file.parsed.scopes
    decl, parent = scopes[k].decl, scopes[k].parent
    assert decl is not None
    if parent == 0:
        return decl.name.startswith("Test" if decl.cls else "test")
    if parent is None:
        return False
    outer = scopes[parent]
    return (
        not decl.cls
        and decl.name.startswith("test")
        and outer.parent == 0
        and outer.decl is not None
        and outer.decl.cls
        and outer.decl.name.startswith("Test")
    )


def _bound_name(item: Import) -> str | None:
    if item.item == "module":
        return item.alias or (item.module[0] if item.module else None)
    if item.item == "name":
        return item.alias or item.name
    return None


def _import_text(item: Import) -> str:
    text = "." * item.level + ".".join(item.module)
    if item.item == "name" and item.name is not None:
        text += ("." if item.module else "") + item.name
    return text


def edge_target(res: Res, symbols_only: bool) -> tuple[tuple[str, ...], Resolution, Basis]:
    if res and (not symbols_only or all(k.startswith("symbol:") for k in res)):
        if len(res) == 1:
            return (res[0],), "resolved", "resolved"
        return tuple(res), "ambiguous", "syntactic"
    return (), "unresolved", "syntactic"


def structural(kind: RelationKind, source: str, target: str, at: SourceRef | None) -> Edge:
    return Edge(kind, source, (target,), "resolved", "syntactic", None, at)


class Resolver:
    """Conservative cross-file resolution over every Python file of one capture."""

    def __init__(self, files: list[tuple[str, str, Parsed]]) -> None:
        self.files: list[_File] = []
        self.modules: dict[str, list[int]] = {}
        self.tests: set[str] = set()
        # Import-root names (``import_root_names``) to the repository-relative ones.
        self.rooted: dict[str, list[str]] = {}
        self.resolving: set[tuple[str, str]] = set()  # ``_from`` calls in progress
        self.scopes: dict[str, tuple[int, int]] = {}  # key -> (file, scope)
        rooted = import_root_names([path for path, _, _ in files])
        for path, file_digest, parsed in files:
            keys, children = symbol_keys(path, _decls(parsed))
            module = module_name(path)
            if module is not None:
                self.modules.setdefault(module, []).append(len(self.files))
                if path in rooted:
                    self.rooted.setdefault(rooted[path], []).append(module)
            init = path.rsplit("/", 1)[-1] == "__init__.py"
            file = _File(path, file_digest, parsed, keys, children, module, init)
            self.tests.update(keys[k] for k in range(1, len(parsed.scopes)) if _is_test(file, k))
            self.scopes.update((key, (len(self.files), k)) for k, key in enumerate(keys))
            self.files.append(file)

    def derive(self, entities: list[Entity], edges: list[Edge]) -> None:
        for name in sorted(self.modules):
            key = module_key(name)
            entities.append(Entity(key, "module", "module", name, None, None, False))
            edges.append(structural("contains", "repo", key, None))
            for i in self.modules[name]:
                file_key = f"file:{self.files[i].path}"
                edges.append(structural("contains_logical", key, file_key, None))
        for i in range(len(self.files)):
            self._emit(i, entities, edges)

    def _emit(self, i: int, entities: list[Entity], edges: list[Edge]) -> None:
        file = self.files[i]
        scopes = file.parsed.scopes

        def evidence(start: int, end: int) -> SourceRef:
            return SourceRef(file.path, start, end, file.digest)

        def edge(kind: RelationKind, source: str, res: Res, name: str, at: Use | Import) -> Edge:
            targets, resolution, basis = edge_target(res, kind == "calls")
            shown = None if resolution == "resolved" else name
            return Edge(kind, source, targets, resolution, basis, shown, evidence(at.start, at.end))

        bindings: list[list[tuple[str, Res]]] = []
        for k, scope in enumerate(scopes):
            bound: list[tuple[str, Res]] = []
            for item in scope.imports:
                target, binding = self._import(i, item)
                edges.append(edge("imports", file.keys[k], target, _import_text(item), item))
                name = _bound_name(item)
                if name is not None and binding is not False:
                    bound.append((name, binding))
            bindings.append(bound)

        for k, scope in enumerate(scopes):
            key = file.keys[k]
            if scope.decl is not None and scope.parent is not None:
                decl, parent = scope.decl, scope.parent
                outer = scopes[parent].decl
                kind = "class" if decl.cls else "method" if outer and outer.cls else "function"
                source = evidence(decl.start, decl.end)
                test = key in self.tests
                entities.append(
                    Entity(key, "symbol", kind, decl.name, decl.signature, source, test)
                )
                edges.append(structural("contains", file.keys[parent], key, None))
                if parent == 0 and file.module is not None:
                    edges.append(structural("defines", module_key(file.module), key, source))
                for base in scope.bases:
                    res = self._lookup(i, bindings, parent, base.name)
                    edges.append(edge("references", key, res, base.name, base))
            for call in scope.calls:
                relation = edge(
                    "calls", key, self._lookup(i, bindings, k, call.name), call.name, call
                )
                callee = relation.targets[0] if relation.resolution == "resolved" else None
                test_key = self._enclosing_test(i, k)
                if (
                    callee
                    and callee.startswith("symbol:")
                    and callee not in self.tests
                    and test_key
                ):
                    at = evidence(call.start, call.end)
                    edges.append(
                        Edge("tested_by", callee, (test_key,), "resolved", "heuristic", None, at)
                    )
                edges.append(relation)

    def _enclosing_test(self, i: int, k: int | None) -> str | None:
        file = self.files[i]
        while k:
            if file.keys[k] in self.tests:
                return file.keys[k]
            k = file.parsed.scopes[k].parent
        return None

    def _lookup(self, i: int, bindings: list[list[tuple[str, Res]]], k: int, name: str) -> Res:
        file = self.files[i]
        head, *members = name.split(".")
        if members:
            return self._member(self._lookup(i, bindings, k, head), members)
        if name in file.parsed.rebound:
            return None
        scopes = file.parsed.scopes
        current: int | None = k
        while current is not None:
            decl = scopes[current].decl
            # Enclosing class bodies are not scopes for nested functions.
            if current == k or decl is None or not decl.cls:
                found = self._in_scope(i, current, name, bindings[current])
                if found is not False:
                    return found
            current = scopes[current].parent
        return None

    def _member(self, res: Res, members: list[str]) -> Res:
        """``a.b.c`` once ``a`` is known: attributes of one in-repo module (its
        submodules and module-level names) or one class (its own body only; inherited
        and instance attributes stay unresolved)."""
        for member in members:
            if not res or len(res) != 1:
                return None
            if res[0].startswith("module:python:"):
                res = self._from(res[0].removeprefix("module:python:"), member)
                continue
            if res[0] not in self.scopes:
                return None
            i, k = self.scopes[res[0]]
            decl = self.files[i].parsed.scopes[k].decl
            if decl is None or not decl.cls:
                return None
            found = self._in_scope(i, k, member, [])
            res = None if found is False else found
        return res

    def _in_scope(
        self, i: int, s: int, name: str, bindings: list[tuple[str, Res]] | None
    ) -> Res | Literal[False]:
        """Candidates bound to ``name`` in scope ``s``; False when ``s`` does not bind it.
        Without ``bindings``, names bound by import resolve through the import."""
        file = self.files[i]
        scope = file.parsed.scopes[s]
        if any(m.item == "star" for m in scope.imports) or name in scope.bound:
            return None
        found = [
            file.keys[c]
            for c in file.children[s]
            if (decl := file.parsed.scopes[c].decl) is not None and decl.name == name
        ]
        bound = bool(found)
        if bindings is None:
            bindings = [
                (name, binding)
                for item in scope.imports
                if _bound_name(item) == name and (binding := self._import(i, item)[1]) is not False
            ]
        for bound_name, res in bindings:
            if bound_name == name:
                if res is None:
                    return None
                bound = True
                found.extend(res)
        return sorted(set(found)) if bound else False

    def _import(self, i: int, item: Import) -> tuple[Res, Res | Literal[False]]:
        """(what the import names, what it binds); False binds nothing."""
        base = self._absolute(i, item)
        module: Res = self._module(base) if base is not None else None
        if item.item == "module":
            if item.alias is not None:
                return module, module
            return module, self._module(item.module[0]) if item.module else None
        if item.item == "name" and item.name is not None:
            found = self._from(base, item.name) if base is not None else None
            return found, found
        return module, False

    def _names(self, name: str) -> list[str]:
        """Repository-relative module names ``name`` imports: itself when it is one,
        else the modules it names from a nested import root."""
        return [name] if name in self.modules else self.rooted.get(name, [])

    def _module(self, name: str) -> Res:
        return [module_key(n) for n in self._names(name)] or None

    def _from(self, module: str, name: str) -> Res:
        """What ``from module import name`` binds. A name the module itself imports
        resolves through that import; a cycle of such re-exports adds nothing."""
        if (module, name) in self.resolving:
            return []
        self.resolving.add((module, name))
        try:
            names = self._names(module)
            found = [module_key(f"{m}.{name}") for m in names if f"{m}.{name}" in self.modules]
            for j in (j for m in names for j in self.modules[m]):
                if name in self.files[j].parsed.rebound:
                    return None
                res = self._in_scope(j, 0, name, None)
                if res is None:
                    return None
                if res is not False:
                    found.extend(res)
            return sorted(set(found)) or None
        finally:
            self.resolving.discard((module, name))

    def _absolute(self, i: int, item: Import) -> str | None:
        if item.level == 0:
            return ".".join(item.module) or None
        file = self.files[i]
        if file.module is None:
            return None
        package = file.module.split(".")
        if not file.init:
            package.pop()
        for _ in range(1, item.level):
            if not package:
                return None
            package.pop()
        return ".".join([*package, *item.module]) if package else None
