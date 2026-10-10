# HelixDB embedded disk vs LadybugDB: feasibility and benchmark

Status: done (2026-10-10) on `rewrite/v2`, after `266263f`.
**Verdict: REJECT** as a LadybugDB replacement at the pinned revision. Embedded
HelixDB is feasible, and every structural read result was identical to
LadybugDB's (below). On real repositories, though, it:
- rebuilds 9–27× slower;
- needs 1.4–1.8× the peak memory and 1.6–3.6× the disk (0.8–2.6× with the
  tuned variant);
- answers lexical, retrieve, single-entity and high-degree adjacency reads
  more slowly.

It is faster on reopen, full adjacency scans, batched lookups and most
routes. Its lexical quality was lower on ContextBench dev, on an
underpowered sample of 10 tasks. LadybugDB stays the store. No ADR, kernel,
retrieval or selection code changed, and the frozen Phase 3/4/5 artifacts
are untouched.

The experiment code (adapter, benchmark harness, contract tests) was removed
after the evaluation; only this report and the measured tables in
[`results/`](results/) are kept.

## What was evaluated

- **Pin.** HelixDB tag `v3.5.1` (`c72e64f0c7e480f3d125f071659d5e3ad7b8da15`,
  2026-10-08) and its slatedb fork at `2fb25fd`. Index storage version 5.
  Rust 1.98.0, Linux x86_64.
- **Path.** Embedded in-process, `HelixDbSource::Disk` (slatedb over
  `object_store` `LocalFileSystem`). This is **not** `helix start --disk`,
  which is the CLI plus a SeaweedFS container.
- **Adapter.** The spike (removed) was a full
  `KnowledgeStore` + `ReadView` with LadybugStore's publication model
  (ADR-0002 § 6):
  - an exclusive `store.lock`;
  - one HelixDB database per generation, built in `<g>.staging/`, then
    closed, fsynced, renamed and published by an atomic `CURRENT` rename;
  - pinned views over sealed read-only handles;
  - the same domain codec as the Ladybug adapter (`codec.rs`, included
    verbatim);
  - stable adjacency order from the stored domain ordinal.

  HelixDB node and edge IDs never leave `load()`. Native error text, which
  can name node IDs, is reduced to a fixed class.
- **Not a drop-in.** The trait was complete, but the spike lacked LadybugStore's
  rebuild/refuse policy for older and newer storage versions (any mismatch
  is `Corrupt`), the derivation re-check on reopen, required source
  retention, `activate` and `hydrate`. Lexical `limit ≥ 800` is
  `Unsupported` (HelixDB's k ceiling), where Ladybug ranks every match.
  Deadlines and cancellation (ADR-0002 § 5) were not evaluated.
- **Controls.** Both backends ran from the same captured and derived dataset
  dump, each in its own process (see Link conflict below). Every process was
  capped with `systemd-run MemoryMax=3G MemorySwapMax=0 CPUQuota=200%`, ran
  offline (`unshare -rn`), and used 2 workers or threads. The disk was LUKS
  on NVMe, not tmpfs. Each benchmark is one process. It builds the
  generation, drops the store and reopens it in the same process. Cold
  means the first call after that reopen, so the process and the OS page
  cache are already warm; it is not a fresh-process cold start. Warm is the
  median of 20 calls. Peak RSS is the process high-water mark (`VmHWM`),
  which includes loading the dump and the parity text.
- **Ladybug buffer pool.** 32 MiB for the fixture and CPython directories,
  1 GiB for repositories (as Phase 5 does).
- **HelixDB settings.** The documented embedded-disk defaults, with
  telemetry off. Only ingest chunk size, GC and edge encoding were tuned;
  memory knobs were not. Ladybug runs as shipped: explicit pool, 2 threads,
  single-threaded load.
- **Adapter design affects some reads.** `entities(n)` is one `is_in`
  request in HelixDB but n prepared lookups in the Ladybug adapter. The
  `lexical()` widening loop's cost was not isolated from HelixDB's own.

## Feasibility (pinned source, docs and runtime probes)

| Area | Finding | Basis |
| --- | --- | --- |
| Embedded Rust API | `HelixDB::open_with_config` / `open_reader_with_config` / `query` / `close` (async, tokio). **crates.io `helix-db` 3.0.0 has no `embedded` feature.** The documented `cargo add helix-db --features embedded` fails with "unrecognized feature". The engine crate `db` and six helper crates are unpublished workspace crates. The only path is a git pin of the tag, which the user accepts (pin and vendor) | measured, source |
| Telemetry | **On by default in embedded mode and undocumented** (the docs describe CLI telemetry only). The documented `Client::open` posted a `query.execute` event (`source: helix-embedded`, canonical query shape with values redacted, latency, persistent `installation_id`) and wrote `~/.helix/metrics.toml`. The SDK does not re-export `QueryTelemetry`, so turning it off in code needs the `db` crate (`QueryTelemetry::Disabled`) or `HELIX_TELEMETRY_LEVEL=off`. With `Disabled`: 0 requests, nothing in `$HOME` | measured (loopback listener) |
| Persistence and reopen | Reopen of a sealed generation through a read-only handle: 3–11 ms, against Ladybug's 10–15 ms | measured |
| Transactions | One request is one serializable transaction. A unique-key violation on row 3 of 4 committed nothing, before and after reopen. A request sees one snapshot. There is **no API to hold a snapshot across requests**; per-generation databases provide pinned views instead | measured, source |
| Crash recovery | `kill -9` of a writer streaming commits: all 300 acknowledged commits survived (WAL replay). `kill -9` of a publishing process after five wall-clock delays (5–160 ms; phases not controlled): `CURRENT` and the last good generation were always intact | measured |
| Durability | **No fsync anywhere on the data path** (`object_store` 0.12.5 `LocalFileSystem` uses temp file plus rename; none found in the slatedb fork). It survives a process crash but not, by itself, power loss. The adapter fsyncs the sealed tree, as Ladybug's does | source |
| Locking | **No lock.** A second writer on the same database opens fine and fences the first (`WriterFencedCommitOutcomeUnknown` on its next commit). OXIDE's `store.lock` is required, and was tested across processes | measured |
| Indexes | Unique and plain equality indexes are maintained synchronously. Index creation is asynchronous (poll `get_index_operation`). Text indexing is asynchronous, but `strong` search covers unpublished work. The adapter seals only a drained queue | measured, docs |
| BM25 | Tantivy BM25 with fixed k1/b, disjunctive, ties broken by native ID, k ≤ 800. The adapter widens k until boundary ties are fully fetched, then ranks in domain order. **The docs list three analyzers (`standard`, `standard_stem_en`, `whitespace_lowercase`), but v3.5.1 requests cannot choose one**: `IndexSpec::NodeText` has no analyzer field, so indexes get `Standard`, which has no stemming. The adapter variant stems in Rust with the same Snowball English stemmer | source, measured |
| Config gaps | `DbConfig::with_wal(false)` is a no-op (the field is read nowhere outside tests). WAL is garbage-collected only by an open writer after 60 s, and superseded SSTs after 24 h. So a closed, read-only generation keeps its WAL unless the adapter reopens the writer for one GC pass (the tuned variant) | source, measured |
| Vector | HNSW (cosine, euclidean, manhattan), approximate, insert layers from process randomness (not reproducible). Not benchmarked: the Ladybug adapter has no vector index, so there is no comparable workload | source |
| Build | 460 packages resolved for the HelixDB embedded path alone (485 with the Ladybug-side harness), against 119 in `oxide_kernel/Cargo.lock`. 13 come from git. Native `aws-lc-sys`, `zstd-sys`, `lz4-sys`. `object_store` with `aws` and `reqwest` are pulled in even for local disk. First release build of the dependency: 4 min 23 s at `-j 4`, 940 CPU-s, 3.3 GB peak RSS. Binary: 50 MiB stripped (kernel plus HelixDB), runtime dependencies libc and libgcc only. The Ladybug side, the whole runtime with lbug, is 36 MiB and needs libstdc++, OpenSSL 3 and zlib | measured |
| Link conflict | **HelixDB and the pinned static `liblbug.a` cannot link into one binary.** `liblbug.a` exports 192 `ZSTD_*` and about 50 `LZ4_*` symbols that collide with `zstd-sys` and `lz4-sys`. Running both adapters side by side in one runtime (dual-write or parity during a migration) would need a source-built or dynamically linked lbug, or separate processes. The spike used two processes | measured |
| License | Repository Apache-2.0, but `helix-metrics` declares **AGPL-3.0** and `db` depends on it unconditionally. Accepted by the user (OXIDE ships as managed OSS) | source |
| Churn | `crates/db` appeared after v3.0.0 (2026-08). 718 commits since, and storage version 5 landed on 2026-10-04 (newer writers upgrade, older ones refuse). No compatibility policy was found | source |
| Kernel boundary | No change. HelixDB types stay inside the adapter; the kernel port and contract suite were unchanged | measured |

## Storage correctness (23 tests, offline)

All 23 pass in both the default and the stemmed/tuned configuration, and
nothing is written to `$HOME`:

- **The unchanged shared kernel `store_cases` (10 cases)** cover:
  - unpublished and failed publications staying invisible;
  - derivation isolation;
  - scoped, ordered lookup;
  - typed, bounded, stable adjacency;
  - views pinned across publication (snapshot isolation and historical
    access);
  - immutability;
  - write-order independence;
  - deletion of superseded generations once unpinned;
  - lexical scope and bounds.
- **Adapter cases:**
  - reopen;
  - cross-process ownership;
  - startup recovery and failing closed on a bad `CURRENT`;
  - `kill -9` during publication (five wall-clock delays);
  - a rejected batch keeps nothing;
  - serialized writes.
- **HelixDB probes:** atomic requests, recovery after kill, fencing, and
  the telemetry default.

**Read parity: exact** on all 14 datasets that completed on both backends.
That is 0 of 142,469 entity lookups, 0 of 142,469 full adjacency reads, 0
of 84,120 exact-name lookups, and 0 of 31 structural retrieve, route and
trace results differing. The same holds for the stemmed/tuned variant.

**Determinism.** Twelve separate HelixDB runs on `asyncio` produced
byte-identical results: entities, adjacency, names, lexical hit order and
route traces. They used chunk sizes 25–20,000, 1–4 write streams and
different allocators, so native ID assignment and physical layout differed
between runs.

## Benchmarks

L = LadybugDB. H = HelixDB with defaults plus the adapter's ingest tuning
(128-row requests, measured fastest: 4.1 s → 1.75 s on `asyncio` edges).
H\* = the stemmed and tuned variant (adapter stemming, Efp edge encoding, one
writer GC pass before sealing). Every cell is L / H / H\*.

- **Rebuild** is write plus publish: bulk load, index build, full readback
  verification and seal. Capture and derive are excluded and identical.
- **db** is the database directory alone; retained source is extra and the
  same on both.
- **lexical** and **retrieve** are medians over the dataset's 1–8 queries,
  one call each, so they are noisy.

| dataset | entities / relations | rebuild s | db MiB | peak RSS MiB | reopen ms | route ms | retrieve ms | lexical ms | entities(1) warm ms | adjacency ms/entity | hub adjacency(64) warm ms |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `fixtures/py_repo` | 65 / 194 | 0.3 / 0.3 / 0.6 | 8.5 / 0.5 / 0.2 | 249 / 99 / 133 | 14.1 / 3.1 / 3.5 | 74.7 / 39.5 / 37.0 | 1.3 / 1.1 / 1.3 | 1.4 / 1.2 / 1.2 | 0.33 / 0.34 / 0.35 | 1.65 / 0.61 / 0.63 | 1.6 / 0.9 / 1.0 |
| CPython `email/` | 814 / 3,702 | 0.5 / 1.4 / 1.7 | 18.1 / 7.6 / 3.7 | 271 / 278 / 279 | 15.2 / 3.9 / 2.6 | 86.0 / 69.3 / 70.1 | 1.2 / 1.9 / 1.8 | 4.8 / 3.1 / 3.4 | 0.31 / 0.35 / 0.31 | 1.72 / 0.70 / 0.71 | 1.8 / 3.0 / 3.2 |
| CPython `asyncio/` | 1,283 / 5,224 | 0.5 / 2.2 / 2.5 | 19.8 / 11.5 / 5.6 | 274 / 312 / 338 | 14.6 / 5.0 / 2.9 | 127.3 / 71.7 / 62.5 | 2.5 / 2.3 / 2.4 | 6.3 / 4.2 / 15.0 | 0.52 / 0.56 / 0.39 | 1.63 / 0.81 / 0.74 | 1.7 / 3.2 / 3.5 |
| ansible | 20,404 / 100,019 | 7.4 / 78.9 / 79.8 | 120.1 / 279.9 / 216.1 | 970 / 1337 / 1237 | 14.4 / 11.2 / 10.0 | 169.4 / 96.7 / 78.8 | 3.3 / 6.6 / 5.7 | 9.0 / 14.0 / 12.3 | 0.29 / 0.53 / 0.59 | 2.11 / 1.27 / 0.98 | 9.4 / 166.3 / 143.5 |
| sympy 1a9633de | 25,527 / 309,728 | 11.8 / 322.5 / 352.0 | 264.1 / 947.2 / 677.9 | 1322 / 1929 / 1983 | 11.5 / 10.1 / 8.5 | 158.2 / 214.7 / 310.9 | 5.6 / 21.7 / 24.2 | 16.3 / 37.6 / 88.6 | 0.33 / 0.66 / 0.49 | 2.24 / 2.23 / 3.43 | 16.2 / 45.9 / 44.4 |
| sphinx 5462d419 | 8,898 / 44,960 | 2.7 / 25.8 / 25.0 | 57.0 / 92.2 / 44.5 | 304 / 512 / 523 | 11.0 / 6.9 / 5.8 | 137.9 / 97.4 / 94.3 | 15.4 / 15.0 / 12.3 | 18.9 / 21.4 / 21.1 | 0.34 / 0.61 / 0.41 | 1.93 / 0.87 / 0.83 | 3.5 / 34.1 / 34.8 |
| sphinx 9c4b27a6 | 9,208 / 46,902 | 2.3 / 27.6 / 27.3 | 58.7 / 95.9 / 46.4 | 310 / 529 / 520 | 11.3 / 6.7 / 5.2 | 148.3 / 93.8 / 99.3 | 18.1 / 31.7 / 27.8 | 22.4 / 45.6 / 53.2 | 0.32 / 0.39 / 0.56 | 1.89 / 0.79 / 0.79 | 3.6 / 36.4 / 36.3 |
| sphinx b8417a37 | 9,142 / 46,885 | 2.8 / 27.7 / 29.8 | 58.7 / 95.7 / 46.3 | 301 / 525 / 506 | 11.8 / 5.5 / 5.9 | 151.0 / 97.3 / 109.4 | 13.2 / 14.5 / 13.5 | 17.5 / 24.6 / 27.5 | 0.33 / 0.43 / 0.68 | 1.85 / 0.83 / 0.85 | 3.5 / 35.5 / 40.1 |
| sphinx f0418d70 | 8,413 / 42,620 | 2.6 / 24.4 / 22.7 | 54.3 / 87.2 / 42.0 | 294 / 515 / 499 | 10.8 / 5.2 / 4.7 | 141.6 / 104.3 / 89.1 | 13.4 / 31.3 / 49.5 | 16.1 / 38.5 / 74.9 | 0.28 / 0.38 / 0.41 | 1.86 / 0.85 / 0.77 | 3.3 / 37.5 / 32.2 |
| keras 57ad5598 | 11,626 / 80,573 | 4.1 / 59.3 / 57.2 | 90.8 / 219.1 / 132.3 | 524 / 782 / 846 | 10.9 / 5.8 / 4.5 | 152.4 / 117.7 / 116.2 | 21.3 / 26.9 / 24.5 | 26.2 / 39.2 / 36.9 | 0.30 / 0.43 / 0.42 | 1.97 / 0.91 / 0.88 | 3.7 / 29.5 / 28.1 |
| keras 31f7341a | 12,466 / 85,556 | 4.2 / 60.1 / 59.4 | 96.4 / 229.4 / 137.4 | 477 / 858 / 890 | 11.3 / 6.6 / 7.8 | 171.2 / 143.3 / 134.6 | 25.8 / 30.4 / 58.0 | 35.6 / 88.0 / 81.7 | 0.29 / 0.42 / 0.42 | 1.99 / 0.93 / 0.92 | 3.3 / 31.1 / 30.7 |
| keras 3f3ff585 | 11,084 / 73,428 | 3.5 / 51.4 / 53.1 | 83.9 / 203.9 / 125.0 | 455 / 757 / 812 | 10.4 / 6.5 / 4.4 | 154.4 / 104.6 / 108.6 | 29.0 / 23.0 / 20.1 | 24.2 / 36.6 / 32.8 | 0.29 / 0.43 / 0.48 | 1.93 / 0.86 / 0.87 | 3.0 / 23.3 / 23.6 |
| keras 64a006b0 | 12,465 / 85,555 | 4.2 / 58.1 / 59.3 | 96.4 / 229.4 / 137.4 | 541 / 887 / 910 | 12.4 / 6.0 / 7.1 | 146.5 / 110.4 / 91.8 | 25.4 / 45.1 / 41.6 | 29.6 / 69.3 / 65.5 | 0.32 / 0.45 / 0.38 | 1.99 / 0.90 / 0.88 | 3.3 / 30.6 / 30.5 |
| keras d492f44e | 11,074 / 73,345 | 3.5 / 51.0 / 53.1 | 83.9 / 203.8 / 124.8 | 445 / 785 / 830 | 10.8 / 6.2 / 4.4 | 183.9 / 85.5 / 95.6 | 22.4 / 32.9 / 31.6 | 32.6 / 51.2 / 48.3 | 0.31 / 0.35 / 0.43 | 1.94 / 0.85 / 0.86 | 2.9 / 24.9 / 23.5 |

`results/repos.md` and `results/contextbench-dev.md` add cold timings,
`entities(64)`, `region`, soak and cycle RSS, and per-kind parity.
`results/side-runs.json` holds the runs outside the table:
- 30- and 40-cycle allocator runs;
- determinism hashes;
- the telemetry capture;
- OOM journal records.

**Scale limits under the 3 GB cap:**
- **Four sympy tasks did not complete.** On three (`1756507b`, `7080a326`,
  `960705f5`; 788k–847k relations) LadybugDB was OOM-killed at publication.
  HelixDB was then not run on them, and they are excluded for both.
- **On sympy `17c22a6f`** (28k entities, 653k relations) **only HelixDB
  failed**: it was OOM-killed inside publication after 30 min of CPU. LadybugDB
  published and read the same task in 2 min 24 s, peaking at 3,011 MiB
  against the 3,072 MiB cap. It fit by about 61 MiB. Whether HelixDB's
  failure came from the engine or from the adapter's full readback was not
  isolated: it died before sealing.

**Memory over time:**
- **Soak.** RSS is flat for both over 20–200 retrieve+route rounds.
- **Republish cycles, default glibc.** HelixDB grew roughly 10 MiB per cycle
  and plateaued after about 16 cycles (49 → 107–120 MiB on `py_repo` over
  40 cycles).
- **Republish cycles, `MALLOC_ARENA_MAX=2`.** HelixDB stayed flat (43–46 MiB
  on `py_repo`; 105–112 MiB over 30 cycles on `asyncio`), and so did Ladybug
  (131–146 MiB). We infer allocator arena growth, not a leak,
  from that flat run. Exactly one generation directory remained after every
  cycle.

### Lexical quality (BM25), against gold

The scorers differ by design: tokenizer, stemmer, stopwords, statistics. So
parity here means quality on the gold tasks, not identical rankings
(macro mean, lexical-only entry retrieval):

| | tasks | recall@5 | recall@10 | recall@20 | MRR | routed gold coverage |
|---|---|---|---|---|---|---|
| L, Phase 3 fixture (reproduces frozen `phase-3/baseline-v1`) | 7 | 0.857 | 1.000 | 1.000 | 0.540 | 1.000 |
| H, Phase 3 fixture | 7 | 0.643 | 0.643 | 0.786 | 0.540 | 0.857 |
| H\*, Phase 3 fixture | 7 | 0.857 | 0.929 | 1.000 | 0.621 | 1.000 |
| L, ContextBench dev | 10 | 0.253 | 0.340 | 0.373 | 0.426 | 0.697 |
| H, ContextBench dev | 10 | 0.217 | 0.267 | 0.267 | 0.400 | 0.400 |
| H\*, ContextBench dev | 10 | 0.217 | 0.267 | 0.300 | 0.292 | 0.417 |

- **Stemming closes the fixture gap.** On ContextBench dev (dev split
  only; the sealed test split was not touched), HelixDB has better recall@1
  (0.217 against 0.117) but lower recall@5–20 and routed coverage.
- **The gap comes from a few tasks.**
  - Recall@10: two sphinx tasks (`5462d419`, `f0418d70`).
  - Routed coverage: `1a9633de` (sympy), `b8417a37` (sphinx) and
    `d492f44e` (keras).
- **Ladybug's default stopword list** is the remaining analyzer difference
  and was not emulated.
- **Ten tasks is underpowered.** This is a measured gap, not a settled one.

## Decision

**REJECT replacing LadybugDB with HelixDB v3.5.1 embedded disk.**

| | Ladybug | HelixDB embedded |
|---|---|---|
| Correctness, parity, determinism, crash safety | ✓ | ✓ (with OXIDE's lock, fsync and telemetry-off) |
| Full rebuild, which OXIDE runs per capture | 2.3–12 s on real repos | **9–27× slower** on real repos (23–352 s); 3–4× on CPython directories |
| Peak memory | baseline | **1.4–1.8× higher** on real repos (lower on the 65-entity fixture); one sympy task OOM where Ladybug fit by 61 MiB |
| Disk (db) | baseline | **1.6–3.6× larger** on real repos; tuned 0.8× (sphinx) to 2.6× (sympy); much smaller on the fixture and CPython |
| Faster reads | baseline | reopen 1.2–2.2× (up to 4.5× small); full adjacency scan up to 2.4× (equal on sympy); batched `entities(64)` 2.2–3× (partly adapter design); route up to 2.1× |
| Slower reads | baseline | high-degree hub adjacency 2.8–18×; lexical 1.1–2.5× (slower on 11 of 14 datasets); retrieve up to 3.9×; `entities(1)` 1.1–2×; route 1.4× slower on sympy (2× for H\*) |
| Lexical | frozen baseline | fixture parity with stemming; lower on ContextBench dev (underpowered) |
| Packaging | pinned static archive, libstdc++, OpenSSL | git-pinned unpublished crates (accepted), 460 packages, no C++ runtime deps, cannot co-link with lbug |

LadybugDB is already an embedded, in-process store. HelixDB's embedded path
works, and some reads are faster. But OXIDE republishes a full generation
for every capture, so the rebuild and memory regressions dominate. They are
an order of magnitude, not noise. The read results are mixed, not a clear
gain.

**Re-test HelixDB if:**
- a bulk-load path appears (load files or bulk edges, comparable to Ladybug
  `COPY`);
- requests can select an analyzer and stopwords;
- `with_wal(false)`, or seal-time WAL and SST reclamation, works;
- the `db` crate is published;
- or OXIDE moves to incremental generations, where per-write cost matters
  more than bulk load.

## Not tested

- Power loss or kernel crash, disk-full, and corrupt-file recovery.
- Storage-version upgrade (5 → next).
- macOS and Windows.
- Vector workloads (no comparable Ladybug adapter workload).
- Stopword-matched lexical comparison.
- The three sympy tasks over 3 GB, and any run above the 3 GB cap.
- Multiple concurrent reader threads on one view; deadlines and cancellation.
- Parity at other limits, directions and kind filters (the parity dump uses `Both`, all kinds, limit 4096), and lexical scores (hit IDs only).
- Two or four concurrent write streams beyond the single `asyncio`
  measurement (saved 10–17%; not adopted, since they need MVCC conflict
  retries).
