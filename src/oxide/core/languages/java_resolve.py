"""Conservative Java resolution over every ``.java`` file of one capture (docs/java.md).

A package is the files sharing a package declaration, keyed ``module:java:<package>``;
default-package files form one package per directory and no module. Names resolve the
way javac scopes them, over captured source only: enclosing classes and their captured
supertypes, the file's types and single-type imports, the package, then on-demand
imports. A member lookup that reaches a supertype outside the capture, or a local or
anonymous class, is unknown and stays unresolved rather than guessed.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field

from oxide.core.languages.java import TYPE_KINDS, Call, Parsed, Scope
from oxide.core.languages.python import Use
from oxide.core.languages.python_resolve import Edge, Res, edge_target, structural
from oxide.models import Entity, RelationKind, SourceRef

# Methods a type has without declaring them, by kind.
_OBJECT = frozenset(
    {"clone", "equals", "finalize", "getClass", "hashCode", "notify", "notifyAll", "toString"}
    | {"wait"}
)
_ENUM = {"compareTo", "describeConstable", "getDeclaringClass", "name", "ordinal", "valueOf"}
_IMPLICIT = {"enum": _OBJECT | _ENUM | {"values"}, "annotation": _OBJECT | {"annotationType"}}
_METHOD = frozenset({"method"})
_IMPORTABLE = frozenset(TYPE_KINDS | _METHOD)

type Site = tuple[int, int]  # (file, scope)
type Package = tuple[str, str]  # ("", package), or (directory, "") for the default package
type Found = list[Site] | None  # None: unknown, so unresolved; []: certainly none
type Variable = tuple[Site, str | None]  # the declaring scope and the declared type


def module_key(package: str) -> str:
    return f"module:java:{package}"


def symbol_keys(path: str, parsed: Parsed) -> list[str | None]:
    """Per scope, ``symbol:<path>#Outer.Inner.m``: the file key for scope 0, None for a
    local or anonymous class. Equal segments (overloads) take ``~n`` in source order,
    and members nest under their owner's segment, ordinal included."""
    keys: list[str | None] = [f"file:{path}"]
    segments = [""]
    seen: dict[str, int] = {}
    for s in parsed.scopes[1:]:
        if s.decl is None or s.parent is None:
            keys.append(None)
            segments.append("")
            continue
        owner = segments[s.parent]
        segment = f"{owner}.{s.decl.name}" if owner else s.decl.name
        n = seen.get(segment, 0)
        seen[segment] = n + 1
        segment += f"~{n}" if n else ""
        segments.append(segment)
        keys.append(f"symbol:{path}#{segment}")
    return keys


@dataclass(slots=True)
class _File:
    path: str
    digest: str
    parsed: Parsed
    keys: list[str | None]
    owners: list[str]  # per scope, the key of the nearest declared scope (or the file)
    package: Package
    single: dict[str, list[Site]] = field(default_factory=dict)  # [] when not captured
    demand: list[Site | str] = field(default_factory=list)  # a type, or a package
    statics: dict[str, list[Site]] = field(default_factory=dict)  # static member -> owners
    static_demand: list[Site] = field(default_factory=list)


def _is_type(scope: Scope) -> bool:
    """A declared type or a local or anonymous class body."""
    return scope.parent is not None and (scope.decl is None or scope.decl.kind in TYPE_KINDS)


def _fits(method: Scope, arity: int) -> bool:
    d = method.decl
    return d is not None and (arity == d.arity or (d.varargs and arity >= d.arity - 1))


class Resolver:
    def __init__(self, files: list[tuple[str, str, Parsed]]) -> None:
        self.files: list[_File] = []
        self.packages: dict[Package, list[int]] = {}
        self.tops: dict[Package, dict[str, list[Site]]] = {}  # top-level types by name
        self.children: dict[Site, dict[str, list[int]]] = {}  # declared members by name
        self.supers: dict[Site, tuple[list[Site], bool]] = {}  # memo: (captured, complete)
        self.tests: set[str] = set()
        for path, digest, parsed in files:
            i, keys = len(self.files), symbol_keys(path, parsed)
            package = ("", parsed.package) if parsed.package else (path.rpartition("/")[0], "")
            owners = [str(keys[0])]
            for k, s in enumerate(parsed.scopes[1:], 1):
                owners.append(keys[k] or owners[s.parent or 0])
                if s.decl is None or s.parent is None:
                    continue
                self.children.setdefault((i, s.parent), {}).setdefault(s.decl.name, []).append(k)
                if s.parent == 0:
                    self.tops.setdefault(package, {}).setdefault(s.decl.name, []).append((i, k))
                if s.decl.test:
                    self.tests.add(owners[k])
            self.files.append(_File(path, digest, parsed, keys, owners, package))
            self.packages.setdefault(package, []).append(i)
        for file in self.files:
            self._bind_imports(file)

    # --- types ---

    def _scope(self, site: Site) -> Scope:
        return self.files[site[0]].parsed.scopes[site[1]]

    def _declared(self, site: Site, name: str, kinds: frozenset[str]) -> list[Site]:
        scopes = self.files[site[0]].parsed.scopes
        return [
            (site[0], k)
            for k in self.children.get(site, {}).get(name, [])
            if (d := scopes[k].decl) is not None and d.kind in kinds
        ]

    def _canonical(self, dotted: str) -> list[Site]:
        """A fully qualified type: the longest captured package prefix, then members."""
        parts = dotted.split(".")
        for cut in range(len(parts) - 1, 0, -1):
            found = self.tops.get(("", ".".join(parts[:cut])), {}).get(parts[cut], [])
            if found:
                return self._nested(found, parts[cut + 1 :])
        return []

    def _nested(self, sites: list[Site], names: list[str]) -> list[Site]:
        for name in names:
            sites = sorted({m for s in sites for m in self._member_types(s, name, set())})
        return sites

    def _member_types(self, site: Site, name: str, seen: set[Site]) -> list[Site]:
        """Member types ``name``, declared or inherited from captured supertypes (one
        outside the capture is assumed to declare none: docs/java.md § Not modeled)."""
        found = self._declared(site, name, TYPE_KINDS)
        if found or site in seen:
            return found
        seen.add(site)
        inherited = {
            m for s in self._supertypes(site)[0] for m in self._member_types(s, name, seen)
        }
        return sorted(inherited)

    def _supertypes(self, site: Site) -> tuple[list[Site], bool]:
        """Captured direct supertypes, and whether every named one is captured (a cycle
        or an ambiguous name leaves the hierarchy incomplete)."""
        if site in self.supers:
            return self.supers[site]
        self.supers[site] = ([], False)  # in progress: a cycle is unknown
        scope = self._scope(site)
        found: list[Site] = []
        complete = True
        for use in scope.supers:
            sites = self._type(site[0], scope.parent or 0, use.name)
            if sites is not None and len(sites) == 1:
                found.append(sites[0])
            else:
                complete = False
        self.supers[site] = (found, complete)
        return found, complete

    def _type(self, i: int, k: int, dotted: str) -> Found:
        """The types a (possibly qualified) type name denotes in scope ``k`` of file ``i``."""
        first, *rest = dotted.split(".")
        found = self._simple(i, k, first)
        if found:
            return self._nested(found, rest) or None
        return (self._canonical(dotted) or None) if rest else found

    def _simple(self, i: int, k: int, name: str) -> Found:
        file = self.files[i]
        scopes = file.parsed.scopes
        while k:
            scope = scopes[k]
            if name in scope.types:
                return None  # a type parameter or local class
            if _is_type(scope) and (found := self._member_types((i, k), name, set())):
                return found
            k = scope.parent or 0
        found = self._declared((i, 0), name, TYPE_KINDS)
        if found:
            return found
        if name in file.single:
            return file.single[name] or None  # an import outside the capture shadows too
        found = self.tops.get(file.package, {}).get(name, [])
        if found:
            return found
        demand = {
            site
            for target in file.demand
            for site in (
                self.tops.get(("", target), {}).get(name, [])
                if isinstance(target, str)
                else self._member_types(target, name, set())
            )
        }
        return sorted(demand) or None

    # --- members ---

    def _methods(self, site: Site, name: str, arity: int, done: dict[Site, Found]) -> Found:
        """Methods ``name`` of a type accepting ``arity`` arguments, from the nearest
        declaring type of its captured hierarchy; [] when it certainly has none. ``done``
        memoizes each type, so a diamond reuses a finished branch and a cycle is None."""
        if site not in done:
            done[site] = None  # in progress: reaching it again is a cycle
            done[site] = self._own_methods(site, name, arity, done)
        return done[site]

    def _own_methods(self, site: Site, name: str, arity: int, done: dict[Site, Found]) -> Found:
        scope = self._scope(site)
        if scope.decl is None:
            return None  # a local or anonymous class's members are not facts
        own = self._declared(site, name, _METHOD)
        fits = [s for s in own if _fits(self._scope(s), arity)]
        if fits:
            return fits
        supers, complete = self._supertypes(site)
        found: list[Site] = []
        for s in supers:
            inherited = self._methods(s, name, arity, done)
            if inherited is None:
                return None
            found.extend(inherited)
        if found:
            return sorted(set(found))
        implicit = _IMPLICIT.get(scope.decl.kind, _OBJECT)
        if scope.decl.kind == "record":
            implicit = implicit | scope.bound.keys()  # component accessors
        return [] if complete and not own and name not in implicit else None

    def _field(self, site: Site, name: str, seen: set[Site]) -> Variable | Found:
        """Field ``name`` of a type, declared or inherited; [] when certainly none. A
        local or anonymous class body's variables are its fields and locals together."""
        scope = self._scope(site)
        if name in scope.bound:
            return site, scope.bound[name]
        if site in seen:
            return None
        seen.add(site)
        supers, complete = self._supertypes(site)
        for s in supers:
            found = self._field(s, name, seen)
            if found != []:
                return found
        return [] if complete else None

    def _variable(self, i: int, k: int, name: str) -> Variable | Found:
        """The variable ``name`` denotes in scope ``k``; [] when it is no variable."""
        scopes = self.files[i].parsed.scopes
        while k:
            scope = scopes[k]
            if _is_type(scope):
                found = self._field((i, k), name, set())
                if found != []:
                    return found
            elif name in scope.bound:
                return (i, k), scope.bound[name]
            k = scope.parent or 0
        file = self.files[i]
        if name in file.statics or any(
            self._field(s, name, set()) not in ([], None) for s in file.static_demand
        ):
            return None  # a statically imported field
        return []

    def _on(self, types: Found, name: str, arity: int) -> Found:
        """Methods ``name`` of any of ``types``."""
        found: list[Site] = []
        for t in types or []:
            methods = self._methods(t, name, arity, {})
            if methods is None:
                return None
            found.extend(methods)
        return sorted(set(found)) or None

    def _call(self, i: int, k: int, call: Call) -> Found:
        if call.name.startswith("new "):
            return self._type(i, k, call.name[4:])
        file = self.files[i]
        scopes = file.parsed.scopes
        qualifier, _, name = call.name.rpartition(".")
        if not qualifier:
            at = k
            while at:
                if _is_type(scopes[at]):
                    found = self._methods((i, at), name, call.arity, {})
                    if found is None or found:
                        return found
                at = scopes[at].parent or 0
            return self._on(file.statics.get(name, file.static_demand), name, call.arity)
        inner = k
        while inner and not _is_type(scopes[inner]):
            inner = scopes[inner].parent or 0
        if not inner:
            return None
        if qualifier == "this":
            return self._on([(i, inner)], name, call.arity)
        if qualifier == "super":
            supers, complete = self._supertypes((i, inner))
            return self._on(supers, name, call.arity) if complete else None
        if qualifier.startswith("this."):
            variable = self._field((i, inner), qualifier[5:], set())
        else:
            first, dot, _ = qualifier.partition(".")
            variable = self._variable(i, k, first)
            if variable == []:  # a type (a static method), or a type's static field
                types = self._type(i, k, qualifier)
                owner, _, member = qualifier.rpartition(".")
                owners = None if types or not dot else self._type(i, k, owner)
                if owners is None or len(owners) != 1:
                    return self._on(types, name, call.arity)
                variable = self._field(owners[0], member, set())
            elif dot:
                return None  # a field of a variable: needs its type's fields
        if not isinstance(variable, tuple):
            return None
        (vi, vk), kind = variable
        return None if kind is None else self._on(self._type(vi, vk, kind), name, call.arity)

    # --- imports and edges ---

    def _bind_imports(self, file: _File) -> None:
        for item in file.parsed.imports:
            path = item.path
            if item.static:
                owner, _, member = path.rpartition(".")
                owners = self._canonical(path if item.wildcard else owner)
                if item.wildcard:
                    file.static_demand.extend(owners)
                else:
                    file.statics.setdefault(member, []).extend(owners)
            elif item.wildcard:
                package = ("", path) in self.packages
                file.demand.extend([path] if package else self._canonical(path))
            else:
                file.single.setdefault(path.rpartition(".")[2], []).extend(self._canonical(path))

    def _import_targets(self, path: str, static: bool, wildcard: bool) -> list[str]:
        """What an import names: a package's module, a type, or a static member (a
        method or member type; the owner type for a field)."""
        if wildcard and not static and ("", path) in self.packages:
            return [module_key(path)]
        if wildcard or not static:
            return self._keys(self._canonical(path))
        owner, _, member = path.rpartition(".")
        owners = self._canonical(owner)
        named = [m for o in owners for m in self._declared(o, member, _IMPORTABLE)]
        return self._keys(named or [o for o in owners if member in self._scope(o).bound])

    def _keys(self, sites: list[Site]) -> list[str]:
        return sorted({str(self.files[i].keys[k]) for i, k in sites})

    def derive(self, entities: list[Entity], edges: list[Edge]) -> None:
        for package in sorted(p for p in self.packages if p[1]):
            key = module_key(package[1])
            entities.append(Entity(key, "module", "module", package[1], None, None, False))
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

        def edge(kind: RelationKind, source: str, res: Res, use: Use | Call) -> Edge:
            targets, resolution, basis = edge_target(res, kind == "calls")
            shown = None if resolution == "resolved" else use.name
            at = evidence(use.start, use.end)
            return Edge(kind, source, targets, resolution, basis, shown, at)

        for item in file.parsed.imports:
            res = self._import_targets(item.path, item.static, item.wildcard) or None
            edges.append(edge("imports", file.owners[0], res, Use(item.path, item.start, item.end)))

        # A declaration's modifiers lie in its parent scope (java._Facts._open) yet are its own.
        heads: dict[int, list[tuple[int, int, int]]] = {}
        for k, scope in enumerate(file.parsed.scopes):
            if k and scope.decl is not None and scope.parent is not None:
                heads.setdefault(scope.parent, []).append((scope.decl.start, scope.decl.end, k))

        def owner(k: int, at: int) -> str:
            row = heads.get(k, [])
            j = bisect.bisect_right(row, (at + 1,)) - 1
            return file.owners[row[j][2] if j >= 0 and at < row[j][1] else k]

        for k, scope in enumerate(file.parsed.scopes):
            key = file.keys[k]
            if k and key is not None and scope.decl is not None:
                d = scope.decl
                at = evidence(d.start, d.end)
                entities.append(Entity(key, "symbol", d.kind, d.name, d.signature, at, d.test))
                edges.append(structural("contains", file.owners[scope.parent or 0], key, None))
                if scope.parent == 0 and file.package[1]:
                    edges.append(structural("defines", module_key(file.package[1]), key, at))
            for ref in scope.refs:
                found = self._type(i, k, ref.name)
                source = owner(k, ref.start)
                edges.append(edge("references", source, self._keys(found or []) or None, ref))
            for call in scope.calls:
                found = self._call(i, k, call)
                source = owner(k, call.start)
                relation = edge("calls", source, self._keys(found or []) or None, call)
                callee = relation.targets[0] if relation.resolution == "resolved" else None
                if source in self.tests and callee and callee not in self.tests:
                    at = evidence(call.start, call.end)
                    edges.append(
                        Edge("tested_by", callee, (source,), "resolved", "heuristic", None, at)
                    )
                edges.append(relation)
