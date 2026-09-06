# Status-quo baseline (what any replacement has to beat)

`OXIDE_EMBED_NATIVE=hashed scripts/perf.sh N`, release build, laptop
(16 cores, 15 GB RAM). The hashed embedder pins the embedding stage so the
numbers are storage and retrieval, not model latency.

**All four points were taken back to back on an otherwise idle machine.** An
earlier sweep was run while a large `cargo build` was saturating the CPU and
produced meaningfully worse and non-monotonic numbers (0.06 s search at 3.4k
symbols, 0.27 s at 15.3k, 0.28 s at 25.5k). Those are discarded; only the clean
sweep below is used.

| N modules/lang | files | symbols | cold index | warm reindex | single edit | `search` (best of 3) | `context` (best of 3) | peak RSS (cold) | db size |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 200 | 804 | 3,412 | 408 ms | 23 ms | 39 ms | 0.04 s | 0.05 s | 28.4 MB | 6.3 MB |
| 450 | 1,804 | 7,662 | 798 ms | 47 ms | 71 ms | 0.08 s | 0.10 s | 34.5 MB | 14 MB |
| 900 | 3,604 | 15,312 | 1,601 ms | 106 ms | 118 ms | 0.17 s | 0.20 s | 49.6 MB | 28 MB |
| 1500 | 6,004 | 25,512 | 2,667 ms | 184 ms | 190 ms | 0.28 s | 0.35 s | 69.3 MB | 47 MB |

## Reading these numbers

Query latency scales **linearly with symbol count**: per-symbol `search` cost
computed from the table above is 11.7, 10.4, 11.1 and 11.0 µs across the four
points — flat to within the measurement's own noise, so **~11 µs per symbol**
is the honest summary. `/usr/bin/time -f %e` has 10 ms resolution, so the
smallest point carries roughly ±12% quantisation and its 11.7 should not be
read as a real deviation.

That is not a query cost — it is a *rebuild* cost, paid on every CLI invocation:

- `LexicalIndex::build` (`src/retrieval.rs`) re-reads every symbol's body from
  disk and rebuilds the whole BM25 posting map, per process. `AGENTS.md` records
  why bodies are in there: gold-context evals showed bugfix targets hide behind
  local identifiers, so bodies are indexed at weight 1.
- `RetrievalEngine::search` loads *all* embeddings into a `HashMap` on first use
  (`store.all_embeddings()`), then scans them with dot products.

Extrapolating ~11 µs/symbol, a 50,000-symbol repository would spend roughly
0.55 s in `search` and 0.7 s in `context` before any ranking work happens.
That is the concrete pain this evaluation exists to address, and it is
what makes "persist the lexical index" the interesting proposal rather than a
rewrite for its own sake. The extrapolation is a fit over four points, not a
measurement — the largest repository actually measured here is 25,512 symbols.

## Migration surface

Any store swap has to move every call site of the `IndexBackend` trait
(`src/index.rs`). Counted on this HEAD:

| Location | Call sites |
| --- | --- |
| `src/index.rs` | 27 |
| `src/context.rs` | 20 |
| `src/retrieval.rs` | 19 |
| `src/service.rs` | 7 |
| `src/watcher.rs` | 5 |
| `src/structural_relations.rs` | 5 |
| `src/review.rs` | 1 |
| **`src/` total** | **84** |
| `tests/` total | ~66 |

For Enhanced SQLite these stay synchronous and most stay untouched — FTS5 and
trigram are new *tables*, not a new trait. For SurrealDB every one of them
becomes `async` or a `block_on`, because the SurrealDB Rust SDK is async-only
(its own concurrency reference says so, and every documented method is
`.await`ed). That collides directly with a load-bearing invariant in
`AGENTS.md`: `oxide context` / `oxide search` from the CLI run "fully
synchronously with no tokio runtime at all", which is precisely why
`RetrievalEngine::search` uses `std::thread::scope` instead of a tokio task.
