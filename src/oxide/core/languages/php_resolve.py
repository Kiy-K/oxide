"""Conservative PHP resolution over every captured PHP file (docs/php.md).

Names resolve from syntax first (absolute names, imports and aliases, the current
namespace), then from Composer autoload evidence, then as a PHP fallback to the
global namespace — which is only a heuristic. Dynamic calls are never resolved:
the extractor does not record them. Ambiguous and unresolved names are emitted
with their source span instead of being dropped.
"""

from __future__ import annotations

from typing import Literal

from oxide.core.languages import php_composer
from oxide.core.languages.php_model import (
    KINDS,
    TYPE_KINDS,
    Call,
    Parsed,
    fqn,
    key_fqn,
    lookup_name,
    ns_at,
    region_at,
    symbol_key,
)
from oxide.core.languages.python_resolve import Edge, edge_target, structural
from oxide.models import Entity, RelationKind, SourceRef

Basis = Literal["syntactic", "resolved", "heuristic"]
Found = tuple[tuple[str, ...], Basis]

_RELATIVE_SCOPES = frozenset({"self", "static", "parent"})
_FUNCTION_KINDS = frozenset({"function"})


class Resolver:
    def __init__(
        self,
        sources: list[tuple[str, str, Parsed]],
        composer: php_composer.Composer,
    ) -> None:
        self.sources = sources
        self.composer = composer
        self.paths = {path for path, _, _ in sources}
        self.by_fqn: dict[str, list[tuple[str, str, str]]] = {}
        self.methods: dict[str, dict[str, list[str]]] = {}
        self.base_names: dict[str, list[tuple[str, str]]] = {}
        # symbol key -> (path, namespace, alias region)
        self.owner: dict[str, tuple[str, str, int]] = {}
        self.parent_of: dict[str, str] = {}  # declaration key -> enclosing declaration key
        # (path, region, kind, alias) -> imported path: aliases are per file, per
        # namespace region and per kind, so a class alias never shadows a function one.
        self.aliases: dict[tuple[str, int, str, str], str] = {}
        self.namespaces: set[str] = set()
        for path, _digest, parsed in sources:
            keys: list[str] = []
            for decl in parsed.decls:
                qualified = fqn(decl, parsed.decls)
                key = symbol_key(path, qualified)
                keys.append(key)
                # PHP names are case-insensitive; entity keys keep the source spelling.
                # Lookup uses the written name: the `~n` ordinal is a key uniquefier,
                # so `class Same {}` and `function Same() {}` both answer to `Same`.
                self.by_fqn.setdefault(lookup_name(decl, parsed.decls).lower(), []).append(
                    (key, path, decl.kind)
                )
                self.owner[key] = (path, decl.ns, region_at(parsed.namespaces, decl.start))
                self.namespaces.add(decl.ns)
                if decl.parent is not None and decl.parent < len(keys):
                    self.parent_of[key] = keys[decl.parent]
                    if decl.kind == "method":
                        holder = keys[decl.parent]
                        self.methods.setdefault(holder, {}).setdefault(
                            decl.name.lower(), []
                        ).append(key)
            for use in parsed.bases:
                if (
                    use.owner is not None
                    and use.owner < len(keys)
                    and use.kind in ("extends", "implements", "trait")
                ):
                    self.base_names.setdefault(keys[use.owner], []).append((use.name, use.kind))
            for item in parsed.imports:
                alias = item.alias or item.path.rsplit("\\", 1)[-1]
                region = region_at(parsed.namespaces, item.start)
                self.aliases[path, region, item.kind, alias.lower()] = item.path
        # A namespace can hold only imports and calls, or be named by an import prefix,
        # yet still needs a module entity for its contains_logical edges (CAP-004).
        for _path, _digest, parsed in sources:
            self.namespaces.update(ns for _s, _e, ns in parsed.namespaces if ns)
        prefixes = {key_fqn(item.path) for _p, _d, parsed in sources for item in parsed.imports}
        for namespace in sorted(self.namespaces):
            for prefix in prefixes:
                if namespace == prefix or namespace.startswith(prefix + "."):
                    self.namespaces.add(prefix)

    def _pick(self, qualified: str, kinds: frozenset[str]) -> tuple[str, ...]:
        return tuple(
            sorted(
                {key for key, _, kind in self.by_fqn.get(qualified.lower(), []) if kind in kinds}
            )
        )

    def _resolve(
        self,
        path: str,
        ns: str,
        region: int,
        name: str,
        kinds: frozenset[str],
        force_absolute: bool = False,
    ) -> Found:
        if not name:
            return (), "syntactic"
        absolute = force_absolute or name.startswith("\\")
        plain = name.lstrip("\\")
        dotted = key_fqn(plain)
        folded = dotted.lower()
        level = ns.lower()
        if absolute:
            if found := self._pick(folded, kinds):
                return found, "resolved"
        else:
            first, sep, rest = plain.partition("\\")
            # A qualified name's first segment is a class/namespace alias; a plain one
            # is a function alias only when the name is used as a function call.
            alias_kind = "class" if sep or kinds != _FUNCTION_KINDS else "function"
            alias = self.aliases.get((path, region, alias_kind, first.lower()))
            if alias is not None:
                target = alias + ("\\" + rest if sep else "")
                if found := self._pick(key_fqn(target), kinds):
                    return found, "resolved"
                # The alias can name a package whose members live only in autoload
                # evidence: keep looking that path up in Composer, never as a plain
                # name in the current namespace.
                plain, absolute = target, True
            elif sep:
                # A qualified name is relative to the current namespace with no
                # global fallback: PHP reads ``namespace App; App\helper()`` as
                # ``App\App\helper()``, never as the imported ``App\helper()``.
                if found := self._pick(f"{level}.{folded}" if level else folded, kinds):
                    return found, "resolved"
                return (), "syntactic"
            elif level and (found := self._pick(f"{level}.{folded}", kinds)):
                return found, "resolved"
            elif found := self._pick(folded, kinds):
                # Only unqualified functions and constants fall back to the global
                # namespace; inside a namespace, class-like names do not, so a
                # same-named global class is not evidence.
                if level and kinds <= TYPE_KINDS:
                    return (), "syntactic"
                return found, "heuristic" if level else "resolved"
        # Composer maps classes by fully qualified name only, so a relative name is
        # tried under the current namespace too; functions are not PSR-4 mapped.
        if not kinds & TYPE_KINDS:
            return (), "syntactic"
        ns_php = ns.replace(".", "\\")
        qualified = plain if absolute or not ns else f"{ns_php}\\{plain}"
        mapped = php_composer.path_for(self.composer, qualified)
        if mapped is not None and mapped[1] in self.paths:
            return (f"file:{mapped[1]}",), "resolved"
        lowered = qualified.lower()
        for candidate in sorted(self.paths):
            inferred = php_composer.fqn_at(self.composer, candidate)
            if inferred and inferred[0] == "classmap" and inferred[1].lower() == lowered:
                return (f"file:{candidate}",), "heuristic"
        return (), "syntactic"

    def _methods(
        self, classes: tuple[str, ...], name: str, seen: frozenset[str] = frozenset()
    ) -> tuple[str, ...]:
        found: set[str] = set()
        for key in classes:
            if key in seen:
                continue
            local = self.methods.get(key, {}).get(name.lower(), ())
            if local:
                # An override hides the same-named inherited method entirely.
                found.update(local)
                continue
            path, ns, region = self.owner.get(key, ("", "", -1))
            for base, _kind in self.base_names.get(key, ()):
                supers, _ = self._resolve(path, ns, region, base, TYPE_KINDS)
                found.update(self._methods(supers, name, seen | {key}))
        return tuple(sorted(found))

    def _relative(self, scope: str, keys: list[str], owner: int | None) -> Found:
        """``self``/``static``/``parent`` from inside a method: its class or bases."""
        holder = self.parent_of.get(keys[owner]) if owner is not None else None
        if holder is None:
            return (), "syntactic"
        if scope.lower() != "parent":
            return (holder,), "resolved"
        path, ns, region = self.owner.get(holder, ("", "", -1))
        classes: list[str] = []
        for name, kind in self.base_names.get(holder, ()):
            if kind == "extends":
                supers, _ = self._resolve(path, ns, region, name, TYPE_KINDS)
                classes.extend(supers)
        return (tuple(sorted(set(classes))), "resolved") if classes else ((), "syntactic")

    def _call(self, path: str, ns: str, region: int, keys: list[str], call: Call) -> Found:
        if call.kind == "function":
            return self._resolve(path, ns, region, call.name, frozenset({"function"}))
        if call.kind == "new":
            if call.name.lower() in _RELATIVE_SCOPES:
                return self._relative(call.name, keys, call.owner)
            return self._resolve(path, ns, region, call.name, TYPE_KINDS)
        if call.kind == "static":
            scope, _, member = call.name.partition("::")
            classes, basis = (
                self._relative(scope, keys, call.owner)
                if scope.lower() in _RELATIVE_SCOPES
                else self._resolve(path, ns, region, scope, TYPE_KINDS)
            )
            if not classes:
                return (), basis
            # Composer can prove the file a class lives in without proving its methods;
            # that file is still real evidence for the call.
            methods = self._methods(classes, member)
            return (methods or tuple(k for k in classes if k.startswith("file:")), basis)
        holder = self.parent_of.get(keys[call.owner]) if call.owner is not None else None
        classes = (holder,) if holder else ()
        return self._methods(classes, call.name.rpartition("->")[2]), "resolved"

    def derive(self, entities: list[Entity], edges: list[Edge]) -> None:
        for namespace in sorted(n for n in self.namespaces if n):
            key = f"module:php:{namespace}"
            entities.append(
                Entity(key, "module", "module", namespace.rsplit(".", 1)[-1], None, None, False)
            )
            edges.append(structural("contains", "repo", key, None))
        for path, digest, parsed in self.sources:
            self._emit(path, digest, parsed, entities, edges)

    def _emit(
        self, path: str, digest: str, parsed: Parsed, entities: list[Entity], edges: list[Edge]
    ) -> None:
        keys = [symbol_key(path, fqn(d, parsed.decls)) for d in parsed.decls]

        def evidence(start: int, end: int) -> SourceRef:
            return SourceRef(path, start, end, digest)

        def context_at(at: int, owner: int | None) -> tuple[str, int]:
            """Namespace and alias region for a name written at ``at``."""
            if owner is not None and owner < len(parsed.decls):
                at = parsed.decls[owner].start
            return ns_at(parsed.namespaces, at), region_at(parsed.namespaces, at)

        def edge(
            kind: RelationKind, source: str, found: Found, name: str, start: int, end: int
        ) -> Edge:
            res, basis = found
            # Composer evidence can name a captured file rather than a symbol; that is
            # still real evidence, so do not discard it with the calls-only symbol filter.
            symbols_only = kind == "calls" and all(t.startswith("symbol:") for t in res)
            targets, resolution, default = edge_target(list(res) if res else None, symbols_only)
            final = "heuristic" if resolution == "resolved" and basis == "heuristic" else default
            shown = None if resolution == "resolved" else name
            return Edge(kind, source, targets, resolution, final, shown, evidence(start, end))

        for _start, _end, ns in parsed.namespaces:
            if ns:
                edges.append(
                    structural("contains_logical", f"module:php:{ns}", f"file:{path}", None)
                )

        for item in parsed.imports:
            _ns, region = context_at(item.start, None)
            found = self._resolve(path, item.ns, region, item.path, KINDS, force_absolute=True)
            if not found[0]:
                prefix = key_fqn(item.path)
                module = (
                    f"module:php:{prefix}"
                    if any(n == prefix or n.startswith(prefix + ".") for n in self.namespaces)
                    else None
                )
                found = ((module,), "resolved") if module else found
            edges.append(edge("imports", f"file:{path}", found, item.path, item.start, item.end))

        for k, decl in enumerate(parsed.decls):
            at = evidence(decl.start, decl.end)
            entities.append(
                Entity(keys[k], "symbol", decl.kind, decl.name, decl.signature, at, False)
            )
            parent = keys[decl.parent] if decl.parent is not None else f"file:{path}"
            edges.append(structural("contains", parent, keys[k], None))
            if decl.parent is None and decl.ns:
                edges.append(structural("defines", f"module:php:{decl.ns}", keys[k], at))

        # A base or a named type is a type position: it never denotes a function.
        for use in (*parsed.bases, *parsed.refs):
            ns, region = context_at(use.start, use.owner)
            found = self._resolve(path, ns, region, use.name, TYPE_KINDS)
            source = keys[use.owner] if use.owner is not None else f"file:{path}"
            edges.append(edge("references", source, found, use.name, use.start, use.end))

        for call in parsed.calls:
            ns, region = context_at(call.start, call.owner)
            source = keys[call.owner] if call.owner is not None else f"file:{path}"
            edges.append(
                edge(
                    "calls",
                    source,
                    self._call(path, ns, region, keys, call),
                    call.name,
                    call.start,
                    call.end,
                )
            )
