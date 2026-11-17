"""Build C# entities and edges from the resolver's facts (``csharp_resolve``)."""

from __future__ import annotations

import bisect
from typing import Protocol

from oxide.core.languages.csharp_model import Call, Import
from oxide.core.languages.csharp_scope import File, Found, Site, module_key
from oxide.core.languages.python import Use
from oxide.core.languages.python_resolve import Edge, Res, edge_target, structural
from oxide.models import Entity, RelationKind, SourceRef


class _Lookup(Protocol):
    files: list[File]
    tops: dict[str, dict[str, list[Site]]]
    tests: set[str]

    def _keys(self, sites: list[Site]) -> list[str]: ...

    def _type(self, i: int, k: int, dotted: str, at: int | None = None) -> Found: ...

    def _call(self, i: int, k: int, call: Call) -> Found: ...

    def _attribute(self, i: int, k: int, use: Use) -> Found: ...

    def _import_targets(self, i: int, item: Import) -> list[str]: ...


def derive(resolver: _Lookup, entities: list[Entity], edges: list[Edge]) -> None:
    for namespace in sorted(n for n in resolver.tops if n):
        key = module_key(namespace)
        entities.append(Entity(key, "module", "module", namespace, None, None, False))
        edges.append(structural("contains", "repo", key, None))
    for i, file in enumerate(resolver.files):
        for ns in sorted({n for _, _, n in file.spans if n}):
            edges.append(structural("contains_logical", module_key(ns), f"file:{file.path}", None))
        emit(resolver, i, entities, edges)


def emit(resolver: _Lookup, i: int, entities: list[Entity], edges: list[Edge]) -> None:
    file = resolver.files[i]

    def evidence(start: int, end: int) -> SourceRef:
        return SourceRef(file.path, start, end, file.digest)

    def edge(kind: RelationKind, source: str, res: Res, use: Use | Call) -> Edge:
        targets, resolution, basis = edge_target(res, kind == "calls")
        shown = None if resolution == "resolved" else use.name
        return Edge(kind, source, targets, resolution, basis, shown, evidence(use.start, use.end))

    for item in file.parsed.imports:
        res = resolver._import_targets(i, item) or None
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
            found = resolver._type(i, k, ref.name, ref.start)
            edges.append(
                edge("references", owner(k, ref.start), resolver._keys(found or []) or None, ref)
            )
        for attr in scope.attrs:
            found = resolver._attribute(i, k, attr)
            edges.append(
                edge("references", owner(k, attr.start), resolver._keys(found or []) or None, attr)
            )
        for call in scope.calls:
            found = resolver._call(i, k, call)
            source = owner(k, call.start)
            relation = edge("calls", source, resolver._keys(found or []) or None, call)
            callee = relation.targets[0] if relation.resolution == "resolved" else None
            if source in resolver.tests and callee and callee not in resolver.tests:
                at = evidence(call.start, call.end)
                edges.append(
                    Edge("tested_by", callee, (source,), "resolved", "heuristic", None, at)
                )
            edges.append(relation)


def attribute(resolver: _Lookup, i: int, k: int, use: Use) -> Found:
    """``[N]`` names type ``N`` or ``NAttribute``; both existing is ambiguous."""
    plain = resolver._type(i, k, use.name, use.start) or []
    suffixed = resolver._type(i, k, use.name + "Attribute", use.start) or []
    return sorted({*plain, *suffixed}) or None
