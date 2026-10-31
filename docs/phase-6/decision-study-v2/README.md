# Decision Study v2: quality passed, operationally INCONCLUSIVE

Historical study, 2026-10-24; [preregistration](preregistration.md), scripts frozen at `96356b7`. **No integration:** held-out H1–H6 passed, H7 availability failed. The 29 held-out tasks are consumed, not reusable as fresh confirmation.

Policy: heuristic admission; JEV `jev-1.14.0`, `ds1-compact-v1`, digest-ordered rendering; admitted values `0.4 + 0.59p`; whole-request fallback on any missing judgment; 60 s transport kill, no retries. The 2.0 s product deadline was reported against, not enforced here; [v3](../decision-study-v3/README.md) corrected that.

Reported LR@4096:

| Split | Heuristic | Random order | Primary | No-rank | Repeat | Reversed |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Dev (15) | 0.659 | 0.583 | 0.783 | 0.783 | 0.783 | 0.810 |
| Held-out (29) | 0.235 | 0.137 | 0.360 | 0.387 | 0.423 | 0.449 |

Held-out primary Δ +0.125 (task-bootstrap 95% CI +0.054…+0.206), 12 tasks up, none down at any budget. But 10/29 primary calls returned HTTP 529 `system_overloaded` (limit ≤10%); control failures were 10/6/3, so their higher recall is not an equal-availability comparison. Failed requests reproduced production exactly. Post-hoc answered-only gains are not a new verdict. Dev had small-budget task regressions despite positive macro results.

Stability investigation: small judgment noise passes through discontinuous greedy packing, causing whole-task recall jumps. The v1 ±0.010 macro tolerance was below one-task resolution on 15 tasks; v2 preregistered gain replication with ±0.030 drift instead. This was settled before new calls, not a retroactive v1 pass.

Costs: 176 requests, $0.121; ~15k billed tokens/~$0.0006 per request; median 1.0 s, p95 1.6 s, max 2.4 s. Evidence: [dev gate](results/gate-dev.txt), [held-out gate](results/gate-heldout.txt), [stability](results/stability-dev.txt), [post-hoc failures](results/posthoc-heldout-failures.txt), scripts. No training/integration justified by this operationally inconclusive result.

## Reproduce

Same corpora/caps/cache setup as [v1](../decision-study-v1/README.md). Requests/raw responses remain external in `$D` (source-bearing).

```sh
S=docs/phase-6/decision-study-v2/scripts
D=~/Projects/oxide-eval-data/oxide-decision-study-v2
V1=~/Projects/oxide-eval-data/oxide-decision-study-v1/dev-controls/judgments
uv run --with tiktoken python $S/harness.py $D/stability compact=$V1/compact.json \
  compact-repeat=$V1/compact-repeat.json compact-reversed=$V1/compact-reversed.json \
  compact-norank=$V1/compact-norank.json --stability
python $S/stability.py $V1 $D/stability
uv run --with tiktoken python $S/harness.py $D/dev
# Each live arm needs explicit authorization and TYPESAFE_API_KEY:
uv run --with tiktoken python $S/judge.py $D/dev compact --authorize <corpora> --ledger $D/ledger.json
uv run --with tiktoken python $S/harness.py $D/dev-scored compact=$D/dev/judgments/compact.json ...
python $S/verdict.py $D/dev-scored
# Historical held-out path: --split heldout --gate .../results/gate-dev.txt
```
