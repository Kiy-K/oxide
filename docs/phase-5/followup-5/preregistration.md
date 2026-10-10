# Phase 5 follow-up 5 preregistration: item cap

Frozen 2026-10-10, after the dev packing audit (`audit.json`) and before
the variant below was measured. It builds on follow-up 4 (`1e49897`) and
changes none of its artifacts. Changing this file after results exist
needs a new, dated version.

## Known before freezing (dev audit, BFS, 64 regions)

- The packer never omitted or reduced a planned item, and its exact
  count never exceeded the plan estimate, for the heuristic, recorded JEV
  or the gold-priority reference at 1,024 or 4,096 units. The Phase 4
  `packing_loss` metric is gold the selector planned as a header, not
  packer loss.
- Plan estimates overcharge overlapping views by 1.0% (heuristic, 1,024)
  to 3.6% (JEV, 4,096) of the estimate. Budget omissions that would fit
  in the unused budget include one gold symbol per heuristic budget,
  each only as a header (the omission's `needed` is its smallest view).
- The 24-item cap binds once: one task, heuristic, 4,096 units, with 269
  units unused and three gold symbols omitted by `MaxItems` (full costs
  63, 83 and 2,296 units). JEV and the gold-priority reference never reach it.
- Header reductions take 18% of heuristic units at 1,024 and hold 3 of its
  55 headers' gold. The gold they displace costs more in full than
  the header units in its task, except for one symbol.
- The gold-priority reference is not an upper bound: recorded JEV packs 9
  gold symbols at 4,096 inside non-gold enclosing items the reference
  never selects.

## Variant

`C`: the baseline selection policy with `max_items` 48 instead of 24.
Nothing else changes: route, graph, capsules, relevance values,
floors, expansion and packing. It is run for the heuristic and for
recorded JEV (replay only; an unrecorded capsule falls back to the
heuristic and is counted, never a negative label). Gold is used only to
score.

## Measures

Per task and budget (1,024 and 4,096 `oxide-units-v1`): fully packed
recall (Phase 4 metric), header-only gold, selection loss, Phase 4
`packing_loss`, unlabeled token share, units used, items, latency (one
run), and process peak RSS.

## Decision rule (dev, paired over 14 tasks against the baseline)

`C` replaces the cap only if all hold:
- heuristic recall at 4,096: mean ≥ +0.02 and 95% task-bootstrap lower
  bound > 0;
- heuristic and JEV recall at 1,024 and JEV recall at 4,096: mean ≥ 0;
- heuristic unlabeled token share at 4,096 rises by at most 0.05;
- identical bundles on a second run, budgets never exceeded;
- heuristic context p50 latency at 4,096 ≤ 1.2× baseline.

Only one task can change, so the lower bound cannot exceed 0. That makes
the expected verdict "no gain detected". The run measures the size of the
effect so the item-cap question is closed by measurement rather than inference. No other
packing or ordering variant is run; the audit gives the reasons.
