"""NetworkX audit benchmarks: current implementations against NetworkX, same inputs.

    uv run --with networkx==3.7 python bench.py walks      # navigation SCC + projection
    uv run --with networkx==3.7 python bench.py synthetic  # SCC on large random graphs
    uv run --with networkx==3.7 python bench.py subclasses # Dispatch.subclasses at index
    uv run --with networkx==3.7 python bench.py imports    # cold import cost

Each command checks that both implementations give identical results before timing
them, and appends one JSON line to ../results/bench.jsonl.

It measures the code before ADR-0012 (commit 0381565): `navigation._cyclic` and the
dict-returning `navigation._bfs` it calls were replaced by this change, so run it from
a checkout of that commit.
"""

# ruff: noqa: B023  (each lambda is timed at once, inside its own loop iteration)
from __future__ import annotations

import json
import random
import statistics
import subprocess
import sys
import time
import tracemalloc
from collections.abc import Callable
from pathlib import Path
from typing import Any

import networkx as nx

import oxide
from oxide.core import navigation
from oxide.core.languages import python_dispatch

EVAL = Path.home() / "Projects/oxide-eval-data"
REPOS = EVAL / "oxide-structural-trial/repos"
STORES = {"flask": EVAL / "oxide-graphnav-eval/data", "requests": EVAL / "oxide-graphnav-eval/data",
          "pytest": EVAL / "oxide-graphnav-eval/data", "xarray": EVAL / "oxide-agc3-eval/data",
          "seaborn": EVAL / "oxide-agc3-eval/data"}  # fmt: skip
OUT = Path(__file__).resolve().parents[1] / "results/bench.jsonl"
SEED = 2026102504


def nx_cyclic(edges: dict[str, set[str]]) -> set[str]:
    g = nx.DiGraph((a, b) for a, bs in edges.items() for b in bs)
    return {n for c in nx.strongly_connected_components(g) if len(c) > 1 for n in c}


def clock(fn: Callable[[], Any], repeat: int = 5) -> float:
    best = float("inf")
    for _ in range(repeat):
        began = time.perf_counter()
        fn()
        best = min(best, time.perf_counter() - began)
    return best


def peak(fn: Callable[[], Any]) -> int:
    tracemalloc.start()
    fn()
    size = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    return size


def emit(row: dict[str, Any]) -> None:
    with OUT.open("a") as out:
        out.write(json.dumps(row) + "\n")
    print(json.dumps(row))


def projected_bfs(view, roots, direction, depth, limit, proven_only):  # type: ignore[no-untyped-def]
    """navigation._bfs with the walked edges kept as a typed nx.DiGraph instead of a
    dict of sets: the candidate projection (traversal direction, edge attr `via`)."""
    seen = set(roots)
    found: dict[str, tuple[int, str]] = {}
    graph = nx.DiGraph()
    layer, examined, level = roots, 0, 0
    while layer:
        adjacency = view.adjacency(layer)
        steps = {k: navigation._steps(direction, k, *adjacency[k], proven_only) for k in layer}
        if level == depth:
            beyond = any(n not in seen for k in layer for n, _ in steps[k])
            return found, graph, "depth" if beyond else None
        level += 1
        upcoming: list[str] = []
        for source in layer:
            examined += sum(map(len, adjacency[source]))
            for neighbor, via in steps[source]:
                graph.add_edge(source, neighbor, via=via)
                if neighbor in seen:
                    continue
                if len(found) >= limit:
                    return found, graph, "limit"
                seen.add(neighbor)
                found[neighbor] = (level, via)
                upcoming.append(neighbor)
            if examined > navigation.MAX_EDGES:
                return found, graph, "edges"
        layer = upcoming
    return found, graph, None


def cmd_walks() -> None:
    rng = random.Random(SEED)
    for repo, store in STORES.items():
        with oxide.open_repository(str(REPOS / repo), data_dir=str(store), create=False) as r:
            view = r._view()
            keys = sorted(row[0] for row in view._query(
                "MATCH (e:Entity) WHERE e.category = 'symbol' RETURN e.key"))  # fmt: skip
            sample = rng.sample(keys, min(100, len(keys)))
            rows = []
            for key in sample:
                roots = navigation._roots(view, key)
                for direction in ("upstream", "downstream"):
                    for depth, limit in ((3, 200), (8, 1000)):
                        args = (view, roots, direction, depth, limit, False)
                        found, edges, stopped = navigation._bfs(*args)
                        found2, graph, stopped2 = projected_bfs(*args)
                        same_edges = {(a, b) for a, bs in edges.items() for b in bs} == set(
                            graph.edges
                        )
                        assert found == found2 and stopped == stopped2 and same_edges, key
                        assert navigation._cyclic(edges) == nx_cyclic(edges), key
                        m = graph.number_of_edges()
                        rows.append({
                            "edges": m,
                            "tarjan_s": clock(lambda: navigation._cyclic(edges)),
                            "nx_s": clock(lambda: nx_cyclic(edges)),
                            "nx_on_projection_s": clock(lambda: {
                                n for c in nx.strongly_connected_components(graph)
                                if len(c) > 1 for n in c}),
                            "bfs_dict_s": clock(lambda: navigation._bfs(*args), 3),
                            "bfs_projection_s": clock(lambda: projected_bfs(*args), 3),
                            "tarjan_peak_b": peak(lambda: navigation._cyclic(edges)),
                            "nx_peak_b": peak(lambda: nx_cyclic(edges)),
                        })  # fmt: skip
        big = [r for r in rows if r["edges"] >= 100]

        def agg(rs: list[dict], k: str) -> float:
            return round(sum(r[k] for r in rs) * 1000, 3)

        emit({"bench": "walks", "repo": repo, "walks": len(rows), "identical": True,
              "edges_median": statistics.median(r["edges"] for r in rows),
              "edges_max": max(r["edges"] for r in rows),
              **{f"total_ms_{k}": agg(rows, k) for k in ("tarjan_s", "nx_s", "nx_on_projection_s",
                                                         "bfs_dict_s", "bfs_projection_s")},
              "walks_ge100_edges": len(big),
              "max_peak_kb_tarjan": max(r["tarjan_peak_b"] for r in rows) // 1024,
              "max_peak_kb_nx": max(r["nx_peak_b"] for r in rows) // 1024})  # fmt: skip


def cmd_synthetic() -> None:
    for n, m in ((5_000, 20_000), (50_000, 200_000)):
        rng = random.Random(SEED + m)
        edges: dict[str, set[str]] = {}
        for _ in range(m):
            edges.setdefault(f"n{rng.randrange(n)}", set()).add(f"n{rng.randrange(n)}")
        assert navigation._cyclic(edges) == nx_cyclic(edges)
        emit({"bench": "synthetic", "nodes": n, "edges": m, "identical": True,
              "tarjan_ms": round(clock(lambda: navigation._cyclic(edges), 3) * 1000, 1),
              "nx_ms": round(clock(lambda: nx_cyclic(edges), 3) * 1000, 1),
              "tarjan_peak_mb": round(peak(lambda: navigation._cyclic(edges)) / 2**20, 1),
              "nx_peak_mb": round(peak(lambda: nx_cyclic(edges)) / 2**20, 1)})  # fmt: skip


def cmd_subclasses() -> None:
    original = python_dispatch.Dispatch.subclasses
    graphs: dict[int, nx.DiGraph] = {}
    spent = {"original": 0.0, "nx": 0.0, "calls": 0, "graph_build": 0.0}

    def nx_subclasses(self: python_dispatch.Dispatch, key: str) -> list[str]:
        if id(self) not in graphs:
            began = time.perf_counter()
            g = nx.DiGraph()
            for derived in sorted(k for k in self.r.scopes if self.is_class(k)):
                g.add_edges_from((base, derived) for base in self.bases(derived))
            graphs[id(self)] = g
            spent["graph_build"] += time.perf_counter() - began
        g = graphs[id(self)]
        return sorted(nx.descendants(g, key)) if key in g else []

    def both(self: python_dispatch.Dispatch, key: str) -> list[str]:
        began = time.perf_counter()
        expected = original(self, key)
        spent["original"] += time.perf_counter() - began
        began = time.perf_counter()
        got = nx_subclasses(self, key)
        spent["nx"] += time.perf_counter() - began
        assert got == expected, key
        spent["calls"] += 1
        return expected

    python_dispatch.Dispatch.subclasses = both  # type: ignore[method-assign]
    scratch = EVAL / "oxide-networkx-eval/index"
    for repo in STORES:
        for k in ("original", "nx", "graph_build"):
            spent[k] = 0.0
        spent["calls"] = 0
        began = time.perf_counter()
        oxide.index_repository(str(REPOS / repo), data_dir=str(scratch / repo))
        total = time.perf_counter() - began
        emit({"bench": "subclasses", "repo": repo, "identical": True, "calls": spent["calls"],
              "index_s_with_both": round(total, 2),
              "original_ms": round(spent["original"] * 1000, 2),
              "nx_ms_incl_build": round(spent["nx"] * 1000, 2),
              "nx_build_ms": round(spent["graph_build"] * 1000, 2)})  # fmt: skip


def cmd_imports() -> None:
    def cold(module: str) -> float:
        code = f"import time;t=time.perf_counter();import {module};print(time.perf_counter()-t)"
        runs = [float(subprocess.run([sys.executable, "-c", code], capture_output=True,
                                     text=True, check=True).stdout) for _ in range(7)]  # fmt: skip
        return round(statistics.median(runs) * 1000, 1)

    emit({"bench": "imports", "networkx": nx.__version__, "import_networkx_ms": cold("networkx"),
          "import_oxide_ms": cold("oxide"), "import_oxide_api_ms": cold("oxide.api")})  # fmt: skip


if __name__ == "__main__":
    {"walks": cmd_walks, "synthetic": cmd_synthetic, "subclasses": cmd_subclasses,
     "imports": cmd_imports}[sys.argv[1]]()  # fmt: skip
