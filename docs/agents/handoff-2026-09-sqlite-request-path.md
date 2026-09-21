# Handoff — OXIDE, September 2026: storage / request-path track

For a fresh session picking up roadmap #9's "Next" items. Read
`AGENTS.md` first (load-bearing invariants; everything below assumes it),
then `docs/review/README.md` before reviewing anything.

## Where main is

- `origin/main` = `5c62edd`, CI green (Quality, Retrieval Gate, No default
  features, Coverage, Tests). No tag; **v0.1.1 is not to be tagged** on
  this work.
- Last 11 commits: Python relative-import fix (`0859161`), deterministic
  review order (`c340ab0`), six byte-identical request-path optimizations
  (`dd2fd9a`), profile doc (`b7da252`), generation-scoped `RelationIndex`
  cache in MCP (`8b15e27`), parallel literal reads (`bf75b52`), docs
  (`2615ae5`, `9f9a7be`), ranking research (`409db3c`, `f47b935`,
  `5c62edd`).
- Untracked, leave alone: `contrib/`, `docs/evals/phase-4.1/`,
  `docs/evals/phase-4.2-typesafe/`. `eval-agent/.venv` and
  `eval-agent/third_party/ContextBench` are gitignored tooling.
- Semantic-quality research is **paused** (`docs/semantic-quality-eval/`):
  audit + self-checked harness, zero measurements. Do not resume it on
  this track.

## Architecture in one paragraph

Single Rust crate. `oxide index` parses with tree-sitter-tags into
`Symbol`s (id = FNV1a(file + `\0` + qualified_name), persisted), writes
SQLite `<repo>/.oxide/index.db` (`symbols`, `embeddings` blob per symbol,
`symbol_relations` calls/bases side table, persisted BM25
`lexical_postings`/`lexical_docs`, `meta`), embeds `symbol_embed_text`
(identifiers only) with `arctic-embed-xs-q` via fastembed. A request
(`oxide search`/`context`/`review`, or `oxide mcp`) is **candidate-first**
(`RetrievalEngine::search`, `src/retrieval.rs`): BM25 over postings → top
200; `embed_query` on its own OS thread while BM25 runs; exhaustive f32
dot product streamed through a bounded top-200 heap; weighted RRF (K=60,
0.6 lexical / 0.4 semantic, f32 accumulation, `cmp_score_id` tie-break);
hydrate only fused candidates via `symbols_by_ids`. Structural expansion
(`RelationGraph` over a `SymbolSnapshot` = `all_symbols`) is the one
consumer that loads the whole corpus; `oxide mcp` caches snapshot +
`RelationIndex` per `(index_id, index_generation, schema keys)`.
`context.rs` allocates a bounded pack (primary cap 5, per-file cap 2,
relevance floor 0.15, 4096 tokens).

## Measured bottlenecks (numbers: `docs/retrieval-profile/README.md`)

Warm process, `pytest` (7,804 symbols), hashed embedder so inference is
excluded:

| stage | cost | status |
| --- | --- | --- |
| `SymbolSnapshot::load` (`all_symbols` + `all_symbol_relations`) | 60–82 ms, ~400k allocs, 27 MB; 55–65% of `context` and of `search` with expansion | **the open ceiling** — per-row `imports_json`/`references_json` parse, ~1 KB rows |
| `search hybrid --no-expand` | 15.5–18 ms (pytest one-shot CLI 32 ms after this pass) | hydration is 11.5k allocs; vector scan 6 ms |
| `COUNT(*) FROM embeddings` in `validate_index` | was 7.5 ms at 15k symbols | fixed by `idx_embeddings_symbol`; pinned in `tests/query_plans.rs` |
| `RelationGraph::build` | 4.7–5.9 ms | now `RelationIndex`, cached in MCP; fresh per one-shot CLI call |
| native model load | 70–130 ms per one-shot CLI call | dominates one-shot native latency; overlap experiment rejected (+15 MB peak) |
| literal search | walk 16–18 ms on pylint + reads | reads parallel (8 readers, in-order fold); walk still serial |

One-shot CLI after the pass (hashed): pytest `context` 107 ms / 38 MB,
pylint `context` 145–151 ms / 39 MB, requests `context` 23–27 ms. Warm MCP:
search −22–25%, context −18–26% vs before the pass.

## Frozen invariants you will run into on this track

- Output must stay **byte-identical** unless the change is a correctness
  fix: the parity protocol is 5 repos × 8 queries × 11 `--json` surfaces
  (440 outputs) + 55 literal outputs + `oxide eval --config
  fixtures/benchmark.json` (`docs/canonical-baseline.md`). Ranking
  constants (`FUSION_CANDIDATE_LIMIT`, K, weights, caps) are inputs, not
  knobs.
- `all_symbols` keeps SQL `ORDER BY file, start_line`; tie order
  `(file, rowid)` is corpus order and `neighbors()` ordering depends on it.
- Nothing on the request path may call `all_symbols`/`all_embeddings`
  except `SymbolSnapshot` (lazy, expansion only) — pinned by
  `search_hydrates_only_candidates_unless_expansion_needs_the_corpus`.
- `tests/query_plans.rs` pins every request-path `EXPLAIN QUERY PLAN`
  with and without `ANALYZE`; the vector scan must stay a plain
  `SCAN embeddings`.
- Any schema change: `schema_version` bump, migration/re-index story,
  `set_meta_all` atomic publish, foreign keys ON with index-driven
  cascades, `wal_autocheckpoint` handling in bulk writes. Never a second
  database (SurrealDB/Turso/sqlite-vec/Zvec are closed questions:
  `docs/storage-backend-eval/`, `docs/sqlite-request-path/README.md` §6).
- Never pair a `RelationIndex` with a snapshot it was not built from
  (pointer check in `RetrievalEngine::relation_graph()`).
- Generation bump inside every write transaction; `index_id` + generation
  key the MCP cache.

## Benchmark / verification protocol

1. `cargo build --release` both the baseline commit and the candidate into
   separate binaries; index the workload repos once with the *writer* so
   both binaries see the same index.
2. Per-stage numbers: `PROFILE_REPEAT=2 target/release/examples/retrieval_profile <repo> <query>`
   (wall clock + allocation count/bytes, cold and warm round).
3. One-shot latency + peak RSS: `/usr/bin/time -v` is absent on the dev
   machine; use a `getrusage(RUSAGE_CHILDREN)` wrapper (the previous
   session's `measure.py` was scratch — rewrite it, ~20 lines) and
   interleave baseline/candidate runs, medians of 7.
4. Warm MCP: `scripts/mcp_bench.py`.
5. Parity: the 440 + 55 outputs above, byte-diffed; then `mise run
   verify` (fmt → clippy → tests incl. benchmark gate → installer
   checks, ~10 min); then an independent Greptile review — the CLI
   cannot see unpushed local `main`, so review from a throwaway branch
   based on `origin/main` (`greptile review --agent --branch main` in a
   worktree), then delete the branch/worktree.
6. Workloads: shallow clones of `requests`, `flask`, `zod`, `pytest`,
   `pylint` (the profile doc's §0 sizes). Keep them and any `target/`
   on disk, never on tmpfs — a scratch target dir filled `/tmp` once and
   broke the shell for the session.

## Files that matter for this track

- `src/storage.rs` — `SqliteStore`, DDL, `all_symbols` (per-file imports
  parse cache, `row_to_symbol_without_imports`), `symbols_by_ids`,
  `for_each_embedding`, bulk-write pragmas.
- `src/retrieval.rs` — `RetrievalEngine::search`, `SymbolSnapshot`,
  `semantic_top_k`, `cmp_score_id`, hit assembly.
- `src/relations.rs` — `RelationIndex` / `RelationGraph`
  (`related_tests` scans every test symbol's `references` by contract).
- `src/service.rs` — `ProcessCache`/`CachedSnapshot`, `validate_index`.
- `src/index.rs` — `IndexBackend` trait, `update_base`/`update_embeddings`,
  schema/extraction versions.
- `src/context.rs` — allocator; `src/literal.rs` — parallel scan.
- `examples/retrieval_profile.rs`, `tests/query_plans.rs`,
  `tests/benchmark_gate.rs`, `docs/retrieval-profile/README.md` (§1 table,
  §2 verdicts incl. negatives, §5 remaining work),
  `docs/sqlite-request-path/README.md` (§5 SQLite experiments already
  rejected: `synchronous=NORMAL`, `mmap_size`, checkpoint off).

## Remaining work, in order (roadmap #9 items 4–6)

1. **Corpus load.** Decide between (a) loading less per row — compact
   `references`/`imports` encoding or a side table so the symbol row fits
   its page — and (b) not loading the corpus: SQL-probe structural
   expansion over `symbol_relations(kind, target)` / `symbols(name)`.
   (b) is blocked by `related_tests`' scan-all contract and corpus-order
   dependence; (a) is a schema change with a re-index. Measure each alone
   against the frozen baseline; byte-identical output is the gate.
   Already rejected: dropping the SQL `ORDER BY` for a Rust sort (no
   gain; sorter cost is overflow-page reads a plain scan only defers).
2. **Request-path fixed costs** after (1): re-profile `validate_index`
   meta reads, `lexical_totals`, connection open/pragmas, blob decode in
   the streaming scan.
3. **Literal walk**: the serial `ignore` walk (per-file open + 1 KB
   binary sniff); `scripts/perf.sh` `getrusage` fallback.

Constraints from the user that stand across sessions: SQLite
authoritative, Rust-native, existing CLI/MCP contracts, deterministic
output, frozen production defaults; run `mise verify` and Greptile;
preserve unrelated untracked dirs; **no commit, push, or tag without
explicit approval**; preserve negative results in `docs/`.
