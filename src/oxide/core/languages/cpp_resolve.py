"""Conservative C++ resolution over every C++ file of one capture (docs/cpp.md).

Includes and visibility are C's (``c_resolve``): a file sees what its includes reach,
transitively, C headers included, whose declarations are global names. Names are
looked up as C++ does, without types or overload resolution: an unqualified name in
its symbol's own scope, then the enclosing classes and their captured bases, then the
enclosing namespaces outwards, the global namespace and ``using namespace`` targets; a
qualified name relative to the same scopes, or from the global namespace after ``::``.
The innermost scope declaring the name decides, and arity narrows its functions.
"""

from __future__ import annotations

from dataclasses import dataclass

from oxide.core.languages import c, c_resolve, cpp
from oxide.core.languages.java import Call
from oxide.core.languages.python import Use
from oxide.core.languages.python_resolve import Edge, Res, edge_target, structural
from oxide.models import Entity, RelationKind, SourceRef

type Site = tuple[int, int]  # (file, scope)
TYPES = ("class", "struct", "union", "enum", "type")
_MISSING: Res = ["<no declaration>"]  # a scope that declares nothing of the name


@dataclass(slots=True)
class _Sym:
    qname: str
    kind: str
    static: bool
    defines: bool
    low: int
    high: int  # -1: any number of arguments


@dataclass(slots=True)
class _File:
    path: str
    digest: str
    parsed: cpp.Parsed | None  # None for a C file: visible here, emitted by c_resolve
    keys: list[str]
    includes: list[list[str]]
    syms: list[_Sym | None]


def _join(*parts: str) -> str:
    return "::".join(p for p in parts if p)


def symbol_keys(path: str, parsed: cpp.Parsed) -> tuple[list[str], list[_Sym | None]]:
    """The file key then each scope's ``symbol:<path>#<segment>``, nested as written
    (``shop.Box.get``; an out-of-line ``Box::get`` in ``namespace shop`` is
    ``shop.Box.get`` too), equal segments taking ``~n`` in source order; and each
    scope's qualified name."""
    keys, segments, qnames = [f"file:{path}"], [""], [""]
    syms: list[_Sym | None] = [None]
    seen: dict[str, int] = {}
    for scope in parsed.scopes[1:]:
        d = scope.decl
        assert d is not None
        qualifier = [p for p in d.qualifier.split("::") if p]
        local = ".".join(p.replace(".", "_").replace("~", "_") for p in [*qualifier, d.name])
        segment = f"{segments[scope.parent]}.{local}" if scope.parent else local
        n = seen.get(segment, 0)
        seen[segment] = n + 1
        segments.append(segment + (f"~{n}" if n else ""))
        keys.append(f"symbol:{path}#{segments[-1]}")
        outer = "" if d.qualifier.startswith("::") or d.kind == "macro" else qnames[scope.parent]
        qnames.append(_join(outer, *qualifier, d.name))  # macros are not scoped
        syms.append(_Sym(qnames[-1], d.kind, d.static, d.defines, d.low, d.high))
    return keys, syms


class Resolver:
    def __init__(
        self,
        files: list[tuple[str, str, cpp.Parsed]],
        c_files: list[tuple[str, str, c.Parsed]],
        paths: set[str],
    ) -> None:
        self.files: list[_File] = []
        self.at: dict[str, int] = {}
        self.qnames: dict[str, list[Site]] = {}
        self.sites: dict[str, Site] = {}
        by_suffix = c_resolve.suffixes(paths)
        for path, digest, parsed in files:
            keys, syms = symbol_keys(path, parsed)
            includes = c_resolve.included(path, parsed.includes, paths, by_suffix)
            self._add(_File(path, digest, parsed, keys, includes, syms))
        for path, digest, cparsed in c_files:
            includes = c_resolve.included(path, cparsed.includes, paths, by_suffix)
            csyms: list[_Sym | None] = [None]
            for scope in cparsed.scopes[1:]:
                d = scope.decl
                assert d is not None
                csyms.append(_Sym(d.name, d.kind, d.static, d.defines, 0, -1))
            keys = c_resolve.symbol_keys(path, cparsed)
            self._add(_File(path, digest, None, keys, includes, csyms))
        self.classes = {
            q
            for q, sites in self.qnames.items()
            if any(self._sym(s).kind in cpp.CLASSES for s in sites)
        }
        self.visible: dict[int, set[int]] = {}

    def _add(self, file: _File) -> None:
        i = len(self.files)
        self.at[file.path] = i
        for k, sym in enumerate(file.syms):
            if sym is not None:
                self.qnames.setdefault(sym.qname, []).append((i, k))
                self.sites[file.keys[k]] = (i, k)
        self.files.append(file)

    def _sym(self, site: Site) -> _Sym:
        sym = self.files[site[0]].syms[site[1]]
        assert sym is not None
        return sym

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

    def _scopes(self, i: int) -> list[cpp.Scope]:
        parsed = self.files[i].parsed
        assert parsed is not None
        return parsed.scopes

    def _chain(self, i: int, k: int) -> list[int]:
        """Scope ``k`` and its enclosing scopes in file ``i``, innermost first."""
        scopes, chain = self._scopes(i), [k]
        while chain[-1]:
            chain.append(scopes[chain[-1]].parent)
        return chain

    def _levels(self, i: int, k: int) -> list[str]:
        """The scopes a name used in scope ``k`` is looked up in, innermost first."""
        sym = self.files[i].syms[k]
        if sym is None:
            context = ""
        elif sym.kind in ("namespace", *cpp.CLASSES):
            context = sym.qname
        else:
            context = sym.qname.rpartition("::")[0]
        parts = context.split("::") if context else []
        levels = ["::".join(parts[:n]) for n in range(len(parts), -1, -1)]
        scopes = self._scopes(i)
        for j in self._chain(i, k):
            for target in scopes[j].usings:
                found = [_join(lv, target) for lv in levels if _join(lv, target) in self.qnames]
                levels += found[:1]
        return levels

    def lookup(
        self, i: int, k: int, name: str, call: Call | None, seen: set[str] | None = None
    ) -> Res:
        """Candidates for a call or a type name used in scope ``k`` of file ``i``;
        ``seen`` holds the classes whose bases this lookup already searched."""
        seen = set() if seen is None else seen
        if name.startswith("this->"):
            name = name.removeprefix("this->")
            levels = [lv for lv in self._levels(i, k)[:1] if lv in self.classes]
        elif name.startswith("::"):
            name, levels = name[2:], [""]
        else:
            first, scopes = name.partition("::")[0], self._scopes(i)
            if any(first in scopes[j].bound for j in self._chain(i, k)):
                return None
            levels = self._levels(i, k)
            sees = self._sees(i)
            macros = [s for s in self.qnames.get(name, []) if self._sym(s).kind == "macro"]
            if expanded := [s for s in macros if s[0] in sees]:  # before any lookup
                return self._keys(expanded)
        for level in levels:
            found = self._at(i, level, name, call, seen)
            if found is not _MISSING:
                return found
        return None

    def _at(self, i: int, level: str, name: str, call: Call | None, seen: set[str]) -> Res:
        """``name`` declared in ``level``, or a captured base of that class; _MISSING
        when neither declares it."""
        sites = self.qnames.get(_join(level, name), [])
        if call is None and level in self.classes and name == level.rpartition("::")[2]:
            sites = [s for s in sites if self._sym(s).kind != "function"]  # the class itself
        if sites:
            return self._pick(i, sites, call)
        if level not in self.classes or "::" in name or level in seen:
            return _MISSING
        seen.add(level)
        for j, k in self.qnames[level]:
            if self.files[j].parsed is None or self._sym((j, k)).kind not in cpp.CLASSES:
                continue
            scope = self._scopes(j)[k]
            if name in scope.bound:  # a field
                return None
            for base in scope.bases:
                if base in scope.bound:  # a template parameter
                    return None
                found = self.lookup(j, scope.parent, base, None, seen)
                at = self.sites[found[0]] if found and len(found) == 1 else None
                if at is None or self._sym(at).kind not in cpp.CLASSES:
                    return None  # a base outside the capture may declare it
                inherited = self._at(i, self._sym(at).qname, name, call, seen)
                if inherited is not _MISSING:
                    return inherited
        return _MISSING

    def _pick(self, i: int, sites: list[Site], call: Call | None) -> Res:
        sees = self._sees(i)
        visible = [s for s in sites if s[0] in sees]
        macros = [s for s in visible if self._sym(s).kind == "macro"]
        if macros:
            return self._keys(macros)
        if call is None:
            types = [s for s in visible if self._sym(s).kind in TYPES]
            aliases = [s for s in types if self._sym(s).kind == "type"]
            return self._keys(aliases or types)  # ``typedef struct S {…} S``: the typedef
        functions = [s for s in sites if self._sym(s).kind == "function"]
        classes = {self._sym(s).qname for s in visible if self._sym(s).kind in cpp.CLASSES}
        if not functions and len(classes) == 1:  # ``T(args)``: a constructor of ``T``
            (q,) = classes
            ctors = self.qnames.get(_join(q, q.rpartition("::")[2]), [])
            functions = [s for s in ctors if self._sym(s).kind == "function"]
        if not functions:
            return None  # a type, variable or namespace of that name hides any function
        # Default arguments are on a declaration, not necessarily the definition: a
        # function takes as few arguments as any same-named declaration of its size.
        fewest: dict[int, int] = {}
        for s in functions:
            sym = self._sym(s)
            fewest[sym.high] = min(fewest.get(sym.high, sym.low), sym.low)
        arity = call.arity
        fits = [
            s
            for s in functions
            if fewest[(high := self._sym(s).high)] <= arity and (high < 0 or arity <= high)
        ]
        declared = [s for s in fits if s[0] in sees]  # C++ calls need a declaration
        sizes = {sym.high for s in declared if not (sym := self._sym(s)).defines}
        defined = [
            s
            for s in fits
            if (sym := self._sym(s)).defines
            and (s[0] in sees or (not sym.static and sym.high in sizes))
        ]
        return self._keys(defined) or self._keys(declared)

    def derive(self, entities: list[Entity], edges: list[Edge]) -> None:
        for i, file in enumerate(self.files):
            if file.parsed is not None:
                self._emit(i, file.parsed, entities, edges)

    def _emit(self, i: int, parsed: cpp.Parsed, entities: list[Entity], edges: list[Edge]) -> None:
        file = self.files[i]

        def evidence(start: int, end: int) -> SourceRef:
            return SourceRef(file.path, start, end, file.digest)

        def edge(kind: RelationKind, source: str, res: Res, use: Use | Call) -> Edge:
            targets, resolution, basis = edge_target(res, kind == "calls")
            shown = None if resolution == "resolved" else use.name
            at = evidence(use.start, use.end)
            return Edge(kind, source, targets, resolution, basis, shown, at)

        for item, found in zip(parsed.includes, file.includes, strict=True):
            res = [f"file:{path}" for path in found] or None
            edges.append(edge("imports", file.keys[0], res, Use(item.path, item.start, item.end)))
        for k, scope in enumerate(parsed.scopes):
            key = file.keys[k]
            if (d := scope.decl) is not None:
                sym = self._sym((i, k))
                kind = d.kind
                if kind == "function" and sym.qname.rpartition("::")[0] in self.classes:
                    kind = "method"
                source = evidence(d.start, d.end)
                entities.append(Entity(key, "symbol", kind, d.name, d.signature, source, False))
                edges.append(structural("contains", file.keys[scope.parent], key, None))
                defs = self._definitions(i, sym) if d.kind == "function" and not d.defines else None
                if defs:
                    edges.append(edge("references", key, defs, Use(d.name, d.start, d.end)))
            for ref in scope.refs:
                edges.append(edge("references", key, self.lookup(i, k, ref.name, None), ref))
            for call in scope.calls:
                edges.append(edge("calls", key, self.lookup(i, k, call.name, call), call))

    def _definitions(self, i: int, proto: _Sym) -> Res:
        """The definitions a prototype declares: same name, parameter count and linkage
        (default arguments are the prototype's)."""
        sites = [
            s
            for s in self.qnames[proto.qname]
            if (sym := self._sym(s)).kind == "function"
            and sym.defines
            and (sym.high, sym.static) == (proto.high, proto.static)
        ]
        if proto.static:
            sites = [s for s in sites if s[0] in self._sees(i)]
        return self._keys(sites)
