"""Conservative PHP resolution over every captured PHP file (docs/php.md).

Names resolve from syntax first (absolute names, imports and aliases, the current
namespace), then from Composer autoload evidence, then as a PHP fallback to the
global namespace — which is only a heuristic. Dynamic calls are never resolved:
the extractor does not record them. Ambiguous and unresolved names are emitted
with their source span instead of being dropped.
"""

from __future__ import annotations

import bisect
from collections.abc import Mapping
from typing import Literal

from oxide.core.languages import php_composer, php_model
from oxide.core.languages.php_model import KINDS, TYPE_KINDS, Call, Parsed, fqn, key_fqn, symbol_key
from oxide.core.languages.python_resolve import Edge, edge_target, structural
from oxide.models import Entity, RelationKind, SourceRef

Basis = Literal["syntactic", "resolved", "heuristic"]
Found = tuple[tuple[str, ...], Basis]


def _ns_at(parsed: Parsed, at: int) -> str:
    regions = parsed.namespaces
    i = bisect.bisect_right(regions, (at,)) - 1
    return regions[i][2] if i >= 0 and at < regions[i][1] else ""


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
        self.base_names: dict[str, list[str]] = {}
        self.owner: dict[str, tuple[str, str]] = {}  # symbol key -> (path, namespace)
        self.parent_of: dict[str, str] = {}  # declaration key -> enclosing declaration key
        self.aliases: dict[tuple[str, str, str], str] = {}
        self.namespaces: set[str] = set()
        for path, digest, parsed in sources:
            keys: list[str] = []
            for decl in parsed.decls:
                qualified = fqn(decl, parsed.decls)
                key = symbol_key(path, qualified)
                keys.append(key)
                self.by_fqn.setdefault(qualified, []).append((key, path, decl.kind))
                self.owner[key] = (path, decl.ns)
                self.namespaces.add(decl.ns)
                if decl.parent is not None and decl.parent < len(keys):
                    self.parent_of[key] = keys[decl.parent]
                    if decl.kind == "method":
                        holder = keys[decl.parent]
                        self.methods.setdefault(holder, {}).setdefault(decl.name, []).append(key)
            for use in parsed.bases:
                if use.owner is not None and use.owner < len(keys) and use.kind in (
                    "extends",
                    "implements",
                ):
                    self.base_names.setdefault(keys[use.owner], []).append(use.name)
            for item in parsed.imports:
                alias = item.alias or item.path.rsplit("\\", 1)[-1]
                self.aliases[path, item.ns, alias] = item.path

    def _pick(self, qualified: str, kinds: frozenset[str]) -> tuple[str, ...]:
        return tuple(
            sorted({key for key, _, kind in self.by_fqn.get(qualified, []) if kind in kinds})
        )

    def _resolve(self, path: str, ns: str, name: str, kinds: frozenset[str]) -> Found:
        if not name:
            return (), "syntactic"
        absolute = name.startswith("\\")
        plain = name.lstrip("\\")
        dotted = key_fqn(plain)
        if absolute:
            return self._pick(dotted, kinds), "resolved"
        first, sep, rest = plain.partition("\\")
        alias = self.aliases.get((path, ns, first))
        if alias is not None:
            target = key_fqn(alias + ("\\" + rest if sep else ""))
            return self._pick(target, kinds), "resolved"
        if ns and (found := self._pick(f"{ns}.{dotted}", kinds)):
            return found, "resolved"
        if found := self._pick(dotted, kinds):
            return found, "resolved" if not ns else "heuristic"
        mapped = php_composer.path_for(self.composer, plain)
        if mapped is not None and mapped[1] in self.paths:
            return (f"file:{mapped[1]}",), "resolved"
        return (), "syntactic"

    def _methods(self, classes: tuple[str, ...], name: str, seen: frozenset[str] = frozenset()) -> tuple[str, ...]:
        found: set[str] = set()
        for key in classes:
            if key in seen:
                continue
            found.update(self.methods.get(key, {}).get(name, ()))
            path, ns = self.owner.get(key, ("", ""))
            for base in self.base_names.get(key, ()):
                supers, _ = self._resolve(path, ns, base, TYPE_KINDS)
                found.update(self._methods(supers, name, seen | {key}))
        return tuple(sorted(found))

    def _call(self, path: str, ns: str, keys: list[str], call: Call) -> Found:
        if call.kind == "function":
            return self._resolve(path, ns, call.name, frozenset({"function"}))
        if call.kind == "new":
            return self._resolve(path, ns, call.name, TYPE_KINDS)
        if call.kind == "static":
            scope, _, member = call.name.partition("::")
            classes, basis = self._resolve(path, ns, scope, TYPE_KINDS)
            return (self._methods(classes, member), basis) if classes else ((), basis)
        holder = self.parent_of.get(keys[call.owner]) if call.owner is not None else None
        classes = (holder,) if holder else ()
        return self._methods(classes, call.name.rpartition("->")[2]), "resolved"

    def derive(self, entities: list[Entity], edges: list[Edge]) -> None:
        for namespace in sorted(n for n in self.namespaces if n):
            key = f"module:php:{namespace}"
            entities.append(Entity(key, "module", "module", namespace.rsplit(".", 1)[-1], None, None, False))
            edges.append(structural("contains", "repo", key, None))
        for path, digest, parsed in self.sources:
            self._emit(path, digest, parsed, entities, edges)

    def _emit(
        self, path: str, digest: str, parsed: Parsed, entities: list[Entity], edges: list[Edge]
    ) -> None:
        keys = [symbol_key(path, fqn(d, parsed.decls)) for d in parsed.decls]

        def evidence(start: int, end: int) -> SourceRef:
            return SourceRef(path, start, end, digest)

        def edge(
            kind: RelationKind, source: str, found: Found, name: str, start: int, end: int
        ) -> Edge:
            res, basis = found
            targets, resolution, default = edge_target(list(res) if res else None, kind == "calls")
            final = "heuristic" if resolution == "resolved" and basis == "heuristic" else default
            shown = None if resolution == "resolved" else name
            return Edge(kind, source, targets, resolution, final, shown, evidence(start, end))

        for start, end, ns in parsed.namespaces:
            if ns:
                edges.append(structural("contains_logical", f"module:php:{ns}", f"file:{path}", None))

        for item in parsed.imports:
            found = self._resolve(path, item.ns, item.path, KINDS)
            if not found[0]:
                prefix = key_fqn(item.path)
                module = f"module:php:{prefix}" if any(n.startswith(prefix) for n in self.namespaces) else None
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

        for use in (*parsed.bases, *parsed.refs):
            ns = parsed.decls[use.owner].ns if use.owner is not None else _ns_at(parsed, use.start)
            kinds = TYPE_KINDS if use.kind else KINDS
            found = self._resolve(path, ns, use.name, kinds)
            source = keys[use.owner] if use.owner is not None else f"file:{path}"
            edges.append(edge("references", source, found, use.name, use.start, use.end))

        for call in parsed.calls:
            ns = parsed.decls[call.owner].ns if call.owner is not None else _ns_at(parsed, call.start)
            source = keys[call.owner] if call.owner is not None else f"file:{path}"
            edges.append(
                edge("calls", source, self._call(path, ns, keys, call), call.name, call.start, call.end)
            )
