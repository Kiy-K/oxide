# Graph navigation: Stage 1 FAIL; no agent trial

Historical study, 2026-10-25; [preregistration](preregistration.md), frozen in [results/freeze.txt](results/freeze.txt). **Decision: do not expose graph navigation on this evidence.** Completeness, not representation, failed; Stage 2 was not run.

Reported setup: 24 targets (12 functions, 12 methods) across flask, requests and pytest; Jedi 0.20.1 reference oracle; direct dependents Q1 and two-hop Q2. Arms: iterated grep, context@4096, impact, proven-only. Tokens used o200k.

| Arm | Q1 precision / recall / F1 | Q2 precision / recall / F1 | Q2 total tokens |
| --- | --- | --- | ---: |
| grep | 0.778 / 1.000 / 0.828 | 0.311 / 1.000 / 0.413 | 134,463 |
| impact | 0.993 / 0.659 / 0.701 | 0.993 / 0.585 / 0.633 | 4,326 |
| proven-only | 1.000 / 0.410 / 0.424 | 1.000 / 0.310 / 0.356 | 2,120 |

Impact's Q2 F1 gain +0.220 passed, but Q1 gain −0.127 failed. Incompleteness sensitivity 0.727 missed the ≥0.9 gate; specificity was 0.462. Three silent misses were a decorator, a bare-name argument/receiver use and fixture injection. `unresolved == 0` is not completeness evidence. Context recovered 41% of direct dependents; warm median latency: grep 8 ms, impact 11–13 ms, context 88 ms.

Audit: all 93 direct hops had witnesses; 77 depth-2 hops had valid intermediates. Three apparent false calls on `redirect` were Jedi misses. Other inspected oracle misses enlarge truth and cannot rescue the FAIL. Audit size deviated downward from the preregistered maximum because its direction could not flip the verdict. Single-language small-corpus study, no agents or editing tasks; see [reassessment](../agc/README.md) and [follow-up](../graph-navigation-v2/README.md).

## Reproduce

```sh
S=docs/phase-6/graph-navigation/scripts
uv run python $S/dump.py
JEDI_VENV/bin/python $S/oracle.py
uv run --with tiktoken python $S/arms.py
python3 $S/analyze.py
```

[results/report.json](results/report.json), frozen truth/targets and scripts are retained. Stores/entity dumps are external: `$OXIDE_GRAPHNAV_DATA` (default `~/Projects/oxide-eval-data/oxide-graphnav-eval`). Set `TIKTOKEN_CACHE_DIR` for offline tokenizer access; Jedi environment must provide 0.20.1.
