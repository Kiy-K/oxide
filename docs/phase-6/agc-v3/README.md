# AGC v3: preregistered PROMOTE G, with guidance caveats

Historical trial, not a production decision. [Preregistration](preregistration.md), [freeze](results/freeze.txt). Seven rule-selected dispatch/two-hop impact tasks × four arms × three reps = 84 scored runs; OpenCode 2.1.4 / `openai/gpt-6-luna`. No technical failures, all schemas present; forced-use compliance 21/21.

| Arm | Success | F1 | False claims/run | CPS ×B (95% CI) | Organic use |
| --- | ---: | ---: | ---: | --- | ---: |
| B grep/read | 0.571 | 0.841 | 2.05 | 1 | — |
| D direct tool | 0.762 | 0.894 | 1.38 | 0.58 (0.36–0.95) | 14/21 |
| G D + guidance | 0.667 | 0.854 | 1.90 | 0.78 (0.48–1.23) | 20/21 |
| F forced relation | 0.857 | 0.932 | 0.67 | 0.58 (0.30–0.94) | forced |

The frozen U/A/P gates passed: forced use demonstrated utility, guidance raised adoption ≥0.20, and G met quality/cost thresholds. **PROMOTE G allowed proposing an ADR, not shipping a tool.** AGC v1/v2 remain rejected on their easier tasks; [v4](../agc-v4/README.md) did not confirm general exposure.

Guidance raised use but worsened outcomes versus D. It discouraged verification and led to third-hop callers reported as second-hop. G's CPS interval crossed parity and its false-claim gate passed narrowly. D1 wording admitted a transitive interpretation; lenient regrading and dropping D1 kept F≥D≥G but were post hoc. Hand-labelled truth aligned with call-graph constructs; seven tasks, two Python repositories, one model/client and proxy costs limit the claim. Two easy tasks saturated all arms.

Evidence: [report](results/report.json), [runs](results/runs.json), [stage 1](results/stage1.json), tasks, selection, freeze and scripts. Raw data: `~/Projects/oxide-eval-data/oxide-agc3-eval`. The harness added a two-second settle to resolve a schema startup race, then checked every scored server run.

## Reproduce

From `scripts/` with the external stores/checkouts and authorized agent setup:

```sh
python select_tasks.py
python trial.py check
python trial.py run REP 2 # three repetitions under systemd-run caps
python trial.py verify && python trial.py grade
python3 analyze.py invalid
python3 analyze.py stage1
python3 analyze.py
python3 sensitivity.py # descriptive, not preregistered
```
