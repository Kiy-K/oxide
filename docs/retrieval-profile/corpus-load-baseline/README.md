# SQLite corpus-load baseline (roadmap #9 item 4, issue #10)

A reproducible, pinned baseline of what the whole-corpus load costs on the
current SQLite layout — the number every later storage challenger is
measured against. **No optimization is implemented or claimed here.** The
deliverable is the harness, the raw samples under [`raw/`](raw/), the
generated [`summary.md`](summary.md), and one evidence-backed next
challenger (§9).

Everything is one command sequence (§10) over existing tools:
`examples/retrieval_profile.rs` gained a `--stage <name> --json` isolation
mode, `scripts/mcp_bench.py` a `--json` raw-sample mode, and
`scripts/corpus_load_baseline.py` drives both plus the release binary and
writes the raw files. No production code, schema, type, query, default,
threshold or output contract changed: `src/` and `Cargo.*` are untouched
(`manifest.json:oxide_src_dirty` is empty), and §8 shows the 495-output
parity matrix byte-identical between a binary built from the base commit
and one built from the harness tree.

## 0. Method and what the labels mean

- **Corpora** (pinned by tag, shallow clones; counts *measured*, not the
  earlier profile's): `requests` v2.34.2 (928 symbols), `pytest` 9.1.1
  (7,281), `pylint` v4.0.8 (14,241), plus the committed `fixtures/py_repo`
  (55) and `fixtures/ts_repo` (41). Exact commits, index metadata (every
  `meta` key, the `all_symbols` query plan), payload shape, toolchain and
  machine are in [`raw/manifest.json`](raw/manifest.json): OXIDE `a21edc2`
  (v0.1.1), rustc 1.98.0, rusqlite 0.32.1 / libsqlite3-sys 0.30.1 =
  bundled SQLite **3.46.0**, i7-13620H (16 CPUs, 16 GB), Linux 7.2.5,
  Balanced mode, `search --limit 10`, `query --budget-tokens 4096`. Frozen
  ranking inputs (RRF K=60, 0.6/0.4, 200 candidates per channel,
  thresholds) are read from `src/retrieval.rs` and not tuned.
- **Embedder**: `hashed-bow-256` for every number except §4's native
  check. The driver forces it rather than assuming it: every child gets
  `OXIDE_EMBED_NATIVE=hashed` with `$OXIDE_EMBED_URL/MODEL`,
  `$OXIDE_EMBED_PROVIDER` and every `$OXIDE_*_API_KEY` removed and
  `$XDG_CONFIG_HOME` pointed at a fresh empty temporary directory (so
  `oxide setup`'s saved remote config, which `open_embedder` ranks above
  `$OXIDE_EMBED_NATIVE`, cannot apply), and the identity every index was
  *written with* (`meta.embedder`) is asserted equal to `hashed-bow-256`
  at `setup`/`manifest` time — and to a `native:` profile for the native
  check's index (`manifest.json:native_check_index`). A mismatch aborts;
  it cannot be mislabeled.
- **Isolated stages**: `retrieval_profile --stage X` runs *only* X's
  untimed prerequisites (open the read-only store; for `relation_index`,
  `callers_index`, `hydrate`, `context_cached` a snapshot load), then
  times X `PROFILE_REPEAT=3` times. One process per sample, **two batches
  of 5, interleaved per repetition**: repetition *r* runs every
  corpus × stage once for batch A, then once for batch B, then moves to
  *r*+1 (A,B,A,B,… at the granularity of one sweep, ~30 s), so drift over
  the ~5-minute run lands in both batches equally. ⇒ 10 process-cold
  samples (rep 0) and 20 warmed samples (reps 1–2) per stage per corpus.
  CLI, MCP first-call and index-cost samples interleave the same way.
- **Cold means process-cold, never disk-cold.** Every sample runs on a
  machine whose page cache already holds `index.db` (the previous process
  read it). "Cold" here = a fresh process: fresh connection, no prepared
  statements, cold allocator, cold CPU caches for this data. Nothing in
  this document is an OS-page-cache-cold or disk-cold number.
- **Instrumentation overhead**: stage numbers come from a binary with a
  counting `#[global_allocator]` (two relaxed atomic adds per
  allocation/reallocation). The uninstrumented release `oxide` binary's
  one-shot CLI and MCP numbers (§4) are the cross-check; on `pytest` the
  instrumented in-process `context` (92.0 ms cold) plus process startup
  reconciles with the uninstrumented one-shot `query` (108.8 ms) within
  the ~15–20 ms a fresh `oxide` process spends before/after the request
  (startup, `validate_index`'s three `COUNT(*)`, JSON rendering, exit).
- **Allocations vs RSS**: `allocs`/`bytes` are cumulative churn (what the
  stage allocated, not what it retained); `VmHWM` (stage rows) and
  `ru_maxrss` from `os.wait4` (CLI rows) are the process peak RSS, kept
  separate on purpose.
- **Overlaps are never summed**: `snapshot_load` ⊃ `all_symbols` +
  `all_symbol_relations`; `search_expand` ⊃ `search_noexpand` + a
  relation-less `all_symbols` + `relation_index`; `context` ⊃
  `snapshot_load` + `relation_index` + `callers_index` + retrieval +
  packing. Rows sit side by side.
- **Payload characterization** (`--stage payload`) is a raw-SQL diagnostic
  read outside every timed window.
- **Provenance**: each measurement step *replaces* its raw file (never
  appends) and stamps every row with a `run_id`; `summary.md` lists the
  run ids per file next to the manifest's, so a rerun cannot silently mix
  with the checked-in samples. The committed samples come from one
  invocation per step run back to back (`stages` 17:11, `cli` 17:11,
  `mcp` 17:12, `index-costs` 17:12 on 2026-09-22; the exact ids are the
  first line of `summary.md`).
- **Medians (min–max)** over all samples of a row; the noise band is §7.

## 1. What the corpus load has to read

| corpus | symbols | `symbols` b-tree pages (4 KB) / db pages | column bytes read by the load | refs items / sym | refs JSON B / sym (p50 / p90 / p99 / max per row) | imports items / sym | imports JSON B / sym (distinct lists) | relations rows / sym |
| --- | ---: | ---: | ---: | ---: | --- | ---: | ---: | ---: |
| requests | 928 | 139 / 1,185 | 0.45 MB | 6.2 | 88 (35 / 121 / 894 / 9,885) | 18.2 | 235 (36) | 2.46 |
| pytest | 7,281 | 1,246 / 8,925 | 4.0 MB | 15.0 | 166 (96 / 254 / 1,822 / 10,891) | 15.3 | 209 (182) | 2.72 |
| pylint | 14,241 | 1,328 / 12,236 | 4.4 MB | 7.7 | 79 (33 / 165 / 833 / 5,659) | 4.1 | 59 (525) | 1.18 |

Two things the earlier profile only inferred are now measured:

- The `symbols` table is **~5 MB** (1,246–1,328 pages) of a 36–50 MB
  database; `embeddings` and `lexical_postings` (+ its index) are the bulk
  of the file. The load reads 4.0–4.4 MB of column bytes on the large
  corpora, ~300–550 B per symbol. Volume is not what makes it slow (§2).
- `imports_json` is file-level and stored once **per symbol row**: on
  `pytest` 1.52 MB for 182 distinct lists; `references_json` is 1.21 MB
  with a heavy tail (p99 1.8 KB, max 10.9 KB — rows that need overflow
  pages).

## 2. Isolated stages, per symbol

Warm medians (reps 1–2), per-symbol values = ms / symbol count. Full rows,
cold numbers, batch A/B medians, allocation bytes and VmHWM:
[`summary.md`](summary.md).

| corpus | `all_symbols` | ↳ rows floor (SQLite, ordered) | ↳ rows floor, unordered control | ↳ Rust decode + alloc (= `all_symbols` − floor) | `all_symbol_relations` | `snapshot_load` | `relation_index` | `callers_index` | `hydrate` ×400 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| requests | 4.3 ms · 4.6 µs/sym · 31.9 allocs/sym | 2.0 ms · 2.2 µs | 1.0 ms · 1.05 µs | 2.3 ms · 2.5 µs | 1.2 ms · 3.4 allocs/sym | 5.7 ms · 6.1 µs | 0.4 ms | 0.1 ms | 1.9 ms (2.5 cold), 14.2k allocs |
| pytest | 36.3 ms · 5.0 µs · 38.9 allocs | 18.2 ms · 2.5 µs | 7.8 ms · 1.07 µs | 18.1 ms · 2.5 µs | 8.7 ms · 3.7 allocs | 47.4 ms · 6.5 µs | 2.3 ms | 0.8 ms | 2.1 ms (3.3 cold), 16.5k allocs |
| pylint | 59.3 ms · 4.2 µs · 18.8 allocs | 35.2 ms · 2.5 µs | 12.9 ms · 0.91 µs | 24.1 ms · 1.7 µs | 7.7 ms · 1.8 allocs | 72.3 ms · 5.1 µs | 4.6 ms | 0.8 ms | 1.5 ms (2.3 cold), 7.5k allocs |

Process-cold `all_symbols` is 6.5 / 53.5 / 79.4 ms (requests / pytest /
pylint); cold `snapshot_load` 8.2 / 66.7 / 91.8 ms.

What the decomposition says:

1. **`all_symbols_rows_floor`** runs the *exact* production statement
   (`… FROM symbols ORDER BY file, start_line`) on a connection opened
   untimed once per process — the same lifecycle as the store
   `all_symbols` uses — with the `prepare` inside the timed window as in
   production, and touches every column's bytes on every row (`get_ref`
   on all 13 columns) but decodes and allocates nothing (2 allocations
   total). This is SQLite's share *including* overflow-page reads, the
   control the earlier step-only probe lacked: **2.2–2.5 µs per symbol,
   47–59 % of warm `all_symbols`**. The floor and the production stage
   differ only in what they do with each row, so the difference is the
   Rust-side cost; what the subtraction cannot separate is the
   allocator's own cache effects on the SQLite half, which is why the
   split is reported to the millisecond and not finer.
2. **The ordered/unordered pair** isolates the mechanism. With every
   column still materialized, dropping the `ORDER BY` more than halves
   SQLite's share: 18.2 → 7.8 ms (pytest), 35.2 → 12.9 ms (pylint), 2.0 →
   1.0 ms (requests). The planner's choice, recorded in the manifest for
   every corpus, explains it: `SCAN symbols USING INDEX idx_symbols_file`
   + `USE TEMP B-TREE FOR LAST TERM OF ORDER BY` — it walks the `file`
   index and fetches each row by rowid (one b-tree seek per symbol into a
   ~1,300-page table, non-sequential), then sorts each file group by
   `start_line` in a temp b-tree. The unordered form is a sequential
   rowid scan. **≈1.1–1.6 µs per symbol of the load (10.4 ms on `pytest`,
   22.2 ms on `pylint`) is the index walk + rowid seeks + per-file temp
   sort, not row volume.**
3. **Rust-side decode + allocation** is the rest: 1.7–2.5 µs and 19–39
   allocations per symbol. The allocation count tracks payload items
   almost exactly: `pytest` 15.0 refs/sym + 15.3 imports/sym + ~8 fixed
   (5 owned strings, 2 `Vec`s, the push) ≈ 38.9; `pylint` 7.7 + 4.1 + ~7
   ≈ 18.8. The file-level `imports` list is parsed once per file but
   **cloned into every symbol** (`s.imports = last_imports.1.clone()`):
   on `pytest` that clone alone is ~16 of the 39 allocations per symbol.
4. `all_symbol_relations` (a `String` per `target`) is 1.8–3.7 allocs and
   0.5–1.3 µs per symbol; `relation_index` 0.3–0.4 µs; the lazy
   `callers_index` ≤ 0.8 ms; `hydrate` of 400 strided candidates is
   1.5–2.1 ms on every corpus (a rowid-probe path, independent of corpus
   size — `tests/query_plans.rs`).

## 3. Where it lands on real requests (instrumented, in-process)

| corpus | `search_noexpand` | `search_expand` | `context` (cold engine) | `context_cached` (snapshot + `RelationIndex` supplied = MCP cache hit) | `snapshot_load` share of `context` |
| --- | ---: | ---: | ---: | ---: | ---: |
| requests | 6.5 / 4.6 ms | 13.7 / 10.7 ms (6 expanded hits) | 17.4 / 12.7 ms | 5.8 / 4.1 ms | 47 % cold, 45 % warm |
| pytest | 15.6 / 13.5 ms | 79.7 / 57.9 ms (5) | 92.0 / 70.5 ms | 13.6 / 11.6 ms | 72 % / 67 % |
| pylint | 28.9 / 24.5 ms | 115.8 / 94.8 ms (2) | 127.9 / 105.7 ms | 22.5 / 20.7 ms | 72 % / 68 % |

(cold / warm medians; "expanded hits" = hits whose `reasons` carry a
`rel←seed` entry — every cold sample of every corpus expanded, so the
queries do exercise the path.) `search_expand` loads the corpus *without*
relations (`RetrievalEngine::snapshot`), `context` with them
(`snapshot_with_relations`); the 11–13 ms between the two on the large
corpora is `all_symbol_relations` + `callers_index` + packing.
`context_cached` is what the corpus load costs when it is *not* paid: the
cache hit is 6.8× (pytest) / 5.7× (pylint) faster than the cold engine.

## 4. End to end, uninstrumented release binary

One-shot CLI (10 samples, wall clock + `ru_maxrss`; the uninstrumented
`oxide` binary, not the profiler):

| corpus | `search` (expand) | `search --no-expand` | `query` (= `context`) | peak RSS search / no-expand / context |
| --- | ---: | ---: | ---: | --- |
| requests | 21.9 ms (20.3–26.0) | 13.9 (12.3–16.8) | 23.3 (22.0–25.4) | 22.0 / 22.0 / 22.0 MB |
| pytest | 93.7 (87.4–104.3) | 25.3 (22.1–32.8) | 108.8 (100.6–121.2) | 34.6 / 22.0 / 35.3 MB |
| pylint | 132.3 (128.4–147.1) | 35.8 (31.6–41.2) | 142.7 (137.3–156.7) | 35.3 / 22.0 / 36.2 MB |

`search` − `search --no-expand` = 68.5 ms (pytest) / 96.5 ms (pylint) is
the corpus load + `RelationIndex::build` paid by expansion, consistent
with the cold isolated stages (53.5 + 2.6 / 79.4 + 5.1 ms) plus the
snapshot's `by_id` map. Loading the corpus adds **+13–14 MB peak RSS**
(the retained `Vec<Symbol>` + index), against 16.5–19 MB of cumulative
allocation churn in the load itself.

MCP (`scripts/mcp_bench.py --json`; first call = a fresh server per
sample, 10 samples; steady = calls 1..16 on one server, two servers; a
JSON-RPC error or `isError` result aborts the run rather than becoming a
sample):

| corpus | `search` first / steady | `query` first / steady | server RSS |
| --- | ---: | ---: | ---: |
| requests | 18.0 / 6.3 ms | 19.4 / 5.8 ms | 23.2 MB |
| pytest | 90.6 / 17.1 ms | 89.9 / 14.9 ms | 41.4 MB |
| pylint | 125.0 / 25.5 ms | 128.0 / 23.7 ms | 42.0 MB |

The first MCP call is the one-shot cost minus process startup (it pays
`SymbolSnapshot::load` + `RelationIndex::build` into the process cache);
every later call at the same generation is the `context_cached` shape.
**The corpus load is therefore a one-shot-CLI and MCP-first-call cost;
it is absent from the steady-state MCP path by construction.**

Native-default check (`requests`, `native:arctic-embed-xs-q` asserted
from the index's `meta.embedder`, its own natively built index, model
load inside every process): `search` 163.5 ms / 73 MB, `--no-expand`
153.5 ms / 73 MB, `query` 156.6 ms / 73 MB. The 10 ms `search` −
`--no-expand` gap is the same corpus load as the hashed row (8 ms, within
noise of it); the other ~140 ms is model load + inference, reported
separately and never compared across spaces.

## 5. Storage and write-cost controls (isolated copies, hashed embedder, 10 samples each)

| corpus | cold index | no-change reindex | single-file edit (largest `.py`, +1 function) | `index.db` | WAL peak during cold index / edit | WAL after exit |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| requests | 749 ms (738–781), 30 MB RSS | 34 ms | 437 ms (2 symbols re-embedded, `tests/test_requests.py`) | 4.84 MB | 16.7 MB / 4.4 MB | 0 |
| pytest | 5,921 ms (5,821–6,016), 54 MB | 174 ms | 612 ms (`testing/python/fixtures.py`) | 36.57 MB | 22.8 MB / 11.9 MB | 0 |
| pylint | 6,663 ms (6,628–6,795), 55 MB | 247 ms | 585 ms (`pylint/checkers/variables.py`) | 50.14 MB | 22.0 MB / 10.6 MB | 0 |

WAL peaks sit under the 16 MB bulk `wal_autocheckpoint` plus one batch,
as `docs/sqlite-request-path` intended; the writer's close leaves no
WAL. A one-file edit that re-embeds 2 symbols still writes 4–12 MB of
WAL and costs 2.4–13× a no-change run — the file's whole symbol set,
postings and relations are replaced in one transaction. These are the
write-amplification controls a later Pareto comparison must not regress.

## 6. Fixtures

`py_repo` / `ts_repo` (55 / 41 symbols) exist for correctness, not
timing: every stage is 0.1–2 ms, `search`/`query` 7–8 ms one-shot, and
all 11 surfaces × 8 queries are in the parity matrix. Their cold index
(53 / 211 ms) is process startup + tree-sitter grammar load.

## 7. Noise band and repeatability

Read off the data, not asserted: `summary.md` carries batch A / B warm
medians and their delta for every stage row, plus a "Batch A vs B
disagreement" table with the largest delta per class. From that table:

| class | rows | largest \|Δ\| between batch medians | rows over 5 % |
| --- | ---: | ---: | ---: |
| `pytest`/`pylint`, warm, stage ≥ 5 ms | 18 | 11.6 % (`pytest` `all_symbols_rows_floor_unordered`, 8.3 vs 7.3 ms) | 3 |
| `pytest`/`pylint`, cold, stage ≥ 5 ms | 19 | 13.4 % (`pytest` `all_symbols_rows_floor`, 21.5 vs 24.4 ms) | 7 |
| `pytest`/`pylint`, stage < 5 ms | 11 | 13.7 % (`pytest` `callers_index` cold, 0.8 vs 0.9 ms) | 3 |
| `requests`, every stage | 24 | 11.1 % | 10 |
| one-shot CLI, `pytest`/`pylint` | 6 | 17.3 % (`pylint` `search --no-expand`, 33.5 vs 39.3 ms); min–max spread up to 43 % of the median (`pytest` `search --no-expand`) | 1 |

The headline stages are much tighter than the class maxima — `pytest`
`all_symbols` warm 36.3 / 36.2, `pylint` `context` warm 105.6 / 105.7,
`pylint` `search_expand` warm 94.6 / 94.8 — but the maxima are what a
comparison has to clear. Index/edit costs: cold-index min–max within 3 %
on every large corpus; WAL peak ±15 % run to run (checkpoint timing).

Run to run: a previous complete run of every step with the batches *not*
interleaved (kept outside the tree because its layout contradicted the
method; the harness now interleaves) gave `pytest` `all_symbols` 54.4 /
36.4 cold / warm vs 53.5 / 36.3 here, `pylint` `context` 124.6 / 107.5
vs 127.9 / 105.7, CLI `pytest` `query` 114.1 vs 108.8 — the same picture.

**Working rule** (derived from the maxima above): a change must move a
warm median of a large-corpus stage by more than **12 %**, or a cold
median / one-shot CLI median by more than **18 %**, on both large
corpora and in both batches, to count as real on this machine; anything
under that is inside what two batches of the *same* binary already
disagree by. Individual samples show a right tail up to +20–40 %
(scheduler stalls); no sample was excluded. The machine was not
otherwise idle (an IDE and a browser were open); nothing was pinned or
isolated.

## 8. Parity (baseline vs itself, baseline vs harness binary)

`scripts/corpus_load_baseline.py parity` hashes every deterministic
output: 5 corpora × 8 queries × 11 surfaces (`search` lexical / semantic /
hybrid / `--no-expand` / `--profile quality` / `--blast-radius`; `query`
fast / balanced / quality / `--git` / `--blast-radius`, all `--json`) =
**440 retrieval outputs**, plus 11 literal patterns × 5 corpora = **55
literal outputs**. The base-commit binary run twice: **0 diffs** (the
baseline is deterministic). Base-commit binary vs the harness-tree binary:
**0 diffs**. `mise run bench` (`oxide eval --config fixtures/benchmark.json`)
passes unchanged. Both binaries were built from the same `src/` (the
harness changes nothing under `src/`), in different directories — their
sha256 differ (`caa07f54…` vs `1f16ba1c…`, build-path-dependent bytes:
`env!("CARGO_MANIFEST_DIR")` in `eval.rs`),
their outputs do not. Raw digests: `raw/parity_*.json`.

## 9. Decision: one challenger, one falsifiable hypothesis

**Challenger: load `all_symbols` by sequential rowid scan and restore
`(file, start_line)` order with a *stable* sort in Rust.** Code-only, in
`SqliteStore::all_symbols`; no schema, type, migration, re-index or
schema-version change.

- **Hypothesis.** The `ORDER BY`'s index-walk + per-row rowid seek +
  per-file temp b-tree (§2 item 2) costs 10.4 ms on `pytest` and 22.2 ms
  on `pylint` of warm `all_symbols` (29 % / 37 % of the stage — 2.4–3×
  the 12 % §7 rule); a stable sort of 7–14k `(&str, u32)` keys over an index
  array (not the `Symbol` structs) is *expected* to cost under 2 ms —
  that expectation is part of the hypothesis, not a measurement, and the
  thresholds below already leave room for it. Expected effect: warm
  `all_symbols` −8 to −20 ms; one-shot `query` and `search` with
  expansion and the MCP first call move by the same absolute amount
  (−7 % to −15 %); `--no-expand`, `context_cached` and steady-state MCP
  unchanged; RSS unchanged.
- **Why it is order-preserving by construction.** A sequential rowid scan
  yields rows in rowid order; a *stable* sort by `(file, start_line)`
  therefore leaves ties in rowid order — exactly the `(file, rowid)` tie
  order `IndexBackend::all_symbols` documents and downstream corpus-order
  consumers (`neighbors()`, `related_tests`, the MCP snapshot) depend on.
  The earlier round's rejected variant sorted by `(file, start_line, id)`
  with the FNV id as tie-break — a different order it could only verify
  empirically; this one needs no verification argument, only the parity
  matrix.
- **Falsification (any one rejects it).** (a) Warm `all_symbols` medians
  improve by < 5 ms on `pytest` or < 10 ms on `pylint` in either batch
  (14 % / 17 % of the stage — above the 12 % §7 rule);
  (b) any of the 495 parity outputs differs, or an MCP cache-miss /
  cache-hit parity pass like the previous round's 192-response check
  differs, or `cargo test`'s corpus-order tests fail; (c) peak RSS on
  `query` rises beyond the ±0.3 MB hashed-path band; (d) a
  `tests/query_plans.rs` pin changes (none covers this statement today —
  the corpus load is the documented exception — but the candidate-only
  paths must keep their plans).
- **Contract note for the reviewer.** Issue #10 freezes *corpus
  ordering*; this challenger keeps the order and changes the mechanism.
  AGENTS.md currently records the SQL `ORDER BY` itself as load-bearing
  on the strength of the earlier round's measurement; §2 is direct
  evidence that measurement conflated the sorter with row
  materialization, so the AGENTS.md note would need re-baselining in the
  same change. That is a maintainer decision, not something this baseline
  makes.

**Alternatives that stay alternatives** (evidence recorded, nothing
committed): (i) sharing the file-level `imports` list instead of cloning
it per symbol — ~16 of `pytest`'s 39 allocations per symbol, but a
`Symbol` type change (`Vec<String>` → shared) with a wider blast radius
than the payoff (≤ 3–5 ms); (ii) a compact `references` encoding or a
side table so rows fit their page — a schema change with re-index; the
data says volume is not the bottleneck (§1), so this is second-order
until (§9) lands; (iii) SQL-driven structural expansion that avoids the
load entirely — architecture, blocked on `related_tests`'s
scan-everything contract and `neighbors()`' corpus-order dependence.

## 10. Reproduction

```bash
cargo build --release -j 2 --bin oxide --example retrieval_profile
W=/path/to/scratch                                  # clones, indexed copies, throwaway copies
scripts/corpus_load_baseline.py setup       --work $W    # clone pinned corpora, index, manifest
scripts/corpus_load_baseline.py index-costs --work $W    # cold / no-change / one-edit, WAL + db sizes
scripts/corpus_load_baseline.py stages      --work $W    # isolated stages, 5+5 processes × 3 reps
scripts/corpus_load_baseline.py cli         --work $W    # one-shot CLI + native check
scripts/corpus_load_baseline.py mcp         --work $W    # first-call (fresh server ×10) + steady (×16)
# parity needs a base-commit build; e.g. a worktree at the base commit built
# with the same target dir, copied out of target/release/deps/:
scripts/corpus_load_baseline.py parity      --work $W --baseline-bin $W/bin/oxide-baseline
scripts/corpus_load_baseline.py report      --work $W    # → summary.md
```

Every step writes to `docs/retrieval-profile/corpus-load-baseline/raw/`
(`--out` to redirect); `manifest` refreshes provenance without
re-indexing; `--corpora`, `--stages`, `--reps` narrow a rerun. Each
individual stage can also be run by hand:
`PROFILE_REPEAT=3 target/release/examples/retrieval_profile <repo> "<query>" --stage all_symbols --json`.
`--stage order_digest` prints the exact `all_symbols` order as data (an
FNV-1a digest of the id sequence, the number of adjacent
`(file, start_line)` ties — 568 on `pylint`, 3 on `ts_repo` — and a
sortedness check); the manifest records it per corpus so a challenger's
order can be compared to the baseline's with one line each.

## 11. Blockers and findings recorded along the way

- **`oxide status` fails on `pylint`** (`"stream did not contain valid
  UTF-8"`): `service.rs::current_file_hashes` re-hashes every scanned file
  through `read_to_string`, and `pylint` ships
  `tests/functional/i/implicit/implicit_str_concat_latin1.py`, a
  deliberately non-UTF-8 source file. `oxide index` tolerates the same
  file. The manifest records the error object for that corpus and takes
  counts/metadata from the `payload` diagnostic instead. Pre-existing,
  out of this issue's scope; worth its own issue.
- `scripts/perf.sh` still needs `/usr/bin/time -v`, absent on this
  machine; the driver's `os.wait4` path (Linux `ru_maxrss` in KB) is the
  `getrusage` wrapper the issue asked for and could replace it later.
- The bundled SQLite has `dbstat` compiled in, which is how the
  per-table page counts in §1 were read; a system SQLite may not.
- Parity's baseline binary came from a worktree at the base commit built
  into the shared `target/` to avoid a cold dependency build. Two things
  to know before repeating that: cargo does not track
  `env!("CARGO_MANIFEST_DIR")` as a fingerprint input, so a crate
  compiled in the worktree is reused as-is by the main tree (its `oxide
  eval` then looks for fixtures under the deleted worktree), and the
  uplifted `target/release/oxide` is not reliably the tree you last
  built in. `cargo clean --release -p oxide` before each of the two
  builds fixes both; the driver takes explicit binary paths.
