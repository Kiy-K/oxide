"""TreeRouter baseline (SPEC § TreeRouter): level-synchronous BFS over TreeIndex projection 1.

Projection 1 is physical containment (repository > module/file > symbol > symbol);
every other relation is a typed cross-edge. Layer 0 is the entry points, or the root
region when there are none (root fallback). An explored region expands to its
physical children, its containing scope (never the repository root), then both ends
of the followed cross-edges, each in domain order, deduplicated. Every bound fires
visibly. Without a decision provider nothing is pruned, which is the Rust
``oxide-router-bfs-v1`` behavior with no judge.

Adjacency for a whole layer is one store read, so store round trips scale with depth,
not with the number of regions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from oxide.storage.ladybug import View

ROUTER_VERSION = "oxide-router-bfs-v2"
MAX_REGIONS = 64
MAX_DEPTH = 2
MAX_FANOUT = 16
MAX_EDGES = 2048
FOLLOW = ("calls", "references", "imports", "tested_by", "contains_logical")


@dataclass(frozen=True, slots=True)
class Routed:
    key: str
    depth: int
    origins: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Route:
    candidates: tuple[Routed, ...]
    root_fallback: bool
    regions: int
    deferred: int
    fanout_truncations: int
    stopped_by: str | None


def _region(
    key: str, out: list[tuple[str, str]], into: list[tuple[str, str]]
) -> tuple[list[tuple[str, str]], bool, int]:
    """(neighbor, origin) in expansion order, whether a list was cut, edges examined."""
    children = sorted({b for kind, b in out if kind == "contains"})
    scopes = sorted({a for kind, a in into if kind == "contains" and a != "repo"})
    cross = sorted(
        {(b, f"{kind}:out:{key}") for kind, b in out if kind in FOLLOW}
        | {(a, f"{kind}:in:{key}") for kind, a in into if kind in FOLLOW}
    )
    cut = len(children) > MAX_FANOUT or len(cross) > MAX_FANOUT
    children, cross = children[:MAX_FANOUT], cross[:MAX_FANOUT]
    found = [(c, f"child:{key}") for c in children]
    found += [(s, f"scope:{key}") for s in scopes]
    found += cross
    unique: dict[str, str] = {}
    for neighbor, origin in found:
        unique.setdefault(neighbor, origin)
    return list(unique.items()), cut, len(children) + len(cross)


def route(view: View, entry_keys: list[str]) -> Route:
    root_fallback = not entry_keys
    starts = ["repo"] if root_fallback else list(dict.fromkeys(entry_keys))
    layer = [(key, "root" if root_fallback else "entry") for key in starts]
    seen = set(starts)
    reached: dict[str, list[str]] = {key: [origin] for key, origin in layer}
    depth_of: dict[str, int] = {}
    regions = edges = deferred = truncations = 0
    stopped_by: str | None = None
    depth = 0
    while layer:
        adjacency = view.adjacency([key for key, _ in layer[: max(0, MAX_REGIONS - regions)]])
        upcoming: list[tuple[str, str]] = []
        for key, _ in layer:
            bound = "regions" if regions >= MAX_REGIONS else "edges" if edges >= MAX_EDGES else None
            if bound is not None:
                stopped_by = stopped_by or bound
                deferred += 1
                continue
            out, into = adjacency.get(key, ([], []))
            neighbors, cut, examined = _region(key, out, into)
            regions += 1
            edges += examined
            depth_of[key] = depth
            if cut or len(neighbors) > MAX_FANOUT:
                neighbors = neighbors[:MAX_FANOUT]
                truncations += 1
            for neighbor, origin in neighbors:
                origins = reached.setdefault(neighbor, [])
                if origin not in origins:
                    origins.append(origin)
                if neighbor in seen:
                    continue
                seen.add(neighbor)
                if depth >= MAX_DEPTH:
                    stopped_by = stopped_by or "depth"
                    deferred += 1
                    continue
                upcoming.append((neighbor, origin))
        layer = upcoming
        depth += 1
    candidates = tuple(Routed(k, depth_of[k], tuple(reached[k])) for k in sorted(depth_of))
    return Route(candidates, root_fallback, regions, deferred, truncations, stopped_by)
