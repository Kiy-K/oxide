# Enhanced SQLite vs SurrealDB for OXIDE

**Verdict: keep SQLite and enhance it. SurrealDB is rejected.** It fails two
hard gates outright — G0 on cost and G1 on concurrent access — it cannot build
the vector index it needs at 40,000 symbols, and it does not win any gate it
passes. The finding is scoped to what was tested: SurrealDB 3.2.4 embedded with
`kv-rocksdb`, not every SurrealDB version, storage engine, or configuration. Zvec stays
frozen as the leading specialist challenger; this round did not exercise it, and
nothing here settles the question it was frozen against.

- Methodology, gate definitions, harness bugs found and fixed, limits: [`methodology.md`](methodology.md)
- Status-quo numbers the proposal has to beat: [`baseline.md`](baseline.md)
- Whether FTS5 can reproduce OXIDE's ranking: [`fts5-parity-notes.md`](fts5-parity-notes.md)
- Raw output: [`raw/`](raw/) — [`g1-concurrency.txt`](raw/g1-concurrency.txt),
  [`sqlite-gates.txt`](raw/sqlite-gates.txt) (three shapes),
  [`surrealdb-10k.txt`](raw/surrealdb-10k.txt) (two independent index builds),
  [`surrealdb-40k.txt`](raw/surrealdb-40k.txt),
  [`perf-baseline.txt`](raw/perf-baseline.txt),
  [`dependency-cost.txt`](raw/dependency-cost.txt) (also holds the G0 build timing)
- Gate harness: [`spike/`](spike/) — both backends and both G1 probes in one binary

## Gate results

Both backends driven through one binary ([`spike/`](spike/)), one seeded
synthetic corpus, one gate list. Headline shape: 10,000 symbols, 384-d vectors.

| Gate | Enhanced SQLite | SurrealDB 3.2.4 (`kv-rocksdb`) |
| --- | --- | --- |
| **G0** build cost | **no new dependency** — FTS5, `bm25()` and `trigram` are all present in the pinned `rusqlite 0.32 bundled` (SQLite 3.46.0) | **FAIL in practice** — +221 crates (369 → 590), compiles RocksDB from C++ source; cold `-j 2` build **22 m 32 s**, 6.7 GB peak RSS |
| **G1** concurrent access | **PASS** — a second process reading through the same flags as `SqliteStore::open_read_only` sees a consistent snapshot while a writer holds an *open, uncommitted* write transaction | **FAIL** — RocksDB takes an exclusive `LOCK`; a second process gets `Resource temporarily unavailable`, and even a same-process reopen after `drop` gets `lock hold by current process` |
| **G2** startup | open 35 ms · reopen populated **0 ms** | open 436 ms · reopen populated 249 ms |
| **G3a** persistence/reopen | PASS | PASS |
| **G3b** atomic file replacement | PASS | PASS — rolls back correctly on `THROW` |
| **G3c** freshness add/edit/rename/delete | PASS — no stale relation or trigram rows | PASS — no stale relation or literal rows |
| **G4a** graph parity | PASS — 10 probes (5 call targets, 5 base targets), every set exact | PASS — same 10 probes, every set exact |
| **G4b** BM25 exact match | PASS — 5 terms, 0 missing, 0 spurious | PASS — 5 terms, 0 missing, 0 spurious |
| **G4c** literal substring exact match | PASS — 5 substrings, 0 missing, 0 spurious | PASS — same 5, but by full scan; SurrealDB has no trigram index |
| **G4d** vector recall@10 == 1.0 | **PASS** — exact by construction | **FAIL** — HNSW returns 7–8/10 at the default `ef=64` across the two captured index builds, and 9–10/10 at `ef=512`, which costs 25 ms against SQLite's 7.8 ms exact answer. An exact in-database control returns 10/10, so this is index recall, not a query-semantics mismatch |
| **G4e** determinism (repeat query) | PASS | PASS within one index; recall varies between index *builds* |
| **scale to 40k symbols** | **PASS** — all gates, 3.3 s total | **FAIL** — 40,000 symbols and vectors ingest fine (38.3 s + 5.2 s, 1.5 GB on disk); the following `DEFINE INDEX … HNSW DIMENSION 384` aborts with a RocksDB `Transaction conflict`, single writer, no concurrency, in every one of six attempts |
| **G5** ContextBench | not reached — no candidate cleared every gate | **not run** (G1 already fatal) |

### Cost side by side, 10,000 symbols

| | Enhanced SQLite | SurrealDB | ratio |
| --- | --- | --- | --- |
| ingest 10k symbols | 530 ms | 10,565 ms | 20× |
| ingest 10k vectors | 64 ms | 1,482 ms | 23× |
| build vector index | 0 ms (none) | 17,804 ms | — |
| **total write phase** | **0.71 s** | **~30 s** | **~42×** |
| BM25 query | 0.51 ms | 20 ms | 39× |
| literal substring | 0.50 ms | 178 ms | 353× |
| `callers_of` | 0.14 ms | 113 ms cold / 40 ms warm | 296× warm |
| top-10 vectors | 8.2 ms cold / 7.8 ms warm, exact | 1 ms @8/10, 25 ms @10/10, plus 25 ms first-in-process | — |
| peak RSS | 42 MB | 693 MB write / 359 MB read | 9–17× |
| on-disk | 28 MB | 89 MB (322 MB before compaction) | 3.2× |

Numbers are from index build #1 in [`raw/surrealdb-10k.txt`](raw/surrealdb-10k.txt)
and the 400×25 run in [`raw/sqlite-gates.txt`](raw/sqlite-gates.txt).

**All runs are on ext4 with 170 GB free, not tmpfs.** An earlier sweep used
`/tmp`, which on this machine is tmpfs; that flattered SurrealDB's write path by
roughly 4× (2.0 s vs 10.6 s to ingest 10k symbols) while barely moving SQLite,
so those numbers were discarded rather than quoted. Run-to-run variation on the
SurrealDB side is real but does not change any ratio's order of magnitude.

Both backends commit vectors in transactions of 1,000 rows (`VEC_CHUNK`), route
every file replacement through the same `replace_file` path, and report a cold
and a warm query number, so neither is credited with a warm cache the other paid
for. Peak RSS includes the harness's own in-memory corpus (~15 MB of vectors
here) identically for both.

## Why G1 is fatal rather than inconvenient

OXIDE is a one-process-per-CLI-call tool with a long-lived watcher. `oxide watch`
holds the store open continuously; `oxide index` can run from any process at any
time; `AGENTS.md` records a deliberate decision that `SqliteStore::open_read_only`
stays a plain read-only connection rather than `immutable=1` **precisely because
`index.db` can change concurrently**, and `tests/cli_e2e.rs` pins that contract.

An exclusive store lock breaks all of it: with `oxide watch` running, every
`oxide context` in another terminal would fail to open the database. The lock is
not released when the handle is dropped either, so the same failure appears
inside a single process. No configuration fixes this — it is what `kv-rocksdb`
is.

## Why the rest does not rescue it

Even setting G1 aside, SurrealDB loses on its own terms:

- **It cannot build its vector index at 40k symbols.** The rows go in fine —
  40,000 symbols in 38.3 s, 40,000 vectors in 5.2 s, 1.5 GB on disk — and then
  `DEFINE INDEX … HNSW DIMENSION 384` aborts with a RocksDB
  `Transaction conflict`, in a strictly sequential single-writer run,
  hit in **every one of six attempts**, across three ingest batching strategies
  and two filesystems (a seventh attempt died of `Disk quota exceeded` on tmpfs
  and is discarded as uninformative; the runs that count had 170 GB free).
  [`raw/surrealdb-40k.txt`](raw/surrealdb-40k.txt) holds the last of them.
  Without that
  index the vector path is a full scan, which is what SQLite already does more
  cheaply. The harness does not isolate whether the cause is an engine limit,
  the HNSW build strategy, or a RocksDB tunable the error message itself names
  (`max_write_buffer_size_to_maintain`) — so read it as "this configuration
  does not get there", not as a capacity claim. For scale: SQLite passes every
  gate at 80,000 symbols in 7.2 s, and OXIDE's own `perf.sh` indexes 25,512
  symbols routinely.
- **221 new crates and a from-source RocksDB build** to reach parity with
  capabilities SQLite already ships. Two of the three "enhancements" in the
  Enhanced SQLite proposal — FTS5/BM25 and trigram literal search — are present
  today in the exact `rusqlite` pin OXIDE already depends on.
- **Async-only SDK.** Every documented method is `.await`ed and the SDK requires
  tokio. All 84 `IndexBackend` call sites in `src/` (plus ~66 in `tests/`) would
  become `async` or `block_on`. `AGENTS.md` records that `oxide context` /
  `oxide search` run "fully synchronously with no tokio runtime at all", which is
  the stated reason `RetrievalEngine::search` uses `std::thread::scope`.
- **Its own serialization trait, not serde.** SurrealDB 3.x replaced serde with
  `SurrealValue`; `Symbol` would have to carry a database-specific derive.
- **No literal-substring index.** G4c passes only because `string::contains`
  scans every row (178 ms vs 0.50 ms). That is a structural gap, not a tuning
  problem — there is no trigram index to add.
- **Approximate vectors slower than exact ones at this scale.** SQLite's
  brute-force cosine is 7.8 ms and exact at 10k symbols; SurrealDB's HNSW needs
  `ef=512` and 25 ms to reach 10/10.

## What Enhanced SQLite actually buys

Not a rewrite — the win is deleting per-invocation rebuild work.

[`baseline.md`](baseline.md) measures `search` latency scaling linearly with
symbol count across four clean points (0.04 s @ 3.4k → 0.28 s @ 25.5k, ~11 µs
per symbol) because `LexicalIndex::build` re-reads every symbol body from disk
and rebuilds the BM25 posting map in every process. Persisting that index in
FTS5 replaces it with a **0 ms** reopen and a **0.5 ms** query.

Measured scaling of the enhanced store, all gates passing at every size:

| symbols | BM25 | literal (trigram) | `callers_of` | top-10 vectors (brute force, warm) | reopen |
| --- | --- | --- | --- | --- | --- |
| 10,000 | 0.51 ms | 0.50 ms | 0.14 ms | 7.8 ms | 0 ms |
| 40,000 | 1.81 ms | 1.28 ms | 0.56 ms | 33.4 ms | 0 ms |
| 80,000 | 3.41 ms | 2.47 ms | 1.00 ms | 63.9 ms | 0 ms |

**The vector path is the only part that needs an actual decision.** Brute-force
cosine costs about 0.8 µs per vector, so it stays comfortable to roughly 50k
symbols and becomes the dominant query cost past that. `sqlite-vec` is the
obvious candidate but is still published as `0.1.10-alpha.4`; an in-process ANN
crate would leave the store unchanged. **That is the question Zvec was frozen
against, and this evaluation does not settle it** — it only establishes that
SurrealDB is not the answer to it.

## What is not yet proven

- **Ranking parity.** FTS5 can express OXIDE's field weights and, with
  pre-tokenized columns, its exact token stream (see
  [`fts5-parity-notes.md`](fts5-parity-notes.md)) — but FTS5's own k1/b and
  document-length accounting differ from `LexicalIndex`'s weight-sum `doc_len`.
  A port must be run through `oxide eval --config fixtures/benchmark.json`
  against `docs/canonical-baseline.md`, with any ordering diff explained, per
  `CLAUDE.md`. This evaluation measured that FTS5 is *fast and correct*, not
  that it ranks identically.
- **G3b tests transaction atomicity, not OXIDE's full `replace_file` contract** —
  embedding reuse for unchanged `content_hash`, and an entry for every symbol
  including empty ones, are not modelled. See `methodology.md`.
- **G4b does not prove the two engines tokenize alike.** Each is held to
  "return every document whose token stream contains this term, and no others"
  against whole-word ground truth (`corpus::contains_token`), with its own
  tokenizer; scores and ordering are not compared at all. See `methodology.md`.
- **The corpus is synthetic**, and its 384-d vectors are near-uniformly random,
  which is the hardest case for a graph ANN index. Real embeddings cluster and
  would likely score better than 8/10. That weakens G4d as evidence about HNSW
  in general; it does not touch G0, G1, the 40k write failure, or any cost
  measurement, and G4d was not the gate that decided the outcome.
- **ContextBench was not run** for either candidate. Under the stated rule it
  runs only after a candidate clears every hard gate.

## Reproducing

```bash
# status quo — run these back to back on an idle machine
for n in 200 450 900 1500; do OXIDE_EMBED_NATIVE=hashed scripts/perf.sh $n; done

# gates (standalone crate; not part of OXIDE's build)
cd docs/storage-backend-eval/spike
cargo build --release              # ~25 min cold: this compiles RocksDB
# Run on a real filesystem, not tmpfs: export TMPDIR to somewhere on disk, or
# the write-path numbers come out ~4x too kind to SurrealDB.
B=./target/release/sdb_gate
$B sqlite 400 25                                 # and 1600 25, 3200 25
D=$(mktemp -d)
$B surreal1 400 25 "$D"                          # writes, then exits
$B surreal2 400 25 "$D"                          # fresh process, reads
$B surreal1 1600 25 "$(mktemp -d)"               # 40k: HNSW build aborts
$B surreal-reopen "$(mktemp -d)/y"               # same-process reopen: also locked

# G1, both backends, same harness
T=$(mktemp -d); $B sqlite-hold  "$T/t.db" 12 & sleep 4; $B sqlite-try  "$T/t.db"
E=$(mktemp -d); $B surreal-hold "$E/x"    12 & sleep 6; $B surreal-try "$E/x"
```
