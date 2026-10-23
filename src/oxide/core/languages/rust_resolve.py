"""Conservative Rust resolution over every ``.rs`` file of one capture (docs/rust.md).

The module tree and Cargo layout are ``rust_cargo``'s. A path is resolved the way rustc
does it syntactically: its first segment is ``crate``, ``self``, ``super``, ``Self``, a
name in scope (local ``use``, then the module's items and explicit imports, then its glob
imports) or an extern crate; later segments are members of the modules, types and traits
found so far. Nothing needs a type checker: methods on any receiver but ``self`` are not
resolved, trait dispatch is not modeled, and macros are not expanded. Whatever cannot be
established stays unresolved with its name; competing candidates make an edge ambiguous.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from oxide.core.languages import rust_cargo
from oxide.core.languages.python import Use
from oxide.core.languages.python_resolve import Edge, Res, edge_target, structural, symbol_keys
from oxide.core.languages.rust_cargo import Module
from oxide.core.languages.rust_facts import Import, Parsed
from oxide.models import Entity, RelationKind, SourceRef

type Space = Literal["type", "value", "any"]
type Seen = frozenset[tuple[str, str, str]]  # (module, name, space) lookups in progress
_TYPES = frozenset({"module", "struct", "enum", "union", "trait", "type"})
_NAMESPACES: dict[str, frozenset[str]] = {
    "type": _TYPES,
    "value": frozenset({"function", "constant", "variable", "struct"}),
    "any": _TYPES | {"function", "constant", "variable", "macro"},
}
_CALLABLE = frozenset({"function", "method", "struct", "enum", "macro"})  # an enum: its variant
_OWNERS = frozenset({"struct", "enum", "union", "trait"})
_NO_NAME = ("::", "crate", "super", "self")
MAX_HOPS = 64  # nested lookups through imports and globs; deeper chains are unresolved


def _sorted(keys: list[str]) -> list[str]:
    return sorted(set(keys))


def _text(imp: Import) -> str:
    """The import's path as written, without a trailing ``self``."""
    path = imp.path[:-1] if imp.path[-1] == "self" and len(imp.path) > 1 else imp.path
    return ("::" + "::".join(path[1:])) if path[0] == "::" else "::".join(path)


@dataclass(slots=True)
class _File:
    path: str
    digest: str
    parsed: Parsed
    keys: list[str]
    children: list[list[int]]
    modules: list[Module]  # per scope: the module it is declared in (the file's for 0)

    def name(self, k: int) -> str:
        decl = self.parsed.scopes[k].decl
        return decl.name if decl is not None else ""

    def parent(self, k: int) -> int:
        return self.parsed.scopes[k].parent or 0


class Resolver:
    def __init__(self, files: list[tuple[str, str, Parsed]], manifests: dict[str, bytes]) -> None:
        self.layout = rust_cargo.layout({p: parsed for p, _, parsed in files}, manifests)
        self.by_key = {m.key: m for m in self.layout.modules}
        self.files: list[_File] = []
        self.kind: dict[str, str] = {m.key: "module" for m in self.layout.modules}
        self.public: dict[str, bool] = {}
        self.items: dict[str, dict[str, list[str]]] = {}
        self.macros: dict[str, dict[str, list[str]]] = {}  # crate root -> name -> macros
        self.variants: dict[str, frozenset[str]] = {}
        self.explicit: dict[str, dict[str, list[tuple[int, Import]]]] = {}
        self.globs: dict[str, list[tuple[int, Import]]] = {}
        self.local: dict[tuple[int, int], list[tuple[int, Import]]] = {}
        self.tests: set[str] = set()
        self.inherent: set[str] = set()  # associated items of impls without a trait
        self.call_targets: set[str] = set()
        self.cache: dict[tuple[object, ...], list[str]] = {}
        self.cuts = 0  # lookups cut short by a cycle: their results are not cached
        self.active: set[tuple[int, int]] = set()  # imports being resolved
        self.impl_types: dict[tuple[int, int], list[str]] = {}
        self._assoc: dict[str, dict[str, list[str]]] | None = None
        self._building = False
        for path, digest, parsed in files:
            decls = [
                (s.parent or 0, s.decl.name, s.decl.start) for s in parsed.scopes[1:] if s.decl
            ]
            keys, children = symbol_keys(path, decls)
            home = self.layout.at[(path, ())]
            modules = [home] + [
                self.layout.at.get((path, s.decl.module), home) for s in parsed.scopes[1:] if s.decl
            ]
            self.files.append(_File(path, digest, parsed, keys, children, modules))
            self._index(len(self.files) - 1)

    # --- indexing -----------------------------------------------------------------

    def _index(self, i: int) -> None:
        file = self.files[i]
        for k, scope in enumerate(file.parsed.scopes):
            decl, key = scope.decl, file.keys[k]
            if decl is None:
                continue
            module = file.modules[k]
            self.kind[key], self.public[key] = decl.kind, decl.public
            if decl.kind == "enum":
                self.variants[key] = frozenset(decl.variants)
            if decl.test and decl.kind in ("function", "method"):
                self.tests.add(key)
            if decl.kind in _CALLABLE:
                self.call_targets.add(key)
            if scope.parent == 0 and decl.kind == "macro":
                self.macros.setdefault(module.crate.root, {}).setdefault(decl.name, []).append(key)
                if decl.public:  # ``#[macro_export]``: a public item of the crate root
                    root = self.layout.roots[module.crate.root]
                    self.items.setdefault(root.key, {}).setdefault(decl.name, []).append(key)
            elif scope.parent == 0 and decl.kind != "impl":
                self.items.setdefault(module.key, {}).setdefault(decl.name, []).append(key)
        for imp in file.parsed.imports:
            home = self.layout.at.get((file.path, imp.module), file.modules[0])
            if imp.scope:
                self.local.setdefault((i, imp.scope), []).append((i, imp))
            elif imp.glob:
                self.globs.setdefault(home.key, []).append((i, imp))
            elif (name := self._binding(imp)) is not None:
                self.explicit.setdefault(home.key, {}).setdefault(name, []).append((i, imp))

    @staticmethod
    def _binding(imp: Import) -> str | None:
        """The name a ``use`` leaf introduces: its alias, else its last segment (the one
        before ``self``); nothing for ``as _``, a glob, or a bare ``crate``/``super``."""
        if imp.glob or imp.alias == "_":
            return None
        path = imp.path[:-1] if imp.path[-1] == "self" and len(imp.path) > 1 else imp.path
        return imp.alias or (path[-1] if path[-1] not in _NO_NAME else None)

    # --- scopes -------------------------------------------------------------------

    def _bound(self, file: _File, k: int, name: str) -> bool:
        while k:
            if name in file.parsed.scopes[k].bound:
                return True
            k = file.parent(k)
        return False

    def _local(self, file: _File, k: int, name: str) -> bool:
        """Whether a use names something bound inside the declaration (a generic, a
        parameter, a local): there is nothing to resolve, so it makes no relation."""
        head = name.split("::", 1)[0]
        return not name.startswith(("::", "self.")) and self._bound(file, k, head)

    def _holder(self, file: _File, k: int) -> int:
        """The enclosing impl or trait of scope ``k`` (itself included), 0 for none."""
        while k:
            decl = file.parsed.scopes[k].decl
            if decl is not None and decl.kind in ("impl", "trait"):
                return k
            k = file.parent(k)
        return 0

    @staticmethod
    def _within(importer: Module, owner: Module) -> bool:
        module: Module | None = importer
        while module is not None:
            if module is owner:
                return True
            module = module.parent
        return False

    # --- name lookup --------------------------------------------------------------

    def names(
        self, mod: Module, name: str, space: Space, importer: Module, seen: Seen
    ) -> list[str]:
        """The items, child modules and imports named ``name`` in ``mod`` that ``importer``
        can see, in ``space``: own items and explicit imports, else the glob imports'."""
        token = (mod.key, name, space)
        if token in seen or len(seen) >= MAX_HOPS:
            self.cuts += 1
            return []
        inside = self._within(importer, mod)
        memo = ("names", *token, inside)
        if (hit := self.cache.get(memo)) is not None:
            return hit
        before, seen = self.cuts, seen | {token}
        allowed, found = _NAMESPACES[space], []
        for key in self.items.get(mod.key, {}).get(name, []):
            if self.kind[key] in allowed and (inside or self.public[key]):
                found.append(key)
        for child in self.layout.children[mod.key].get(name, []):
            if "module" in allowed and (inside or child.public):
                found.append(child.key)
        for i, imp in self.explicit.get(mod.key, {}).get(name, []):
            if inside or imp.public:
                found += [k for k in self._targets(i, imp, seen) if self.kind[k] in allowed]
        if not found:
            for i, imp in self.globs.get(mod.key, []):
                if inside or imp.public:
                    for source in self._targets(i, imp, seen):
                        if (module := self.by_key.get(source)) is not None:
                            found += self.names(module, name, space, mod, seen)
        result = _sorted(found)
        if self.cuts == before:
            self.cache[memo] = result
        return result

    def _targets(self, i: int, imp: Import, seen: Seen) -> list[str]:
        """What a ``use`` leaf names: its path resolved where it is declared."""
        memo = ("use", i, imp.start)
        if (hit := self.cache.get(memo)) is not None:
            return hit
        if (i, imp.start) in self.active:  # a `use` that names itself, directly or in a ring
            self.cuts += 1
            return []
        before, file = self.cuts, self.files[i]
        home = self.layout.at.get((file.path, imp.module), file.modules[0])
        self.active.add((i, imp.start))
        try:
            result = self._resolve(i, imp.scope, _text(imp), "any", home, True, seen) or []
        finally:
            self.active.discard((i, imp.start))
        if self.cuts == before:
            self.cache[memo] = result
        return result

    # --- path resolution ----------------------------------------------------------

    def _owner_types(self, i: int, k: int) -> list[str] | None:
        """``Self`` in scope ``k``: the trait, or the impl's self type."""
        file = self.files[i]
        holder = self._holder(file, k)
        decl = file.parsed.scopes[holder].decl if holder else None
        if decl is None:
            return None
        if decl.kind == "trait":
            return [file.keys[holder]]
        if (i, holder) not in self.impl_types:
            self.impl_types[(i, holder)] = []  # a self type that names itself terminates
            found = self._resolve(i, holder, decl.target, "type") if decl.target else None
            self.impl_types[(i, holder)] = [t for t in found or [] if self.kind[t] in _OWNERS]
        return self.impl_types[(i, holder)] or None

    def assoc(self) -> dict[str, dict[str, list[str]]]:
        """Associated functions and constants by owning type or trait, from every impl and
        trait (built once, with no member lookups through types)."""
        if self._assoc is None:
            self._building = True
            built: dict[str, dict[str, list[str]]] = {}
            for i, file in enumerate(self.files):
                for h, scope in enumerate(file.parsed.scopes):
                    if scope.decl is None or scope.decl.kind not in ("impl", "trait"):
                        continue
                    own = [file.keys[h]] if scope.decl.kind == "trait" else self._owner_types(i, h)
                    if scope.decl.kind == "impl" and scope.decl.trait is None:
                        self.inherent.update(file.keys[c] for c in file.children[h])
                    for child in file.children[h]:
                        for owner in own or []:
                            names = built.setdefault(owner, {}).setdefault(file.name(child), [])
                            names.append(file.keys[child])
            self._building = False
            self._assoc = built
        return self._assoc

    def _member(
        self, owner: str, name: str, space: Space, importer: Module, seen: Seen
    ) -> list[str]:
        kind = self.kind.get(owner)
        if kind == "module":
            return self.names(self.by_key[owner], name, space, importer, seen)
        if kind not in _OWNERS or self._building:
            return []
        if kind == "enum" and name in self.variants.get(owner, ()):
            return [owner]  # a variant is not a symbol: it resolves to its enum
        return [] if space == "type" else self._prefer(self.assoc().get(owner, {}).get(name, []))

    def _prefer(self, found: list[str]) -> list[str]:
        """Inherent associated items shadow a trait's of the same name."""
        return [m for m in found if m in self.inherent] or list(found)

    def _first(
        self,
        i: int,
        k: int,
        name: str,
        space: Space,
        mod: Module,
        absolute: bool,
        use: bool,
        seen: Seen,
    ) -> list[str]:
        file = self.files[i]
        scope = k
        while not absolute:
            for j, imp in self.local.get((i, scope), []):
                if self._binding(imp) == name:
                    found = [
                        t for t in self._targets(j, imp, seen) if self.kind[t] in _NAMESPACES[space]
                    ]
                    if found:
                        return _sorted(found)
            if not scope:
                break
            scope = file.parent(scope)
        deps = dict(mod.crate.deps)
        extern = self.layout.roots.get(deps.get(name, ""))  # the extern prelude
        if (
            extern is not None
            and "module" in _NAMESPACES[space]
            and (absolute or not self.names(mod, name, space, mod, seen))
        ):
            return [extern.key]
        if mod.crate.edition < 2018 and (use or absolute):  # paths start at the crate root
            return self.names(self.layout.roots[mod.crate.root], name, space, mod, seen)
        return [] if absolute else self.names(mod, name, space, mod, seen)

    def _resolve(
        self, i: int, k: int, text: str, space: Space, mod: Module | None = None,
        use: bool = False, seen: Seen = frozenset(),
    ) -> Res:  # fmt: skip
        """Candidate keys of the path ``text`` used in scope ``k`` of file ``i`` (from module
        ``mod``); None when it is opaque or unknown. ``space`` is the last segment's."""
        file = self.files[i]
        mod = mod or file.modules[k]
        absolute = text.startswith("::")
        head, *rest = text.removeprefix("::").split("::")
        if absolute or head not in ("crate", "self", "super", "Self"):
            if self._bound(file, k, head) and not absolute:
                return None
            found = self._first(i, k, head, space if not rest else "type", mod, absolute, use, seen)
        elif head == "Self":
            found = self._owner_types(i, k) or []
        else:
            here: Module | None = self.layout.roots[mod.crate.root] if head == "crate" else mod
            if head == "super":
                here = mod.parent
                while here is not None and rest and rest[0] == "super":
                    here, rest = here.parent, rest[1:]
            found = [here.key] if here is not None else []
        for n, segment in enumerate(rest):
            step: Space = space if n == len(rest) - 1 else "type"
            found = _sorted(
                [m for owner in found for m in self._member(owner, segment, step, mod, seen)]
            )
        return found or None

    # --- macros and methods -------------------------------------------------------

    def _macro(self, i: int, k: int, text: str) -> Res:
        """``name!``, a macro imported or named by path (``#[macro_export]`` ones live at
        their crate root), else any macro of that name in this crate: there is no textual
        scoping, ``macro_use`` or prelude."""
        name = text.removesuffix("!")
        found = [m for m in self._resolve(i, k, name, "any") or [] if self.kind[m] == "macro"]
        if not found and "::" not in name:
            found = self.macros.get(self.files[i].modules[k].crate.root, {}).get(name, [])
        return _sorted(found) or None

    def _method(self, i: int, k: int, name: str) -> Res:
        """``self.name`` inside an impl (its self type's associated functions, then its
        trait's) or a trait (its own)."""
        file = self.files[i]
        holder = self._holder(file, k)
        decl = file.parsed.scopes[holder].decl if holder else None
        if decl is None or self._bound(file, k, "self"):
            return None
        found = [
            m for o in self._owner_types(i, k) or [] for m in self.assoc().get(o, {}).get(name, [])
        ]
        found = self._prefer(found)
        if not any(self.kind[m] == "method" for m in found) and decl.kind == "impl" and decl.trait:
            traits = self._resolve(i, holder, decl.trait, "type") or []
            found = [m for t in traits for m in self.assoc().get(t, {}).get(name, [])]
        return _sorted([m for m in found if self.kind[m] == "method"]) or None

    # --- graph --------------------------------------------------------------------

    def derive(self, entities: list[Entity], edges: list[Edge]) -> None:
        for module in self.layout.modules:
            entities.append(Entity(module.key, "module", "module", module.label, None, None, False))
            edges.append(structural("contains", "repo", module.key, None))
            if module.parent is not None:
                edges.append(structural("contains_logical", module.parent.key, module.key, None))
            if self.layout.at.get((module.file, ())) is module:
                file_key = f"file:{module.file}"
                edges.append(structural("contains_logical", module.key, file_key, None))
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

        for imp in file.parsed.imports:
            found = self._targets(i, imp, frozenset())
            name = _text(imp) + ("::*" if imp.glob else "")
            edges.append(
                edge("imports", file.keys[0], found or None, Use(name, imp.start, imp.end))
            )

        for k, scope in enumerate(file.parsed.scopes):
            key = file.keys[k]
            if scope.decl is not None and scope.parent is not None:
                d = scope.decl
                source = evidence(d.start, d.end)
                test = key in self.tests
                entities.append(Entity(key, "symbol", d.kind, d.name, d.signature, source, test))
                edges.append(structural("contains", file.keys[scope.parent], key, None))
                if scope.parent == 0:
                    edges.append(structural("defines", file.modules[k].key, key, source))
            for ref in scope.refs:
                if self._local(file, k, ref.name):
                    continue
                res = self._resolve(i, k, ref.name, "any")
                symbols = [t for t in res or [] if self.kind[t] != "module"]
                edges.append(edge("references", key, symbols or None, ref))
            for call in scope.calls:
                if self._local(file, k, call.name):
                    continue
                if call.name.endswith("!"):
                    targets = self._macro(i, k, call.name)
                elif call.name.startswith("self."):
                    targets = self._method(i, k, call.name[5:])
                else:
                    targets = self._resolve(i, k, call.name, "value")
                if targets is not None:
                    targets = [t for t in targets if t in self.call_targets] or None
                relation = edge("calls", key, targets, call)
                callee = relation.targets[0] if relation.resolution == "resolved" else None
                if key in self.tests and callee and callee not in self.tests:
                    at = evidence(call.start, call.end)
                    edges.append(
                        Edge("tested_by", callee, (key,), "resolved", "heuristic", None, at)
                    )
                edges.append(relation)
