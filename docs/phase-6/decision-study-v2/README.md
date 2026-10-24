# Decision Study v2: heuristic admission, JEV order (2026-10-24)

Outcome: *pending; the study is frozen in [preregistration.md](preregistration.md).*

Numbers are measured unless marked *inferred*. Nothing in `src/` changes.

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
