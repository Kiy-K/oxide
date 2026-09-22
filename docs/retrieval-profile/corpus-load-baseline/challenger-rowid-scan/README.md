# Challenger: table scan + Rust sort for `all_symbols`

The one challenger the [baseline](../README.md) §9 named, evaluated in
isolation against that baseline with the issue #10 protocol. Verdict at
the end (§6): **the change is correct and faster but does not clear its
own absolute gate**. It was later re-judged under a second,
independently written gate ([PG-1](../prospective-gate.md)) and
[failed that too](../pg1-validation/README.md), on end-to-end cost and
maintenance complexity rather than on magnitude — nothing in *this*
document was altered to fit that outcome, so it stays on `challenger/rowid-scan`, AGENTS.md
is unmodified (§7), and nothing is merged, pushed or tagged.

## 1. The change

`SqliteStore::all_symbols` (`src/storage.rs`) replaces

```sql
SELECT … FROM symbols ORDER BY file, start_line
```

with `SELECT … FROM symbols` (plan: `SCAN symbols`) followed by a sort
in Rust on `(file, start_line, id as i64)`. Three details carry the
result:

- **The tie-break is sorted on explicitly, not inherited from the
  scan.** The trait documents ties on `(file, start_line)` as rowid
  order, and `id INTEGER PRIMARY KEY` *is* the rowid, so "stable sort
  over a scan that yields rowid order" would also reproduce it — but
  that scan order is a planner choice, not a guarantee: SQLite already
  answers `SELECT id FROM symbols` from `idx_symbols_name` (checked:
  `SCAN symbols USING COVERING INDEX idx_symbols_name`, *not* rowid
  order), and a future index could make this statement coverable too.
  The key is therefore `(file, start_line, id as i64)`, which is a total
  order and makes the result independent of the access path. The cast
  matters: rowid order is **signed** `i64` order — the bit-cast every
  statement crosses SQLite with — not `u64` id order. An intermediate
  version that sorted by `u64` id produced a different sequence on the
  two corpora that have ties (`pylint` 568, `ts_repo` 3) and
  `order_digest` caught it. The ids are hashed once per row into a
  `Vec<i64>`, not once per comparison (`Symbol::id()` is an FNV hash of
  file + qualified name; the per-comparison version was 30 % *slower*
  than the SQL form).
- **`imports_json`.** The SQL form parsed the file-level list once per
  run of adjacent identical rows; a plain scan interleaves files. The
  list is now memoized by an `FxHash` of the text, verified against the
  text on a hit (a collision costs one extra parse, never a wrong list),
  so each *distinct* list is parsed once (182 on `pytest`, versus one
  parse per file transition before) and every row still gets the same
  per-row `clone()` it always did. Net: 80–1,233 *fewer* allocations per
  load than the baseline.
- **Sorting without a second row buffer.** Sorting `Vec<Symbol>`
  directly needs a merge scratch buffer of n/2 × `size_of::<Symbol>()`
  (measured: +2 MB / +3.6 MB `VmHWM` on `pytest` / `pylint` in an
  intermediate version). The final version sorts a `u32` permutation and
  applies it in place by cycle following (`apply_permutation`, one
  `Vec<bool>`), so the load's peak memory stays the `Vec<Symbol>` plus
  13 B per symbol of transient key/permutation/visited arrays.

Nothing else changes: no schema, index, migration, schema-version bump,
type change, or output contract; every other statement keeps its plan
(`tests/query_plans.rs` passes; the corpus load itself was never pinned
there, and its new plan is the plain `SCAN symbols`).

## 2. Ordering and tie parity

`retrieval_profile --stage order_digest` (FNV-1a over the id sequence
`all_symbols` returns, adjacent-tie count, sortedness), baseline binary
vs challenger binary, per corpus — identical on all five, including the
two with ties:

| corpus | n | adjacent `(file, start_line)` ties | digest (both binaries) |
| --- | ---: | ---: | --- |
| requests | 928 | 0 | `6bee04c757d3bbf4` |
| pytest | 7,281 | 0 | `56646a8764864b12` |
| pylint | 14,241 | 568 | `ab31aa980a10c7b4` |
| py_repo | 55 | 0 | `22f76bcf326f665d` |
| ts_repo | 41 | 3 | `2386b10865ad638c` |

Recorded in both manifests (`raw-baseline/manifest.json`,
`raw-challenger/manifest.json`, key `order_digest`). Two extra checks on
a `VACUUM`ed copy of the `pytest` index — where rowids are rewritten —
and on the same copy after `ANALYZE` (which gives the planner real
statistics): the statement still plans as `SCAN symbols` and both
binaries still return `56646a8764864b12`.

## 3. Output parity

`scripts/corpus_load_baseline.py parity --baseline-bin <base> --oxide
<challenger>`: 495 outputs (440 retrieval: 5 corpora × 8 queries × 11
surfaces; 55 literal). Baseline vs itself **0 diffs**; baseline vs
challenger **0 diffs** (`raw-challenger/parity_summary.json`).
`oxide eval --config fixtures/benchmark.json` stdout byte-identical
(hybrid recall@5 0.909 / vector-only 0.818, unchanged). Binaries:
baseline `1f16ba1c…` (built from the committed baseline HEAD),
challenger `7c267078…`.

## 4. Measurements (one window, challenger then baseline per step)

**Machine state differs from the committed baseline run.** Every
absolute number below is ~1.7× lower than the [baseline
report](../README.md)'s for *both* binaries (baseline `pytest` warm
`all_symbols` 21.3 ms here vs 36.3 ms there; the rows-floor control
10.2 vs 18.2). That run followed two full release rebuilds and the test
suite; this one ran on a cooler machine. The decomposition ratios are
unchanged (floor ≈ 48 % of the load; ordered floor ≈ 2.6× the unordered
one), which is why the comparison is binary-vs-binary inside one window
and reported in relative terms. Warm = reps 1–2, cold = rep 0; 10
processes per row (5 + 5, interleaved per repetition).

### 4.1 Isolated stages (instrumented; medians, batch A / B in parentheses)

| corpus | stage | baseline warm | challenger warm | Δ | cold Δ | allocs Δ | alloc bytes Δ | VmHWM Δ |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| pytest | `all_symbols` | 21.3 (21.2 / 21.6) | 17.2 (17.2 / 17.0) | **−19.4 %** | −23.4 % | −80 | +0.20 MB | +36 KB |
| pytest | `snapshot_load` | 27.6 (27.6 / 27.6) | 23.7 (23.6 / 23.7) | **−14.1 %** | −19.9 % | −80 | +0.20 MB | +254 KB |
| pytest | `search_expand` | 33.8 (33.6 / 33.9) | 29.8 (29.8 / 29.7) | **−11.7 %** | −15.8 % | −80 | +0.19 MB | +112 KB |
| pytest | `context` | 40.9 (40.6 / 41.3) | 36.6 (36.4 / 36.9) | **−10.4 %** | −7.1 % | −80 | +0.20 MB | +124 KB |
| pytest | `search_noexpand` | 7.2 | 7.2 | +0.1 % | −1.1 % | 0 | 0 | +142 KB |
| pytest | `context_cached` | 7.0 | 7.6 | +8.7 % | +7.5 % | 0 | 0 | +148 KB |
| pylint | `all_symbols` | 34.0 (34.0 / 34.0) | 26.5 (26.0 / 26.8) | **−22.2 %** | −14.7 % | −1,233 | +0.41 MB | +182 KB |
| pylint | `snapshot_load` | 41.9 (42.1 / 41.5) | 32.7 (33.2 / 32.7) | **−21.8 %** | −12.0 % | −1,233 | +0.42 MB | +208 KB |
| pylint | `search_expand` | 56.3 (56.0 / 56.6) | 46.5 (46.3 / 47.7) | **−17.5 %** | −12.6 % | −1,233 | +0.41 MB | −18 KB |
| pylint | `context` | 62.5 (62.7 / 62.3) | 54.0 (54.0 / 54.1) | **−13.7 %** | −9.3 % | −1,233 | +0.42 MB | +652 KB |
| pylint | `search_noexpand` | 13.1 | 13.5 | +3.2 % | +8.2 % | 0 | 0 | +42 KB |
| pylint | `context_cached` | 12.6 | 13.3 | +5.9 % | +4.8 % | 0 | 0 | +26 KB |
| requests | `all_symbols` | 3.7 (3.0 / 4.1) | 2.3 (2.0 / 3.4) | −37.6 % | −29.6 % | +13 | +0.03 MB | −278 KB |
| requests | `context` | 6.4 | 6.1 | −4.7 % | −5.3 % | +13 | +0.02 MB | +16 KB |

The rows-floor controls (raw SQL, not through `all_symbols`) are the
same for both binaries within noise — `pytest` 10.2 → 10.5 ms ordered,
3.9 → 4.7 unordered; `pylint` 19.9 → 20.6, 6.9 → 7.2 — confirming the
two binaries saw the same machine. The load's whole gain is the
statement change plus the memoized imports.

Stages that never load the corpus should be unchanged, and mostly are
(`search_noexpand` +0.1 % / +3.2 %, `hydrate` −0.6 % / −0.9 %,
`relation_index` +0.6 % / +8.1 %), but `context_cached` reads +8.7 % /
+5.9 % here. That stage cannot touch the changed code (the snapshot and
index are supplied), its allocation counts are byte-identical, and the
previous run of the same comparison measured it at −2.1 % / −0.1 %, so
this is run-level drift in a 7–13 ms row, not a regression — see §7 of
the baseline for the ±5–12 % band these rows carry. `requests` rows
(2–6 ms) swing ±30 % in both directions for the same reason.

### 4.2 One-shot CLI, uninstrumented (`wait4` wall clock + `ru_maxrss`, 10 samples)

| corpus | surface | baseline | challenger | Δ | peak RSS |
| --- | --- | ---: | ---: | ---: | --- |
| pytest | `search` (expand) | 56.3 ms (48–64) | 50.6 (45–62) | **−10.2 %** | 34.55 → 34.54 MB |
| pytest | `search --no-expand` | 13.9 (12–18) | 15.3 (12–19) | +9.7 % | 22.25 → 22.34 MB |
| pytest | `query` | 59.2 (55–70) | 58.3 (51–68) | −1.6 % | 35.02 → 35.29 MB |
| pylint | `search` (expand) | 74.6 (68–87) | 65.5 (60–73) | **−12.2 %** | 35.34 → 35.51 MB |
| pylint | `search --no-expand` | 19.1 (18–26) | 19.7 (17–25) | +3.4 % | 22.25 → 22.34 MB |
| pylint | `query` | 82.2 (78–98) | 77.3 (70–87) | −5.9 % | 36.21 → 36.18 MB |
| requests | `search` | 18.9 | 18.8 | −0.5 % | 22.25 → 22.34 MB |
| requests | `query` | 18.5 | 18.5 | +0.2 % | 22.25 → 22.34 MB |

Peak RSS moves by at most +0.27 MB and by the same +0.09 MB on
`--no-expand`, which never calls `all_symbols` — a binary-level offset,
not the load; inside the ±0.3 MB hashed-path band. `search --no-expand`
reads +3…+10 % on both large corpora in this run (it was +8.5 % / +3.3 %
in the previous one); it shares no code with the change, its in-process
stage is flat, and its min–max spans 40 % of the median, so it is noise
in a 14–20 ms process-dominated row. One-shot `query` improves less than
the in-process `context` stage does (−1.6 % vs −10.4 % on `pytest`)
because ~20 ms of a one-shot process is startup, validation and
rendering; the MCP first call below isolates the request itself.

### 4.3 MCP (`scripts/mcp_bench.py --json`)

| corpus | tool | first call (fresh server ×10) | steady (calls 1..16, two servers) | server RSS |
| --- | --- | ---: | ---: | ---: |
| pytest | `search` | 54.1 → 52.5 ms (−2.9 %) | 8.7 → 9.5 | 41.53 → 41.59 MB |
| pytest | `query` | 54.4 → 53.2 (−2.3 %) | 7.9 → 9.1 | |
| pylint | `search` | 75.5 → 69.8 (−7.6 %) | 14.0 → 15.0 | 41.96 → 41.99 MB |
| pylint | `query` | 77.3 → 67.5 (**−12.7 %**) | 13.8 → 14.8 | |
| requests | `search` | 11.6 → 14.9 | 6.0 → 4.6 | 23.33 → 23.28 MB |
| requests | `query` | 12.5 → 12.7 | 2.9 → 4.1 | |

First calls (the cache-miss corpus load) drop 2–13 % on the large
corpora — smaller and noisier than the isolated stage: the previous run
of the same comparison put them at −12.9 % / −13.6 % (`pytest`) and
−18.3 % / −11.2 % (`pylint`), so the direction is consistent across runs
and the magnitude is not. Steady-state calls, which never load the
corpus, read +7…+15 % here and −1…+2 % in the previous run — the same
sub-10 ms drift as `context_cached`. Server RSS ±0.1 MB.

### 4.4 Index and edit costs (isolated copies, 10 samples)

`update_index` calls `all_symbols` too. Cold index: `pytest` 3,444 →
3,339 ms (−3.0 %), `pylint` 3,998 → 3,942 (−1.4 %), `requests` 445 →
427 (−4.1 %). No-change reindex: 106 → 97 ms (−7.9 %), 144 → 128
(−11.4 %), 23 → 26 (+13 %, a 3 ms row). Single-file edit: 356 → 340,
343 → 326, 261 → 248 (−4…−5 %). `index.db` within one page-allocation
either way (`pytest` identical; `pylint` 50.21 → 50.12 MB; `requests`
4.86 → 4.85); WAL peaks 22.7 → 22.7 / 21.3 → 22.6 / 17.3 → 16.9 MB,
inside the ±15 % checkpoint-timing band. Peak RSS ±0.5 MB. No write
amplification change; the indexing side is neutral-to-slightly-better.

## 5. The §9 falsification criteria, one by one

| criterion | result |
| --- | --- |
| (a) warm `all_symbols` improves < 5 ms on `pytest` or < 10 ms on `pylint` in either batch → reject | `pytest` −3.9 / −4.6 ms (batch A / B), `pylint` −8.1 / −7.2 ms. **Both miss the absolute thresholds** (`pytest` by 1.1 / 0.4 ms, `pylint` by 1.9 / 2.8 ms) — but those were written against a 36.3 / 59.3 ms baseline on the throttled machine, and this run's baseline is 21.3 / 34.0 ms. As a fraction of the stage the criterion asked for −14 % / −17 %; the measured −19.4 % / −22.2 % (per batch: −18.6 / −21.2 % and −23.7 / −21.3 %) clears that on both corpora in both batches, and clears the 12 % §7 noise rule. Read as: **the relative claim holds on this machine state, the absolute milliseconds do not transfer between thermal states.** A rerun on the baseline's own machine state is the honest way to close this line. |
| (b) any of the 495 parity outputs differs, `oxide eval` differs, corpus-order tests fail → reject | 0 / 0 diffs; eval byte-identical; order digests identical on all five corpora (571 tied pairs) and on a `VACUUM`ed + `ANALYZE`d copy; `cargo test` (all 40 suites) green. |
| (c) peak RSS on `query` rises beyond ±0.3 MB → reject | +0.27 MB (`pytest`), −0.03 MB (`pylint`); the +0.09 MB floor appears on `--no-expand` too, so it is not the load. Allocation count −80 / −1,233; allocated bytes +0.20 / +0.42 MB (the `Vec<i64>` keys, `u32` permutation and `Vec<bool>` — 13 B per symbol, transient). |
| (d) a `tests/query_plans.rs` pin changes → reject | Passes. The corpus-load statement plans as `SCAN symbols` (was `SCAN symbols USING INDEX idx_symbols_file` + `USE TEMP B-TREE`); every candidate-only statement keeps its plan, with and without `ANALYZE`. |

## 5b. Final gate run (fresh, interleaved, unmodified gates)

A third complete comparison, run specifically to settle §5(a) rather than
to re-measure: both binaries rebuilt from their exact commits
(`cce3907` baseline `2c98ec72…`, `65dbbc5` challenger `7c267078…`),
`cargo clean --release -p oxide` between the two builds, then the same
four protocol steps interleaved challenger-first in one window, on the
same indexed corpora with the same warm page cache. Raw samples:
[`raw-final-baseline/`](raw-final-baseline/),
[`raw-final-challenger/`](raw-final-challenger/) (run id
`20260922T190907`).

**The original §9 thresholds are applied exactly as written; none was
adjusted.**

| gate | requirement | measured | verdict |
| --- | --- | --- | --- |
| (a) | warm `all_symbols` improves ≥ 5 ms on `pytest` **and** ≥ 10 ms on `pylint`, in **both** batches | `pytest` −3.70 / −4.32 ms (short by 1.30 / 0.68); `pylint` −8.80 / −8.68 ms (short by 1.20 / 1.32) | **FAIL** — all four batches short |
| (b) | 495 parity outputs, fixture eval, order digests, corpus-order tests all identical | 0 / 0 parity diffs; eval byte-identical (hybrid recall@5 0.909); digests identical on all five corpora (571 tied pairs) **and** on the `VACUUM`ed + `ANALYZE`d copy; `cargo test` 40/40 suites green | **PASS** |
| (c) | peak RSS on `query` within ±0.3 MB | `pytest` +0.12 MB, `pylint` +0.18 MB, `requests` −0.06 MB | **PASS** |
| (d) | no `tests/query_plans.rs` pin changes | passes with and without `ANALYZE`; the corpus-load statement (never pinned) moves `SCAN symbols USING INDEX idx_symbols_file` + `USE TEMP B-TREE` → `SCAN symbols` | **PASS** |

Relative gate (§7's noise rule: a warm large-corpus median must move by
more than 12 %): `pytest` −17.8 % / −20.4 %, `pylint` −25.7 % / −25.4 %
— **PASS**, in every batch.

### What did not reproduce, and why that is the honest answer

The absolute thresholds were derived on a machine state this run could
not recreate. The committed baseline's session had run two from-scratch
release rebuilds plus the full test suite immediately before measuring;
this run's preamble (the same two rebuilds and the same `mise run test`)
completed in 33 s because every dependency and test binary was already
compiled. Both binaries therefore measured ~1.6× faster than the
baseline report throughout — the *baseline* binary's own warm
`all_symbols` is 21.0 ms (`pytest`) / 34.2 ms (`pylint`) here versus
36.3 / 59.3 ms in the committed report, and the raw-SQL rows-floor
control (which touches no changed code) moved the same way, 10.1 vs
18.2 ms. A −19 % improvement of a 21 ms stage is −4.0 ms; the gate asks
for −5 ms. **The shortfall is the denominator, not the change.**

Three independent runs of the same comparison agree on the relative
effect and disagree on the millisecond:

| run | `pytest` warm `all_symbols` | `pylint` warm `all_symbols` |
| --- | --- | --- |
| run 1 (earlier patch shape) | 21.5 → 17.0 ms (−20.6 %, −4.5) | 35.1 → 24.6 (−29.8 %, −10.5) |
| run 2 (final patch) | 21.3 → 17.2 (−19.4 %, −4.1) | 34.0 → 26.5 (−22.2 %, −7.5) |
| run 3 (this one) | 21.0 → 17.0 (−19.2 %, −4.0) | 34.2 → 25.5 (−25.4 %, −8.7) |

Only run 1's `pylint` cleared −10 ms. **Under the gate as written, the
challenger has not passed**, and this document does not claim it has.
Two ways to close it, both the maintainer's call, neither taken here:
re-run the whole protocol on a machine deliberately loaded to the
baseline's state (thermal throttling is the variable, so this is
reproducible only approximately), or re-state gate (a) in the relative
terms §7's noise rule already uses — which would be changing the gate
after seeing the result, and is exactly what was avoided here.

### Everything else in run 3

Warm medians, baseline → challenger: `snapshot_load` 28.1 → 23.1 ms
(`pytest`, −17.6 %) and 42.2 → 33.1 (`pylint`, −21.6 %); `search_expand`
34.1 → 29.9 (−12.5 %) and 56.4 → 46.0 (−18.4 %); in-process `context`
41.4 → 36.6 (−11.7 %) and 64.0 → 53.2 (−16.9 %). One-shot CLI: `search`
56.1 → 54.9 ms (`pytest`, −2.1 %) and 72.8 → 64.2 (`pylint`, −11.8 %);
`query` 62.6 → 62.2 (−0.7 %) and 89.1 → 75.3 (−15.4 %). MCP first call
−2…−15 %. Indexing: cold −3 %, no-change reindex −11…−13 %, single-file
edit −0…−8 %; `index.db` and WAL peaks unchanged. Allocation counts
−80 (`pytest`) / −1,233 (`pylint`). Stages that never load the corpus
drift ±6 % (`search_noexpand` +2…+6 %, `context_cached` +4 %) with
byte-identical allocation counts — the same sub-10 ms noise §4.1
describes, and it reverses sign between runs.

## 6. Pareto verdict

**Not accepted under the gates as written.** Gate (a) is unmet in all
four batches of the deciding run (§5b), by 0.7–1.3 ms. Every other gate
passes, and every other axis is neutral or better:

| axis | direction |
| --- | --- |
| correctness / provenance | order identical by construction *and* empirically (five corpora, 571 tied pairs, plus `VACUUM` + `ANALYZE`); 495 outputs and the fixture eval byte-identical; no schema/type/contract change |
| agent utility per context token | unchanged (identical outputs) |
| latency (run 3, the deciding run) | corpus load −19.2 % / −25.4 % warm (`pytest` / `pylint`), −3.6 % / −22.2 % cold; `snapshot_load` −17.6 % / −21.6 %; in-process `context` −11.7 % / −16.9 %; one-shot `search` −2.1 % / −11.8 %, `query` −0.7 % / −15.4 %; MCP first call −2…−15 %; indexing −3…−13 %. **In absolute ms the corpus-load gain is 4.0 / 8.7 ms, below gate (a)'s 5 / 10 ms.** |
| memory | peak RSS on `query` +0.12 / +0.18 MB, inside gate (c)'s ±0.3 MB; 80–1,233 fewer allocations; +13 B per symbol transient |
| storage / write amplification | unchanged (db bytes, WAL peaks within band) |
| maintenance | ~45 lines replacing a one-line `ORDER BY`, plus one helper (`apply_permutation`); the order now rests on an explicit sort key a comment justifies instead of on SQLite's sorter; `order_digest` is the regression check |

The benefit is repeatable in *relative* terms — both batches, both large
corpora, cold and warm, three independent runs of the whole comparison
(§5b), and larger than §7's noise rule on every corpus-loading surface.
The regressions that appear sit on surfaces that share no code with the
change (`context_cached`, `search --no-expand`, steady-state MCP), carry
byte-identical allocation counts, and reverse sign between runs.

**What blocks acceptance is one number, stated plainly**: gate (a) asks
for −5 ms (`pytest`) and −10 ms (`pylint`) of warm `all_symbols` in both
batches, and the deciding run delivers −3.7 / −4.3 and −8.8 / −8.7 ms.
The gate was written against a machine state ~1.6× slower than any state
reachable in this session (§5b), in which the same −19 % / −25 % would
have been −6.9 / −15.1 ms and would have passed — but that is an
inference about a state that was not measured, not a measurement, and
the gate is not being re-stated after the fact to accommodate it.

So the change is **correct, neutral-or-better on every other axis, and
short of its own acceptance bar.** The maintainer's options are to
re-run the protocol with the machine deliberately loaded to the
baseline's state, to re-state gate (a) relatively (and say so), or to
leave the challenger on its branch as a recorded, reproducible negative
on the absolute gate. Nothing further is decided here: AGENTS.md is
unmodified, and §7's replacement wording stays a proposal.

## 7. Not done: the AGENTS.md invariant

AGENTS.md states, under load-bearing invariants: *"`all_symbols` keeps
its SQL `ORDER BY file, start_line`: measured, the ordered step is no
slower than a rowid scan plus a Rust sort (the sorter's cost is the
overflow-page reads a plain scan only defers), and its tie order is
`(file, rowid)`, which downstream corpus-order consumers depend on."*

The baseline (§2 there) and this evaluation show the first clause's
measurement conflated two things: with every column materialized on
both sides, the ordered statement is 2.3× the unordered one, and the
cost is the `idx_symbols_file` walk with one rowid seek per row plus a
temp b-tree per file — not overflow-page reads. The second clause (tie
order `(file, rowid)`) is exactly what the challenger preserves, by
construction.

**It has not been edited**, because gate (a) is unmet (§5b). If the
maintainer accepts on the relative evidence, the invariant would be
re-baselined to something like:
*"`all_symbols` returns `(file, start_line)` order with ties in rowid
order; it now does so with a plain `SCAN symbols` and a Rust sort on
the total key `(file, start_line, id as i64)` — the cast is the
tie-break, since rowid order is signed order
(docs/retrieval-profile/corpus-load-baseline/challenger-rowid-scan),
because the SQL `ORDER BY` planned as an index walk + per-row rowid seek
+ per-file temp b-tree, 2.3× the sequential scan's cost with every
column materialized. Rowid order is signed `i64` order, not `u64` id
order, so the tie-break is spelled `id as i64`. The sort is
`sort_unstable_by` over a `u32` permutation applied in place; it does
not rely on the scan yielding rowid order, which is a planner choice
(`SELECT id FROM symbols` already comes back from a covering index).
`retrieval_profile --stage order_digest` pins the sequence."* That edit, a merge, a push and any tag are the
maintainer's call; none of them has been made.

## 8. Reproduction

```bash
# baseline binaries from HEAD, challenger from the patched tree (cargo clean -p oxide between)
W=/path/to/scratch   # the baseline's --work dir (idx/ already built)
for step in stages cli mcp index-costs; do
  for v in chal base; do
    scripts/corpus_load_baseline.py $step --work $W --out $W/raw-$v \
      --oxide $W/$v/oxide --profile $W/$v/retrieval_profile --skip-native
  done
done
scripts/corpus_load_baseline.py parity --work $W --out $W/raw-chal --baseline-bin $W/base/oxide --oxide $W/chal/oxide
for v in chal base; do $W/$v/retrieval_profile $W/idx/pylint x --stage order_digest --json; done
```

**If you are reading this on a branch based on `origin/main`**, the
`--stage order_digest` mode, the `--json` stage output and the two raw
directories below live in the baseline commits (`b26d2b1`, `cce3907`)
that are themselves unpushed; a Greptile review of this change against
`origin/main` therefore sees neither, and reported exactly that. On
`main` both are present.

Raw samples: [`raw-baseline/`](raw-baseline/) and
[`raw-challenger/`](raw-challenger/) (same layout as the baseline's
`raw/`; run id `20260922T1753…1758`). The two intermediate versions
(a stable sort on `Vec<(Symbol, String)>` with a post-sort imports
pass: +2–3.6 MB VmHWM; an unstable sort keyed on the `u64` id: wrong
tie order and 30 % slower) are recorded in §1 and not kept as patches.
