## heldout: 19 tasks, reps [1, 2, 3], 228 runs

Cells: mean (median); resolved and coverage are means.

| variant | runs | resolved | input_tokens | uncached_input_tokens | output_tokens | tool_calls | wall_s | inspected_files | gold_file_coverage | unnecessary_files | oxide_prompt_units | oxide_calls | timeouts | ungraded |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A | 57 | 0.667 | 92499 (74499) | 15811 (14277) | 1032 (756) | 5 (4) | 26 (21) | 2 (1) | 0.846 | 1 (0) | 0 (0) | 0 (0) | 0 | 0 |
| B | 57 | 0.667 | 114034 (75420) | 18066 (16866) | 1286 (684) | 5 (4) | 30 (22) | 2 (2) | 0.858 | 1 (1) | 4020 (4087) | 0 (0) | 0 | 0 |
| C | 57 | 0.649 | 89071 (69276) | 14624 (12478) | 1086 (645) | 5 (4) | 27 (21) | 2 (2) | 0.836 | 1 (1) | 1500 (1468) | 0 (0) | 0 | 0 |
| E | 57 | 0.684 | 105165 (76927) | 16535 (13709) | 1303 (730) | 5 (5) | 29 (21) | 2 (2) | 0.855 | 1 (1) | 1500 (1468) | 0 (0) | 0 | 0 |

Paired by task, 95% bootstrap CI over tasks (success: difference; others: geometric-mean ratio):

| pair | success diff | input tokens | tool calls | wall time |
|---|---|---|---|---|
| B-A (n=19) | +0.000 [-0.053, +0.053] | 1.18 [1.05, 1.34] | 0.96 [0.84, 1.10] | 1.12 [0.99, 1.28] |
| C-A (n=19) | -0.018 [-0.070, +0.035] | 0.97 [0.90, 1.05] | 0.89 [0.81, 0.98] | 1.04 [0.94, 1.16] |
| C-B (n=19) | -0.018 [-0.053, +0.000] | 0.82 [0.75, 0.90] | 0.93 [0.85, 1.01] | 0.93 [0.84, 1.03] |
| E-A (n=19) | +0.018 [+0.000, +0.053] | 1.10 [1.00, 1.21] | 0.98 [0.88, 1.09] | 1.06 [0.97, 1.16] |
| E-B (n=19) | +0.018 [+0.000, +0.053] | 0.93 [0.85, 1.01] | 1.02 [0.95, 1.11] | 0.95 [0.84, 1.06] |
| E-C (n=19) | +0.035 [+0.000, +0.088] | 1.12 [1.04, 1.24] | 1.10 [1.03, 1.20] | 1.02 [0.92, 1.13] |

Per task (1 resolved, 0 not, ? ungraded; one character per repetition):

| task | A | B | C | D | E |
|---|---|---|---|---|---|
| django__django-15104 | 111 | 111 | 111 | - | 111 |
| scikit-learn__scikit-learn-13439 | 111 | 111 | 111 | - | 111 |
| pydata__xarray-4966 | 111 | 111 | 111 | - | 111 |
| pydata__xarray-4687 | 000 | 000 | 000 | - | 000 |
| django__django-16662 | 111 | 111 | 111 | - | 111 |
| scikit-learn__scikit-learn-10844 | 111 | 111 | 111 | - | 111 |
| scikit-learn__scikit-learn-14629 | 000 | 000 | 000 | - | 000 |
| django__django-11603 | 111 | 111 | 111 | - | 111 |
| django__django-15569 | 111 | 111 | 111 | - | 111 |
| astropy__astropy-13236 | 000 | 000 | 000 | - | 000 |
| django__django-16136 | 111 | 111 | 111 | - | 111 |
| pydata__xarray-7229 | 000 | 000 | 000 | - | 000 |
| django__django-10880 | 111 | 111 | 111 | - | 111 |
| pytest-dev__pytest-10051 | 111 | 101 | 011 | - | 111 |
| pytest-dev__pytest-10081 | 111 | 111 | 110 | - | 111 |
| astropy__astropy-13977 | 000 | 000 | 000 | - | 000 |
| pytest-dev__pytest-7205 | 101 | 111 | 111 | - | 111 |
| psf__requests-6028 | 000 | 000 | 000 | - | 000 |
| pydata__xarray-4695 | 111 | 111 | 111 | - | 111 |

Gates (C and E): **INCONCLUSIVE**

- C: superior=False non_inferior=True efficient=False reliable=True
- E: superior=False non_inferior=True efficient=False reliable=True
