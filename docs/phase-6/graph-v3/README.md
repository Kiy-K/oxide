# Graph v3: budget allocation needs signal, not wider graph policy

Historical dev-only study, 2026-10-24: 15 Python and 25 TypeScript tasks with verified gold; held-out unopened. [Preregistration](preregistration.md). No product change or baseline regeneration.

**Decision: reject parent-precedence ordering; keep the floor and caps.** Reported LR@4096 ceilings on the same candidate pool:

| Arm | Python | TypeScript |
| --- | ---: | ---: |
| Production | 0.659 | 0.370 |
| Perfect ordering, same admission | 0.792 | 0.423 |
| Perfect ordering/picking, floor ignored | 0.886 | 0.428 |
| Unlimited budget and items, floor kept | 0.948 | 0.429 |
| Routed coverage (no floor) | 0.957 | 0.443 |

Python's largest delivered-context loss is budget allocation; TypeScript's is discovery (55.7% of gold lines absent from the pool). Removing the item cap at the real budget changed no recall; region caps 32–128 changed no task. Routed neighbours held gold only 2.5–3.5% of the time, versus 4–8% even for lexical ranks 11+: structure alone did not identify the missing gold.

The preregistered parent-precedence arm changed 14/40 payloads but no recall at any budget (needed pooled +0.010 LR@4096). Gold moved from item-cap loss to budget loss; held-out was not run. This rejects that order, not the existence of ordering headroom. [Decision studies](../decision-study-v1/README.md) investigated relevance judgments separately.

Evidence: [Python pool](results/pool-py-dev.txt), [TypeScript pool](results/pool-ts-dev.txt), [precondition](results/order-precondition.txt), [gate](results/order-gate-dev.txt), and scripts. Pool traces asserted scorer equality and reproduced baseline-v13/typescript-v10 hashes (15/15 and 26/26).

## Reproduce

Same corpora as graph-v2; private on-disk `TMPDIR`; one job under `systemd-run --user --scope -p MemoryMax=3G -p MemorySwapMax=0 -p CPUQuota=200%`.

```sh
S=docs/phase-6/graph-v3/scripts
uv run python $S/pool_trace.py py py-dev.json # also ts ts-dev.json
python $S/pool_summary.py py-dev.json
python $S/order_precondition.py py-dev.json ts-dev.json
uv run python $S/order_arm.py py dev B OUT_B # also R; ts likewise
python $S/order_gate.py dev py=OUT_B,OUT_R ts=TS_B,TS_R
```
