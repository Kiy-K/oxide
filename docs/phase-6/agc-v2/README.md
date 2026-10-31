# AGC v2: reject on adoption; direct MCP removes wrapper cost

Historical trial; [preregistration](preregistration.md), [freeze](results/freeze.txt). **REJECT both AGC arms.** OpenCode 2.1.4 / `openai/gpt-6-luna`; 12 new tasks × four arms × three reps = 144 scored runs, no technical failures.

| Arm | Success | F1 | False claims/run | CPS ×B | Tokens ×B | Heavy-run OXIDE use |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| B grep/read | 0.944 | 0.980 | 0.028 | 1 | 1 | — |
| Bcm code mode | 0.917 | 0.966 | 0.056 | 1.50 | 2.92 | — |
| Ad direct MCP | 1.000 | 0.995 | 0.000 | 0.91 | 1.09 | 5/24 |
| Acm code-mode MCP | 0.972 | 0.993 | 0.028 | 1.31 | 2.55 | 1/24 |

Ad and Acm missed ≥50% heavy-run adoption; Acm also failed cost. Ad's CPS CI 0.78–1.04 includes parity, 29/36 runs never used OXIDE, and its advantage on B's failed task used no tool: not evidence of utility. The baseline already solved 34/36 runs. Direct MCP's first-step schema added 287 tokens and startup ~2.5 s; code mode alone cost 1.50× CPS.

**Correction to v1:** OpenCode supports native MCP after waiting for `/api/mcp` connection. The earlier missing-schema observation was a startup race. The useful follow-up shape was one direct tool, no code mode, on tasks where grep actually fails—not a production recommendation. [v3](../agc-v3/README.md) tested harder structural tasks.

Evidence: [report](results/report.json), [runs](results/runs.json), tasks, freeze and scripts. One client/model, three Python repositories, 12 tasks; only eight invoking runs; token-price proxy, not billed cost.

## Reproduce

From `scripts/`, using external `~/Projects/oxide-eval-data/oxide-agc2-eval` and the prior trial's stores/checkouts:

```sh
python trial.py check
python trial.py run REP 2 # three repetitions, resource-capped by harness
python trial.py verify
python trial.py grade
python3 analyze.py
```
