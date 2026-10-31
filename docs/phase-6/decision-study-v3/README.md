# Decision Study v3: independent quality confirmation PASS

Historical one-shot study, 2026-10-24; [preregistration](preregistration.md), frozen at `bf86d3a`. **PASS permitted proposing an optional-judge ADR, not automatic integration.** The 52-task v3 set is consumed. Current policy is in [ADR-0009](../../adr/0009-jev-assisted-context-ordering.md).

Policy unchanged from v2: heuristic admission, JEV `jev-1.14.0` / `ds1-compact-v1` order `0.4 + 0.59p`, whole-request fallback. Difference: enforced 2.0 s deadline; localhost tests killed hung/late children, rejected wrong/partial replies, and verified unchanged fallback after late arrival.

Set: 52 tasks from eight repositories new to the Python engine and decision studies (not new to legacy Rust experiments). [tasks](tasks.json), [gold audit](gold-audit.json), [separation](separation.json) record exclusions/source overlap. At most 3,000 Python files; 40/52 queries clipped at 1,024 bytes. Leak checks and request-digest equality passed; v2 dev replay reproduced its gate before freeze.

| End-to-end LR | Heuristic | Random | Primary | Repeat | Oracle |
| --- | ---: | ---: | ---: | ---: | ---: |
| @256 | 0.0201 | 0.0058 | 0.0334 | 0.0318 | 0.0458 |
| @1024 | 0.1779 | 0.0505 | 0.3026 | 0.2977 | 0.3064 |
| @4096 | 0.3161 | 0.1405 | 0.4755 | 0.4686 | 0.5022 |

Primary Δ@4096 +0.1594; task-bootstrap CI +0.103…+0.224, repository-cluster CI +0.075…+0.250; 33 wins, no losses, 19 ties. Leave-one-repository-out gains +0.117…+0.186. Smaller budgets had nine primary/eight repeat task-budget regressions (worst −0.375@256).

Operations: primary 52/52 answered, p50/p95/max 1.01/1.19/1.42 s; repeat 50/52 answered, two deadline fallbacks (3.8%, Wilson CI 1.1–13.0%), p95 1.45 s. Fallback bundles matched production. Total $0.106, ~24.3k billed tokens/request. Q1–Q5/O1–O3 passed: [gate-v3.txt](results/gate-v3.txt).

Limits: one Python model/snapshot, uneven gains (keras drives some mean; langchain/yt-dlp little), clipped queries, little deadline headroom and only a short clean availability window. No 529 episode sampled; retry/breaker untested. Nothing in product code changed in this experiment.

## Reproduce

Use private data, on-disk `TMPDIR`, tokenizer cache and one job under `systemd-run --user --scope -p MemoryMax=3G -p MemorySwapMax=0 -p CPUQuota=200%`.

```sh
S=docs/phase-6/decision-study-v3/scripts
D=~/Projects/oxide-eval-data/oxide-decision-study-v3
P=~/Projects/oxide-eval-data/oxide-jev-eval/contextbench-full.parquet
uv run --with pyarrow python $S/taskset.py $P # historical pre-freeze selection
uv run --with tiktoken python $S/call.py selfcheck
uv run --with tiktoken python $S/score.py $D/v3 --split v3 --requests-only
uv run --with tiktoken python $S/call.py estimate $D/v3
# Live calls require explicit corpus/spend authorization and TYPESAFE_API_KEY:
uv run --with tiktoken python $S/call.py run $D/v3 compact --authorize <corpora> --ledger $D/ledger.json
uv run --with tiktoken python $S/call.py run $D/v3 compact-repeat --authorize <corpora> \
  --ledger $D/ledger.json --sent $D/v3/judgments/compact.json
uv run --with tiktoken python $S/score.py $D/v3-scored compact=$D/v3/judgments/compact.json \
  compact-repeat=$D/v3/judgments/compact-repeat.json --split v3
python $S/report.py $D/v3-scored --sent $D/v3 --ledger $D/ledger.json
```

Requests, raw responses and ledger remain external in `$D`; full replay requires them. Do not overwrite frozen reports or reuse this set as fresh evidence.
