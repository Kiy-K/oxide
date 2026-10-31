"""Query-local traversal vs whole-graph adjacency vs a hub cache, on the product's walk.

    uv run python traverse.py CORPUS SCRATCH > row.json

Indexes CORPUS, then runs ``navigation.walk`` (the code behind ``Repository.impact``,
depth 3, limit 200, both directions) from sampled roots under three adjacency sources.
``walk`` reads the store only through ``view.adjacency``, so each arm swaps that:

  L  the product: two LadybugDB queries per BFS layer (query-local)
  G  every Rel edge loaded into Python dicts once (whole-graph CSR analogue)
  H<b>  LEANN's cache: the top b% entities by degree served from memory, the rest by query
  M  query-local memo: each walk caches the adjacency it fetched, so its second (proven)
     BFS pass re-reads nothing already read by the first; nothing outlives the walk

Roots: 60 uniform symbols, 30 from the top 1% by degree (hubs), 30 from the bottom half.
Every arm must produce identical walks (checked). Costs: load seconds, retained Python
heap (tracemalloc), per-walk median and p95 milliseconds.
"""

from __future__ import annotations

import json
import random
import shutil
import statistics
import sys
import time
import tracemalloc
from pathlib import Path
from typing import Any

import oxide
from oxide.core import navigation

FLAGS = "r.resolution = 'resolved' AND r.basis <> 'heuristic', r.basis = 'heuristic'"


class Cached:
    """``view`` whose ``adjacency`` serves ``memo`` keys from memory, the rest by query."""

    def __init__(self, view: Any, memo: dict[str, tuple[list[Any], list[Any]]]) -> None:
        self.view, self.memo = view, memo

    def adjacency(self, keys: list[str]) -> dict[str, tuple[list[Any], list[Any]]]:
        miss = [k for k in keys if k not in self.memo]
        found = self.view.adjacency(miss) if miss else {}
        return {k: self.memo[k] if k in self.memo else found[k] for k in keys}


class Memo(Cached):
    """``Cached`` that keeps every adjacency it fetches; one per walk."""

    def adjacency(self, keys: list[str]) -> dict[str, tuple[list[Any], list[Any]]]:
        found = super().adjacency(keys)
        self.memo.update(found)
        return found


def walks(view: Any, roots: list[str], memo: bool = False) -> tuple[list[float], list[Any]]:
    times, outs = [], []
    for root in roots:
        for direction in ("upstream", "downstream"):
            started = time.perf_counter()
            source = Memo(view, {}) if memo else view
            w = navigation.walk(source, root, direction, 3, 200)
            times.append((time.perf_counter() - started) * 1000)
            outs.append((w.roots, w.hops, w.stopped_by))
    return times, outs


def summary(times: list[float]) -> dict[str, float]:
    times = sorted(times)
    return {
        "median_ms": round(statistics.median(times), 2),
        "p95_ms": round(times[int(len(times) * 0.95)], 2),
        "total_s": round(sum(times) / 1000, 2),
    }


def main() -> None:
    corpus, scratch = Path(sys.argv[1]), Path(sys.argv[2])
    shutil.rmtree(scratch, ignore_errors=True)
    data = scratch / "data"
    oxide.index_repository(corpus, data_dir=data)
    with oxide.open_repository(corpus, data_dir=data, create=False) as repo:
        view = repo._view()
        tracemalloc.start()
        started = time.perf_counter()
        rows = view._query(
            f"MATCH (a:Entity)-[r:Rel]->(b:Entity) RETURN a.key, r.kind, b.key, {FLAGS}"
        )
        memo: dict[str, tuple[list[Any], list[Any]]] = {}
        for a, kind, b, certain, heuristic in rows:
            memo.setdefault(a, ([], []))[0].append((kind, b, certain, heuristic))
            memo.setdefault(b, ([], []))[1].append((kind, a, certain, heuristic))
        del rows
        load_s = time.perf_counter() - started
        heap_mb = tracemalloc.get_traced_memory()[0] / 2**20
        tracemalloc.stop()
        degree = {k: len(o) + len(i) for k, (o, i) in memo.items()}
        ranked = sorted(degree, key=lambda k: (-degree[k], k))
        symbols = sorted(
            k for k, *_ in view._query("MATCH (e:Entity) WHERE e.category = 'symbol' RETURN e.key")
        )
        hubs = sorted(set(ranked[: max(1, len(ranked) // 100)]) & set(symbols))
        low = sorted(set(ranked[len(ranked) // 2 :]) & set(symbols))
        rng = random.Random(11)
        roots = rng.sample(symbols, 60) + rng.sample(hubs, 30) + rng.sample(low, 30)

        arms: dict[str, Any] = {}
        reference: list[Any] = []
        for name in ("L", "G", "H1", "H5", "H10", "M", "L"):  # L twice: the first is a warm-up
            if name == "G":
                source: Any = Cached(view, memo)
            elif name.startswith("H"):
                top = ranked[: max(1, len(ranked) * int(name[1:]) // 100)]
                source = Cached(view, {k: memo[k] for k in top})
            else:
                source = view
            times, outs = walks(source, roots, name == "M")
            if not reference:
                reference = outs
            arms[name] = {**summary(times), "identical": outs == reference}
        stops: dict[str, int] = {}
        for _, _, stopped in reference:
            stops[str(stopped)] = stops.get(str(stopped), 0) + 1
        by_group = {}
        for label, keys in (("uniform", roots[:60]), ("hub", roots[60:90]), ("low", roots[90:])):
            by_group[label] = summary(walks(view, keys)[0])
        total = sum(degree.values())
        cache_share = {
            f"H{b}": round(
                sum(degree[k] for k in ranked[: max(1, len(ranked) * b // 100)]) / total, 3
            )
            for b in (1, 5, 10)
        }
    print(
        json.dumps(
            {
                "corpus": corpus.name,
                "nodes_with_edges": len(memo),
                "walks": 2 * len(roots),
                "global_load_s": round(load_s, 2),
                "global_heap_mb": round(heap_mb, 1),
                "cache_edge_share": cache_share,
                "arms": arms,
                "product_by_root_group": by_group,
                "stopped_by": stops,
            }
        )
    )
    shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    main()
