# Phase 5 follow-up 4: selection scoring

Status: done (2026-10-10) on `rewrite/v2`, after `38c4967`. Dev split only
(14 tasks), offline, no JEV call, no model training. No variant passed,
so the heuristic selector (`heuristic-evidence` 1,
`oxide-select-greedy-v1`) is kept and calibration was not opened. The
kernel is unchanged. No Phase 3/4/5 artifact changed. Protocol, frozen
after the audit and before any variant ran:
[phase-5/followup-4/preregistration.md](phase-5/followup-4/preregistration.md).
Results: `phase-5/followup-4/{audit,variants}.json`, from
`runtime/tests/phase5_selection.rs` (opt-in, machine-local).

The route (BFS, 64 regions), the 64-node candidate graph, the relevance
capsules and the selection policy are the baseline's in every run (asserted
per task). Only relevance values change.

## 0.583 vs 0.383 routed coverage: a metric difference

Both numbers are correct, and the routing outcome is the same. Follow-up 2's 0.583 is the task mean over all 68
verified gold entities (symbols and file-level gold). Follow-up 3's 0.383
is 23 of 60 gold symbols pooled over tasks. Over gold symbols alone the
task mean is 0.605: three one-symbol tasks with their symbol routed
weigh as much as a 9-symbol task with none. The packed recall both reports
quote (0.200) is the task mean over all verified gold, like 0.583
(measured, `audit.json`).

## Audit: why routed candidates lose (measured)

Heuristic, 64 regions, per graph node over 14 tasks:

| Class | Nodes | Gold symbols (rate) | Heuristic value | Recorded JEV, gold / other | Gold planned @1,024 / @4,096 |
| --- | --- | --- | --- | --- | --- |
| lexical ranks 1–5 | 58 | 9 (15.5%) | 0.75–0.85 | 0.85 / 0.40 | 9 / 9 |
| lexical ranks 6–15 | 122 | 3 (2.5%) | 0.5–0.725 | 0.88 / 0.29 | 2 / 2 |
| lexical ranks 16+ | 62 | 1 (1.6%) | 0.5 | 0.24 / 0.23 | 0 / 1 |
| routed, depth 1 | 332 | 8 (2.4%) | 0.45 | 0.93 / 0.28 | 0 / 2 |
| routed, depth 2 | 177 | 2 (1.1%) | 0.3 | 0.66 / 0.21 | 0 / 0 |

- **Scoring, the cause of the bias:** the heuristic values every lexical
  entry point (≥ 0.5) above every routed-only candidate (0.45, or 0.3 below
  the 0.4 include floor). It has no signal to tell routed candidates
  apart. JEV does: its recorded answers rank depth-1 routed gold at 0.93,
  against 0.28 for the rest. Every graph node had a recorded answer.
- **Budget, the binding limit at 1,024 units:** lexical ranks 1–5 take
  10,014 of the 14 plans' 14,255 units, ranks 6–15 another 3,732. Every
  routed gold symbol is omitted as `BudgetExceeded`, never on value
  alone. Depth-2 routed gold falls below the include floor.
- **Item cap at 4,096 units:** 3 of 8 depth-1 routed gold symbols are
  omitted by `max_items` (24) and 3 by the budget.
- **Where the routed gold is:** 8 of the 10 routed-only gold symbols are callers
  of an entry point (4) or members of an entry-point symbol (4). The
  depth-1 candidates matching either pattern ("structural") are 8 gold
  of 131 (6.1%). Depth-1 candidates whose every origin is a callee or
  an enclosing class are 0 of 128 (per-task audit caches). The preregistration's estimates (about 7.6% of about 105,
  0 of about 180) counted first origins only. 6.1% lies between lexical
  ranks 1–5 and 6–15.

## Variants (dev)

Preregistered, capsule-only rescorings (no gold): `L` decays lexical
values faster (ranks ≥ 10 drop below routed depth 1). `S` puts
structural candidates just after lexical rank 5. `LS` does both. `B`
alternates lexical and structural candidates. Fully packed recall (task
mean, all verified gold), with the paired difference against `H`:

| Variant | Recall @1,024 (vs H [95% CI]) | Recall @4,096 (vs H) | Gold symbols planned / packed @1,024 | Unlabeled share @1,024 | p50 latency | Plan Jaccard vs H @1,024 |
| --- | --- | --- | --- | --- | --- | --- |
| `H` (baseline) | 0.200 | 0.312 | 11 / 8 | 0.803 | 313 ms | 1 |
| `L` | 0.200 (0 [0, 0]) | 0.336 (+0.024 [0, 0.071]) | 12 / 8 | 0.799 | 324 ms | 0.72 |
| `S` | 0.200 (0 [−0.036, 0.036]) | 0.348 (+0.036 [−0.036, 0.143]) | 14 / 8 | 0.790 | 322 ms | 0.56 |
| `LS` | 0.200 (0 [−0.036, 0.036]) | 0.348 (+0.036 [−0.036, 0.143]) | 14 / 8 | 0.790 | 325 ms | 0.50 |
| `B` | 0.195 (−0.005 [−0.038, 0.031]) | 0.360 (+0.048 [0, 0.143]) | 13 / 7 | 0.804 | 340 ms | 0.41 |
| oracle (reference) | 0.312 | 0.555 | 23 / 15 | 0.060 | — | — |
| recorded JEV (reference) | 0.217 | 0.591 | 17 / 11 | 0.756 | — | — |

None passes the dev rule (recall at 1,024 ≥ +0.02 with lower bound > 0).

- **The rescorings move budget, not recall.** For example, `S` raises
  routed depth-1 spend at 1,024 units from 141 to 3,104 units and plans
  3 more gold symbols (selection loss 0.295 → 0.248). Packing loss
  rises by the same amount (0.088 → 0.136): the promoted gold mostly
  fits only as a header (header-only gold 0.10 → 0.15).
- **Gains and losses cancel per task (per-task caches).** Under `S`, one task gains
  (+0.167) and one loses its rank-6 lexical gold (−0.167). At 4,096
  units, one task (+0.667, two member methods) drives the whole mean
  gain, against one loss.
- **The structural signal is too weak for 1,024 units.** About 1 in 16
  structural candidates is gold, against 1 in 6.5 for lexical ranks
  1–5. Spending 1,024 units on them costs as much gold as it gains
  (inferred).
- **References.** The oracle uses 37% of the 1,024 budget and gains
  +0.112: precision, not order, is the headroom. Recorded JEV gains
  +0.017 at 1,024 (interval −0.11 to +0.14) and +0.279 at 4,096. Its
  answers covered every relevance and expansion capsule; the only
  fallbacks are its declined routing questions.
- **JEV agreement.** Pairwise order agreement with recorded JEV values
  stays between 0.555 and 0.587 for every variant (`H` 0.573), so none
  ranks noticeably more like JEV.
- **Cost and determinism.** Every variant ran deterministically (a
  second run gave an identical bundle), with no extra retrieval,
  routing, graph or capsule work, and a p50 within 1.09× of `H`.
- **Redundancy-aware selection was not tried.** The audit showed no
  redundancy loss to correct: the loss is budget spent on low-precision
  lexical items and missing routed-candidate signal.

## Decision

No variant beats the baseline at 1,024 units. **The heuristic selector
and its default are unchanged.** Calibration and test were not opened.

The structural rule was built from the same 14 dev tasks it was scored on
(about 10 routed gold symbols). A dev pass would have meant only "worth a
calibration check", never a default change. It did not pass even with
that advantage.

The 4,096-unit means for `S`, `LS` and `B` lean positive (+0.036 to
+0.048; every interval reaches 0 or below). That is one task, so it is
undecided and not a basis for a default. A selector that extracts more
from existing candidates needs per-candidate precision, which the
capsule's structure alone does not give. Recorded JEV supplies it at
4,096 units (+0.279). That points to judging, not rescoring, for the
routed tail (inferred).

## Limitations

- 14 dev tasks and 60 gold symbols; only large effects are detectable.
- Erratum to the frozen preregistration (not edited): it says every
  routed gold symbol is omitted as `BudgetExceeded` at 1,024 units. That
  holds for the 8 at depth 1; the 2 at depth 2 are below the include
  floor (`audit.json`).
- Latency is one run per configuration under a 200% CPU quota.
- The per-task cache is keyed by the harness, preregistration, retrieval,
  router and selector versions.
