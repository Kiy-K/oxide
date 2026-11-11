"""The blast radius of every entity, computed once per generation (ADR-0014).

It is the upstream walk behind ``Repository.impact``, bounded as search and context report
it, run over the generation's relations in memory when it is published. The stored columns
must equal what ``impact`` returns for the same entity; the tests compare them.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from typing import TYPE_CHECKING, Any

from oxide.core import navigation
from oxide.models import BlastRoot, Entity

if TYPE_CHECKING:
    from oxide.core.languages.python_resolve import Edge
    from oxide.storage.ladybug import Adjacent

BLAST_ROOTS = 3  # the entry points of a search or context result that carry a blast radius
BLAST_DEPTH = 2
BLAST_LIMIT = 20
BLAST_FILES = 8  # distinct paths listed; ``file_count`` counts them all
# A walk's result depends on the navigation algorithm, so its version is part of this one.
BLAST_VERSION = f"oxide-blast-v1 {navigation.NAVIGATION_VERSION}"


class Graph:
    """The generation's relations as the read view serves them, for ``navigation.walk``.

    Same edges, flags and allowance rules as ``View.adjacency`` and ``View.mentions``:
    a row exists only when both endpoints are entities, as the store's joins require."""

    def __init__(self, entities: Sequence[Entity], edges: Iterable[Edge]) -> None:
        self.known = {e.key: e for e in entities}
        self._out: dict[str, list[tuple[str, str, bool, bool]]] = {}
        self._into: dict[str, list[tuple[str, str, bool, bool]]] = {}
        self._opaque: list[tuple[str, str]] = []  # (kind, name) of each unresolved edge
        self._sites: dict[str, list[int]] = {}  # name tail -> rows of ``_opaque`` it ends
        for edge in edges:
            if edge.source not in self.known:
                continue
            if edge.resolution == "unresolved":
                name = edge.name or ""
                for tail in _tails(name):
                    self._sites.setdefault(tail, []).append(len(self._opaque))
                self._opaque.append((edge.kind, name))
            proven = edge.resolution == "resolved" and edge.basis != "heuristic"
            heuristic = edge.basis == "heuristic"
            for target in edge.targets:
                if target in self.known:
                    self._out.setdefault(edge.source, []).append(
                        (edge.kind, target, proven, heuristic)
                    )
                    self._into.setdefault(target, []).append(
                        (edge.kind, edge.source, proven, heuristic)
                    )
        for table in (self._out, self._into):  # sorted once: every walk reads them in order
            for rows in table.values():
                rows.sort()

    def adjacency(
        self, keys: list[str], allowance: int | None = None, kinds: tuple[str, ...] = ()
    ) -> dict[str, tuple[Adjacent, Adjacent]]:
        found: dict[str, tuple[Adjacent, Adjacent]] = {key: ([], []) for key in keys}
        cut: str | None = None  # the first key whose edges an allowance may have cut
        ordered = sorted(set(keys))
        for direction, table in enumerate((self._out, self._into)):
            taken = 0  # rows read so far, in key order, as the read view's ``ORDER BY k LIMIT``
            for key in ordered:
                rows = table.get(key, ())
                if kinds:
                    rows = [row for row in rows if row[0] in kinds]
                if allowance is not None and taken + len(rows) > max(allowance, 0):
                    cut = key if cut is None else min(cut, key)
                    break
                taken += len(rows)
                found[key][direction].extend(rows)
        return found if cut is None else {k: v for k, v in found.items() if k < cut}

    def mentions(self, names: list[str], kinds: tuple[str, ...]) -> int:
        """Unresolved edges of ``kinds`` whose name is one of ``names`` or ends with
        ``.name`` or ``::name``: sites that may target them, never resolved to them."""
        rows = {
            row
            for name in names
            for row in self._sites.get(name, ())
            if self._opaque[row][0] in kinds
        }
        return len(rows)


def _tails(name: str) -> set[str]:
    """``name`` and each tail after a ``.`` or ``::``: the strings ``mentions`` finds it by."""
    tails = {name}
    tails.update(name[i + 1 :] for i, c in enumerate(name) if c == ".")
    tails.update(name[i + 2 :] for i in range(len(name) - 1) if name.startswith("::", i))
    return tails


def compute(entities: Sequence[Entity], edges: Iterable[Edge]) -> list[BlastRoot]:
    """One blast radius per entity, in ``entities`` order."""
    graph = Graph(entities, edges)
    return [_root(graph, entity.key) for entity in entities]


def _root(graph: Graph, key: str) -> BlastRoot:
    found = navigation.walk(graph, key, "upstream", BLAST_DEPTH, BLAST_LIMIT)
    hops = [graph.known[h.key] for h in found.hops]
    names = [
        graph.known[k].name
        for k in found.roots
        if k in graph.known and graph.known[k].category == "symbol"
    ]
    files = sorted({e.source.path for e in hops if e.source is not None})
    return BlastRoot(
        key,
        len(hops),
        sum(e.test for e in hops),
        tuple(files[:BLAST_FILES]),
        len(files),
        graph.mentions(names, ("calls", "references")),
        found.stopped_by,
    )


def blast_columns(root: BlastRoot) -> tuple[object, ...]:
    """The ``blast_*`` columns of one entity row (ADR-0014), in schema order."""
    files = json.dumps(list(root.files), ensure_ascii=False, separators=(",", ":"))
    return (
        root.dependents,
        root.tests,
        root.file_count,
        files,
        root.unresolved,
        root.stopped_by or "",
    )


def blast_from_columns(key: str, columns: Sequence[Any]) -> BlastRoot:
    dependents, tests, file_count, files, unresolved, stopped_by = columns
    listed = tuple(json.loads(files))
    return BlastRoot(key, dependents, tests, listed, file_count, unresolved, stopped_by or None)
