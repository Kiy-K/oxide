# AGC v4: STOP at stage 1; no new graph interface

Historical trial; [preregistration](preregistration.md), [freeze](results/freeze.txt). **STOP:** neither continuation signal passed. ADR-0013 stays Proposed; v1/v2 REJECT and v3 PROMOTE G remain unchanged.

Reported stage 1: eight fresh mixed tasks × four arms × one repetition = 32 scored runs; OpenCode / gpt-6-luna, no technical failures, all schemas present. B = grep/read; S = single `oxide_search`; T = search + context tools; F = single tool forced first.

| Arm | Successes | F1 | CPS ×B | Navigation use on structural runs |
| --- | ---: | ---: | ---: | ---: |
| B | 6/8 | 0.968 | 1 | — |
| S | 6/8 | 0.929 | 1.14 | 2/6 |
| T | 3/8 | 0.868 | 2.26 | 2/6 |
| F | 6/8 | 0.917 | 1.47 | 2/6 |

Adoption missed ≥0.50; forced-use utility did not beat B. F mostly chose search, so it forced the tool, not the graph—a design limitation. One repetition cannot rank single versus split tools; tasks were largely grep-solvable. Evidence: [stage1](results/stage1.json), [runs](results/runs.json), tasks and scripts.

Post-hoc v3 D/G analysis ([v3_dg.json](results/v3_dg.json)): D's success advantage was not significant (Fisher p=0.73); G cost 1.33× D CPS (CI 1.10–1.77), doubled graph calls and halved verification greps. Inferred lesson: avoid “call first, don't verify” guidance, not graph tools universally.

## NetworkX review

`530c6c5` versus `0381565`: [review script](../networkx-v1/scripts/review.py), [raw results](../networkx-v1/results/review.jsonl). Five repositories, three index repetitions, fresh interpreters. Snapshot/derivation and all 2,000 impact walks matched. Walk time unchanged within noise; Python indexing paid +10–64 ms (+1–13%) and ~11 MB peak RSS for NetworkX import. `import oxide` did not load it. The SCC projection and subclass closure were adopted; [NetworkX decision](../networkx-v1/README.md) records scope, not a ranking improvement.

## Reproduce

The frozen [protocol](preregistration.md) and `scripts/trial.py` / `scripts/analyze.py` define the stage-1 run and STOP rule. `scripts/v3_dg.py` reanalyzes prior runs; NetworkX reproduction is linked above. External sessions/stores and authorized OpenCode/model access are required; no additional runs were performed during consolidation.
