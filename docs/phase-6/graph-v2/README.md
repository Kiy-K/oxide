# Graph v2: heuristic dispatch promoted; routing rejected

Historical decision, 2026-10-24; [preregistration](preregistration.md). Measurements below were not rerun for documentation cleanup.

- **Promote Python v5 receiver-aware dispatch:** C3 MRO for `self`, `cls`, `super`, inherited methods and uniquely constructed instances. All edges are heuristic, never proven or routed. External/unknown bases, rebinding and custom dispatch conservatively stop resolution.
- Across flask, requests, xarray and pytest: no v4 edge lost/changed; 3,084 new sites. Jedi agreed on 2,894/2,924 resolved sites, missed 29, and disagreed once (an inspected Jedi MRO error); all 160 ambiguous sets contained its answer. Index time +2–12%, RSS +1–6 MB (single runs).
- **Ship indexed unresolved-name lookup:** VS Code impact upstream p50 543→101 ms, mentions p50 437→3.5 ms; counts identical for 40/40 sampled roots on every corpus. `UNWIND`-per-key was slower than scanning (17 vs 15 ms), so rejected.
- **Reject dispatch routing:** Python dev (15 tasks) LR@4096 stayed 0.659 (needed +0.02), gold-unit share 0.189→0.187, with one previously routed gold span displaced. B reproduced baseline-v13 hashes.
- Graph v1's six newly routed verified spans were lost at selection (3) or budget (3), not discovery. [Graph v3](../graph-v3/README.md) refines this diagnosis with counterfactual packing; do not infer that removing the selection floor helps.

Evidence: [edge comparison](results/edge-diff-v4-v5.txt), [Jedi sites](results/jedi-sites-v5.txt), [mentions](results/mentions-v2.jsonl), [loss trace](results/loss-trace-v11-v12.txt), [routing gate](results/dispatch-gate.txt), plus `audit/`, raw arms and scripts. [baseline-v13](../baseline-v13/README.md) and [typescript-v10](../typescript-v10/README.md) were identity-only; Go/Rust context metrics matched. The unresolved VS Code row-count discrepancy between v1 and v2 (254k vs 376,892) was unexplained.

## Reproduce

Corpora: `~/.cache/oxide/contextbench/corpora/`; v4 comparator `1b11ab6`. Use private on-disk `TMPDIR`, one job at a time under `systemd-run --user --scope -p MemoryMax=3G -p CPUQuota=200%`.

```sh
S=docs/phase-6/graph-v2/scripts
uv run python $S/call_shapes.py OUT.json REPO...
uv run python $S/edge_dump.py edges-v5.json REPO... # also from a worktree of 1b11ab6
python $S/edge_diff.py edges-v4.json edges-v5.json new-rows.json
JEDI_VENV/bin/python -I $S/jedi_dispatch.py new-rows.json jedi-v5.json
uv run python $S/mentions_v2.py REPO # from each tree
python $S/loss_trace.py baseline-v11 baseline-v12
uv run python $S/dispatch_arm.py B OUT_B # then T OUT_T
python $S/arm_gate.py OUT_B OUT_T
```
