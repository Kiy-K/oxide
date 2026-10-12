## dev: 9 tasks, reps [1, 2], 90 runs

Cells: mean (median); resolved and coverage are means.

| variant | runs | resolved | input_tokens | uncached_input_tokens | output_tokens | tool_calls | wall_s | inspected_files | gold_file_coverage | unnecessary_files | oxide_prompt_units | oxide_calls | timeouts | ungraded |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A | 18 | 0.556 | 187036 (137470) | 26197 (25136) | 2225 (1706) | 8 (8) | 44 (33) | 3 (2) | 0.726 | 1 (0) | 0 (0) | 0 (0) | 0 | 0 |
| B | 18 | 0.500 | 169460 (121791) | 24209 (21626) | 1773 (1380) | 7 (6) | 41 (30) | 3 (2) | 0.774 | 1 (1) | 4016 (4073) | 0 (0) | 0 | 0 |
| C | 18 | 0.556 | 164307 (138629) | 21886 (20842) | 1944 (1566) | 8 (6) | 41 (37) | 3 (3) | 0.745 | 2 (1) | 1470 (1521) | 0 (0) | 0 | 0 |
| D | 18 | 0.556 | 165161 (178098) | 24275 (24476) | 1952 (1642) | 7 (8) | 46 (35) | 3 (2) | 0.753 | 2 (1) | 1470 (1521) | 0 (0) | 0 | 0 |
| E | 18 | 0.611 | 162097 (130304) | 21923 (21446) | 2170 (1895) | 7 (7) | 45 (41) | 4 (3) | 0.819 | 2 (1) | 1470 (1521) | 0 (0) | 0 | 0 |

Paired by task, 95% bootstrap CI over tasks (success: difference; others: geometric-mean ratio):

| pair | success diff | input tokens | tool calls | wall time |
|---|---|---|---|---|
| B-A (n=9) | -0.056 [-0.167, +0.000] | 0.86 [0.66, 1.14] | 0.77 [0.60, 1.00] | 0.84 [0.64, 1.13] |
| C-A (n=9) | +0.000 [+0.000, +0.000] | 0.87 [0.73, 1.05] | 0.87 [0.73, 1.03] | 0.95 [0.74, 1.22] |
| D-A (n=9) | +0.000 [+0.000, +0.000] | 0.90 [0.72, 1.14] | 0.84 [0.66, 1.05] | 1.03 [0.78, 1.37] |
| C-B (n=9) | +0.056 [+0.000, +0.167] | 1.01 [0.92, 1.12] | 1.13 [1.03, 1.24] | 1.13 [0.89, 1.41] |
| D-B (n=9) | +0.056 [+0.000, +0.167] | 1.05 [0.86, 1.25] | 1.09 [0.92, 1.28] | 1.22 [0.97, 1.50] |
| D-C (n=9) | +0.000 [+0.000, +0.000] | 1.04 [0.88, 1.21] | 0.97 [0.85, 1.13] | 1.08 [0.89, 1.32] |
| E-A (n=9) | +0.056 [+0.000, +0.167] | 0.87 [0.71, 1.06] | 0.85 [0.68, 1.03] | 1.03 [0.84, 1.27] |
| E-B (n=9) | +0.111 [+0.000, +0.278] | 1.01 [0.87, 1.17] | 1.11 [0.96, 1.27] | 1.22 [0.91, 1.59] |
| E-C (n=9) | +0.056 [+0.000, +0.167] | 1.00 [0.89, 1.12] | 0.99 [0.88, 1.10] | 1.08 [0.95, 1.21] |

Per task (1 resolved, 0 not, ? ungraded; one character per repetition):

| task | A | B | C | D | E |
|---|---|---|---|---|---|
| sympy__sympy-18189 | 11 | 11 | 11 | 11 | 11 |
| sympy__sympy-13757 | 11 | 11 | 11 | 11 | 11 |
| sympy__sympy-12481 | 11 | 11 | 11 | 11 | 11 |
| sphinx-doc__sphinx-9320 | 11 | 11 | 11 | 11 | 11 |
| sympy__sympy-15599 | 00 | 00 | 00 | 00 | 00 |
| sympy__sympy-20438 | 00 | 00 | 00 | 00 | 00 |
| sphinx-doc__sphinx-11510 | 00 | 00 | 00 | 00 | 00 |
| sphinx-doc__sphinx-10673 | 10 | 00 | 01 | 10 | 01 |
| sphinx-doc__sphinx-8056 | 01 | 10 | 10 | 01 | 11 |

Gates (C and D): **INCONCLUSIVE**

- C: superior=False non_inferior=True efficient=False reliable=True
- D: superior=False non_inferior=True efficient=False reliable=True
- A >= C and A >= D on success: OXIDE's navigation value has not been demonstrated
