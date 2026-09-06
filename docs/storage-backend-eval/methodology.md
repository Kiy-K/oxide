# Storage-backend gate methodology

## What is being compared

| Candidate | Definition |
| --- | --- |
| **Enhanced SQLite** | OXIDE's current authoritative SQLite store, plus FTS5/BM25 over symbol text, plus an FTS5 `trigram` index for literal substring search, plus a vector path. |
| **SurrealDB** | `surrealdb` 3.x embedded (`kv-rocksdb`), asked to replace *everything at once*: metadata/state, the relation graph, BM25, literal search, and vector retrieval. |
| **Zvec** | Frozen. Held as the leading specialist challenger; not exercised in this round. |

The comparison is deliberately **storage-level**, not end-to-end: both candidates
are driven through one binary (`spike/`), one deterministic corpus, and the same
gate list, so a difference in a number is a difference in the store rather than in
parsing, embedding, or fusion.

## Corpus

`spike/src/corpus.rs` generates a seeded synthetic corpus shaped like OXIDE's
`Symbol`: file, qualified name, name, signature, a body carrying local
identifiers (which is why OXIDE indexes bodies at weight 1 — `AGENTS.md`),
`calls`/`bases` bare-name relations, and one L2-normalised 384-d vector per
symbol (arctic-embed-xs's dimension, OXIDE's current default). The generator is
a xorshift64* seeded per run, so both backends ingest byte-identical rows.

Three shapes are run: 400×25 = **10,000 symbols**, 1600×25 = **40,000**, and
3200×25 = **80,000**. For scale reference, `scripts/perf.sh 900` on the
synthetic repo produces 15,312 symbols across 3,604 files.

Each correctness gate probes **five** distinct targets/terms/substrings (ten for
G4a, five call targets plus five base targets), chosen from the corpus so every
probe has a non-empty ground truth. A single probe proves a single query worked;
that is not the same as the gate's name.

**Everything runs on ext4 with 170 GB free, never on tmpfs.** An earlier sweep
used `/tmp`, which is tmpfs on this machine. It flattered SurrealDB's write path
by roughly 4× — 2.0 s versus 10.6 s to ingest 10,000 symbols — while barely
moving SQLite, and one 40k attempt died of `Disk quota exceeded` rather than of
anything about the store. Those runs were discarded, not quoted.

## Gates

Ordered cheapest-first; a candidate that fails a gate does not proceed to
ContextBench.

| Gate | What it proves |
| --- | --- |
| **G0 build/dependency cost** | Whether adopting the store is affordable at all on the target machine. |
| **G1 concurrent access** | OXIDE runs one process per CLI call, and `oxide watch` holds the store open while those run. A store that takes an exclusive lock breaks that model. |
| **G2 startup / RSS / footprint** | Cost paid on *every* CLI invocation, not amortised over a long-lived server. |
| **G3a persistence / reopen** | Rows written by one process are all there for the next one. |
| **G3b atomic file replacement** | `IndexBackend::replace_file`'s all-or-nothing contract: a transaction whose second half fails must leave the first half's `DELETE` unapplied. `tests/interrupted_index_recovery.rs` exists because torn state here is silent and permanent. |
| **G3c freshness** | add / edit / rename / delete a file and every derived structure (rows, relations, full-text) follows. |
| **G4a graph parity** | `callers_of` / `implementors_of` return exactly the ground-truth set, no more and no less. |
| **G4b BM25 correctness** | Full-text recall against ground truth computed independently in Rust. |
| **G4c literal substring correctness** | Exact substring match — the query class trigram indexes exist for. |
| **G4d vector correctness** | Top-10 must equal brute-force cosine's top-10 (recall@10 == 1.0). |
| **G4e determinism** | The same query twice returns the same rows in the same order. |
| **G5 ContextBench** | Only if every gate above passes. |

## Harness bugs found and fixed

Recorded because a gate harness that is wrong in the candidate's favour — or
against it — is worse than no harness. Both were found after a first full run
and before any conclusion was drawn.

1. **G3b's "failing" transaction did not fail.** It used
   `INSERT INTO symbol (SELECT * FROM $notatable.bogus)`; SurrealQL binds an
   unknown parameter to `NONE`, so the statement succeeded as a no-op, the
   transaction committed, and the `DELETE` stuck. That produced a spurious G3b
   failure plus four cascading ones (G3a 9975/10000, G4a 166/167 and 97/98,
   G4b 399/400, G4c 399/400) — every one of them just the 25 rows that
   transaction really did delete. Replaced with `THROW`, after which SurrealDB
   rolls back correctly and G3b passes.
2. **Vector ingest was not an apples-to-apples comparison.** The SQLite control
   committed all vectors in one transaction while the SurrealDB side issued one
   autocommit `UPDATE` per vector, which inflated the reported gap from 40x to
   95x. Both now commit in transactions of `VEC_CHUNK` (1000) rows.

Three gates were also strengthened after review, because each could pass while
being wrong:

- G4b compared `truth ⊆ results`, which admits false positives. Now set equality,
  and it reports missing/spurious counts.
- G4c compared result *counts*, not identities. Now set equality.
- G3c checked row counts and one full-text token, so stale relation rows or a
  stale trigram/literal entry could survive a delete unnoticed. It now asserts
  those are gone too.

Timing symmetry was also fixed: the SurrealDB side reported a separate
first-query-in-process number for HNSW while the SQLite side folded its first
query into the headline. Both now report a cold and a warm number.

A third round found the sharpest one yet. Each of G4a/G4b/G4c probed exactly
**one** target, term and substring, so a single lucky query carried the gate.
Widening them to five probes each immediately failed G4b on *both* backends with
6,000 missing documents at 10k — and the backends were right, the harness was
wrong: ground truth was raw substring containment, and with bodies holding
`tmp_0` .. `tmp_24`, `body.contains("tmp_1")` is also true of `tmp_10` ..
`tmp_19`, so it demanded eleven times the documents any correct token index
would return. Ground truth for G4b is now whole-word (`corpus::contains_token`),
which is what both engines' tokenizers actually resolve the term to; G4c keeps
raw substring, because there substring *is* the contract. The single-probe
version of this gate had been passing since the first run.

A second review round found three more, all fixed:

- **G1 was only measured for SurrealDB.** The SQLite side of the concurrency
  claim was an ad-hoc external script, so the headline compared two different
  harnesses. Both backends now have `*-hold` / `*-try` subcommands in this
  binary, and the SQLite reader opens exactly the way `SqliteStore::open_read_only`
  does — a plain read-only connection, never `immutable=1`. The same-process
  reopen result was likewise only an observation from a crashed run; it is now
  reproducible as `surreal-reopen`.
- **G3c's rename and delete steps were asymmetric.** The SurrealDB side used
  bare `DELETE` statements while the SQLite control routed both through
  `replace_file`. Both now go through `replace_file` (which tolerates an empty
  symbol list), so the same atomic path is exercised.
- **G3c's cleanup assertions could be vacuous.** "No stale rows after delete"
  proves nothing if there were none to begin with, and the SQLite side checked
  only one hard-coded call target. Both sides now measure relation and literal
  rows *before* the delete, require that count to be nonzero, and cover every
  deleted symbol's `calls` and `bases`. The gate line reports the
  before→after transition so a vacuous pass is visible. The two sides reach
  that coverage differently and are not line-for-line symmetric: the SQLite
  control counts every row in its `relations` table keyed by the deleted
  symbol ids, which covers both kinds at once; SurrealDB has no such table, so
  it queries each `calls` and `bases` target and intersects with the deleted
  ids.

One asymmetry is inherent and left in place: SurrealDB has no literal-substring
index, so its "stale literal rows" check is a full scan of live rows. It proves
the row is gone; unlike the control's trigram shadow table it cannot prove an
index entry was cleaned up, because there is no such index to leave behind.

## Honest limits of this harness

- The corpus is synthetic. It fixes the *shape* (field lengths, relation fan-out,
  vector dimension) but its token distribution is not real code, so absolute
  BM25 latencies are indicative, not predictive.
- The SQLite control implements "Enhanced SQLite" as it would have to be built,
  not as OXIDE is built today. The difference that matters is the lexical stage:
  OXIDE rebuilds BM25 in memory on every invocation (`LexicalIndex::build`),
  and the control persists it in FTS5 instead. The control's relation table is
  *not* hypothetical — OXIDE already precomputes call/base relations at index
  time into a `symbol_relations` SQLite side table (`AGENTS.md`,
  `src/structural_relations.rs`); the control's `relations(symbol_id, kind,
  target)` shape is a simplification of it with an explicit reverse index,
  where production builds the reverse index lazily in memory
  (`RelationGraph`'s `OnceCell`s). So the control measures the *proposal*, and
  the `perf.sh` numbers in the README measure the *status quo*.
- The vector path in the control is OXIDE's existing brute-force cosine over a
  blob column. That is the honest baseline any "viable vector extension" has to
  beat, and the gate reports where it stops being good enough.
- **G3b tests transaction atomicity, not OXIDE's full `replace_file` contract.**
  The real contract (`src/index.rs`, `AGENTS.md`) also requires that a symbol
  whose `content_hash` is unchanged keeps its embedding across the rewrite, and
  that *every* symbol in the file gets a relation entry — including an empty one,
  which is what clears a stale relation after an edit removes a symbol's last
  call. Neither is modelled here. A candidate that passed G3b could still fail
  the real contract; SurrealDB did not get far enough for that to matter.
- **G4b does not prove the two engines tokenize alike.** Ground truth is
  whole-word presence in the body (`corpus::contains_token`), and the two
  engines are asked to find the same term with different tokenizers — FTS5's default `unicode61` in the
  control, a `class` tokenizer with `lowercase, ascii` filters in SurrealDB.
  Each engine is therefore held to "return every document containing this term
  and no others", which is a real per-engine precision/recall check, but the
  gate does not establish that the two tokenize identically, and it does not
  compare scores or result ordering at all. Cross-engine ranking equivalence is
  a separate question, discussed for FTS5 in `fts5-parity-notes.md`.
- **The 40k SurrealDB failure is not fully attributed.** The harness shows that
  `DEFINE INDEX … HNSW DIMENSION 384` aborts with a RocksDB transaction
  conflict after 40,000 rows have been ingested successfully — in every one of
  six attempts, across three ingest batching strategies and two filesystems.
  (A seventh attempt died of `Disk quota exceeded` on tmpfs and is discarded as
  uninformative rather than counted.) `raw/surrealdb-40k.txt` holds the last
  run; the rest differed only in the harness revision that produced them. It does not isolate
  whether the cause is an engine limit, SurrealDB's HNSW build strategy, a
  tunable (`max_write_buffer_size_to_maintain`, which the error message itself
  names), or the specific write history this harness produces. Read it as
  "this configuration does not get there", not as a general capacity claim.
- **Vector distributions are adversarial for ANN.** The corpus vectors are
  near-uniformly random in 384 dimensions, so they are close to mutually
  orthogonal — the hardest possible case for a graph-based index like HNSW.
  Real embeddings cluster. G4d's SurrealDB result should be read as "HNSW loses
  recall here", not as a general claim about HNSW.

## Retest round (after the first verdict was challenged)

The first verdict was pushed back on, correctly: G1 as originally run conflated
*multiple processes opening one embedded store* with *concurrent access inside
one process*, and the whole evaluation used `kv-rocksdb` rather than the engine
SurrealDB documents for embedded use. The retest is written up in
[`surrealdb-retest.md`](surrealdb-retest.md). Gates added or changed for it:

- **G1a in-process concurrency** — `surreal-conc` / `sqlite-conc`. Cloned
  `Surreal` handles across concurrent Tokio tasks on a multi-thread runtime,
  against OS threads with one pre-opened connection each. Reads are checked for
  exact results; writers target disjoint files so a failure is store contention
  rather than a logical conflict.
- **G1b multi-process** — the original probe, now reported as the engine's
  model rather than as a failure.
- **G1c cold-process cost** — `surreal-query` / `sqlite-query`, timed by the
  caller around the whole process, because seven of OXIDE's nine subcommands are
  one-shot processes.
- **Context-shaped composite** — `surreal-ctx` / `sqlite-ctx`. One BM25 + one
  vector top-10 + three relation lookups in an already-warm process. This is the
  number a daemon would *not* buy back, so it is the one the architecture
  decision turns on.
- **`SDB_ENGINE`** selects `rocksdb` or `surrealkv`; **`SDB_INDEX_FIRST=1`**
  defines the HNSW index before ingest instead of after a bulk load.
- **`surreal-audit`** counts rows missing the `vec` field. Added after an
  exact-cosine control failed with `Expected array<number> but found NONE`,
  which is how the SurrealKV lost-write finding surfaced.

A fourth harness bug was found in this round, on the SQLite side: `replace_file`
used a `DEFERRED` transaction, and since it reads before it writes it hit
`SQLITE_BUSY_SNAPSHOT`, which `busy_timeout` does not retry. That made SQLite
look like it lost 15 of 16 concurrent writers. `BEGIN IMMEDIATE` fixed it and
SQLite lands 64/64 — slowly (744 ms against SurrealDB's ~100 ms), but correctly.

**Not done:** the Codex review of this retest round did not complete — it was
killed by its own 50-minute timeout without producing output. The first round's
four review passes do not cover any of the retest code or claims. Anything in
`surrealdb-retest.md` should be read as unreviewed by that second pair of eyes,
in particular the SurrealKV lost-write finding, which deserves an independent
attempt to break it before it is treated as established.
