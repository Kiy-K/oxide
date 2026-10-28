# LEANN-inspired storage and traversal for the LadybugDB store

Date: 2026-10-28. Branch `rewrite/v2` at `7916951`. No `src/` change; frozen baselines
untouched. Each claim is labelled **measured** or **inferred**.

## Question

LEANN (Wang et al., *LEANN: A Low-Storage Vector Index*, arXiv 2506.08276) cuts a graph
index to under 5% of the raw data. It uses four ideas:

1. It stores the proximity graph, but not the embeddings, and recomputes embeddings only
   for the nodes a query visits.
2. It prunes edges while preserving hubs: high-degree nodes keep up to M edges, the rest
   keep up to m < M.
3. It searches in two levels with dynamic batching, so it recomputes few nodes per query.
4. It caches the most-visited nodes (a 10% cache gave 1.5× in the paper).

OXIDE has no embeddings (ADR-0011). Each idea therefore maps onto what OXIDE stores and
traverses:

| LEANN idea | OXIDE analogue tested here |
|---|---|
| don't store what can be recomputed | drop `Entity.terms` (rebuilt from the entity by `document()`), the FTS index, and the `Rel.ev_path` values that equal the source entity's path |
| compact graph metadata | INT8 codes for `Rel.kind/resolution/basis` instead of strings |
| query-local search | the product's per-layer `View.adjacency` queries, against loading the whole graph into memory |
| hub-aware | degree skew; the cost of walks rooted at hubs; a top-b% hub adjacency cache |

Hub-preserving pruning itself is **not** an option. LEANN tolerates lossy recall because
ANN search is approximate. `impact` must return every resolved edge and report its
bounds visibly (SPEC § Relations, `navigation.py`), so dropping edges would make it
silently wrong. It is rejected without a benchmark.

## Setup

- Corpora (all **measured**, from the runs below):

  | corpus | lang | entities | Rel rows | Opaque rows | graph.lbug MiB |
  |---|---|---:|---:|---:|---:|
  | nushell@399319476aa4 | Rust | 14,715 | 80,601 | 30,917 | 35.6 |
  | teleport@c2ace99b1c8b | Go | 22,052 | 85,133 | 89,425 | 47.7 |
  | transformers@33cb21150c03 | Python | 32,881 | 87,669 | 144,005 | 75.1 |
  | vscode@2dff5deef71f | TS | 81,832 | 306,128 | 242,890 | 170.4 |

- Every run happens under `systemd-run --user --scope -p MemoryMax=3G -p MemorySwapMax=0
  -p CPUQuota=200%`, one job at a time, with a private `OXIDE_DATA_DIR`. CPython 3.13,
  LadybugDB 0.21.0, btrfs.
- `scripts/storage.py` indexes the corpus through the product path, then rebuilds its
  rows into standalone databases with schema variants (A–E). It runs the product's
  adjacency query (300 batches of 16 random keys) on each variant and checks that the
  answers are identical.
- `scripts/traverse.py` runs the product's `navigation.walk` (what `Repository.impact`
  calls: depth 3, limit 200, both directions) from 120 roots per corpus. The roots are
  60 uniform symbols, 30 from the top 1% by degree and 30 from the bottom half, giving
  240 walks. Only the adjacency source changes between arms, and every arm's walks are
  checked identical to the product's.
- Results are in `results/storage.jsonl`, `results/traverse.jsonl` and
  `results/traverse-run1.jsonl` (the first run, without arm M).
- Each storage variant was measured once. The traversal arms were measured in two runs,
  and the product arm's total varied by up to 12% between them (**measured**).

## 1. Where the bytes are (measured)

Each value is the share of the product's `graph.lbug`. Variant A reproduces the
product's file size exactly on all four corpora.

| | nushell | teleport | transformers | vscode |
|---|---:|---:|---:|---:|
| FTS index (A − B) | 57.8% | 55.0% | 56.9% | 56.4% |
| `Entity.terms` column (B − C) | 4.9% | 6.2% | 7.6% | 8.5% |
| compact Rel: INT8 codes + elided `ev_path` (C − D) | 0.22% | 0.10% | 0.09% | 0.33% |
| everything else: the graph proper (D) | 37.1% | 38.8% | 35.4% | 34.8% |
| `Rel.ev_path` equal to the source entity's path | 71% | 50% | 57% | 63% |

- **Compact graph metadata is worth nothing.** The string columns hold 0.6–0.7 MiB of raw
  `kind`/`resolution`/`basis` text per 80k rows, but LadybugDB already
  dictionary-compresses them. Recoding them as INT8 and eliding the 50–71% of redundant
  `ev_path` values saves at most 0.33%. Adjacency answers stay identical. The query was
  4–15% faster on D in one run each, which is within the 12% run-to-run spread, so no
  speed claim is made. **REJECT**: a schema bump for under 0.4%.
- **The FTS index is the storage.** It takes more than half of every generation's graph
  file and 1.1–6.6 s of each build (`fts_s`). This matches indexing-throughput § 1:
  `CREATE_FTS_INDEX` is the largest single statement.
- **LEANN's core move is not available on the lexical side.**
  - Keeping the index while dropping its stored source (variant E) is refused by
    LadybugDB: "Cannot drop property terms in table Entity because it is used in one or
    more indexes" (**measured**). The `terms` column (5–8.5%) can only go if the FTS
    index goes with it.
  - Recomputing the FTS index at query time is not the same trade as LEANN's. LEANN
    recomputes O(log N) embeddings because each one is local to a node. BM25 needs
    corpus-wide document frequencies, so a lazily built index costs the full 1.1–6.6 s
    per process that opens it, against 0 today (**inferred** from `fts_s`).
  - The only FTS size levers LadybugDB exposes are `stemmer` and `stopwords` (Ladybug
    docs, full-text-search extension). Both change BM25 scores, so they belong to the
    frozen-baseline gate and closed lexical R&D, not to storage.
- **Selective recomputation already exists where it is sound.**
  - `facts.json` is a cache that is re-parsed when it is missing.
  - Source blobs are hard-linked between generations (blob-reuse, `abcc202`).
  - Per-file invalidation was studied and closed (change-classification, `7916951`).
  - Nothing new is left to recompute instead of store.

## 2. Query-local traversal and hubs (measured)

### Degree skew

Share of all edge endpoints held by the top b% of entities by degree:

| | nushell | teleport | transformers | vscode |
|---|---:|---:|---:|---:|
| top 1% | 31.6% | 22.6% | 22.4% | 33.6% |
| top 5% | 44.1% | 39.7% | 38.7% | 55.9% |
| top 10% | 53.8% | 50.3% | 49.9% | 67.2% |

- Hubs are real. Walks rooted at a top-1% entity take 1.7–4.2× longer than walks from
  uniform roots (median 53–131 ms against 16–39 ms).
- They rarely exhaust a bound: across all 960 walks, only 8 stopped on `edges` and 38 on
  `limit`.

### Arms

The arms differ only in where `view.adjacency` reads from (`scripts/traverse.py`):

- **L** (the product): two LadybugDB queries per BFS layer.
- **G**: every Rel edge loaded once into Python dicts, the analogue of a whole-graph CSR.
- **H1, H5, H10**: LEANN's cache. The top b% entities by degree are served from memory,
  the rest by query.
- **M**: a per-walk memo. `walk` runs its BFS twice, once over all edges and once over
  proven edges only. Today both passes query the same layers. Under M, the second pass
  reuses what the first one read, and nothing outlives the walk.

All arms produced identical walks on all 960 walks.

**Speed-up in total walk time over L** (240 walks per corpus; L totals 11.6 / 10.3 /
10.5 / 21.1 s):

| arm | nushell | teleport | transformers | vscode |
|---|---:|---:|---:|---:|
| H1 | 1.79× | 1.21× | 1.36× | 1.19× |
| H5 | 1.90× | 1.17× | 1.45× | 1.37× |
| H10 | 2.33× | 1.62× | 1.61× | 1.52× |
| **M** | 1.94× | 1.83× | 2.04× | **2.07×** |
| G | 97× | 147× | 210× | 301× |

**Extra cost per process:**

| arm | memory held | load time |
|---|---|---|
| L, M | 0 | 0 |
| H10 | ≈ 50–67% of G's heap (**inferred** from H10's edge share) | needs a degree pass over the whole graph first |
| G | 35 / 36 / 44 / 144 MB | 1.0 / 1.1 / 1.2 / 4.0 s |

### Pareto reading

- **M dominates the hub cache.** It is equal or faster than H10 on three of four corpora
  (nushell H10 2.33× vs M 1.94×) and holds no memory between walks.
- The vscode p95 under M (215 ms) is above H10's (148 ms). H10 helps hub-heavy tails,
  but at about 97 MB resident (**inferred**).
- LEANN's own 10% cache gave 1.5×, the same as H10 here (1.5–1.6× on the large
  corpora).
- **G is the other point on the frontier.** A walk becomes sub-millisecond, at the cost
  of 1–4 s of loading and 35–144 MB per process. G pays back after about 21–46 walks in
  one process (load seconds over the per-walk saving). That only makes sense for a
  long-lived server issuing many `impact` calls.
  - `impact` is not exposed through MCP (graph-navigation FAIL, AGC v4 STOP), so no
    current client does that.
  - ADR-0002 keeps LadybugDB as the one query engine, and ADR-0012 limits NetworkX to
    bounded projections. **Defer**: a whole-graph mirror needs a client that makes
    many `impact` calls.
- The product is already query-local, and graph-v1's VS Code `mentions` scan (74% of
  upstream latency then) was since fixed the same way: one `Unresolved` node per name
  segment, read with `u.key IN $keys`.

## Verdicts

| idea | verdict | evidence |
|---|---|---|
| compact graph metadata (codes, elided `ev_path`) | **REJECT** | ≤ 0.33% of graph.lbug; LadybugDB already compresses |
| drop recomputable stored text (`terms`) keeping FTS | **NOT POSSIBLE** in LadybugDB 0.21 | binder refuses; FTS index is 55–58% |
| recompute FTS instead of storing it | **REJECT** | BM25 is global, so recomputation costs a full rebuild per process (1.1–6.6 s) |
| hub-preserving edge pruning | **REJECT** (not benchmarked) | `impact` must be exact; pruning silently drops dependents |
| hub adjacency cache (top b%) | **REJECT** | 1.2–2.3×, dominated by M with no resident memory |
| whole-graph in-memory adjacency | **DEFER** | 97–301× per walk; 1–4 s and 35–144 MB per process; no high-volume `impact` client |
| per-walk adjacency memo (M) | **DEFERRED** (candidate) | 1.83–2.07× total walk time, identical outputs, no memory between walks; no end-to-end benefit shown |

M is the only measured win, and it touches only navigation. It would be a few lines in
`navigation.walk`: share the adjacency dict between the two `_bfs` passes. It changes
no output, so `NAVIGATION_VERSION` would stay.

**Decision (2026-10-28): defer production changes.** M is kept here as a candidate and
is not implemented. The 2× is on isolated walks over sampled roots. It is not yet a
benefit to anyone, for three reasons:

- `impact` is not exposed through MCP and no agent workflow calls it (graph-navigation
  FAIL, AGC v4 STOP).
- A walk takes 16–40 ms at the median, and graph-navigation measured a whole
  `build_context` call at 88 ms. So M saves tens of milliseconds per call (**inferred**).
- AGENTS.md says to build only what the phase needs.

Reopen M only when representative usage shows a meaningful end-to-end gain:

1. A real client issues `impact` (or anything else that runs `walk`) in its normal
   workflow.
2. Traces from that workflow show walk time is a material share of the call or task
   latency.
3. Re-measured with `scripts/traverse.py` arm M on those workloads' roots, M makes that
   latency measurably shorter, with byte-identical walk outputs.

The same evidence would also be the trigger to reconsider the deferred whole-graph arm G.

## Reproduce

```bash
S=docs/phase-6/leann-graph-storage/scripts E=~/Projects/oxide-eval-data/oxide-leann-eval
cap() { OXIDE_DATA_DIR=$E/oxdata systemd-run --user --scope -q -p MemoryMax=3G \
  -p MemorySwapMax=0 -p CPUQuota=200% uv run python "$@"; }
for c in CORPUS...; do
  cap $S/storage.py  $c $E/scratch | tail -1 >> $E/storage.jsonl
  cap $S/traverse.py $c $E/scratch | tail -1 >> $E/traverse.jsonl
done
```

The FTS extension must be prepared (`oxide prepare`). The corpora are the worktrees
listed in Setup. transformers and vscode come from `oxide-jev-eval/cb-wt/`, and their
commits are recorded above.

## Limits

- Each storage variant was built once. The byte sizes are deterministic for a given row
  set, but the timings are single samples.
- Traversal ran twice, but arm M only in the second run. M's margin (1.8–2.1×) is far
  above the 12% spread of L. M saves the second BFS pass's queries, so about 2× is the
  expected ceiling (**inferred**). Only random and stratified roots were tested, not a
  real task workload.
- Memory for the H arms was not measured directly.
- The transformers and vscode commits differ from the ones in indexing-throughput
  (fewer entities). The shares in § 1 agree within 3 points across all four languages.
