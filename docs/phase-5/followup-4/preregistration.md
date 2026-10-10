# Phase 5 follow-up 4 preregistration: selection scoring

Frozen 2026-10-10, after the dev selection audit (`audit.json`) and
before any variant below was measured. It builds on follow-up 3
(`38c4967`) and changes none of its artifacts. Changing this file after
results exist needs a new, dated version.

## Known before freezing (dev audit, heuristic, BFS, 64 regions)

- 0.583 (follow-up 2) and 0.383 (follow-up 3) measure different things:
  the task mean over all 68 verified gold entities, versus 23 of 60 gold
  symbols pooled over tasks. The task mean over gold symbols alone is
  0.605. Small tasks with one routed gold symbol weigh as much as a
  14-symbol task in a task mean.
- Every lexical entry point (0.5–0.85) outranks every routed-only
  candidate (0.45 at depth 1, 0.3 deeper, below the 0.4 include floor).
  Measured gold rates per node: lexical ranks 1–5 15.5% (9 of 58), ranks
  6–15 2.5% (3/122), ranks 16+ 1.6% (1/62), routed depth 1 2.4% (8/332),
  depth 2 1.1% (2/177). At 1,024 units the 14 plans spend 10,014 units
  on lexical ranks 1–5 and 3,732 on ranks 6–15; every routed gold symbol
  is omitted as `BudgetExceeded`. At 4,096 units, 3 of 8 depth-1 routed
  gold symbols are omitted by `max_items` (24) and 3 by the budget.
- Routed-only gold is almost all callers of an entry point (4) or members
  of an entry-point symbol (4). Those depth-1 candidates have a gold rate
  of about 7.6% (8 of about 105); callees and enclosing classes 0 of
  about 180. Recorded JEV relevance separates depth-1 routed gold from
  the rest (mean 0.93 vs 0.28). These rates come from dev, so the dev
  comparison below is optimistic for rules built on them; calibration
  confirms.

## Variants

Dev split only (14 tasks), offline, no JEV call. The route, the
candidate graph and the relevance capsules are the baseline's (asserted
equal per variant), and so are the selection policy (floors, 24 items,
expansion) and packing. Only `Relevance` values change; `NeighborValue`
and `BranchValue` stay the heuristic's. Every variant reads only the
capsule (or, for `B`, the batch of relevance capsules), never gold.

A **structural** candidate is a routed-only candidate at depth 1 with an
origin that is a call from it to an entry point (`Calls`, incoming) or
membership in an entry point's region whose anchor is a symbol.

- `H`: the heuristic (baseline).
- `L` (lexical normalization): lexical value `max(0.85 − 0.05·(rank − 1),
  0.4)`, so lexical ranks 10 and beyond fall below routed depth 1 (0.45).
- `S` (structural evidence): structural candidates 0.74, just after
  lexical rank 5 (0.75) on the baseline lexical scale.
- `LS`: `L` and structural candidates 0.625 (after rank 5, before 6).
- `B` (balanced): lexical entry points and structural candidates
  alternate, one each, in graph order: the k-th lexical (0-based)
  `max(0.85 − 0.02·k, 0.46)`, the k-th structural
  `max(0.84 − 0.02·k, 0.46)`. It needs the batch, so as a product rule it
  would belong to selection, not the per-capsule heuristic.

Context budgets 1,024 and 4,096 `oxide-units-v1`. References, never
gates: the evaluation-only oracle (gold 1.0, other gold 0.5, else 0)
and the recorded JEV answers. A capsule without a recorded answer falls
back to the heuristic and is counted; it is never a negative label.

## Measures, per task, variant and budget

Fully packed recall (Phase 4 metric, all verified gold), selection and
packing loss, planned and packed gold symbols (pooled), gold and
unlabeled token share, units used and budget utilization, plan items by
provenance class (count, units, gold), plan Jaccard against `H`,
expansion work, latency (one run), and pairwise order agreement of each
variant's relevance values with recorded JEV values (pairs of symbol
nodes within a task, both recorded, ties in either skipped).

## Decision rule

A variant passes dev if, paired against `H` over tasks:
- fully packed recall at 1,024: mean ≥ +0.02 and 95% task-bootstrap
  lower bound > 0;
- at 4,096: mean ≥ 0;
- unlabeled token share at 1,024 rises by at most 0.05;
- identical bundles on a second run (determinism);
- heuristic context p50 latency ≤ 1.2× `H`.

Among passing variants, the highest dev mean at 1,024 is chosen, ties
to the simpler (`S`, `L`, `LS`, `B`). Only that variant is run once on
calibration (14 tasks, untouched until then), by the same harness: it is
confirmed if its calibration mean at 1,024 is ≥ +0.02 with a lower bound
> −0.02, its pooled dev + calibration lower bound is > 0, and its
calibration mean at 4,096 is ≥ 0. The test split is not used. The
default changes only after confirmation. A gain under the oracle or JEV
alone never changes it. With 14 tasks per split only large effects are
detectable; a non-pass means "no gain detected".
