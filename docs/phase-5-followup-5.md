# Phase 5 follow-up 5: packing and selection audit (cycle closed)

Status: done (2026-10-10) on `rewrite/v2`, after `1e49897`. Dev split only
(14 tasks), offline, no JEV call, no model training. The packer and
selector (`oxide-select-greedy-v1`, `oxide-payload-v1`) are unchanged: no
correctness bug was found, and the one preregistered variant gains
nothing. This closes the selection and packing R&D cycle. No Phase 3/4/5
artifact changed. Protocol, frozen after the audit and before the
variant: [phase-5/followup-5/preregistration.md](phase-5/followup-5/preregistration.md).
Results: `phase-5/followup-5/{audit,item-cap}.json`, from
`runtime/tests/phase5_packing.rs` (opt-in, machine-local).

Every run uses the baseline route (BFS, 64 regions), graph and relevance
capsules (asserted per task). Gold only scores. "JEV replay" judges
relevance and expansion neighbors from recordings. Every such capsule
had a recorded answer (0 fallbacks). Routing stays the heuristic's: its
616 branch questions per budget are declined (`Unsupported`), as in
follow-up 4.

The audit was rerun after the preregistration was frozen, under an
extended cache key. Every number except latency and memory reproduced
exactly.

## The "oracle" is a gold-priority reference, not an upper bound (measured)

It values gold symbols 1.0, other gold 0.5 and everything else 0, through
the real selection and packing. Two consequences:

- **It never selects a non-gold item.** At 4,096 units recorded JEV packs 9
  gold symbols whole inside enclosing items (classes, modules) that are
  not themselves gold. The reference cannot select them, so JEV's 0.591
  beats its 0.555. They come from 3 tasks, and none is a graph node.
- **It is cost-blind**, but that costs nothing here. At 1,024 units each
  of its 8 header-only gold symbols costs more in full (370 to 2,967
  units) than the budget it left. By those standalone costs, cheapest-first
  order would fit no more whole gold symbols (inferred; overlap could change
  the costs).

It is renamed `GoldPriority` (identity `gold-priority`) in the test code.
Follow-up 3 and 4 artifacts keep their `oracle` keys.

## Audit (measured, dev, task means)

| Run @ units | Recall | Header-only | Selection loss | Phase 4 `packing_loss` | Packer loss (`packer_omitted` + `packer_reduced`) | Units used | Unlabeled share | p50 latency |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| heuristic @1,024 | 0.200 | 0.100 | 0.295 | 0.088 | 0 | 1,008 | 0.803 | 340 ms |
| heuristic @4,096 | 0.312 | 0.041 | 0.248 | 0.024 | 0 | 4,015 | 0.831 | 333 ms |
| JEV replay @1,024 | 0.217 | 0.288 | 0.145 | 0.248 | 0 | 993 | 0.756 | 334 ms |
| JEV replay @4,096 | 0.591 | 0.048 | 0.086 | 0 | 0 | 3,602 | 0.594 | 336 ms |
| gold priority @1,024 | 0.312 | 0.255 | 0.029 | 0.243 | 0 | 377 | 0.060 | 309 ms |
| gold priority @4,096 | 0.555 | 0.012 | 0.029 | 0 | 0 | 1,148 | 0.015 | 307 ms |

- **The packer loses nothing.** Across all 84 audit runs it never omitted or
  reduced a planned item. Its exact count never exceeded the plan
  estimate, and no bundle exceeded its budget.
- **Phase 4's `packing_loss` is selection loss.** It counts gold the
  selector itself planned as a header because the full view did not fit.
  The metric is unchanged (frozen artifacts use it); its definition now
  says so.
- **Estimates overcharge overlap slightly.** The plan estimate exceeds
  the packed count by 0.7% (JEV, 1,024) to 3.6% (JEV, 4,096) of the
  estimate. The likely cause is a container planned after one of its
  members: it is charged standalone, while the packer counts the merged
  range once (inferred from select.rs). The budget omissions that would fit
  in what was left unused include one gold symbol per heuristic budget.
  Each would fit only in its smallest view, a header when one exists,
  because an omission's `needed` is that view. SPEC allows unused budget,
  so this is not a bug.
- **The item cap binds once.** One task, heuristic at 4,096 units, with
  269 units unused and 3 gold symbols omitted by `MaxItems`. JEV and the
  gold-priority reference never reach 24 items.
- **Header fallbacks are cheap insurance, not the loss.** Headers take 18%
  of heuristic units at 1,024 (55 items, 3 gold). The gold they could
  displace costs more in full than the header units in its task, for all
  but one symbol (inferred from recorded costs).
- **The binding limits are value and size, not packing.** At 1,024 units
  the heuristic's budget-omitted gold symbols cost 63 to 2,296 units in
  full, and gold priority fits about one whole gold symbol per task.
  Precision of relevance values (JEV, at 4,096) is what moves recall.

## Variant (preregistered): item cap 48

Rejected. Paired against the 24-item cap on dev, fully packed recall
changes by 0.000 (interval [0, 0]) for the heuristic and JEV at both
budgets (`item-cap.json`). In the one capped task, one gold symbol enters only as a
header; the freed slots go to higher-valued non-gold items first
(inferred from value order). Heuristic
p50 latency at 4,096 is 1.01× (one run).

Not run, on audit evidence:
- **utility/cost ordering:** it would change no whole-gold count for the
  gold-priority reference, and heuristic values cannot tell gold apart;
- **exact incremental cost estimates:** at most one header-only gold
  symbol per budget;
- **header-fallback removal:** at most one whole gold symbol, against 3
  header-only gold lost;
- **range deduplication:** the packer already merges overlaps, with no
  loss.

## Cost

No change to the product path. Per context: 307 to 340 ms p50 (one run,
200% CPU quota). Process peak RSS is 3.3 to 3.6 GiB, dominated by indexing
as in follow-up 3.

## Cycle closed

Follow-ups 3 to 5 locate the remaining loss in relevance precision, not in
routing budget, scoring rules or packing. On dev, recorded JEV at
4,096 units is the only configuration that moves recall (+0.279 over the
heuristic). No further selection or packing experiment is planned. The
remaining limits:

- 14 dev tasks and 60 gold symbols: only large effects are detectable.
- 28 to 37 gold symbols per run (of 60) are neither graph nodes, nor
  reached by expansion, nor inside a packed item. Packing cannot recover
  them. Routed candidates the graph dropped beyond its node limit are
  included and are not separated.
- Gold-priority is a reference, not a ceiling. No true ceiling (best
  plan over containers and expansion) was computed.
- JEV gains are from recordings. Live JEV quality and calibration/test
  remain as frozen in Phase 5.
- The per-task cache key covers the harness, runs, budgets and pinned
  versions, not the recordings' content (as in follow-ups 3 and 4).
- Erratum to the frozen preregistration (not edited): its estimate-slack
  range "1.0% to 3.6%" should read 0.7% to 3.6%, and the 1.0% is the
  heuristic's at 1,024 units. Its "only as a header" should read "in its
  smallest view".
