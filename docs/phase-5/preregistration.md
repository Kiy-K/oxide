# Phase 5 preregistration: JEV vs heuristic judgment on DecisionBench v1

Frozen 2026-10-09, before any JEV quality result, calibration statistic or
test-split metric was computed. Its SHA-256 is recorded in every metrics
file the ContextBench run writes. Changing this file after results exist
needs a new, dated version. A silent edit is not allowed.

## Question

Can JEV (`jev-1.13.0`, hosted) improve OXIDE's candidate judgments and
final ContextBundle over the deterministic heuristic (`heuristic-evidence/1`)
at acceptable latency, cost and privacy? JEV is not assumed to help. The
heuristic stays the default whatever the outcome. Promotion would need a
separate decision.

## Data

- DecisionBench v1 (`oxide-decisionbench-v1`): 52 ContextBench Python tasks
  over 16 public repositories at their benchmark base commits, plus the 7
  fixture tasks (`fixtures/py_repo`, dev only, CI regression set).
- Splits by repository (`decisionbench-v1/splits.json`, frozen before this
  file): dev 14 tasks, calibration 14, test 24. No repository spans splits.
- Labels: ContextBench `gold_context` spans mapped to entities
  (`cb-span-innermost-v1`). Only spans whose content matches the snapshot
  label `relevant`. Enclosing scopes and unverified spans are `uncertain`
  and excluded. Every other routed candidate is `irrelevant`, which means
  absent from incomplete gold. Region records are `relevant` when their
  subtree holds verified gold.
- Records come from the heuristic run at 1,024 units. Only subjects that
  routing observed exist, so unrouted gold is reported
  (`gold_unobserved_by_routing`) and never treated as a negative.

## Configurations (paired: same snapshot, query, routed universe, capsules, budget, labels)

| Config | Judgment |
| --- | --- |
| A-constant | 0.5 for every subject (no judgment) |
| B-heuristic | `heuristic-evidence/1`, offline default |
| B-no-expansion, B-no-routing | diagnostic ablations of B |
| C-jev | JEV for Relevance and NeighborValue. BranchValue is unsupported, so routing and the candidate universe equal B's. Disclosure `Source`, deadline 600 s per call, 2 retries on 429/529, no confidence floor (`DecisionPolicy::default()`) |
| C-jev-branch | JEV BranchValue on 8 region records per task (smallest capsule digests), offline from routing |

Budgets: 256, 1,024 and 4,096 `oxide-units-v1`. 4,096 is added because
real-repository gold symbols are larger than the fixture's.

## Metrics

- **Candidate** (graph-node records, `relevant` vs `irrelevant`): mean
  within-task AUROC (tasks with both classes), pooled AUROC (only with ≥30
  per class), precision and recall at the inclusion floor 0.4, fallback
  rate. Brier and ECE of the value are reported only when n ≥ 200 with ≥30
  per class, as value-as-probability descriptive statistics.
- **Confidence** (C only): correct means `value ≥ 0.5` agrees with the
  label. Reported: accuracy at coverage 0.25/0.5/0.75/1, AURC, confidence
  ECE/Brier (same sufficiency rule) and reliability bins.
- **Branch**: AUROC, selection accuracy, pruning loss and unnecessary
  exploration at the router's prune threshold 0.25.
- **Context**: Phase 4 definitions per config and budget (fully packed
  required-symbol recall, header-only, file recall, gold token share as
  labeled precision, tokens, items, omissions), plus routing work.
- **Validity**: schema-valid rate, model identity, repeatability (100 dev
  relevance capsules sent 2 more times each), latency p50/p95, input tokens
  and cost.
- Paired differences use a task-level percentile bootstrap (10,000
  resamples, fixed seed) for the 95% interval.

## Gates (read on the test split only, after this file is frozen)

JEV candidate judgment is **promotable** only if all hold:

1. **Validity**: ≥ 99% of 2xx responses schema-valid. Only `jev-1.13.0`
   answers. ≥ 90% of repeated requests have value spread ≤ 0.05.
2. **Candidate quality**: mean within-task AUROC, C − B, ≥ +0.05, with the
   95% interval's lower bound > 0.
3. **Context quality at 1,024 units**: C − B fully packed required-symbol
   recall has a 95% lower bound > −0.05 (non-inferior), and C − B gold
   token share has a 95% lower bound > 0.
4. **Cost**: p95 latency per request ≤ 2 s. Mean input cost per context
   request ≤ $0.01.

A failed gate blocks promotion. It does not fail Phase 5 infrastructure.
With too few tasks for an interval, the gate reads INSUFFICIENT and
promotion stays blocked.

## Calibration

JEV's reported confidence is fitted only on the calibration split
(10-bin histogram binning, a statistical map, not a model). It is
**CALIBRATED** only if the calibration split has ≥ 200 answered points with
≥ 30 correct and ≥ 30 incorrect, its raw confidence ECE is reported, and
the binned map's test-split ECE is ≤ 0.05. Otherwise it is **NOT
CALIBRATED**: `DecisionPolicy::confidence_calibrated` stays false and no
confidence floor is set. No threshold is chosen on the test split.

## Authorization and privacy

Live calls are authorized by the user (2026-10-09) for `fixtures/py_repo`
and the public ContextBench repositories only, with full capsules, under
6,000 requests and 20M input tokens in total. Recorded exchanges with
source stay machine-local (`OXIDE_DECISIONBENCH_DATA/jev/`), except the
fixture's, whose source is OXIDE's own.

## Amendment 1 (2026-10-09, before any ContextBench or test-split result)

At the user's request, made while the live ContextBench run was still in
progress, the quality gates were loosened. At that point only the 7-task
fixture (dev split) results had been read: C − B within-task AUROC −0.054,
95% CI [−0.159, 0.028]. No ContextBench dev, calibration or test metric
existed. The gates above are replaced by:

2. **Candidate quality**: mean within-task AUROC, C − B, ≥ +0.02, with the
   95% interval's lower bound > 0.
3. **Context quality at 1,024 units**: C − B fully packed required-symbol
   recall has a 95% lower bound > −0.05 (non-inferior). Gold token share
   is reported but no longer gated.

Gates 1 and 4 and the calibration rule are unchanged.
