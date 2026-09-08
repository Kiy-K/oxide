# Enhanced SQLite: what shipped, what was rejected, and the freeze

Follow-on to [`README.md`](README.md), which established that SQLite stays and
that "Enhanced SQLite" was the thing to build. This document is the build and
its measurements. **Storage architecture is frozen after this** — see
[Freeze](#freeze).

Everything here uses only features already compiled into the pinned
`rusqlite 0.32 bundled` (SQLite 3.46.0). No new dependency was added.

All numbers are from one machine (16 cores, 15 GB RAM, ext4, not tmpfs) on the
`scripts/perf.sh 900` synthetic corpus — 3,604 files, 15,312 symbols — unless
stated otherwise. Cold-index timings on this machine drift ±20% run to run;
where that matters it is called out rather than smoothed over.

## Headline

| | before | after | |
| --- | --- | --- | --- |
| `search` latency (best of 3) | 0.17 s | **0.08–0.09 s** | ~−50% |
| `context` latency (best of 3) | 0.22 s | **0.12 s** | −45% |
| cold index | 5.2 s | **11.6 s** | +123% |
| no-change reindex | 107 ms | 105 ms | — |
| single-file edit | 155 ms | 160 ms | — |
| peak RSS | 50.4 MB | 50.3 MB | — |
| `index.db` | 28 MB | **42 MB** | +50% |

Ranking is unchanged, and not merely "close": `oxide eval --config
fixtures/benchmark.json` is **byte-identical** before and after, and
`tests/lexical_persistence.rs` asserts the persisted and in-memory scorers
agree on the exact `f32` bit patterns of every BM25 score, term-coverage
count, and coverage-IDF sum.

The trade is one-time indexing cost and disk for per-query latency. That is
the right direction for a tool an agent calls repeatedly and indexes once,
but it is a real cost and it is not hidden here.

## 1. Foreign keys: the audit inverted its own premise

**OXIDE relies on `ON DELETE CASCADE`, and enforcement was on by accident.**

The code said the opposite. `remove_files` carried two repo-wide anti-join
sweeps and a comment stating "foreign keys aren't enforced — `PRAGMA
foreign_keys` is never turned on in this codebase", and `replace_file`'s
comment claimed embeddings "cascade-delete with their symbols" while (on that
same stated belief) nothing deleted them.

Both cannot be true, and the second one was: reading the pragma back from a
real store returns **1**. SQLite's own default is OFF; the reason it is ON is
that `libsqlite3-sys` compiles its bundled amalgamation with
`-DSQLITE_DEFAULT_FOREIGN_KEYS=1` (0.30.1 `build.rs`, the version pinned by
`rusqlite 0.32`). Nothing in OXIDE asked for it.

That is a live hazard, not a curiosity. `replace_file` deletes a file's
symbols and depends on the embeddings and relations going with them — it even
snapshots the embeddings it wants to keep first, which only makes sense if the
cascade fires. A `rusqlite` bump, or ever linking a system SQLite, would have
silently switched cascades off and started stranding one embedding row and one
relation row per deleted symbol, in a codebase whose comments told the next
reader those rows were being cleaned up by hand.

What changed:

- `SqliteStore::open` now issues `PRAGMA foreign_keys = ON` explicitly, before
  any transaction (it is a silent no-op inside one). The dependency is
  declared rather than inherited.
- The two post-commit anti-join sweeps in `remove_files` are deleted. They had
  been no-ops in every run OXIDE has ever made, and they scanned both tables
  in full to achieve it. `drop_embeddings_without_symbols` went with them —
  it was the trait's only unused method.
- The comments asserting the cascades were decorative are corrected.
- `src/storage.rs`'s unit test pins the pragma *and* the behavior: a dependent
  row with no parent must be refused, and deleting a symbol must take its
  embedding and relation with it. `tests/incremental.rs` pins the same
  property end to end through `update_index`, so a change that keeps foreign
  keys on but stops routing deletes through `symbols` still fails.
- `PRAGMA foreign_key_check` on a freshly built 15k-symbol index: **0
  violations** in 110 ms.

Cost of enforcement, median of 3 cold indexes at `perf.sh 450`:

| | cold index |
| --- | --- |
| `foreign_keys = ON` | 2316 ms |
| `foreign_keys = OFF` | 2713 ms |

OFF measured *slower*, which enforcement cannot cause. The honest reading is
that the cost is below this harness's run-to-run drift, so there is nothing to
buy by turning it off. It stays on.

## 2. Persisted BM25 — shipped

### Why `bm25()` could not be used

The ask was "persisted FTS5 + BM25 replacing ephemeral `LexicalIndex::build`,
preserving tokenizer, field weights, ranking, freshness and determinism."
Those two halves are mutually exclusive, and the second half wins.

FTS5's built-in `bm25()` differs from OXIDE's lexical stage in four ways at
once, none of them configurable:

| | OXIDE | FTS5 `bm25()` |
| --- | --- | --- |
| `k1` | 1.5 (`retrieval.rs`) | 1.2, hardcoded |
| IDF | `ln_1p(max(0, (N−df+.5)/(df+.5)))` | `log((N−df+.5)/(df+.5))`, can go negative |
| document length | sum of **field weights** | token count |
| return value | `(score, distinct terms matched, Σidf)` | one scalar |

The fourth is fatal on its own: fusion consumes the term-coverage evidence
(`config.rs`'s `TERM_COVERAGE_ALPHA_DEFAULT`), and `bm25()` cannot produce it.
Recovering per-document term frequencies from FTS5 in SQL means the
`fts5vocab` `instance` table, which is one row per token *occurrence* — the
per-term cost scales with total occurrences rather than with matching
documents, which is the wrong shape for the one query BM25 actually needs.

So SQLite stores the postings and the scorer stays in Rust. `src/lexical.rs`
now has exactly one copy of the BM25 arithmetic, reached by both the in-memory
and the persisted source through a common `LexicalQuery`, which is why parity
is structural rather than a property a test has to keep rediscovering.

### Why persisting was worth it

`docs/storage-backend-eval/baseline.md` attributed `search`'s ~11 µs/symbol to
the per-process lexical rebuild but never split it. Split (15,312 symbols,
warm page cache, `examples/lexical_build_probe.rs`, 3 runs):

| stage | time |
| --- | --- |
| `all_symbols` (SQL load) | 35 ms |
| read symbol bodies from disk | 9 ms |
| tokenize every field | 26 ms |
| **build the posting map** | **48 ms** |
| `LexicalIndex::build` total | 84 ms |

The posting map dominates — more than reading and tokenizing combined. That
settled the design: persisting the *token stream* would have saved only the
35 ms of read+tokenize, so it had to be a persisted **inverted index**.

### Shape

```sql
CREATE TABLE lexical_postings(
    term TEXT NOT NULL,
    symbol_id INTEGER NOT NULL REFERENCES symbols(id) ON DELETE CASCADE,
    tf INTEGER NOT NULL,
    PRIMARY KEY(term, symbol_id)
) WITHOUT ROWID;
CREATE INDEX idx_lexical_postings_symbol ON lexical_postings(symbol_id);
CREATE TABLE lexical_docs(symbol_id INTEGER PRIMARY KEY REFERENCES symbols(id) ON DELETE CASCADE,
                          len INTEGER NOT NULL);
```

`WITHOUT ROWID` with the key in `(term, symbol_id)` order makes a query-term
lookup a covering range scan — confirmed by `EXPLAIN QUERY PLAN`:

```
SEARCH p USING PRIMARY KEY (term=?)
SEARCH d USING INTEGER PRIMARY KEY (rowid=?)
```

The secondary index is what the cascade uses (`SEARCH lexical_postings USING
COVERING INDEX idx_lexical_postings_symbol`); without it, deleting one symbol
would scan the whole postings table, and `replace_file` deletes every symbol
in a file.

Postings are written inside `replace_file`'s existing transaction, from the
`ParsedFile::src` the parser already has open — so the persisted path never
reads a file from disk at query time, and a file's symbols and its postings
cannot end up describing different revisions. Because that write lives in the
one helper both `update_base` and `update_base_for_files` share, the watcher's
fs-event path keeps postings current too; if it did not, a watcher edit would
leave a published index silently incomplete.

### Generation and completeness

**Table existence proves nothing, and neither does row count.** An index built
before these tables existed has zero rows; a backfill killed halfway has rows
for some files and not others — and every file it already covered has a
matching `content_hash`, so no later incremental run would ever revisit it.
Exposing that as current is the same failure mode `schema_version` had, where
a torn write could present itself as a healthy index.

So `meta.lexical_index_version` carries `LEXICAL_INDEX_VERSION`, and it is
written **only** at the end of a completed full-corpus base pass. Readers
accept the persisted tables on an exact match and on nothing else:

- no key → in-flight or pre-feature → fall back to the in-memory build
- a different value → written under different tokenizer or weight rules → fall
  back, and rebuild
- exact match → every symbol is covered

Fallback means "slow but identical", never "wrong" or "empty" — which is the
whole point of having one scorer.

The backfill deliberately does **not** force a reparse. Postings are derivable
from the symbols already stored plus the source already read that run, so the
repair rides the same unchanged-file path the structural-relations backfill
already uses (`put_file_lexical`), leaving `reparsed_files` honest. Writing
postings outside `replace_file`'s transaction is safe there and only there:
those files' symbols are not being rewritten, and an interruption leaves the
generation key unpublished, which makes the whole persisted index unreadable
rather than partially trusted.

`tests/lexical_persistence.rs` covers old-index migration, an interrupted
backfill (raw SQL, because no correct code path can produce that state), that
a partial index is never read, that the next run repairs it to exact parity,
an unrecognized generation, and single-file edits both during and after
migration.

### Write-path variants that lost

The +6.4 s cold-index cost is roughly 700,000 posting rows at ~9 µs each.
Three plausible fixes were measured and none of them worked:

| variant | cold index | verdict |
| --- | --- | --- |
| as shipped, one `INSERT` per row | 11.6–11.8 s | kept |
| `PRAGMA cache_size = -32768` (32 MB) | 11.9 s, RSS 50→82 MB | rejected: no time change, +31 MB RSS |
| 128-row multi-row `INSERT` | 13.3 s | rejected: **slower** |
| rows pre-sorted by term for b-tree locality | 11.6 s | rejected: inside noise |

The cost is b-tree and WAL work proportional to row count, not per-statement
overhead and not page-cache pressure. The simplest code is also the fastest,
so it is what ships.

## 3. FTS5 trigram — measured, works, rejected on cost

Built over raw symbol bodies in a separate table (trigrams of pre-tokenized
text are meaningless, so it cannot share the postings table):

| | |
| --- | --- |
| build | 1,424 ms for 15,312 symbols (+12% on cold index) |
| size | **34.5 MB**, against a 43 MB `index.db` |
| `should_retry` | 3 hits in 0.49 ms vs 7.8 ms scanning |
| `_retry(` | 3 hits in 0.24 ms vs 7.3 ms |
| `def compute` | 0 hits in 0.12 ms vs 7.1 ms |
| `zzz_absent` | 0 hits in 0.04 ms vs 7.1 ms |

Exact against a full scan on every probe, and 15–175× faster. It is a good
index.

**Rejected anyway: OXIDE exposes no literal-substring search.** There is no
CLI flag, no MCP tool, and no retrieval stage that would call it, so adopting
it means +34 MB and +12% indexing time for a feature with no consumer — a 79%
increase in database size to serve nobody. The measurement is recorded here so
that the day a substring surface exists, the decision is a one-line schema
addition and not another evaluation. FTS5 maintenance settings (`automerge`,
`crisismerge`) were not swept: they only shape an FTS5 table's write
amplification, and no FTS5 table ships.

## 4. Structural graph — SQL push-down rejected, index claim corrected

The plan for this work asserted `idx_symbol_relations_symbol_id` was dead
weight because no query filters on `symbol_id`. **That was wrong.** It is a
covering index for the `ON DELETE CASCADE` path:

```
DELETE FROM symbol_relations WHERE symbol_id = ?
  => SEARCH symbol_relations USING COVERING INDEX idx_symbol_relations_symbol_id
```

Every symbol delete uses it. Dropping it would turn each one into a full scan.

The real candidate was pushing `RelationGraph::callers_of` into SQL. A
covering index on `(kind, target, symbol_id)` does what it should:

```
-- without:  SCAN symbol_relations
-- with:     SEARCH symbol_relations USING COVERING INDEX (kind=? AND target=?)
```

and 20 indexed lookups cost 0.09 ms against 3.1 ms to load the whole relation
table. **Rejected on the access pattern.** `RelationGraph` pays that 3.1 ms
once per process and then answers from two `OnceCell` reverse maps in memory;
break-even against ~4.5 µs per SQL lookup is roughly 690 lookups per process.
A `context` call is bounded to far fewer by `CONTEXT_EXPANSION_PER_SEED` and
`CONTEXT_EXPANSION_TOTAL`. Paying a 44 ms index build and permanent disk to
lose on the workload OXIDE actually runs is not a trade.

**Recursive CTEs: rejected, no measurement needed.** Structural expansion is
bounded at one hop. There is no multi-hop traversal here to accelerate, and
the file-scope intersection `AGENTS.md` requires would have to be applied
inside the recursion anyway.

## 5. Native tuning — one keeper, the rest rejected

| knob | measured | verdict |
| --- | --- | --- |
| `foreign_keys = ON` | below noise (§1) | **kept**, now explicit |
| `PRAGMA optimize` | 5.6 ms | rejected — cheap, but changes no plan we care about |
| `ANALYZE` (full) | 59.8 ms | rejected — the lexical query plan is byte-identical before and after |
| `cache_size = -32768` | no time change, +31 MB RSS | rejected |
| `integrity_check` | 208.7 ms → `ok` | diagnostic only, never on a CLI path |
| `foreign_key_check` | 110.5 ms → 0 violations | diagnostic only; used as audit evidence above |
| WAL, `busy_timeout`, no `immutable=1`, request-scoped readers | unchanged | frozen by `AGENTS.md` |

`PRAGMA optimize` is the interesting rejection: it is nearly free, and the
reflex is to run it at the end of indexing. But the plans that matter are
already index-driven, and running `ANALYZE` in full does not change the
persisted-lexical plan by a single line. A pragma that costs something and
buys nothing measurable does not go in.

## Second-opinion review

Codex reviewed the uncommitted change (`codex review --uncommitted`) and
raised three findings, all accepted and fixed before commit:

- **P1 — a full backfill could publish stale postings.** If `oxide watch` (or
  a second `oxide index`) replaced a file between this run's scan and its
  backfill write, `put_file_lexical` would overwrite that writer's fresh
  postings with the older snapshot's, and the generation key would then mark
  them trusted — permanently, since the file's `content_hash` now matches.
  Fixed by re-reading the file's stored hash *inside* `put_file_lexical`'s
  transaction (the pattern `ensure_migration_marker` already uses) and
  refusing the write on a mismatch; a run that hits one cannot vouch for the
  corpus, so it leaves the generation key unpublished for the next run.

- **P1 — the query prefetch serialized what must stay concurrent.**
  Materializing postings before the thread scope made a long, many-token
  query pay lexical and semantic in series, against the documented
  `max(lexical, semantic)` invariant (RET-003). Fixed by inverting which side
  is spawned: the *semantic* side gets the thread, since `embed_query` is the
  blocking one and touches nothing unsendable, while lexical runs on the
  thread that owns the non-`Sync` store. `catch_unwind` preserves the old
  behavior that a panicking provider drops its own evidence rather than
  failing the search.

- **P2 — the probe mutated the index it measured.**
  `examples/sqlite_enhancements_probe.rs` created a candidate index and ran
  `ANALYZE` (which persists `sqlite_stat1`) against a live `.oxide/index.db`.
  Fixed by copying the database into a temp dir first; the live file's md5 is
  now identical before and after a run. The reverse-lookup figures in §4 are
  from the corrected, non-mutating run — the earlier numbers were taken on a
  database the probe had already altered.

## Gates

| gate | result |
| --- | --- |
| `cargo fmt`, `cargo clippy --all-targets` | clean |
| `cargo test` | all pass |
| `tests/benchmark_gate.rs` (hybrid ≥ vector-only) | pass |
| `oxide eval --config fixtures/benchmark.json` | **byte-identical** to pre-change |
| score parity, in-memory vs persisted | bit-exact (`f32::to_bits`) |
| determinism (`tests/determinism_stress.rs`) | pass |
| freshness: add / edit / rename / delete | pass, incl. no stranded rows |
| crash recovery (`tests/interrupted_index_recovery.rs`) | pass |
| interrupted lexical backfill | never read; repaired to exact parity |
| multiprocess reader under writer (`tests/cli_e2e.rs`) | pass |
| cold CLI latency | 0.17 s → 0.08 s |
| RSS | unchanged |
| DB size | +50% |

## Freeze

Storage architecture is closed. SQLite is the backend; enhancements are
limited to features already bundled with it; a different backend requires a
fresh evaluation round of its own. Recorded as an invariant in `AGENTS.md`.

Two questions this does **not** settle, both deliberately out of scope:

- **The vector path.** Still a brute-force scan, still comfortable to roughly
  50k symbols. That is the question Zvec was frozen against, and nothing here
  touches it.
- **Cross-file, same-run reference staleness**, which needs real dependency
  tracking. Unchanged, still documented in `AGENTS.md`.

## Reproducing

```bash
cargo build --release -j 2
export TMPDIR=/somewhere/on/disk        # not tmpfs; it flatters the write path
for n in 450 900; do scripts/perf.sh $n; done

W=$TMPDIR/probe-repo
python3 scripts/gen_bench_repo.py $W 900
(cd $W && OXIDE_EMBED_NATIVE=hashed ../../target/release/oxide index .)

cargo run --release --example lexical_build_probe -- $W
cargo run --release --example sqlite_enhancements_probe -- $W
./target/release/oxide eval --config fixtures/benchmark.json
```
