# PG-3: prospective gate for a lean-snapshot prototype

> Judged in [`../lean-snapshot/README.md`](../lean-snapshot/README.md).

**Disclosure.** This gate was written *after* the stage screen in
[README.md](README.md) had been run and read. It judges only runs that do
not exist yet: an end-to-end prototype in `src/`. It does not re-judge the
screen. It follows PG-1/PG-2: performance thresholds are relative to a
same-window null, correctness thresholds are absolute, and maintenance is a
written judgment. The screen served as the stop-early step. It was not
prospective, so its result counts only as a reason to write this gate, not
as a passed gate.

## What would be judged

`SymbolSnapshot` gains a lean load, without `imports`, and with
`references` only on test symbols (`relations.rs::is_test_symbol`), for
the structural graph. The prototype must also provide:

- a completeness marker on the snapshot;
- a full load whenever the in-memory lexical fallback is active;
- rehydration through `symbols_by_ids` of every snapshot symbol that
  becomes a `neighbors()` seed (search's strong seeds, `review`, `--git`,
  the evidence coordinator) or leaves the process in a serialized `Symbol`
  (`SearchHit`, `ContextItem`, `ChangedSymbol` — the flattening
  serializers; `BlastItem` copies only identity and span fields).

`RelationGraph` and `RelationIndex` stay one implementation. No schema,
index, SQL write path, ranking, or default changes. The one new read
statement (the lean projection) is pinned in `tests/query_plans.rs` with
and without `ANALYZE`.

The MCP shape is declared before measuring, as one of:

- **(a)** full snapshot kept in the process cache (lean one-shot only); or
- **(b)** lean everywhere, with per-request rehydration.

Each is judged separately.

## Null and runs

Reuse `scripts/corpus_load_baseline.py stages`/`cli`/`mcp`/`parity` on
the #10/#14 pinned corpora. One process per sample, 3 in-process reps
(rep 0 process-cold), A/B interleaved 5 + 5, baseline and prototype
binaries in the same sweep, `OXIDE_EMBED_SESSIONS=1`, every sample pinned
to one P-core (`taskset -c 6`; unpinned samples on this i7-13620H were
bimodal across P/E cores, see the screen's discarded sweeps).
`null(row) = |median(base,B) − median(base,A)| / median(base,A)`. A row
a gate depends on with null > 25 % voids the run, which is then repeated,
not interpreted. Use 10 + 10 only when a gate is within 1× null of its
threshold. Nothing is page-cache-cold; "cold" means process-cold.

## Gates (all must pass for "production candidate")

- **G1 — Correctness (absolute).**
  - (a) The #10 440-retrieval + 55-literal parity matrix, including
    fast/balanced/quality, `--git`, `--blast-radius` and `review`, is
    byte-identical between the baseline and the prototype binary on all
    five corpora.
  - (b) A `neighbors()` id oracle over every symbol of all five corpora,
    with **snapshot-sourced** seeds rather than hydrated ones, matches
    production.
  - (c) `oxide eval --config fixtures/benchmark.json` output is
    byte-identical, and `tests/benchmark_gate.rs` passes.
  - (d) The lexical fallback path (index without
    `meta.lexical_index_version`) is byte-identical on the parity queries.
  - (e) `mise run verify` and `tests/query_plans.rs` pass.
- **G2 — One-shot end to end.** On both `pytest` and `pylint`, in both
  batches, warm in-process `search_expand` and cold-engine `context` must
  each improve by > 2× null and ≥ 5 %. The uninstrumented CLI one-shot
  expanded search must improve by > 1× null.
- **G3 — Controls.** `search --no-expand` must move by ≤ 1× null. Warm
  MCP steady search/context and `context_cached` must not be slower than
  baseline by more than 1× null. Under shape (b), rehydration cost is
  inside this measurement, not excluded from it.
- **G4 — Memory.** One-shot `VmHWM` must not be above baseline. Under
  shape (b), steady-state MCP RSS is reported as well.
- **G5 — Storage.** No schema change, no new index, and `index.db`
  byte-identical after `oxide index` on a copy. Verified, not assumed.
- **G6 — Maintenance (written judgment).**
  - A lean `Symbol` must be unable to reach a seed or a serializer
    without rehydration, and a committed mechanical check must enforce
    that: a type distinction, or a test that fails if any serialized or
    seeded symbol came from a lean snapshot.
  - Report lines changed and the revert path.
  - Passes only if the write-up names the measured G2 benefit and this
    invariant's cost in the same sentence and judges the benefit larger.

## Outcomes

- **Production candidate**: G1–G6 all pass.
- **Reject**: any gate fails on a valid run.
- **Insufficient evidence**: the run is void twice, or a gate sits within
  1× null of its threshold after 10 + 10.

## Amendment PG-3.1: the `--no-expand` control (written 2026-09-25, before its measurement)

**The original clause is invalid, and that finding is kept.** G3 required
`search --no-expand` to "move by ≤ 1× null", where the null is one
same-window baseline A/B median difference. On the final sweep, the lean
binary exceeded it on 3 of 6 rows (−2.0 % to +0.9 %, mixed signs). An A/A
control ran the same baseline binary as both variants, 10 + 10
(`../lean-snapshot/raw/control-aa/`), and *identical binaries* exceeded it
on 4 of 6 rows, by up to +1.8 %. A single-median difference that happens
to be ~0 is not a noise bound, so the clause cannot tell same from
different. All other gates (G1, G2, G3's warm-MCP clause, G4–G6) are
unchanged.

**Revised criterion: one-sided equivalence (non-inferiority).**
- **Rows (6).** `search --no-expand` on `pytest` and `pylint`, measured as the
  in-process warm stage, the process-cold stage, and the uninstrumented CLI.
- **Unit.** One process: the warm stage takes the median of in-process reps
  1..N, the cold stage takes rep 0, and the CLI takes one invocation. Both
  batches are pooled.
- **Statistic.** R = median(challenger) / median(base), with a 90 %
  percentile bootstrap CI (10,000 resamples, each binary resampled
  independently, fixed seed). This is a one-sided α = 0.05 test.
  Implementation: `scripts/equivalence.py --mode noexpand` (first written as `scripts/noexpand_equivalence.py`; renamed, results reproduced exactly).
- **Margin δ = 3 %.** A row is *equivalent* if upper(R) < 1.03, a
  *regression* if R ≥ 1.03, and *inconclusive* otherwise.
  - **Floor.** Identical binaries showed median shifts up to 1.8 %. A margin
    at or below that would call a no-op a regression.
  - **Ceiling.** PG-3 counts 5 % as the smallest meaningful effect (G2). A
    regression tolerance must sit below the smallest change the gate would
    call a win.
  - **Practical size.** 3 % of a 7–18 ms non-expanded request is 0.2–0.5 ms.
- **Sample size: 30 + 30 processes per binary (n = 60).** The existing A/A
  control, the only data used for this calibration, is inconclusive at
  n = 20 on one row: pytest warm, 90 % CI [0.969, 1.050]. That shows n = 20
  cannot resolve δ. n = 60 narrows the interval by about √3. No challenger
  data informed δ or n.
- **Instrument validity (same session, same n).** An A/A pair (base vs base)
  must be *equivalent* on all 6 rows. If it is not, the run is void and is
  repeated once. A second void is *insufficient evidence*.
- **Outcome.**
  - **Pass:** all 6 lean rows equivalent, on a valid run.
  - **Reject:** any row is a regression.
  - **Insufficient evidence:** any row inconclusive after the one repeat.

### PG-3.1 outcome (final build, same window)

The run was valid: the same-session A/A (base vs base) was equivalent on all 6 rows at n = 60 (`../lean-snapshot/raw/final-aa/equivalence.json`). The final formatted build (`oxide` `7e87ba9a…`, `profile` `d6bf1155…`) is **equivalent on all 6 `--no-expand` rows** (`../lean-snapshot/raw/final-e2e/equivalence.json`).

| corpus | row | R | 90 % CI |
| --- | --- | ---: | --- |
| pylint | CLI | 1.0005 | [0.993, 1.006] |
| pylint | stage cold | 0.9940 | [0.989, 0.998] |
| pylint | stage warm | 0.9931 | [0.989, 0.999] |
| pytest | CLI | 1.0186 | [1.007, 1.027] |
| pytest | stage cold | 0.9976 | [0.985, 1.005] |
| pytest | stage warm | 0.9983 | [0.980, 1.013] |

The pytest CLI interval is inside the margin but excludes 1. A sub-2 % CLI-only difference there is not ruled out. The in-process stages show none.

**Unchanged G3 warm-MCP clause: fails on this valid run.** The clause says steady `oxide mcp` calls must not be slower than base by more than 1× the base A/B null. On pytest `query`, batch B was +4.58 % against a 2.19 % null, so the excess is 2.39 points. That is more than 1× null beyond the threshold, so PG-3's 10 + 10 extension rule does not apply. Batch A was −4.88 %, and the lean binary's own two servers differed by 12 %.

As a diagnostic only, not an override, identical binaries fail the same clause in 4 of 8 cells (`../lean-snapshot/raw/final-aa/mcp.jsonl`; e.g. pylint `search` +1.04 % against a 0.30 % null). The clause carries the same single-median-null flaw as the original `--no-expand` clause.

It was frozen for this round, so **the gate fails and the production change is not committed.** The smallest corrective action, which needs approval, is to give warm MCP the same pre-registered treatment as PG-3.1:
- one-sided equivalence on steady-call medians;
- 10 servers per batch per binary, with the harness's steady loop iterating `--reps`;
- margin and sample size calibrated on an MCP A/A only;
- a same-session A/A validity check.

## Amendment PG-3.2: warm `oxide mcp` over independent servers (written 2026-09-25, before any challenger measurement under it)

**What it replaces, which is kept.** G3's warm-MCP clause ("steady calls
not slower than base by more than 1× null") failed on the final build in
one cell (above). Identical binaries failed it in 4 of 8 cells
(`../lean-snapshot/raw/final-aa/mcp.jsonl`). It compared medians of 16
calls from *one* server per batch, so between-server variation was never
sampled. That failure and its raw data stay as recorded. The clause is
not evidence of either regression or equivalence. All other gates are
unchanged.

**Calibration, baseline only.** A pilot ran the baseline binary as both
variants: 10 independent servers per batch, 20 per binary
(`../lean-snapshot/raw/mcp-pilot-aa/`). Across-server CV was 0.9–2.0 %
on pylint and 4.1–4.8 % on pytest. At that size, identical binaries
were *inconclusive* on both pytest rows (90 % CI upper 1.047 / 1.057),
so 10-server batches cannot resolve the margin. No challenger data was
used.

**Revised criterion.**
- **Unit.** One fresh `oxide mcp` server (`corpus_load_baseline.py
  mcp-servers`). Before each batch, one discarded warmup server per
  (corpus, binary) warms the page cache. Each measured server makes the
  cache-miss call 0, then 3 discarded warmup calls, then 16 measured
  calls per tool. The unit value is the median of those 16.
- **Order.** Every (corpus, binary, server slot) of a batch runs in a
  seeded random order, 2 batches. The A/A validity pair (base vs a
  second base variant) is in the **same schedule** as the base-vs-lean
  comparison, so all three share one window.
- **Configuration.** Pinned to one P-core (`taskset -c 6`),
  `OXIDE_EMBED_SESSIONS=1`, the hashed embedder, and the `pytest`/
  `pylint` parity copies.
- **Rows (4).** {pytest, pylint} × {`search`, `query`}.
- **Sample size.** 40 servers per batch per binary, 80 per binary. From
  the pilot's 20-server interval, the projected A/A upper bound is about
  1 + 0.045·√(20/80) ≈ 1.023 on pytest.
- **Statistic.** R = median(challenger servers) / median(base servers),
  with a 90 % bootstrap CI over servers (`scripts/equivalence.py --mode
  mcp`). The estimate and CI are reported for every row, together with
  across-server CV and per-server VmHWM.
- **Margin δ = 3 %.** This is the same margin, for the same reasons, as
  PG-3.1. It is above identical-binary drift and below G2's 5 %
  meaningful-effect floor, and 3 % of a 7–14 ms warm call is 0.2–0.4 ms.
  It was not chosen from challenger data.
- **Validity.** The A/A pair must be *equivalent* (upper < 1.03) on all
  4 rows. Otherwise the run is void and repeated once, and a second void
  is insufficient evidence.
- **Outcome.**
  - **Pass:** all 4 lean rows equivalent (upper < 1.03).
  - **Confirmed regression:** any row with lower > 1.03. Then investigate
    the smallest correction: keep the complete cached snapshot for
    `oxide mcp`.
  - **Otherwise inconclusive:** equivalence is not declared.

### PG-3.2 outcome

The run is `../lean-snapshot/raw/mcp-pg32/`: seed 2, 80 servers per binary, base / `base_aa` / lean `7e87ba9a…` in one shuffled schedule, 4 min, load average about 1.5.

**Validity: pass.** The A/A pair is equivalent on all 4 rows, with 90 % CIs inside [0.998, 1.002] and across-server CV of 0.4–1.3 % (`equivalence_aa.json`).

**Lean vs frozen `main`: pass.** All 4 rows are equivalent, and all are faster (`equivalence_lean.json`):

| corpus | tool | base → lean (median of server medians) | R | 90 % CI | VmHWM |
| --- | --- | --- | ---: | --- | --- |
| pytest | search | 8.101 → 7.628 ms | 0.942 | [0.941, 0.943] | 48.5 → 36.4 MB |
| pytest | query | 7.541 → 7.165 ms | 0.950 | [0.949, 0.951] | 48.5 → 36.4 MB |
| pylint | search | 13.273 → 12.898 ms | 0.972 | [0.971, 0.973] | 48.5 → 37.4 MB |
| pylint | query | 13.102 → 12.796 ms | 0.977 | [0.976, 0.978] | 48.5 → 37.4 MB |

The earlier warm-MCP "failure", one server per batch against a single-median null, is not reproduced once variability across independent servers is sampled. That failure and its data remain recorded above. **PG-3's outcome: production candidate.** G1, G2, G4, G5 and G6 pass as written. G3 passes under PG-3.1 (`--no-expand`) and PG-3.2 (warm MCP), both pre-registered before their challenger measurements, with the original clauses' failures preserved.

### Review of the PG-3.1/PG-3.2 methodology

**Greptile.** `greptile review` reviews committed branch changes only. It was run from a disposable local clone holding the diff as two commits on a throwaway branch; nothing was committed here and nothing was pushed. Both attempts failed server-side ("review failed unexpectedly", correlation IDs `b4e4c407-efcb-48d5-a5c3-ba029aec4fc1` and `d9aa9b1e-2c29-456e-bbca-e1c2d76fb18b`), so no Greptile findings exist.

**Codex (fallback).**
- **Production change:** no BLOCKER or MAJOR.
- **Methodology:** two MAJOR evidence findings, both fixed:
  1. `scripts/equivalence.py` could print a pass for a partial run. It now enforces the pre-registered design (noexpand: 6 rows and n ≥ 60 per binary; mcp: 4 rows and n ≥ 80) and prints INCOMPLETE otherwise, with exit codes 0 / 1 / 2. Every recorded gate run still passes unchanged. The n = 20 pilot now reports INCOMPLETE, as it should: it was calibration only.
  2. `mcp-servers` recorded no executable hashes. It now writes `mcp_servers_manifest.json` with each variant's sha256, the design and the argv. The two runs taken before the fix have a manifest reconstructed post hoc and labelled as such. The command is as run, and the binaries are hashed at their unchanged paths: base `e69d27d9…`, lean `7e87ba9a…` (built 18:32:40, run 18:43). That lean binary is the same one named in `final-e2e/manifest.json`. The pilot's seed was 1.
