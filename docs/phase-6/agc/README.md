# AGC: graph and tiered context rejected on cost

Historical trial, 2026-10-25. **REJECT all arms** under the [preregistered rule](preregistration.md), frozen in [results/freeze.txt](results/freeze.txt). No product/API changes. Follow-ups: [v2](../agc-v2/README.md), [v3](../agc-v3/README.md), [v4](../agc-v4/README.md); their different task sets do not overwrite this verdict.

Reported setup: OpenCode 2.1.4, `openai/gpt-6-luna`, 12 hand-labelled Python tasks (flask/requests/pytest), four arms, two repetitions; 96/96 scored runs completed, checkouts unchanged. B = grep/read; G = graph; T = tiered views; AGC = both.

| Arm | Macro F1 | False claims/run | Tokens ×B | OXIDE use |
| --- | ---: | ---: | ---: | ---: |
| B | 0.918 | 1.08 | 1.00 | — |
| G | 0.944 | 0.54 | 1.80 | 7/24 |
| T | 0.875 | 0.88 | 1.85 | 19/24 |
| AGC | 0.940 | 0.33 | 2.09 | 17/24 |

All failed efficiency (required token ratio ≤0.85); G also adoption, T correctness. AGC improved chain F1 0.74→0.95 descriptively, but all F1 confidence intervals included zero. Code-mode prompt overhead explained much, not all, of the cost. **Correction from v2:** MCP schemas were absent at step 1 because OpenCode did not wait for connection, not because direct MCP was impossible.

Graph-navigation reassessment kept its FAIL: original completeness sensitivity 8/11 (0.73), specificity 6/13 (0.46). A grep residual caught 11/11 incomplete targets (CI 0.74–1.00), specificity 8/13; it was an evaluation aid, not proof of completeness. Of 44 missed sites, 36 were unknown receiver calls, seven values and one decorator.

Evidence: [report](results/report.json), [runs](results/runs.json), [reassessment](results/reassess.json), tasks, freeze and scripts. Raw sessions remain external at `~/Projects/oxide-eval-data/oxide-agc-eval/runs/`. One model/client, small Python repositories, no edits, single-author truth and token-price/cached-token caveats limit generalization.

## Reproduce

```sh
cd docs/phase-6/agc/scripts
python3 reassess.py
uv run --project ../../../.. python trial.py check
uv run --project ../../../.. python trial.py dev
uv run --project ../../../.. python trial.py run 1 2
uv run --project ../../../.. python trial.py run 2 2
uv run --project ../../../.. python trial.py verify
uv run --project ../../../.. python trial.py grade
python3 analyze.py
```

Requires the external graph-navigation store and structural-trial checkouts. Live runs require fresh authorization; spent tasks are replay evidence, not fresh confirmation.
