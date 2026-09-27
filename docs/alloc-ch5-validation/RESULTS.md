# ch5 fresh held-out validation — results

Disposition: **REJECT**. Gate E1 failed. Production is unchanged.

The preregistration is `PREREGISTRATION.md`, SHA-256 `08297146…`, frozen at
2026-09-27T18:31:45+07:00 before any fresh task existed. The scorer
`validate_ch5.py` was hashed at 18:36:12, and the task and gold files at
19:29:08. All of these are recorded before the first arm ran
(`results/prereg.sha256`). No rule, gate or scorer changed afterwards.

- Current `main`: `6ec73b6` (local only; `origin/main` = `1abb3d7`). The
  production baseline is `1abb3d7dd601aa63da06e5336b1ae8e93db75e1d`, and
  `src/`, `examples/`, `tests/` and the Cargo files are identical in both.
- ch5 is `docs/alloc-utilization-eval/research.patch`, applied unmodified,
  challenger 5. The rebuilt harness reproduced all 126 v3 ch0/ch5 packs from
  the previous study exactly, used as an equivalence control only.

## 1. Fresh dataset

`make_tasks.py`, unmodified, ran in `--worktree` mode on 8 repositories. Then
came the parent-commit corpora and gold filter (as in `parent.sh`), the
edit-locus gold (`heldout_edit_gold.py`) and near-duplicate removal
(`scripts/`).

- **Exclusions**: 357 used SHAs and snapshot prefixes, plus their parents. A
  candidate was excluded if the candidate or its parent matched (183 excluded).
- **Counts**: 64 tasks were generated. 3 were dropped because no gold symbol
  exists at the parent, and 0 near-duplicates were found. **61 tasks** remain,
  all in the primary set: no task relied *entirely* on the gold fallback.
  - One task, `clap-1ab0dbd2`, has 6 edit lines in `command.rs` outside every
    gold symbol. `heldout_edit_gold.py` falls back per file, while the
    task-level check only needed some line inside a gold symbol.
  - Strict sensitivity with those lines removed
    (`results/fresh_gold_strict.json`): Δ coverage is unchanged at +0.038
    [+0.004, +0.083], and relative efficiency is −0.1 % [−12.8 %, +25.3 %].
    The disposition is the same.
  - Per repo: axios 8, clap 8, flask 8, httpx 8, requests 8, zod 8,
    ripgrep 7, rayon 6. The maximum share is 13.1 %.
  - Mean 1.75 gold symbols per task.
- **Freshness**: 0 overlap with previously used commit SHAs, 0 with parent
  snapshots, 0 with previous queries. Every parent was verified as `commit~1`.
  - Two tasks (`requests-4bd79e39`, `zod-2ec972ec`) have the same repo and gold
    symbol set as earlier tasks, but different commits and queries (Codex
    finding 1).
  - Two pairs of fresh tasks share a gold set internally. Both fall below the
    preregistered 0.6 query-Jaccard threshold, so both tasks in each pair were
    kept.
- **Query procedure note**: `make_tasks.py` strips issue references from the
  body but not from the subject, so 17/61 queries contain one. The
  preregistration's wording overstated the stripping (Codex finding 11). The
  procedure was run unmodified.
- The validity rule passes: ≥ 50 tasks, ≥ 6 repos, no repo above 20 %.

## 2. Aggregates (balanced, budget 4096)

| arm | gold-line cov | relevant tok | used tok | rel/used | items | util | files |
|---|---:|---:|---:|---:|---:|---:|---:|
| A shipped | 0.310 | 19.4 | 1598 | 0.0134 | 7.18 | 0.390 | 6.07 |
| B ch5 | 0.347 | 24.2 | 1795 | 0.0133 | 8.21 | 0.438 | 6.07 |

The paired bootstrap used 10,000 resamples with seed 0:

- **Δ coverage: +0.038 [+0.004, +0.083].**
- **Relative efficiency change: −0.5 % [−13.0 %, +24.1 %].**
- Tasks improved / unchanged / regressed by coverage: **4 / 57 / 0.**
  Efficiency dropped on 11 tasks.

## 3. Gates

| gate | result | pass |
|---|---|---|
| Q1 coverage improves, CI excludes 0 | +0.038 [+0.004, +0.083] | ✅ |
| E1 CI lower bound of relative efficiency ≥ −5 % | lower bound **−13.0 %** (point −0.5 %) | ❌ |
| R1 regressions bounded and explained | 0 coverage regressions; all 11 efficiency drops explained (§5) | ✅ |
| O1 median Δ context time ≤ 5 % of search | +0.66 ms vs 37.8 ms search (+1.75 %) | ✅ |
| O2 peak RSS Δ ≤ 5 % (3 largest corpora) | +0.4 %, −1.4 %, −0.6 % | ✅ |
| P1 default-path byte parity | 61/61 `oxide query --json` byte-identical to pristine `1abb3d7`; 0/61 `index.db` hashes changed (`results/parity_p1.tsv`) | ✅ |
| P2 fixture gate | hybrid 0.909 / vector 0.818, unchanged (`results/fixture_gate_p2.txt`) | ✅ |

O2 (`results/rss_o2.tsv`), P1 and P2 were re-run after the review with
per-task outputs saved. The first run printed the same verdicts (O2 +0.4 %,
−1.4 %, −0.6 %) but saved no artifacts. The saved re-run gives O2 +0.05 %,
+0.30 %, +0.93 %.

E1 fails, so the preregistered disposition is REJECT.

- The point estimate is flat. The failure comes from the interval: only 4 of
  61 tasks gain, while 11 lose efficiency. At this effect size, the fresh set
  cannot rule out a loss larger than 5 %.
- This is the preregistered outcome, not a sign that the data is invalid. The
  validity rule passed, so INSUFFICIENT EVIDENCE does not apply.

## 4. ch5 activity and mechanism

- ch5 activated on **33/61** tasks and added **63 items**. All 63 were
  per-file-cap drops: 54 primaries and 9 dependencies.
- **49 items (on 27 tasks) crossed the 5-primary cap.**
- 7 added items carry gold (11.1 %), against 6.2 % for baseline pack items.
- Tokens added: 197 per task on average, 363 per activated task.

| added items | n | gold-bearing |
|---|---:|---:|
| primary, crossed the cap | 49 | 6 (12.2 %) |
| primary, within the cap | 5 | 1 (20.0 %) |
| dependency | 9 | 0 (0 %) |

**Same-file recovery or just more primaries?** The gain is same-file recovery
that depends on permission to exceed the primary cap:

- All 7 gold-bearing recoveries are siblings, in the same file, of a symbol
  already packed.
- 6 of the 7 crossed the primary cap.
- No gold came from dependencies.

The previous study's ch7 (caps honored) made the same point: without crossing
the cap, the gain disappears.

## 5. Tasks

Full list: `results/diagnostics.txt`, `results/added_items.tsv`.

**Improved (4):**

- `axios-2d2a21af` (0.20 → 0.80): `syncHandlerEntries` and
  `InterceptorManager.forEach` are gold siblings in `InterceptorManager.js`,
  whose two slots were already full. Fused ranks 0 and 2. Both crossed the cap.
- `httpx-a11fc384` (0.22 → 0.67): `is_safe` and `urlencode` in `_urlparse.py`.
  Both crossed the cap.
- `requests-96ba401c` (0.00 → 1.00): `get_netrc_auth` (fused rank 4, the
  edited function) was blocked by `utils.py`'s full slots. Crossed the cap.
  It came with a non-gold `requote_uri`.
- `ripgrep-241b87b3` (0.44 → 0.70): `excludes_file_default` and
  `gitconfig_xdg_contents` in `gitignore.rs`, plus two non-gold `dir.rs`
  items. Efficiency −0.7 %.

**Efficiency drops (11; 0 coverage regressions).** Every added item comes from
a file that already had two packed items. None of the drops adds a delivered
gold line. Candidate quality is the cause in every case, not token accounting
or ordering.

| task | added items (tokens) | same file as gold? | why it hurt |
|---|---|---|---|
| axios-e8147e6a | `onFinished`, `lookup` (215) | yes | non-gold siblings of the edited handler |
| clap-1ab0dbd2 | `render_usage_`, `_build_bin_names_internal` (447) | yes | non-gold `Command` methods |
| clap-a1b6be72 | `write_positionals_of`, `escape_value` (459) | yes | non-gold zsh helpers |
| flask-438edcdf | `Flask.__init__` (357, dependency) | yes | large constructor, no gold |
| httpx-99cba6ac | `_resolve_qop`, `_parse_challenge` (489) | yes | non-gold `DigestAuth` helpers |
| rayon-6b1367fe | `IdleState`, `Sleep.wake_specific_thread` (353) | **no** (gold in `latch.rs`) | wrong hot file |
| ripgrep-0d7054d8 | `Worker`, `Worker.get_work` (703) | yes | `get_work` is gold, but its gold lines fall outside the 350-token window |
| ripgrep-241b87b3 | 2 gold `gitignore.rs` + 2 non-gold `dir.rs` (581) | partly | coverage 0.44 → 0.70, but the `dir.rs` pair dilutes (−0.7 %) |
| ripgrep-6e527e92 | two `dir.rs` dependencies (452) | **no** (gold in `pathutil.rs`) | wrong hot file |
| zod-08ba069e | `defineLazyInternal` (357) | **no** (gold in `schemas.ts`) | wrong hot file |
| zod-68a609ac | `generateObjectCheck`, `literalPropertyKey` (416) | **no** (gold in `api.ts`) | wrong hot file |

Full traces: `results/diagnostics.txt`.

## 6. Codex review (read-only, after results were frozen)

Codex confirms that **REJECT follows from the preregistered gate**. It
independently reproduced Δ coverage +0.03777 [+0.00425, +0.08330] and relative
efficiency −0.4796 % [−12.962 %, +24.058 %]. ch5 was not tuned in response.

| # | sev | finding | resolution |
|---|---|---|---|
| 1 | MINOR | 2 tasks reuse earlier repo+gold-symbol sets (different commits and queries); 2 internal gold-set repeats below the Jaccard threshold | disclosed in §1 |
| 2 | NOTE | contract compliance verified on all 61 A/B traces (eligibility, file cap, budget, prefix, omitted list) | — |
| 3 | NOTE | freeze order verified: prereg 18:31:45, scorer 18:36:12, tasks/gold 19:29:08, arms 19:29:26 | — |
| 4 | MINOR | `clap-1ab0dbd2` has 6 per-file fallback gold lines | disclosed; strict re-score in §1; E1 lower bound −12.8 %, disposition unchanged |
| 5–6 | NOTE | bootstrap and E1 ratio computation verified | — |
| 7 | MINOR | regression write-up was incomplete, and wrongly said every drop was "same-file" | §5 now covers all 11 and marks the 5 wrong-hot-file cases |
| 8 | NOTE | 49/63 cap-crossing items confirmed | — |
| 9 | MAJOR | O2/P1/P2 passes had no saved artifacts | re-run with per-task artifacts saved; all pass (§3) |
| 10 | NOTE | REJECT justified, including after the finding 4 correction | — |
| 11 | MINOR | issue references remain in commit subjects (17/61) | disclosed in §1 |

## 7. Disposition

**REJECT**, per the preregistered gate. E1's efficiency CI lower bound is
−13.0 % (−12.8 % under strict gold), beyond the −5 % margin. Every other gate
passed.

**Smallest justified next step:** none for ch5. Keep the shipped allocator.
- The measured signal is real but small. Same-file siblings of packed symbols
  carry gold about twice as often as baseline items (12 % vs 6 %).
- But the signal is inseparable from exceeding the primary cap, and 5 of the
  11 efficiency losses came from the wrong hot file.
- Any future rule would be new and would need its own preregistration.
