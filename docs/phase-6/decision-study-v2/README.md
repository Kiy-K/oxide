# Decision Study v2: heuristic admission, JEV order (2026-10-24)

Outcome: **INCONCLUSIVE** as preregistered. The dev gate passed on fresh calls, and on
held-out (opened once) every quality criterion passed. The only failure is operational:
JEV answered `529 system_overloaded` to 10 of 29 primary requests, against a limit of 10%
(H7). No judge is integrated into production.

| Question | Answer |
| --- | --- |
| Does JEV ordering of production's admitted set raise packed recall on held-out? | **Yes, but not confirmed under the frozen gate.** `line_recall@4096` 0.235 → **0.360** (Δ +0.125, paired bootstrap 95% CI +0.054 … +0.206), 12 tasks up, **0 down at any budget**, even with 10 requests falling back to the heuristic |
| Beyond a random order of the same set? | Yes: random order is 0.137, below production |
| Rank copying? | No: without rank and score, 0.387 |
| Reproducible? | Repeat 0.423, reversed 0.449. All controls clear +0.020 (they had fewer 529s) |
| Why did v1's stability check fail? | Judgment noise is small. Greedy packing turns rare reorders into whole-task recall steps of 0.2–1.0, and v1's ±0.010 macro tolerance sat below one-task resolution (§ Stability) |
| Cost and latency | ~15k billed input tokens a request (~$0.0006). Median 1.0 s, p95 1.6 s (within the 2.0 s `DEADLINE`), max 2.4 s. About 1,200 tokens a request per +1 recall point. The whole study cost $0.121 |

Numbers are measured unless marked *inferred*. Nothing in `src/` changes.

## Protocol

[preregistration.md](preregistration.md) (sha256 `a8cce87f…`) and the scripts were
committed in `96356b7` before any v2 call. The hybrid: the heuristic admits, admitted
candidates are valued `0.4 + 0.59 × p`, and if any admitted candidate lacks a judgment the
whole request falls back to the heuristic. The model is JEV `jev-1.14.0` with the
`ds1-compact-v1` renderer, digest order, 60 s kill, and no retries. Nothing changed between
the freeze, the dev gate and the held-out run. The user authorized sending the 9 dev
and 29 held-out corpora (public repositories at pinned commits) under a $0.30 cap. In
total: 176 requests, $0.121 billed.

| Script | Does |
| --- | --- |
| [scripts/harness.py](scripts/harness.py) | v2 arms over v1's harness (`decision-study-v1/scripts/study.py`): heuristic (asserted equal to production), oracles, random order, the `hybrid` mapping; `--stability` adds the investigation arms. Refuses held-out unless the dev gate output carries `DEV GATE: PASS` and the preregistration digest |
| [scripts/judge.py](scripts/judge.py) | JEV calls through v1's `adapter.py`, with per-corpus authorization and a spend ledger capped at $0.30 |
| [scripts/verdict.py](scripts/verdict.py) | Tables, per-task deltas, bootstrap, cost/latency, and the preregistered gates |
| [scripts/stability.py](scripts/stability.py) | The pre-freeze stability investigation |

Validity: dev requests are byte-identical to v1's. Heuristic replay equals the scorer on
every task and budget (asserted), and dev oracles reproduce graph-v3 (0.682 / 0.792).

## Dev gate, fresh calls ([results/gate-dev.txt](results/gate-dev.txt))

| Arm | LR@256 | LR@1024 | LR@4096 | used@4096 / budget | admitted-set AUROC |
| --- | ---: | ---: | ---: | ---: | ---: |
| heuristic (production) | 0.180 | 0.548 | 0.659 | 0.661 | 0.683 |
| random order, mean of 5 | 0.124 | 0.416 | 0.583 | 0.679 | 0.534 |
| **compact~order** | 0.262 | 0.609 | **0.783** | 0.676 | 0.784 |
| no-rank | 0.262 | 0.609 | 0.783 | 0.689 | 0.776 |
| repeat | 0.262 | 0.609 | 0.783 | 0.686 | 0.789 |
| reversed | 0.262 | 0.625 | 0.810 | 0.682 | 0.812 |
| `oracle_order` (diagnostic) | 0.454 | 0.682 | 0.792 | 0.676 | 1.000 |

D1–D6 all pass: Δ@4096 +0.124, 5 tasks up and none down at 4,096, 0 failed requests.
Spearman is 0.992 for repeat and 0.925 for reversed. Reversed drifts +0.027 @4096 (limit
0.030). The fresh primary reproduces v1's recorded judgments exactly (0.7829).

Per-task losses below 4,096 (reported, not gated): two fixture tasks lose at 256
(−0.25, −0.70), and `py-related-test-gives-up` loses 1.0 @1024 (one small holder ordered
out). The macro at each budget is still above production.

## Held-out, once ([results/gate-heldout.txt](results/gate-heldout.txt))

| Arm | LR@256 | LR@1024 | LR@4096 | used@4096 / budget | failed requests |
| --- | ---: | ---: | ---: | ---: | ---: |
| heuristic (production) | 0.034 | 0.129 | 0.235 | 0.988 | |
| random order, mean of 5 | 0.006 | 0.043 | 0.137 | 0.996 | |
| **compact~order** | 0.052 | 0.230 | **0.360** | 0.996 | 10 / 29 |
| no-rank | 0.023 | 0.272 | 0.387 | 0.992 | 10 |
| repeat | 0.053 | 0.272 | 0.423 | 0.994 | 6 |
| reversed | 0.063 | 0.335 | 0.449 | 0.997 | 3 |
| `oracle_order` (diagnostic) | 0.074 | 0.343 | 0.448 | 0.988 | |

H1–H6 pass and **H7 fails** (10 failures > 2.9 allowed) → **INCONCLUSIVE**. Every failure
was HTTP 529 `system_overloaded` (provider load, ~1 s, before any judgment). Budget use is
matched: every arm fills ≥ 0.99 of 4,096.

**Post hoc, not a verdict** ([results/posthoc-heldout-failures.txt](results/posthoc-heldout-failures.txt)),
using the recorded judgments only:

- Fallback worked as designed. On the failed requests every arm equals production
  exactly (Δ 0.000). So failures dilute the gain but never cause harm.
- On the requests JEV answered, the primary gains +0.191 @4096 (19 tasks) with no
  task-budget loss. The controls gain +0.231 to +0.238 on their 19–26 answered tasks.
  *Inferred*: the measured +0.125 is a lower bound under these outage rates.
- The held-out Spearman figures in the gate output (0.88 and 0.84) compare judged tasks
  with fallback tasks. Over the 12 tasks that all three arms judged, they are 0.976
  (repeat) and 0.915 (reversed).
- The no-rank control loses on one task at one budget; the primary loses on none.

## Recommendation

1. **Do not integrate.** The preregistered verdict is INCONCLUSIVE, and production is
   unchanged.
2. The quality question is answered as far as this held-out split allows: on every
   quality criterion, ordering production's admitted set by JEV beats production, a
   random order and the no-rank control, with zero per-task regressions at 4,096. What
   is not established is **operational reliability**. A third of requests failed on
   provider overload during the run (the dev run an hour earlier had none).
3. Before any ADR, decide two things: whether a bounded retry on 529 fits within the 2.0 s
   `DEADLINE` (median latency is 1.0 s, so one retry does not fit at p95 *inferred*), and
   whether fallback-on-failure, which is harmless by construction, makes availability a
   cost question rather than a gate. The held-out split has now been opened: any further
   confirmation needs new held-out tasks, not a rerun of these 29.
4. Still no training: JEV costs ~$0.0006 a request at ~1 s.

## Stability: why v1's repeat and reversed runs drifted ([results/stability-dev.txt](results/stability-dev.txt))

Measured before the freeze, on v1's recorded dev judgments, with no new calls
(`scripts/harness.py --stability`, `scripts/stability.py`).

**Judgment variability is small.** Against the `compact` call, an identical resend leaves
55% of the 707 probabilities unchanged (p90 |Δ| 0.02, max 0.07, 3 floor crossings) and
reorders 9.2% of admitted pairs. Reversed presentation moves more (p90 0.06, max 0.23, 9
floor crossings, 16.6% of pairs reordered), so there is a real, modest position effect.
JEV answers on a 0.01 grid: 1,137 admitted pairs are exact ties.

**Packing turns it into whole-task steps.** The packer is greedy first fit in value order,
so a small reorder near the budget boundary adds or drops whole items, and one task's
recall jumps by 0.17–1.0. On 15 tasks one such task moves the macro by ~0.015–0.025:

- v1's repeat drift (|ΔΔ@4096| 0.015) is **one task**, requests-5414: two values crossed
  the 0.4 floor, admitting two test symbols that carry gold.
- v1's reversed drift (0.0115) is two tasks; in one (requests-2317) a symbol crossing the
  floor displaced six module-level views of `utils.py`.
- Under the v2 hybrid (admission fixed), the repeat run changes **no** task; the reversed
  run changes three (+0.25 @1024 on one; −0.345 and +0.304 @4096 on two that largely
  cancel, |ΔΔ@4096| 0.003). In requests-2931 a reorder put the large `Session` class ahead
  and the budget then held 14 instead of 20 items.

**Simulation (20 seeds each):**

| Perturbation of the recorded `compact` judgments | sd@1024 | sd@4096 | P(\|ΔΔ@4096\| > 0.010) | tasks moved / run | max task \|Δ\|@4096 |
| --- | ---: | ---: | ---: | ---: | ---: |
| re-break exact ties only (hybrid) | 0.000 | 0.000 | 0.00 | 0.0 | 0.000 |
| repeat noise, hybrid | 0.003 | 0.008 | 0.20 | 0.2 | 0.304 |
| reversal noise, hybrid | 0.010 | 0.008 | 0.20 | 1.2 | 0.304 |
| repeat noise, v1 raw mapping | 0.006 | 0.004 | 0.15 | 0.6 | 0.172 |
| reversal noise, v1 raw mapping | 0.011 | 0.008 | 0.60 | 1.1 | 0.500 |

Conclusions:

1. Tie-breaking (deterministic, by pool position) never changed recall on dev.
2. The drift is judgment noise passed through a discontinuous packer: rare, all-or-
   nothing task flips. The raw v1 mapping adds floor crossings on top of that; the
   hybrid removes them by construction.
3. v1's ±0.010 macro tolerance sits below one-task resolution on 15 tasks. The hybrid's
   gain (+0.124 on v1 data) is about 15 sd of this noise, yet it would fail ±0.010 about
   20% of the time per control. v2 therefore gates reproducibility on whether each
   control run reproduces the gain, with ±0.030 drift (preregistration § D5).

## Reproduce

Corpora as in graph-v3. Run under `systemd-run --user --scope -p MemoryMax=3G -p
MemorySwapMax=0 -p CPUQuota=200%`, with `TIKTOKEN_CACHE_DIR` set and `TMPDIR` on disk.
Raw requests and responses stay in `$D`, outside the repository (they contain corpus source).

```sh
S=docs/phase-6/decision-study-v2/scripts D=~/Projects/oxide-eval-data/oxide-decision-study-v2
V1=~/Projects/oxide-eval-data/oxide-decision-study-v1/dev-controls/judgments
uv run --with tiktoken python $S/harness.py $D/stability compact=$V1/compact.json \
  compact-repeat=$V1/compact-repeat.json compact-reversed=$V1/compact-reversed.json \
  compact-norank=$V1/compact-norank.json --stability
python $S/stability.py $V1 $D/stability
uv run --with tiktoken python $S/harness.py $D/dev                       # requests
for a in compact "compact-norank --norank" compact-repeat "compact-reversed --reversed"; do
  uv run --with tiktoken python $S/judge.py $D/dev $a --authorize <corpora> --ledger $D/ledger.json
done                                                                      # TYPESAFE_API_KEY
uv run --with tiktoken python $S/harness.py $D/dev-scored compact=$D/dev/judgments/compact.json ...
python $S/verdict.py $D/dev-scored > docs/phase-6/decision-study-v2/results/gate-dev.txt
# held-out: the same three steps with --split heldout --gate .../results/gate-dev.txt
```
