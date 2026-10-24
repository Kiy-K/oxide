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
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from oxide.storage.ladybug import Adjacent, View

NAVIGATION_VERSION = "oxide-impact-bfs-v1"
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
) -> tuple[dict[str, tuple[int, str]], dict[str, set[str]], str | None]:
    seen = set(roots)
    found: dict[str, tuple[int, str]] = {}
    edges: dict[str, set[str]] = {}
    layer, examined, level = roots, 0, 0
    while layer:
        adjacency = view.adjacency(layer)
        steps = {k: _steps(direction, k, *adjacency[k], proven_only) for k in layer}
        if level == depth:
            beyond = any(n not in seen for k in layer for n, _ in steps[k])
            return found, edges, "depth" if beyond else None
        level += 1
        upcoming: list[str] = []
        for source in layer:
            examined += sum(map(len, adjacency[source]))
            for neighbor, via in steps[source]:
                edges.setdefault(source, set()).add(neighbor)
                if neighbor in seen:
                    continue
                if len(found) >= limit:
                    return found, edges, "limit"
                seen.add(neighbor)
                found[neighbor] = (level, via)
                upcoming.append(neighbor)
            if examined > MAX_EDGES:
                return found, edges, "edges"
        layer = upcoming
    return found, edges, None


def walk(view: View, key: str, direction: Direction, depth: int, limit: int) -> Walk:
    roots = _roots(view, key)
    found, edges, stopped_by = _bfs(view, roots, direction, depth, limit, False)
    proven, _, _ = _bfs(view, roots, direction, depth, limit, True)
    cyclic = _cyclic(edges)
    hops = tuple(
        Hop(k, d, proven[k][0] if k in proven else None, via, k in cyclic)
        for k, (d, via) in sorted(found.items(), key=lambda item: (item[1][0], item[0]))
    )
    return Walk(tuple(roots), hops, stopped_by)


def _cyclic(edges: dict[str, set[str]]) -> set[str]:
    """Members of strongly connected components larger than one (iterative Tarjan)."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    on: set[str] = set()
    out: set[str] = set()
    for root in sorted(edges):
        if root in index:
            continue
        work = [(root, iter(sorted(edges.get(root, ()))))]
        index[root] = low[root] = len(index)
        stack.append(root)
        on.add(root)
        while work:
            node, children = work[-1]
            for child in children:
                if child not in index:
                    index[child] = low[child] = len(index)
                    stack.append(child)
                    on.add(child)
                    work.append((child, iter(sorted(edges.get(child, ())))))
                    break
                if child in on:
                    low[node] = min(low[node], index[child])
            else:
                work.pop()
                if work:
                    low[work[-1][0]] = min(low[work[-1][0]], low[node])
                if low[node] == index[node]:
                    component = []
                    while (member := stack.pop()) != node:
                        on.discard(member)
                        component.append(member)
                    on.discard(node)
                    if component:
                        out.update([node, *component])
    return out
