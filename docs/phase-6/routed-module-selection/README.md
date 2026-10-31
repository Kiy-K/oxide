# Routed-only module-level selection: INCONCLUSIVE (2026-10-16)

One bounded experiment was planned against the frozen [baseline-v3](../baseline-v3/README.md):
should a file reached only by routing be allowed to pack its module-level view?
The dev split has one task where that policy could help, so the experiment stopped
at the coverage check, as the task required. Nothing was implemented or run, and
the held-out split was not used. `oxide-select-greedy-v3` stays the default, and
baseline-v1/v2/v3 are unchanged.

## Policies

- **A (control):** baseline-v3. A routed-only file is valued 0.2 (`container`), which
  is below `INCLUDE_FLOOR` 0.4, so it never packs (`packing.value`).
- **B (variant, preregistered, not built):** a routed-only file whose `Routed.origins`
  include `scope:<entry point>` (it contains a retrieved symbol) is valued 0.45. That
  is the existing value of a depth-1 routed symbol, with the basis
  `scope of entry point`. It competes with its existing module-level view (full,
  then header). B adds no new channel, model, signal or constant, and leaves
  `MAX_ITEMS` (24), the floor and the budgets unchanged. Routed files reached any
  other way (importer, root fallback) stay containers.

## Preregistered measurements and promotion criteria

These were written before any run of B. B would be built only if the coverage check
passed.

- **Coverage check (precondition).** The dev split needs at least 5 distinct tasks
  with verified module-level gold lost as `selection: value N below floor`. The
  reason is resolution. A dev metric is a macro mean over 15 tasks, so one task
  moving from 0 to 1 shifts it by up to 0.067, and a partial move shifts it by about
  the gate tolerance (0.02). With fewer tasks, the result depends on one file's
  decision.
- **Measure, on dev, A vs B:** module-level coverage of the affected spans;
  `span_recall` and `line_recall` at 256/1,024/4,096; `gold_unit_share` and
  displaced units (gold units in A that are lost in B); loss-stage counts; p50/p95
  `build_context` latency and peak RSS over the full run; determinism (two runs
  produce byte-identical outputs and bundles rebuild identically) and
  `used <= budget` for every bundle.
- **Promote only if all of these hold:** at least 3 affected dev tasks gain verified
  module-level lines at 4,096; no gated metric regresses by more than 0.02 at any
  budget; `gold_unit_share` drops by no more than 0.02 at any budget; latency and
  RSS overhead each stay within 10%; and determinism and budget accounting hold
  everywhere. Otherwise reject, or mark inconclusive.

## Coverage check (measured from `baseline-v3/results.json`, verified gold)

| Split | tasks | verified spans | routed-only module-level losses | distinct tasks |
| --- | ---: | ---: | ---: | ---: |
| dev | 15 | 47 | 2 | **1** |
| heldout | 29 | 138 | 5 | 5 |

The only dev case is `psf__requests-2317`, `requests/sessions.py`, with spans 11–23
and 13–19. The second span lies inside the first, so the two spans are one
observation: one import prologue in one file. If B packed both spans at every budget
(an oracle upper bound), dev would gain +0.022 `span_recall` and about +0.009
`line_recall` (12 of that task's 87 gold lines, averaged over 15 tasks). Both gains
are at the tolerance, and they come from a single decision. The eighth module-level
loss (pytest-10051 `logging.py` 44–52, item cap) is held-out. It is not a cap
defect, so the cap was not touched.

**Verdict: INCONCLUSIVE.** One task falls short of the preregistered 5. Dev cannot
separate a policy effect from one file's outcome, and tuning on the five exposed
held-out tasks is not allowed. Cost metrics (displacement, latency, RSS) were not
measured, because they cannot justify promotion without a measurable benefit.

Disclosure: the per-split counts above were already in the committed baseline-v3
artifacts (its README reports 7 routed-only losses). The threshold was set after
those artifacts existed, but it follows from the resolution argument above, not
from the dev count.

## Closing the selection iteration

This closes the selection R&D iteration that began with baseline-v1. Remaining
verified loss at 4,096 is mostly upstream or in the budget: 74 spans are never
reached (35 entry, 39 routing) and 45 lose to the budget, against 26 selection
losses. Reopen routed-file selection only with a dev split that has enough
module-level cases (new tasks, frozen before any run), or through a future
`DecisionProvider`. The next implementation milestone is **Incremental Indexing +
UX** (`docs/BOOTSTRAP.md` § Phase 6, Remaining).
