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

from oxide.core.languages import csharp_emit
from oxide.core.languages.csharp import TYPE_KINDS
from oxide.core.languages.csharp_model import Call, Import, Parsed, Scope
from oxide.core.languages.csharp_scope import (
    NO_BLOCK,
    File,
    Found,
    Site,
    Variable,
    alias_at,
    block_at,
    levels,
    module_key,
    scopes_at,
    symbol_keys,
    visible_blocks,
)
from oxide.core.languages.python import Use
from oxide.core.languages.python_resolve import Edge
from oxide.models import Entity

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


def _is_type(scope: Scope) -> bool:
    return scope.parent is not None and scope.decl is not None and scope.decl.kind in TYPE_KINDS


def _fits(method: Scope, arity: int) -> bool:
    d = method.decl
    if d is None:
        return False
    # A trailing ``params`` and every preceding optional parameter can all be omitted.
    minimum = d.arity - d.optional - (1 if d.varargs else 0)
    return arity >= minimum if d.varargs else minimum <= arity <= d.arity


class Resolver:
    def __init__(self, files: list[tuple[str, str, Parsed]]) -> None:
        self.files: list[File] = []
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
                    # A file-local type is never a part of another file's same-named type.
                    ident = (
                        None if s.parent == 0 else parent,
                        d.ns,
                        d.name,
                        d.tparams,
                        i if d.local else None,
                    )
                    first = identity.setdefault(ident, (i, k))
                    self.primary[i, k] = first
                    self.parts.setdefault(first, []).append((i, k))
                    if first == (i, k) and s.parent == 0:
                        self.tops.setdefault(d.ns, {}).setdefault(d.name, []).append(first)
                if s.parent == 0:
                    spans.append((d.start, d.end, d.ns))
            self.files.append(File(path, digest, parsed, keys, owners, sorted(spans)))
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

    def _visible(self, i: int, sites: list[Site]) -> list[Site]:
        """Captured types visible from file ``i``: another file's file-local type is not."""
        return [s for s in sites if (d := self._scope(s).decl) is None or not d.local or s[0] == i]

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

    def _canonical(self, dotted: str, i: int | None = None) -> list[Site]:
        """A fully qualified type: the longest captured namespace prefix, then members."""
        parts = dotted.split(".")
        for cut in range(len(parts) - 1, -1, -1):
            found = self.tops.get(".".join(parts[:cut]), {}).get(parts[cut], [])
            if i is not None:
                found = self._visible(i, found)
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
        file = self.files[i]
        if dotted.startswith("global::"):
            # Absolute qualification starts at the global namespace, not lexical scope.
            return self._canonical(dotted[len("global::") :], i) or None
        first, *rest = dotted.split(".")
        found = self._simple(i, k, first, ns, at)
        if found:
            return self._nested(found, rest) or None
        if not rest:
            return None
        tail = ".".join(rest)
        if (alias := alias_at(file.aliases, file.parsed.blocks, first, ns, at)) is not None:
            if isinstance(alias, str):
                return self._canonical(f"{alias}.{tail}", i) or None
            return None  # an alias to an external namespace or an uncaptured type
        for level, _block in scopes_at(file.parsed.blocks, ns, at):
            if level and (found := self._canonical(f"{level}.{dotted}", i)):
                return found
        return self._canonical(dotted, i) or None

    def _simple(self, i: int, k: int, name: str, ns: str, at: int | None) -> Found:
        file = self.files[i]
        scopes = file.parsed.scopes
        while k:
            scope = scopes[k]
            if name in scope.types:
                return None  # a type parameter or local function
            if _is_type(scope) and (found := self._member_types((i, k), name, set())):
                return found
            k = scope.parent or 0
        for level, block in scopes_at(file.parsed.blocks, ns, at):
            if top := self._visible(i, self.tops.get(level, {}).get(name, [])):
                return top
            if block == NO_BLOCK:
                continue  # an enclosing namespace level without its own usings
            if (alias := file.aliases.get((block, name))) is not None:
                return (alias or None) if isinstance(alias, list) else None
            imported = {
                site
                for target in file.usings.get(block, [])
                for site in (
                    self.tops.get(target, {}).get(name, [])
                    if isinstance(target, str)
                    else self._member_types(target, name, set())
                )
            }
            if visible := self._visible(i, sorted(imported)):
                return visible
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
        supers, complete = self._supertypes(site)
        found: list[Site] = list(fits)
        for s in supers:
            inherited = self._methods(s, name, arity, done)
            if inherited is None:
                return None if not fits else sorted(set(found))
            found.extend(i for i in inherited if not self._overridden(i, fits))
        # Without argument types an inherited overload may be the better match (an
        # argument cast to ``object``), so own and inherited candidates stay together.
        if found:
            return sorted(set(found))
        implicit = _IMPLICIT.get(scope.decl.kind, _OBJECT)
        return [] if complete and not own and name not in implicit else None

    def _overridden(self, inherited: Site, own: list[Site]) -> bool:
        """An ``override`` of the same parameter types replaces the inherited method;
        a same-arity overload with different types stays a candidate."""
        d = self._scope(inherited).decl
        return d is not None and any(
            (o := self._scope(s).decl) is not None
            and o.arity == d.arity
            and o.ptypes == d.ptypes
            and " override " in f" {o.signature} "
            for s in own
        )

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

    def _variable(self, i: int, k: int, name: str, at: int) -> Variable | Found:
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
        demand = [
            s
            for block in visible_blocks(self.files[i].parsed.blocks, at)
            for s in self.files[i].static_demand.get(block, [])
        ]
        if any(self._field(s, name, set()) not in ([], None) for s in demand):
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

    def _static(self, i: int, at: int, name: str, arity: int) -> Found:
        """Unqualified calls from ``using static``: only imports visible at ``at`` count."""
        file = self.files[i]
        for block in visible_blocks(file.parsed.blocks, at):
            targets = file.static_demand.get(block, [])
            if not targets:
                continue
            found = self._on(targets, name, arity)
            if found:
                return found
            if found is None:  # a base outside the capture could declare it
                return None
        return []

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
            return self._static(i, call.start, name, call.arity)
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
            variable = self._variable(i, k, first, call.start)
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

    def _relative(self, i: int, level: str, dotted: str) -> tuple[str | None, list[Site]]:
        """What a using path names from namespace ``level``: its namespace and the
        types, resolved outward through the enclosing namespaces."""
        absolute = dotted.startswith("global::")
        if absolute:
            dotted = dotted[len("global::") :]
        for outer in [""] if absolute else levels(level):
            path = f"{outer}.{dotted}" if outer else dotted
            sites = self._canonical(path, i)
            if path in self.tops or sites:
                return (path if path in self.tops else None), sites
        return None, []

    def _bind_imports(self, i: int, file: File) -> None:
        for owner, item in [*((i, m) for m in file.parsed.imports), *self.globals]:
            if owner != i and not item.is_global:
                continue
            level = "" if item.is_global else item.ns
            block = None if item.is_global else block_at(file.parsed.blocks, item.start)
            namespace, sites = self._relative(i, level, item.path)
            if item.kind == "alias":
                file.aliases[block, item.alias] = namespace if namespace is not None else sites
            elif item.kind == "static":
                file.static_demand.setdefault(block, []).extend(sites)
                file.usings.setdefault(block, []).extend(sites)
            elif namespace is not None:
                file.usings.setdefault(block, []).append(namespace)

    def _import_targets(self, i: int, item: Import) -> list[str]:
        """What a using names: a namespace's module or a type."""
        namespace, sites = self._relative(i, "" if item.is_global else item.ns, item.path)
        if namespace is not None and item.kind != "static":
            return [module_key(namespace)]
        return self._keys(sites)

    def _keys(self, sites: list[Site]) -> list[str]:
        return sorted({str(self.files[i].keys[k]) for i, k in sites})

    def derive(self, entities: list[Entity], edges: list[Edge]) -> None:
        csharp_emit.derive(self, entities, edges)

    def _attribute(self, i: int, k: int, use: Use) -> Found:
        return csharp_emit.attribute(self, i, k, use)
