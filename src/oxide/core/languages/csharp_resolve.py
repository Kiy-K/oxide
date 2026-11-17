"""Conservative C# resolution over every ``.cs`` file of one capture (docs/csharp.md).

A namespace is every type declared in it across files, keyed ``module:csharp:<ns>``; a
partial type's parts form one type, whose entity is its first part. Names resolve the way
the C# spec scopes them, over captured source only: enclosing types and their captured
base types, then for each enclosing namespace outward the types declared in it, then the
usings and aliases written in that namespace block (global usings reach every file). A
member lookup that reaches a base type outside the capture, or an extension method, is
unknown and stays unresolved rather than guessed.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field

from oxide.core.languages.csharp import TYPE_KINDS, Call, Import, Parsed, Scope
from oxide.core.languages.python import Use
from oxide.core.languages.python_resolve import Edge, Res, edge_target, structural
from oxide.models import Entity, RelationKind, SourceRef

# Methods a type has without declaring them, by kind.
_OBJECT = frozenset(
    {"Equals", "GetHashCode", "GetType", "ToString", "MemberwiseClone", "Finalize"}
    | {"ReferenceEquals"}
)
_IMPLICIT = {
    "enum": _OBJECT | {"CompareTo", "HasFlag", "GetTypeCode"},
    "record": _OBJECT | {"Deconstruct", "PrintMembers"},
    "delegate": _OBJECT | {"Invoke", "BeginInvoke", "EndInvoke", "DynamicInvoke", "Clone"},
}
_METHOD = frozenset({"method"})

type Site = tuple[int, int]  # (file, scope)
type Found = list[Site] | None  # None: unknown, so unresolved; []: certainly none
type Variable = tuple[Site, str | None]  # the declaring scope and the declared type
type Alias = list[Site] | str  # a type ([] when not captured) or a namespace
type Using = str | Site  # a namespace, or a statically imported type


def module_key(namespace: str) -> str:
    return f"module:csharp:{namespace}"


def symbol_keys(path: str, parsed: Parsed) -> list[str | None]:
    """Per scope, ``symbol:<path>#Outer.Inner.m``: the file key for scope 0. Equal
    segments (overloads, constructors' names aside) take ``~n`` in source order, and
    members nest under their owner's segment, ordinal included."""
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


def _levels(namespace: str) -> list[str]:
    """``A.B`` -> ``A.B``, ``A``, the global namespace."""
    parts = namespace.split(".") if namespace else []
    return [".".join(parts[:n]) for n in range(len(parts), -1, -1)]


@dataclass(slots=True)
class _File:
    path: str
    digest: str
    parsed: Parsed
    keys: list[str | None]
    owners: list[str]  # per scope, the key of the nearest declared scope (or the file)
    spans: list[tuple[int, int, str]]  # top-level declarations: (start, end, namespace)
    aliases: dict[tuple[str, str], Alias] = field(default_factory=dict)  # (level, alias)
    usings: dict[str, list[Using]] = field(default_factory=dict)  # level -> imported
    static_demand: list[Site] = field(default_factory=list)


def _is_type(scope: Scope) -> bool:
    return scope.parent is not None and scope.decl is not None and scope.decl.kind in TYPE_KINDS


def _fits(method: Scope, arity: int) -> bool:
    d = method.decl
    if d is None:
        return False
    return d.arity - d.optional <= arity <= d.arity or (d.varargs and arity >= d.arity - 1)


class Resolver:
    def __init__(self, files: list[tuple[str, str, Parsed]]) -> None:
        self.files: list[_File] = []
        self.tops: dict[str, dict[str, list[Site]]] = {}  # namespace -> type name -> types
        self.children: dict[Site, dict[str, list[int]]] = {}  # declared members by name
        self.primary: dict[Site, Site] = {}  # a part of a partial type -> its first part
        self.parts: dict[Site, list[Site]] = {}  # first part -> every part
        self.supers: dict[Site, tuple[list[Site], bool]] = {}  # memo: (captured, complete)
        self.tests: set[str] = set()
        identity: dict[tuple[object, ...], Site] = {}
        for path, digest, parsed in files:
            i, keys = len(self.files), symbol_keys(path, parsed)
            owners, spans = [str(keys[0])], []
            for k, s in enumerate(parsed.scopes[1:], 1):
                owners.append(keys[k] or owners[s.parent or 0])
                d = s.decl
                if d is None or s.parent is None:
                    continue
                self.children.setdefault((i, s.parent), {}).setdefault(d.name, []).append(k)
                if d.test:
                    self.tests.add(owners[k])
                if d.kind in TYPE_KINDS:
                    parent = self.primary.get((i, s.parent), (i, s.parent))
                    ident = (None if s.parent == 0 else parent, d.ns, d.name, d.tparams)
                    first = identity.setdefault(ident, (i, k))
                    self.primary[i, k] = first
                    self.parts.setdefault(first, []).append((i, k))
                    if first == (i, k) and s.parent == 0:
                        self.tops.setdefault(d.ns, {}).setdefault(d.name, []).append(first)
                if s.parent == 0:
                    spans.append((d.start, d.end, d.ns))
            self.files.append(_File(path, digest, parsed, keys, owners, sorted(spans)))
        self.globals = [
            (i, m) for i, f in enumerate(self.files) for m in f.parsed.imports if m.is_global
        ]
        for i, file in enumerate(self.files):
            self._bind_imports(i, file)

    # --- types ---

    def _scope(self, site: Site) -> Scope:
        return self.files[site[0]].parsed.scopes[site[1]]

    def _parts(self, site: Site) -> list[Site]:
        return self.parts.get(self.primary.get(site, site), [site])

    def _declared(self, site: Site, name: str, kinds: frozenset[str]) -> list[Site]:
        found: set[Site] = set()
        for part in self._parts(site):
            scopes = self.files[part[0]].parsed.scopes
            for k in self.children.get(part, {}).get(name, []):
                if (d := scopes[k].decl) is not None and d.kind in kinds:
                    found.add(self.primary.get((part[0], k), (part[0], k)))
        return sorted(found)

    def _ns(self, i: int, k: int, at: int | None) -> str:
        """The namespace scope ``k`` of file ``i`` is in; at file scope, that of the
        declaration holding position ``at`` (a base list or attribute)."""
        file = self.files[i]
        decl = file.parsed.scopes[k].decl
        if decl is not None:
            return decl.ns
        row = bisect.bisect_right(file.spans, (at if at is not None else -1, "\U0010ffff", "")) - 1
        return file.spans[row][2] if at is not None and row >= 0 and at < file.spans[row][1] else ""

    def _canonical(self, dotted: str) -> list[Site]:
        """A fully qualified type: the longest captured namespace prefix, then members."""
        parts = dotted.split(".")
        for cut in range(len(parts) - 1, -1, -1):
            found = self.tops.get(".".join(parts[:cut]), {}).get(parts[cut], [])
            if found:
                return self._nested(found, parts[cut + 1 :])
        return []

    def _nested(self, sites: list[Site], names: list[str]) -> list[Site]:
        for name in names:
            sites = sorted({m for s in sites for m in self._member_types(s, name, set())})
        return sites

    def _member_types(self, site: Site, name: str, seen: set[Site]) -> list[Site]:
        """Member types ``name``, declared or inherited from captured base types (one
        outside the capture is assumed to declare none: docs/csharp.md § Not modeled)."""
        found = self._declared(site, name, TYPE_KINDS)
        if found or site in seen:
            return found
        seen.add(site)
        inherited = {
            m for s in self._supertypes(site)[0] for m in self._member_types(s, name, seen)
        }
        return sorted(inherited)

    def _supertypes(self, site: Site) -> tuple[list[Site], bool]:
        """Captured direct base types of every part, and whether each named one is captured
        (a cycle or an ambiguous name leaves the hierarchy incomplete)."""
        site = self.primary.get(site, site)
        if site in self.supers:
            return self.supers[site]
        self.supers[site] = ([], False)  # in progress: a cycle is unknown
        found: set[Site] = set()
        complete = True
        for part in self._parts(site):
            scope = self._scope(part)
            for use in scope.supers:
                sites = self._type(part[0], scope.parent or 0, use.name, use.start)
                if sites is not None and len(sites) == 1:
                    found.add(sites[0])
                else:
                    complete = False
        self.supers[site] = (sorted(found), complete)
        return self.supers[site]

    def _type(self, i: int, k: int, dotted: str, at: int | None = None) -> Found:
        """The types a (possibly qualified) type name denotes in scope ``k`` of file ``i``;
        ``at`` locates a use at file scope."""
        ns = self._ns(i, k, at)
        first, *rest = dotted.split(".")
        found = self._simple(i, k, first, ns)
        if found:
            return self._nested(found, rest) or None
        if not rest:
            return found
        file, tail = self.files[i], ".".join(rest)
        levels = _levels(ns)
        for level in levels:
            if isinstance(alias := file.aliases.get((level, first)), str):
                return self._canonical(f"{alias}.{tail}") or None
        for level in levels:
            if level and (found := self._canonical(f"{level}.{dotted}")):
                return found
        return self._canonical(dotted) or None

    def _simple(self, i: int, k: int, name: str, ns: str) -> Found:
        file = self.files[i]
        scopes = file.parsed.scopes
        while k:
            scope = scopes[k]
            if name in scope.types:
                return None  # a type parameter or local function
            if _is_type(scope) and (found := self._member_types((i, k), name, set())):
                return found
            k = scope.parent or 0
        for level in _levels(ns):
            if top := self.tops.get(level, {}).get(name):
                return top
            if (alias := file.aliases.get((level, name))) is not None:
                return (alias or None) if isinstance(alias, list) else None
            imported = {
                site
                for target in file.usings.get(level, [])
                for site in (
                    self.tops.get(target, {}).get(name, [])
                    if isinstance(target, str)
                    else self._member_types(target, name, set())
                )
            }
            if imported:
                return sorted(imported)
        return None

    # --- members ---

    def _methods(self, site: Site, name: str, arity: int, done: dict[Site, Found]) -> Found:
        """Methods ``name`` of a type accepting ``arity`` arguments, from the nearest
        declaring type of its captured hierarchy; [] when it certainly has none. ``done``
        memoizes each type, so a diamond reuses a finished branch and a cycle is None."""
        site = self.primary.get(site, site)
        if site not in done:
            done[site] = None  # in progress: reaching it again is a cycle
            done[site] = self._own_methods(site, name, arity, done)
        return done[site]

    def _own_methods(self, site: Site, name: str, arity: int, done: dict[Site, Found]) -> Found:
        scope = self._scope(site)
        if scope.decl is None:
            return None
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
        return [] if complete and not own and name not in implicit else None

    def _field(self, site: Site, name: str, seen: set[Site]) -> Variable | Found:
        """Field, property or constant ``name`` of a type, declared or inherited; []
        when certainly none."""
        site = self.primary.get(site, site)
        for part in self._parts(site):
            if name in (bound := self._scope(part).bound):
                return part, bound[name]
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
        if any(self._field(s, name, set()) not in ([], None) for s in self.files[i].static_demand):
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
                if name in scopes[at].types or name in scopes[at].bound:
                    return None  # a local function, or a delegate-valued variable
                if _is_type(scopes[at]):
                    found = self._methods((i, at), name, call.arity, {})
                    if found is None or found:
                        return found
                at = scopes[at].parent or 0
            return self._on(file.static_demand, name, call.arity)
        inner = k
        while inner and not _is_type(scopes[inner]):
            inner = scopes[inner].parent or 0
        if not inner:
            return None
        if qualifier == "this":
            return self._on([(i, inner)], name, call.arity)
        if qualifier == "base":
            supers, complete = self._supertypes((i, inner))
            return self._on(supers, name, call.arity) if complete else None
        if qualifier.startswith("this."):
            variable = self._field((i, inner), qualifier[5:], set())
        else:
            first, dot, _ = qualifier.partition(".")
            variable = self._variable(i, k, first)
            if variable == []:  # a type (a static method), or a type's static field
                types = self._type(i, k, qualifier, call.start)
                owner, _, member = qualifier.rpartition(".")
                owners = None if types or not dot else self._type(i, k, owner, call.start)
                if owners is None or len(owners) != 1:
                    return self._on(types, name, call.arity)
                variable = self._field(owners[0], member, set())
            elif dot:
                return None  # a member of a variable: needs its type's members
        if not isinstance(variable, tuple):
            return None
        (vi, vk), kind = variable
        return None if kind is None else self._on(self._type(vi, vk, kind), name, call.arity)

    # --- imports and edges ---

    def _relative(self, level: str, dotted: str) -> tuple[str | None, list[Site]]:
        """What a using path names from namespace block ``level``: its namespace and the
        types, resolved outward through the enclosing namespaces."""
        for outer in _levels(level):
            path = f"{outer}.{dotted}" if outer else dotted
            sites = self._canonical(path)
            if path in self.tops or sites:
                return (path if path in self.tops else None), sites
        return None, []

    def _bind_imports(self, i: int, file: _File) -> None:
        for owner, item in [*((i, m) for m in file.parsed.imports), *self.globals]:
            if owner != i and not item.is_global:
                continue
            level = "" if item.is_global else item.ns
            namespace, sites = self._relative(level, item.path)
            if item.kind == "alias":
                file.aliases[level, item.alias] = namespace if namespace is not None else sites
            elif item.kind == "static":
                file.static_demand.extend(sites)
                file.usings.setdefault(level, []).extend(sites)
            elif namespace is not None:
                file.usings.setdefault(level, []).append(namespace)

    def _import_targets(self, item: Import) -> list[str]:
        """What a using names: a namespace's module or a type."""
        namespace, sites = self._relative("" if item.is_global else item.ns, item.path)
        if namespace is not None and item.kind != "static":
            return [module_key(namespace)]
        return self._keys(sites)

    def _keys(self, sites: list[Site]) -> list[str]:
        return sorted({str(self.files[i].keys[k]) for i, k in sites})

    def derive(self, entities: list[Entity], edges: list[Edge]) -> None:
        for namespace in sorted(n for n in self.tops if n):
            key = module_key(namespace)
            entities.append(Entity(key, "module", "module", namespace, None, None, False))
            edges.append(structural("contains", "repo", key, None))
        for i, file in enumerate(self.files):
            for ns in sorted({n for _, _, n in file.spans if n}):
                edges.append(
                    structural("contains_logical", module_key(ns), f"file:{file.path}", None)
                )
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
            res = self._import_targets(item) or None
            edges.append(edge("imports", file.owners[0], res, Use(item.path, item.start, item.end)))

        # A declaration's modifiers lie in its parent scope (csharp._Facts._open) yet are its own.
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
                if scope.parent == 0 and d.ns:
                    edges.append(structural("defines", module_key(d.ns), key, at))
            for ref in scope.refs:
                found = self._type(i, k, ref.name, ref.start)
                edges.append(
                    edge("references", owner(k, ref.start), self._keys(found or []) or None, ref)
                )
            for attr in scope.attrs:
                found = self._attribute(i, k, attr)
                edges.append(
                    edge("references", owner(k, attr.start), self._keys(found or []) or None, attr)
                )
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

    def _attribute(self, i: int, k: int, use: Use) -> Found:
        """``[N]`` names type ``N`` or ``NAttribute``; both existing is ambiguous."""
        plain = self._type(i, k, use.name, use.start) or []
        suffixed = self._type(i, k, use.name + "Attribute", use.start) or []
        return sorted({*plain, *suffixed}) or None
