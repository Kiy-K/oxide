# NetworkX v1: audit and benchmarks for ADR-0012

The evidence behind [ADR-0012](../../adr/0012-networkx-graph-algorithms.md) (Proposed):
NetworkX as OXIDE's library for generic graph algorithms over bounded projections, with
LadybugDB authoritative.

- **Setup:** NetworkX 3.7, CPython 3.13, all runs under `systemd-run` (MemoryMax 3G,
  CPUQuota 200%).
- **Data:** raw rows are in [results/bench.jsonl](results/bench.jsonl); the script is
  [scripts/bench.py](scripts/bench.py), run against `0381565`, the code before this
  change.
- All numbers are measured unless marked *inferred*.

## Audit: graph code in `src/oxide`

| Code | What it is | Verdict |
| --- | --- | --- |
| `core/navigation.py` `_bfs`, `_roots` | Impact walk: level-synchronous BFS, one LadybugDB read per layer; domain order; depth/limit/`MAX_EDGES` budgets with `stopped_by`; `via` provenance; a second pass over proven edges | **keep**: domain traversal over the store. NetworkX would first need the graph in memory |
| `core/navigation.py` `_cyclic` | Iterative Tarjan SCC over the walked edges | **replace** with `nx.strongly_connected_components` |
| `core/routing.py` `route` | TreeRouter: BFS over containment plus followed cross-edges, with region/depth/fan-out/edge budgets and origins | **keep**: domain policy with budgets (SPEC § TreeRouter) |
| `core/languages/python_dispatch.py` `mro` | C3 linearization with `?` unknown bases and cycle/conflict markers | **keep**: not a graph algorithm NetworkX offers |
| `core/languages/python_dispatch.py` `subclasses` | Transitive closure of the inheritance graph (DFS) | **replace** with `nx.descendants` |
| `core/languages/*_resolve.py` cycle guards | Re-entrancy guards inside binding and re-export resolution | **keep**: memoized resolution, not a graph pass |
| `core/indexing.py` `validate` | Each entity has exactly one `contains` parent | **keep**: a count, not traversal |

Earlier PPR and community detection (graph-v1, 6dd0817..1b11ab6) lived only in eval
scripts and were REJECTED for ranking. Nothing in production uses them.

## Benchmarks

**1. Real impact walks.** 100 sampled symbols per repository × 2 directions × 2 bounds:
(depth 3, limit 200) is the API default and (8, 1000) is the API maximum. That gives
400 walks per repository.

The projection is `_bfs` building an `nx.DiGraph` (edge attribute `via`) in place of its
dict of sets. Its nodes, `found` and `stopped_by` were **identical in all 2 000 walks**,
and so were the SCC members.

| Repo | Edges (median / max) | Tarjan total | nx SCC (graph from dict) | nx SCC on projection | BFS, dict | BFS, projection | Peak memory, Tarjan / nx |
| --- | --- | --- | --- | --- | --- | --- | --- |
| flask | 0 / 149 | 0.68 ms | 4.0 ms | 0.75 ms | 588 ms | 586 ms | 9 / 69 KB |
| requests | 1 / 95 | 1.44 ms | 5.9 ms | 1.37 ms | 691 ms | 700 ms | 5 / 33 KB |
| pytest | 0 / 82 | 0.75 ms | 4.3 ms | 0.81 ms | 947 ms | 943 ms | 6 / 36 KB |
| xarray | 1 / 469 | 2.81 ms | 9.3 ms | 2.45 ms | 2 219 ms | 2 208 ms | 17 / 140 KB |
| seaborn | 0 / 293 | 1.59 ms | 6.1 ms | 1.45 ms | 921 ms | 920 ms | 17 / 135 KB |

- **Building the projection inside the BFS costs nothing measurable** (±1.3%), because
  store reads dominate (about 1.5–5.5 ms per walk).
- **SCC on the projection** costs the same as the hand-written Tarjan.
- **Converting a dict to a graph afterwards** costs about 4×, which is why the projection
  is built during the walk.

**2. Synthetic SCC** (random directed graphs; results identical):

| Nodes / edges | Tarjan | nx (build + SCC) | Peak memory, Tarjan / nx |
| --- | --- | --- | --- |
| 5 000 / 20 000 (the walk's `MAX_EDGES` scale) | 8.0 ms | 20.6 ms | 1.1 / 4.4 MB |
| 50 000 / 200 000 | 157 ms | 441 ms | 14.3 / 48.4 MB |

On large graphs NetworkX costs about 2.7× the time and 3.4× the memory. Real walks never
come near that size: the largest was 469 edges.

**3. `Dispatch.subclasses` while indexing.** Both versions ran on every call, and the
results were compared. **Identical on 2 735 calls.**

| Repo | Calls | Original | nx (incl. building the graph) | Index time (both running) |
| --- | --- | --- | --- | --- |
| flask | 167 | 2.6 ms | 2.0 ms | 0.60 s |
| requests | 76 | 1.6 ms | 0.5 ms | 0.35 s |
| pytest | 750 | 6.1 ms | 2.8 ms | 1.44 s |
| xarray | 1 006 | 9.1 ms | 7.6 ms | 1.67 s |
| seaborn | 736 | 4.0 ms | 3.2 ms | 1.20 s |

- **Under 1% of index time either way.** The original ran first and warmed the shared
  `bases` cache, which favors the nx column slightly; it doesn't change the conclusion.
- **Not performance-critical.**

**4. Import cost** (cold interpreter, median of 7 runs):
- `import networkx`: 86.9 ms;
- `import oxide`: 158 ms;
- `import oxide.api`: 170 ms.

NetworkX is therefore imported lazily, inside `navigation._bfs`/`walk` and
`Dispatch.subclasses`. A test checks that `import oxide` doesn't load it.

## What changed (ADR-0012)

**Dependencies.**
- `networkx>=3.4,<4`: runtime, pure Python, no required dependencies.
- `types-networkx`: dev only, for mypy `--strict`.

**`navigation`.**
- `_bfs` builds the walk's `nx.DiGraph` projection, exposed as `Walk.graph` (traversal
  direction, edge attribute `via`). Its bounds and `stopped_by` are unchanged.
- The cycle flags come from `nx.strongly_connected_components`, and the hand-written
  Tarjan is removed.
- `NAVIGATION_VERSION` is unchanged, because the output is identical.

**`python_dispatch.Dispatch.subclasses`.**
- An inheritance `DiGraph` is built once per `Dispatch`, and the closure is
  `sorted(nx.descendants(...))`.
- Derivation outputs are unchanged, so no version bump is needed.

**Tests.**
- `test_impact.py` adds 2 tests, for the projection's edges and `via`, and for the lazy
  import.
- The full suite passes.

**Not activated:** PageRank/PPR, centrality, shortest paths and communities stay off for
retrieval, routing and packing until a preregistered study passes (ADR-0012 § 6).

## Limitations

- The walks used one sample of 100 symbols per repository. Synthetic graphs are random,
  not code-shaped.
- Memory was measured with `tracemalloc` (Python allocations only).
- Import cost was measured on one laptop with a warm disk cache.

## Post-merge review (530c6c5 against 0381565)

[scripts/review.py](scripts/review.py) → [results/review.jsonl](results/review.jsonl).
Each measurement ran in a fresh interpreter with one implementation on the path.
Summarized in [../agc-v4/README.md § NetworkX review](../agc-v4/README.md):

- **Parity:** identical snapshot and derivation ids and impact-walk outputs on 5
  repositories.
- **`import oxide`:** unchanged, and NetworkX is not loaded.
- **Indexing:** a Python index now loads NetworkX, costing +10 to +64 ms (+1% to +13%) and
  about +11 MB of peak RSS.
- **Walks:** walk time is unchanged.

Whether `Dispatch.subclasses` should keep NetworkX on the index path is left to the
maintainer.
