"""Bounded impact and dependency navigation over typed relations (SPEC § Relations).

``upstream`` answers "what depends on this": callers, referrers and importers (incoming
``calls``/``references``/``imports``) and the tests that exercise it (outgoing
``tested_by``). ``downstream`` answers "what does this depend on" (outgoing ``calls``/
``references``/``imports``). The root stands for itself and everything it physically
contains (a class's methods, a file's declarations) and, for a file, the modules that
logically contain it, since imports name modules.

Level-synchronous BFS in domain order with a visited set, so cycles terminate and
results do not depend on store row order. It runs twice: over every followed edge, and
over proven edges only (``resolved`` with a non-heuristic basis), so ambiguous
candidates and the ``tested_by`` heuristic never make a path proven. Reached entities
in a dependency cycle with another reached entity are flagged (strongly connected
components of the reached subgraph). Bounds fire visibly.

The reached subgraph is kept as a NetworkX projection (ADR-0012): ``Walk.graph`` holds
every walked edge in traversal direction, with the ``via`` of its first step in domain
order, as bounded as the walk
itself; ``Walk.stopped_by`` says whether it is complete. NetworkX is imported lazily.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    import networkx as nx

    from oxide.storage.ladybug import Adjacent, View

NAVIGATION_VERSION = "oxide-impact-bfs-v2"
MAX_EDGES = 20_000  # adjacency rows examined per pass
Direction = Literal["upstream", "downstream"]
_CROSS = ("calls", "references", "imports")


@dataclass(frozen=True, slots=True)
class Hop:
    key: str
    depth: int
    proven_depth: int | None
    via: str  # "<kind>:<in|out>:<key reached from>" on a first shallowest path
    cyclic: bool


@dataclass(frozen=True, slots=True)
class Walk:
    roots: tuple[str, ...]
    hops: tuple[Hop, ...]
    stopped_by: str | None  # "depth", "limit" or "edges"; None when exhausted
    graph: nx.DiGraph[str]  # the walked edges, traversal direction, edge attr ``via``


def _steps(
    direction: Direction, key: str, out: Adjacent, into: Adjacent, proven_only: bool
) -> list[tuple[str, str]]:
    """(neighbor, via) of one entity, in domain order."""
    if direction == "upstream":
        found = [(a, f"{kind}:in:{key}", p) for kind, a, p, _ in into if kind in _CROSS]
        found += [(b, f"tested_by:out:{key}", p) for kind, b, p, _ in out if kind == "tested_by"]
    else:
        found = [(b, f"{kind}:out:{key}", p) for kind, b, p, _ in out if kind in _CROSS]
    return sorted({(n, via) for n, via, p in found if p or not proven_only})


def _roots(view: View, key: str) -> list[str]:
    """``key``, its physical descendants and, for a file, its logical modules."""
    roots, layer = [key], [key]
    while layer:
        adjacency = view.adjacency(layer)
        layer = sorted({b for k in layer for kind, b, *_ in adjacency[k][0] if kind == "contains"})
        roots += layer
    if key.startswith("file:"):
        into = view.adjacency([key])[key][1]
        roots += sorted(a for kind, a, *_ in into if kind == "contains_logical")
    return list(dict.fromkeys(roots))


def _bfs(
    view: View, roots: list[str], direction: Direction, depth: int, limit: int, proven_only: bool
) -> tuple[dict[str, tuple[int, str]], nx.DiGraph[str], str | None]:
    import networkx as nx

    seen = set(roots)
    found: dict[str, tuple[int, str]] = {}
    graph: nx.DiGraph[str] = nx.DiGraph()
    layer, examined, level = roots, 0, 0
    while layer:
        # Keys the allowance cut are absent, so a pass never reads past its edge budget.
        adjacency = view.adjacency(layer, MAX_EDGES - examined + 1)
        steps = {k: _steps(direction, k, *adjacency[k], proven_only) for k in adjacency}
        if level == depth:
            beyond = any(n not in seen for k in steps for n, _ in steps[k])
            return found, graph, "depth" if beyond else "edges" if len(steps) < len(layer) else None
        level += 1
        upcoming: list[str] = []
        for source in layer:
            if source not in adjacency:
                return found, graph, "edges"
            examined += sum(map(len, adjacency[source]))
            for neighbor, via in steps[source]:
                if not graph.has_edge(source, neighbor):  # keep the first step's ``via``
                    graph.add_edge(source, neighbor, via=via)
                if neighbor in seen:
                    continue
                if len(found) >= limit:
                    return found, graph, "limit"
                seen.add(neighbor)
                found[neighbor] = (level, via)
                upcoming.append(neighbor)
            if examined > MAX_EDGES:
                return found, graph, "edges"
        layer = upcoming
    return found, graph, None


def walk(view: View, key: str, direction: Direction, depth: int, limit: int) -> Walk:
    import networkx as nx

    roots = _roots(view, key)
    found, graph, stopped_by = _bfs(view, roots, direction, depth, limit, False)
    proven, _, _ = _bfs(view, roots, direction, depth, limit, True)
    cyclic = {k for c in nx.strongly_connected_components(graph) if len(c) > 1 for k in c}
    hops = tuple(
        Hop(k, d, proven[k][0] if k in proven else None, via, k in cyclic)
        for k, (d, via) in sorted(found.items(), key=lambda item: (item[1][0], item[0]))
    )
    return Walk(tuple(roots), hops, stopped_by, graph)
