# SQLite request path: candidate-first retrieval, streaming vectors, process cache

Follow-on to [`../storage-backend-eval/enhanced-sqlite.md`](../storage-backend-eval/enhanced-sqlite.md).
That round persisted the BM25 postings and froze the schema and write path.
This round removes the O(N) *request* costs that remained around it: every
`search`/`context` still loaded every symbol and every embedding before it
knew which handful it would return. SQLite stays, the schema is untouched,
and ranking is byte-identical — the whole point was to reach the same
answers from a bounded amount of work.

Scope stop: no sqlite-vec, no Zvec, no FTS5 trigram, no CLI/MCP contract
change. The streaming exhaustive scan measured at the end is the **control**
those experiments must beat.

Machine: the same 16-core laptop as the previous round, not idle (a game
and a browser ran during parts of it — contended rounds are called out and
discarded). Offline hashed embedder (256-dim) unless stated; `nice -n 5`.
Real repositories copied from the ContextBench cache and indexed fresh:

| repo | files | symbols | index.db |
| --- | ---: | ---: | ---: |
| requests | 35 | 831 | 4.0 MB |
| flask | 80 | 1,754 | 7.3 MB |
| pytest | 222 | 5,352 | 25 MB |
| pylint | 2,186 | 13,360 | 47 MB |
| sympy | 1,409 | 44,254 | 225 MB |

## 1. Architecture, before and after

### Before

```
request
  open_read_only ─► validate_index (3× COUNT(*))
  open_embedder  (NativeEmbedder: ONNX model load, every request)
  RetrievalEngine::new
      all_symbols()                       ← O(N) rows + JSON parse, every request
      by_id: HashMap<u64, usize>          ← O(N)
  search
      all_embeddings() → HashMap<u64, Vec<f32>>   ← O(N·dim) heap, every request
      dot product for every symbol → HashMap<u64, f32>   ← O(N)
      sort all N by score, take 200       ← O(N log N)
      BM25 over postings (bounded)        ✓ already candidate-shaped
      fuse; hits = lookup in the N-symbol snapshot
      expansion: RelationGraph::build(all symbols)   (when strong seeds)
  context
      engine.search(expand=false)          (all of the above)
      load_symbols_with_relations()        ← a SECOND full load, + relations
      RelationGraph::build; neighbors (rescans corpus per seed / per import)
```

### After

```
request
  open_read_only ─► cache key = (index_id, index_generation, schema_version,
                    extraction_version, embedding_fingerprint, embedder, dim,
                    lexical_index_version, embedding_migration)
  embedder: process-cached in `oxide mcp` (keyed by configured provider name)
  validate_index(stats)   stats from the cache entry on a hit, else COUNT(*)
  RetrievalEngine::{new | with_snapshot}
      symbol_count()                      ← one COUNT(*) on a covering index
  search
      lexical:  postings → BM25 (bounded) → top-200 by select_nth + sort
      semantic: embed_query on a thread ‖ lexical on the store thread;
                then for_each_embedding: decode one row → dot → bounded
                heap of 200 → drop           ← O(N·dim) time, O(200) memory
      fuse (≤400 ids) → symbols_by_ids(ids)   ← the only hydration
      expansion (only if requested, not Fast, and a strong seed exists):
                snapshot() → all symbols, once per engine; from the process
                cache in MCP
  context
      engine.search(expand=false) as above
      RelationGraph::build(snapshot_with_relations())   one load, or cached
      neighbors: per-file and test-symbol indexes built once in `build`
```

Files: `src/storage.rs` (`symbol_count`, `symbols_by_ids`,
`for_each_embedding`, `index_id`/`index_generation`, `explain_query_plan`,
bulk checkpoint bracket), `src/retrieval.rs` (`SymbolSnapshot`, `TopK`,
`RetrievalEngine::with_snapshot`, streaming `semantic_top_k`),
`src/context.rs` (`build_context_with`), `src/relations.rs` (precomputed
`by_file`/`test_symbols`), `src/service.rs` (`ProcessCache`,
`with_process_cache`), `src/mcp.rs` (opts in), `src/index.rs`
(`begin/end_bulk_writes` around the base pass).

## 2. O(N) work removed from the request path

| was | now |
| --- | --- |
| `all_symbols()` on every engine construction | `symbol_count()`; symbols loaded only when expansion needs the corpus, at most once per engine, and from the process cache in `oxide mcp` |
| `by_id` map over N symbols per request | built once per `SymbolSnapshot` (cached) |
| `all_embeddings()` → `HashMap<u64, Vec<f32>>` (one heap allocation per symbol) | `for_each_embedding` streams rows; nothing per row is retained |
| N-sized `HashMap<u64, f32>` of dot products | bounded heap of `FUSION_CANDIDATE_LIMIT` (200) |
| `sort_by` over all N vector scores, then `take(200)` | heap admission, O(N log 200); lexical side `select_nth_unstable_by` |
| second `all_symbols()` + `all_symbol_relations()` inside `build_context` | shared with the engine's snapshot |
| `RelationGraph::neighbors`: `related_tests` lowercased every symbol per seed; `resolve_import` rescanned every symbol per import | filtered/indexed once in `RelationGraph::build` |
| `NativeEmbedder::new` (ONNX model load) per MCP request | once per process |
| three `COUNT(*)` per MCP request (`embeddings` walks every row's page: 21 ms at 44k) | cached with the snapshot at the same generation |
| lexical scoring in `VectorOnly` mode (never consumed) | skipped |

What is still O(N) per request, by design:

- The exhaustive vector scan itself (streamed): 44k × 256-dim = 28 ms,
  44k × 1024-dim = 82 ms (§5). This is the number sqlite-vec/Zvec target.
- `RelationGraph::build` when expansion runs (18 ms at 44k, in-process),
  plus the corpus load it needs in a one-shot CLI call. `related_tests`
  scans every test symbol by contract, so the graph cannot be built from a
  candidate subset; `oxide mcp` amortizes the load via the cache but still
  rebuilds the graph's hash maps per request.
- `search` with default expansion in a one-shot CLI process: the corpus
  load moves rather than disappears (sympy `oxide search` 0.44 → 0.33 s,
  vs 0.05 s with `--no-expand`). `--no-expand` / `--profile fast` /
  `context` seeds / MCP are the paths that are now bounded.

Candidate depth: fixed at `FUSION_CANDIDATE_LIMIT = 200` per side, exactly
as fusion consumed before. A different depth changes RRF inputs and
therefore results, so the 25/50/100/200/500 sweep in the brief could only
be run as a *cost* sweep: the bounded heap's admission cost is O(log K) per
row and was not measurable against the row decode at any K in that range
(`bounded_top_k_equals_full_sort_then_take` pins set-and-order equality
for all of them). The only depth that preserves parity is 200; nothing
here needs a wider pool.

## 3. Parity evidence

| gate | result |
| --- | --- |
| 5 real repos × 13 queries (phrases, symbol names, a repeated-token query) × 8 surfaces (`search` lexical / semantic / hybrid / hybrid `--no-expand` / hybrid `--profile quality`, `context` balanced / fast / quality), `--json`, 520 outputs | **byte-identical** to the pre-change binary, at every stage of the change |
| `oxide eval --config fixtures/benchmark.json` | identical rows (the row *order* differs between two runs of the *same* binary — `eval.rs` iterates a `HashMap` of repos; pre-existing, out of scope) |
| `tests/benchmark_gate.rs` | pass |
| `streaming_semantic_scan_matches_materialized_scan_exactly` | the streaming scan vs the old `all_embeddings` + full sort, verbatim, incl. wrong-length and empty vectors: same ids, same order, **same `f32` bits** for K ∈ {1, 25, 200, 500} |
| `bounded_top_k_equals_full_sort_then_take` | heap and `select_nth` vs full sort, heavy ties, N ∈ {0…5000}, K ∈ {0, 1, 25, 50, 100, 200, 500} |
| `search_hydrates_only_candidates_unless_expansion_needs_the_corpus` | a counting store proves `all_symbols`/`all_embeddings` are never called without expansion (any mode, and `Fast`), and exactly once with it; results equal the eager engine bit-for-bit |
| `tests/query_plans.rs` | every request-path statement's `EXPLAIN QUERY PLAN` pinned, with and without `ANALYZE` statistics |
| `tests/index_generation.rs` | each of the 9 write paths bumps the counter exactly once; reads never do; a rebuilt database gets a new `index_id` at the same generation; a pre-counter index is unkeyable until a writer opens it |
| `tests/mcp_e2e.rs::a_live_server_sees_an_out_of_process_reindex_on_the_next_call` | warm cache, then `oxide index` from another process: removed symbol gone, added symbol present, on the very next `search` and `context` |
| `service::process_cache_hits_at_the_same_generation_and_reloads_after_a_write` | same `Arc` on a repeat, new entry after a write, `None` with the cache off |
| `tests/interrupted_index_recovery.rs::sigkill_mid_index_under_bulk_checkpointing_recovers_on_the_next_run` | real `kill -9` with >6 MB of un-checkpointed WAL: torn index refused (nothing published), WAL replayed, next `oxide index` completes and serves |
| `a_failed_embedding_scan_yields_no_semantic_evidence_not_partial_evidence` | a scan that errors mid-table contributes nothing (lexical-only degrade), never a ranking from the rows it got through |
| `context_propagates_a_failed_relations_load` | an unreadable `symbol_relations` table is still an error from `context`, not a silently structure-less pack |
| `cargo fmt`, `cargo clippy --all-targets`, `cargo test` (31 binaries) | clean / pass |

Not re-run: the ContextBench Tier A canonical table
(`docs/canonical-baseline.md`) needs the qwen3 llama.cpp server and the
cached indexes it was built on. The 520-output byte identity on real
repositories is a stronger "unchanged" statement than a metric-level
re-run would be, and the fixture eval covers the committed gate.

## 4. Real-repository performance

### One-shot CLI (median of 5, `/usr/bin/time`, 10 ms resolution)

| repo | mode | before | after | RSS before → after |
| --- | --- | ---: | ---: | --- |
| requests | semantic | 0.02 s | 0.01 s | 20 → 17 MB |
| pytest | semantic | 0.04 s | 0.02 s | 34 → 18 MB |
| pytest | hybrid `--no-expand` | 0.05 s | 0.02 s | 35 → 19 MB |
| pytest | hybrid (expand) | 0.05 s | 0.05 s | 35 → 30 MB |
| pytest | context | 0.08 s | 0.05 s | 47 → 30 MB |
| pylint | lexical (expand) | 0.08 s | 0.07 s | 34 → 35 MB |
| pylint | semantic | 0.09 s | 0.03 s | 47 → 17 MB |
| pylint | hybrid `--no-expand` | 0.09 s | 0.03 s | 48 → 18 MB |
| pylint | hybrid (expand) | 0.11 s | 0.08 s | 49 → 35 MB |
| pylint | context | 0.16 s | 0.08 s | 67 → 36 MB |
| sympy | lexical (expand) | 0.32 s | 0.30 s | 146 → 149 MB |
| sympy | semantic | 0.39 s | **0.05 s** | 190 → **18 MB** |
| sympy | hybrid `--no-expand` | 0.37 s | **0.05 s** | 190 → **20 MB** |
| sympy | hybrid (expand) | 0.44 s | 0.33 s | 195 → 150 MB |
| sympy | context | 0.72 s | **0.39 s** | 330 → **162 MB** |

`db`/WAL sizes are unchanged by this work (no schema change): the WAL is
0 bytes after every completed run.

### Repeated MCP calls (`scripts/mcp_bench.py`, 8 calls, median of the 7 after the first)

| repo | search before | search after | context before | context after | server RSS |
| --- | ---: | ---: | ---: | ---: | --- |
| requests | 16.2 ms | **4.4 ms** | 11.3 ms | **4.0 ms** | 29 → 25 MB |
| flask | 16.6 ms | **5.0 ms** | 21.3 ms | **5.7 ms** | 37 → 27 MB |
| pytest | 46.0 ms | **11.6 ms** | 69.4 ms | **10.6 ms** | 85 → 39 MB |
| pylint | 88.0 ms | **19.2 ms** | 131.7 ms | **19.9 ms** | 123 → 43 MB |
| sympy | 355.4 ms | **63.8 ms** | 636.9 ms | **70.2 ms** | 668 → 177 MB |
| requests, native `arctic-embed-xs-q` | 68.7 ms | **6.1 ms** | 71.6 ms | **6.7 ms** | 113 → 82 MB |

First-call latency (cache miss) is unchanged to slightly better; the cache
costs one corpus load per `(index_id, index_generation)`.

### Attribution (sympy MCP search / context, median)

| stage | search | context |
| --- | ---: | ---: |
| before | 355 ms | 637 ms |
| data path only (streaming scan, candidate hydration, one snapshot load) | 365 ms\* | 353 ms |
| + process cache (snapshot, stats, embedder) | 70 ms | 64 ms |
| + `RelationGraph` per-seed rescans removed | 64 ms | 70 ms |

\* MCP `search` requests expansion, so without the cache the one-shot corpus
load still runs; the data-path change alone shows up as `--no-expand`
(0.37 → 0.05 s in the CLI table).

### Indexing

| pylint (13,360 symbols), interleaved best of 3 | before | after |
| --- | ---: | ---: |
| cold index | 9.21 s | **7.06 s** (−23%) |
| no-change reindex | 0.14 s | 0.14 s |
| single-file edit | 0.27 s | 0.28 s |
| peak RSS | 54–57 MB | 52–56 MB |

The cold-index gain is the bulk checkpoint policy (§5), not the data
path. A separate, pre-existing observation while building the corpus:
`oxide index` on sympy peaks at 383 MB RSS in the embedding stage
(`update_embeddings` loads `all_embeddings`+`all_symbols`); that is the
*indexer's* O(N), outside this brief.

## 5. SQLite experiments

Run independently, after the data-path refactor, on `pylint` (write
side) and `pylint`/`sympy` (read side), through temporary env-driven
pragma hooks that were removed afterwards. Rounds taken while the machine
was contended (runs 2–4× slower across every configuration at once) are
discarded; figures are best-of-N from quiet rounds.

| knob | measured | verdict |
| --- | --- | --- |
| `PRAGMA optimize` / `ANALYZE` | plans byte-identical before/after on every request-path statement (now pinned by `tests/query_plans.rs`); lexical prepare 2.1 → 2.0 ms (pylint), 3.8 → 3.7 ms (sympy) — noise; `ANALYZE` itself 0.6 s (pylint) / 2.3 s (sympy) at index time | **reject**, same as last round; the plan pin is the deliverable |
| WAL `wal_autocheckpoint = 0` during a full index, `TRUNCATE` at close | cold index 9.0 → 6.8 s; p95 commit 13.1 → 3.1 ms; **WAL peak 1,160 MB for a 46 MB db**; final checkpoint 162 ms | **reject as-is**: unbounded WAL growth |
| `wal_autocheckpoint = 16000` (64 MB) | 6.6 s; p95 2.5 ms; WAL peak 67–75 MB; final checkpoint 24–60 ms | reject: same time as 4000 within noise for 2.5× the WAL |
| `wal_autocheckpoint = 4000` (16 MB) during `update_base` only | 7.2 s (−21%); p95 3.5 ms; **WAL peak 22–32 MB**; final checkpoint 3 ms; shipped binary confirms 9.2–9.8 → 7.1–7.3 s | **accept** — `IndexBackend::begin/end_bulk_writes`, bracketed around the base pass and restored on every exit, so `oxide watch`'s per-batch writes keep SQLite's default |
| `synchronous = NORMAL` | 7.8–8.0 s (−14%, inside this machine's noise band); p50 commit 0.9 → 0.3 ms; p95 unchanged (checkpoint-bound); with checkpoints off as well 5.1 s | **not shipped**. Safe on paper: in WAL mode NORMAL can only lose a *suffix* of committed transactions after power loss, so a surviving `set_meta_all` implies every earlier file transaction survived, and every freshness check OXIDE makes is content-hash based rather than generation-trusted. But no local test can distinguish NORMAL from FULL — process kills are fully durable under both — so the "never a falsely current generation" proof the brief requires would be an argument, not evidence, and the measured gain is too small to justify shipping on an argument. |
| `mmap_size = 256 MB / 1 GB` (read connections) | vector scan sympy 28.5 → 24.4 ms (fresh connection; 21 ms on a reused one), pylint 9.7 → 8.6 ms; **peak RSS 17 → 110 MB (sympy), 17 → 46 MB (pylint)** as the mapped pages count against the process while the connection is open | **reject**: marginal, worse memory behavior, and platform-dependent mapping semantics for a release that targets three OSes |

Not reopened, per the brief: `cache_size`, `VACUUM`, presorted postings,
multi-row inserts (all rejected last round with measurements).

## 6. Is the exhaustive scan still slow enough to justify sqlite-vec / Zvec?

The control, `examples/scan_probe.rs` (fresh read-only connection, full
`VectorOnly` search including top-10 hydration; raw mode = decode + dot
only), median of 15:

| corpus | dim | vectors | search (fresh conn.) | raw scan |
| --- | ---: | ---: | ---: | ---: |
| pytest | 256 | 5,352 | 5.4 ms | — |
| pylint | 256 | 13,360 | 9.7 ms | — |
| sympy | 256 | 44,254 | 28.5 ms | 24.6 ms |
| sympy (synthetic vectors) | 1024 | 44,254 | — | 82 ms |
| sympy, native default (384-dim, interpolated) | 384 | 44,254 | ≈ 40 ms | — |

Memory is O(K) during the scan (server RSS for sympy is the cached symbol
snapshot, not vectors). Cost is linear in `N × dim` at roughly 2 ns/float
(11.3M floats in 24.6 ms; 45M in 82 ms) — row iteration and little-endian
decode out of SQLite's page cache, not arithmetic.

Verdict: **not yet, at the sizes OXIDE targets.** At the 44k-symbol upper
end of the "comfortable to ~50k" envelope the scan is 28 ms with the
hashed embedder and ~40 ms with the shipped 384-dim default — a minority
of a `context` call whose structural stage costs as much again (and the
whole repeated-MCP `search` on sympy is 64 ms). An
ANN index would have to beat *this* number, exact, with no recall loss at
K=200 (fusion consumes the full top-200, so approximate top-K changes
results), and pay for its own build and storage. It becomes worth
measuring when either (a) corpora pass ~100k symbols, where the scan
crosses 100 ms even at 256 dims, or (b) a 1024-dim provider becomes the
default (82 ms at 44k). Until then the exhaustive streaming scan is the
control, and the two experiments should be scoped against it rather than
against the pre-change 0.39 s figure, which was the O(N) load, not the
scan.

## Second-opinion review

Codex reviewed the uncommitted change (`codex review --uncommitted`) and
raised two findings, both accepted and fixed before this document was
finished:

- **P1 — a vector scan that failed part-way ranked from a partial heap.**
  `for_each_embedding`'s error was dropped while the rows already visited
  stayed in the top-K, so a corrupt row late in the table would have fed
  incomplete semantic evidence into fusion. The old
  `all_embeddings().unwrap_or_default()` dropped semantic evidence
  entirely on failure; `semantic_top_k` now does the same (returns empty
  on `Err`), pinned by the test above.
- **P2 — `context` stopped reporting an unreadable relations table.** The
  shared snapshot accessor swallowed `SymbolSnapshot::load` errors into an
  empty snapshot (correct for search-side expansion, whose contract has
  always been degrade-not-fail), but `build_context` used to propagate
  that error with `?`. `snapshot_with_relations` now returns a `Result`
  and `build_context_with` propagates it; search's `snapshot()` keeps the
  degrade contract.

## Operational notes

- **Indexes built before this change** carry no `index_id`/
  `index_generation` and are served correctly but never cached by `oxide
  mcp` until any writer opens them — any `oxide index` run, including a
  no-change one, adds both keys (`SqliteStore::open`).
- **`SearchHit.symbol.calls`/`bases`** are populated when a hit is hydrated
  from a snapshot that carries relations (MCP cache, or a `context` call).
  The public `Evidence` surfaces never included those fields, and the
  parity run above is over those surfaces.
- **The process cache holds one snapshot per repository root the process
  has served.** A `oxide mcp` serving many repositories holds one each;
  nothing evicts. For the single-repository case it is designed for, that
  is the same memory the old code allocated per request, kept instead of
  rebuilt.
