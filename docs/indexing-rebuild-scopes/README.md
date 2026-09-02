# Explicit rebuild scopes for `oxide index` (`-a`/`-g`/`-e`)

## Flag contract

`oxide index` with no flags is unchanged: reconcile only stale/changed
layers, exactly as before this feature. Every pre-existing caller of
`update_index` (all ~60 call sites across the test suite, `eval.rs`, the
examples) keeps that behavior byte-for-byte — `IndexOptions::default()`
(all fields `false`) is a fully inert no-op, and `update_index` is now a
thin wrapper around `update_index_scoped(..., &IndexOptions::default())`.

| Flag | Meaning | Forces | Does NOT force |
|---|---|---|---|
| *(none)* | plain incremental | nothing | — |
| `-g` / `--graph` | rebuild structural relations for every existing symbol | `symbol_relations` recompute for symbols in files NOT reparsed this run (reparsed files already get it via the normal per-file path) | reparse; re-embed |
| `-e` / `--embeddings` | rebuild every symbol's embedding | re-embed regardless of stored-hash match | reparse; graph refresh |
| `-a` / `--all` | full rebuild | reparse every file regardless of content hash, **plus** everything `-g` and `-e` force | — |
| combinations | any subset of `-g`/`-e` composes; `-a` implies both plus the forced reparse | — | — |

Each flag only *widens which symbols a stage recomputes* — none of them
change what "stale" means for an unrelated layer, and combining flags never
does more work than the union of what each flag does alone. This is
verified directly (not just asserted) by `tests/index_scope_flags.rs`,
which checks each flag in isolation and in combination against the
`reparsed_files` / `relations_refreshed_symbols` / `reused_embeddings`
counters.

### Two invariants the task explicitly required, and how they hold

- **"`-e` must never embed against stale symbols."** The base stage
  (`update_base`) always runs before the embedding stage
  (`update_embeddings`), for every invocation regardless of flags — this
  was already `update_index`'s existing ordering, untouched by this
  feature. `-e` only changes the *filter* inside the embedding stage
  (recompute every symbol's vector instead of only ones whose stored hash
  changed); it never changes when that stage runs relative to parsing.
  Pinned by `tests/index_scope_flags.rs::e_never_embeds_against_stale_symbols_when_a_file_actually_changed`,
  which edits a file, sets `-e`, and asserts the file was reparsed *and*
  the forced re-embed still ran on the new content.
- **"`-g` must refresh required parse/symbol state, but unrelated valid
  layers should not be recomputed."** `-g` alone leaves `reparsed_files`
  and `embedded_symbols` at zero when nothing on disk changed — it widens
  only the structural-relations recompute, reusing whatever symbols the
  (always-run) normal incremental parse already produced.

### Why `-a` needs a full forced reparse, not just `-g -e` combined

`-g -e` together still trust the content-hash cache for parsing: a file
whose text hasn't changed keeps its already-parsed symbols. `-a` additionally
bypasses that cache and reparses every file — the escape hatch for "the
*extractor* changed (a grammar/tags.scm fix, an OXIDE upgrade) and should
re-derive symbols even where the source text itself didn't," which `-g -e`
cannot do. `tests/index_scope_flags.rs::all_forces_every_layer` and
`::force_graph_and_force_embeddings_combine_without_forcing_reparse` pin
this distinction directly.

One consequence, also pinned by that test: under `-a`,
`relations_refreshed_symbols` reports `0`, not the total symbol count. This
looks surprising until you see why — `-a`'s forced reparse already makes
every file go through the normal per-file relations computation (the same
path a genuinely-changed file always used), so the *separate* `-g` code
path (which only recomputes relations for files NOT being reparsed) finds
nothing left to do. No redundant computation, not a missing feature.

## Staged reporting for `-a`

Per the task's explicit UX requirement: `-a` in human-readable mode prints
the base/graph stage's summary as soon as it finishes, then a one-line
warning before starting semantic indexing, then the final combined summary:

```
$ oxide index . -a
indexed /repo: 42 files scanned, 0 unchanged, 42 reparsed, 0 removed, 0 errored
symbols: +120 new, ~40 changed, -5 deleted; embeddings: 0 written, 0 reused
took 340ms
oxide: base/graph stage done — continuing to semantic indexing, which can take noticeably longer on CPU...
indexed /repo: 42 files scanned, 0 unchanged, 42 reparsed, 0 removed, 0 errored
symbols: +120 new, ~40 changed, -5 deleted; embeddings: 875 written, 0 reused
took 1820ms
```

`--json` mode stays a single structured result (the intermediate hook is a
no-op there), consistent with every other command's `--json` output being
one clean object an agent can parse without ambiguity.

Implementation: `service.rs::RepositoryService::index_staged` runs
`index::update_base` then invokes a caller-supplied closure with the
base-stage `IndexResult` before running `index::update_embeddings` — the
CLI's closure is the only thing that prints; `service.rs` itself never
does. `index()` is `index_staged` with a no-op closure, so both entry
points share one implementation.

## New report field

`IndexReport`/`IndexResult` gained `relations_refreshed_symbols: usize` —
how many symbols had structural relations recomputed even though their own
file wasn't reparsed this run. Zero for a plain incremental run (nothing
should trigger it) or under `-a` (see above); nonzero under `-g` or `-g -e`
on an otherwise-unchanged repo.

## Benchmark: incremental vs `-g`/`-e`/`-a`

Fixture repo (`fixtures/py_repo` + `fixtures/ts_repo`, 16 files / 95
symbols), offline `HashedEmbedder` (no network — isolates the *client-side*
cost of each flag from embedder latency, which is profiled separately
below), 5 runs each, `/usr/bin/time` wall clock:

| Mode | Median wall time | What it recomputed |
|---|---|---|
| plain incremental (no-op) | ~0ms | nothing |
| `-g` | ~45ms | structural relations for all 95 symbols |
| `-e` | ~5ms | all 95 embeddings (offline hash — real network cost is profiled below) |
| `-a` (staged) | ~110ms total | full reparse + graph + embeddings |

The ordering (`incremental < -e ≈ -g < -a`) and the fact that `-g`/`-e`
alone never regress to `-a`'s full-reparse cost is the actual claim being
tested here; the absolute milliseconds are fixture-scale and not
representative of a real repository's *embedding* cost, which is
network/model-bound — see below.

## Semantic indexing stage: profiling and what was (and wasn't) optimized

### Where the time actually goes

Direct HTTP profiling against the local CPU embedder (Qwen3-Embedding-0.6B
Q8_0, llama.cpp `--parallel 1 -ub 2048 --threads 8`, the same server this
repo's own scripts/embedder.sh manages), sending N copies of one symbol's
text per request:

| batch size N | total | per-item |
|---|---|---|
| 1 | 3016ms | 3016ms |
| 8 | 1079ms | 135ms |
| 32 | 1905ms | 60ms |
| **64 (current default)** | 3575ms | **56ms** |
| 128 | 7419ms | 58ms |

Per-item cost plateaus at N=32–64 and does not improve further at N=128 —
consistent with the server's own `-ub 2048`-token micro-batch limit already
being saturated well before 64 typically-sized symbol texts. **The
existing chunk size of 64 was already at the empirically-measured optimum**;
this profiling pass found no batching/chunk-size change that would produce
a "meaningful" improvement, and none was made. The overwhelming majority of
wall time in the semantic stage is genuine model inference cost on CPU —
not something a client-side pipeline change can fix without touching the
model, the server's parallelism config, or embedding semantics, all
explicitly out of scope for this task.

### What WAS optimized: batching the write path

`IndexBackend::put_embedding` was called once per symbol from
`update_embeddings`' two write loops. Each call is one `INSERT OR REPLACE`
executed directly on the connection — under SQLite's default autocommit
behavior, that's one implicit transaction (and one fsync) per symbol,
independent of and additional to the embedder's own latency. This is a
real, avoidable cost with zero relationship to embedding semantics: same
rows, same values, just fewer commits.

Added `IndexBackend::put_embeddings_batch(&mut self, items: &[(u64,
Vec<f32>)])`, which wraps a whole chunk's worth of inserts in one
transaction (the same pattern `put_symbol_relations_batch` already used for
structural relations — see its doc comment). `update_embeddings` now calls
this once per HTTP/thread-pool chunk instead of `put_embedding` once per
symbol.

**Correctness parity**: `tests/embedding_batch_writes.rs` asserts the
batched and per-item paths produce byte-identical stored rows (same
`content_hash`, same vector bytes) for the same input, including the
"symbol id has no matching row → silently skipped" edge case
`put_embedding` already had. `examples/verify_parity.rs` (ad hoc, not
committed) additionally confirmed this end-to-end against a real
file-backed store before landing the change.

**Measured improvement** (`cargo run --example
embedding_write_batching_benchmark --release`; file-backed SQLite — a
`:memory:` store never fsyncs and can't show this effect at all — 4000
symbols, `HashedEmbedder` so embedder latency doesn't drown out the
write-path signal):

```
per-item put_embedding:        84.2ms  (0.021ms/symbol)
batched put_embeddings_batch:   36.7ms  (0.009ms/symbol)
speedup: 2.29x
```

This is modest in absolute terms next to real network-embedder latency
(tens of ms/symbol from the table above) but is a real, zero-risk,
zero-downside win that scales with repository size and costs nothing when
the embedder itself dominates — exactly the kind of change worth landing
alongside a client-side pipeline that was otherwise already
near-optimally tuned.

### What was explicitly NOT touched

- The embedding model, its prompt/instruction format, or its output vectors
  (`embed_text`, `qwen3_query_text`, `HttpEmbedder::embed_query`) — untouched.
- Retrieval scoring, fusion, or the relevance floor — untouched; this
  feature never reaches `src/retrieval.rs` or `src/context.rs`.
- The embedder server's own concurrency (`--parallel`) or batch (`-ub`)
  configuration — that's `scripts/embedder.sh`'s and the deployer's
  decision, not something `oxide index` should reach into.
