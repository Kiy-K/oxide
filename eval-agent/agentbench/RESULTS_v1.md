# Phase 0 pilot results

36 runs captured (6 tasks x 2 conditions).

> **Headline finding: the agent never called the OXIDE MCP tool.** Across all 18 "oxide"-condition runs, `oxide_calls` is 0 -- the tool was available (verified working in a separate smoke test) but gpt-5.6-luna via Codex never chose to use it on any of these 6 tasks, preferring its native shell tools (`rg`, `cat`, `sed`) throughout. Every delta below is therefore run-to-run noise from re-running the same underlying agent behavior twice, **not an effect of OXIDE** -- this pilot did not exercise OXIDE's value proposition at all under this exact setup. See Methodology problems.

## Per-task, all runs

### flask-propagate-exceptions (locate_impl_from_behavior)

| condition | rep | quality (acc+comp/2) | grounded | input_tokens | tool_calls | oxide_calls | files | wall_s | error |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 0 | 4.5 | True | 89334 | 2 | 0 | 3 | 30.1 | False |
| baseline | 1 | 4.5 | True | 88457 | 3 | 0 | 2 | 30.6 | False |
| baseline | 2 | 4.5 | True | 194701 | 3 | 0 | 2 | 33.0 | False |
| oxide | 0 | 4.5 | True | 67336 | 2 | 0 | 3 | 24.3 | False |
| oxide | 1 | 5.0 | True | 113974 | 2 | 0 | 2 | 28.5 | False |
| oxide | 2 | 5.0 | True | 68661 | 2 | 0 | 2 | 25.3 | False |

### flask-propagation-test (find_tests_constraining_behavior)

| condition | rep | quality (acc+comp/2) | grounded | input_tokens | tool_calls | oxide_calls | files | wall_s | error |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 0 | 5.0 | True | 94938 | 3 | 0 | 2 | 31.6 | False |
| baseline | 1 | 5.0 | True | 93918 | 2 | 0 | 1 | 27.3 | False |
| baseline | 2 | 4.5 | True | 118302 | 2 | 0 | 1 | 27.3 | False |
| oxide | 0 | 5.0 | True | 106090 | 2 | 0 | 1 | 23.3 | False |
| oxide | 1 | 5.0 | True | 153407 | 4 | 0 | 3 | 35.5 | False |
| oxide | 2 | 5.0 | True | 190278 | 4 | 0 | 1 | 33.6 | False |

### seaborn-lineplot-call-flow (trace_call_flow)

| condition | rep | quality (acc+comp/2) | grounded | input_tokens | tool_calls | oxide_calls | files | wall_s | error |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 0 | 3.5 | True | 342739 | 9 | 0 | 5 | 73.3 | False |
| baseline | 1 | 3.5 | True | 321183 | 8 | 0 | 7 | 69.2 | False |
| baseline | 2 | 4.0 | True | 342836 | 8 | 0 | 5 | 77.3 | False |
| oxide | 0 | 3.5 | True | 489609 | 9 | 0 | 4 | 74.1 | False |
| oxide | 1 | 4.5 | True | 368168 | 9 | 0 | 7 | 82.7 | False |
| oxide | 2 | 4.5 | True | 241361 | 5 | 0 | 5 | 69.3 | False |

### darkreader-devtools-storage-implementors (find_implementors_of_interface)

| condition | rep | quality (acc+comp/2) | grounded | input_tokens | tool_calls | oxide_calls | files | wall_s | error |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 0 | 4.5 | True | 136076 | 3 | 0 | 1 | 33.7 | False |
| baseline | 1 | 5.0 | True | 137864 | 3 | 0 | 2 | 33.4 | False |
| baseline | 2 | 4.5 | True | 135294 | 4 | 0 | 2 | 46.0 | False |
| oxide | 0 | 5.0 | True | 139054 | 4 | 0 | 2 | 39.7 | False |
| oxide | 1 | 5.0 | True | 193938 | 5 | 0 | 5 | 55.1 | False |
| oxide | 2 | 5.0 | True | 133694 | 4 | 0 | 2 | 38.3 | False |

### tailwindcss-variants-config-to-impl (config_to_implementation)

| condition | rep | quality (acc+comp/2) | grounded | input_tokens | tool_calls | oxide_calls | files | wall_s | error |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 0 | 5.0 | True | 237540 | 6 | 0 | 21 | 79.9 | False |
| baseline | 1 | 3.5 | False | 216930 | 5 | 0 | 16 | 70.5 | False |
| baseline | 2 | 4.0 | True | 216247 | 5 | 0 | 21 | 62.9 | False |
| oxide | 0 | 2.0 | False | 222733 | 5 | 0 | 17 | 60.2 | False |
| oxide | 1 | 4.0 | True | 267075 | 6 | 0 | 18 | 72.7 | False |
| oxide | 2 | 4.5 | True | 349174 | 8 | 0 | 18 | 93.7 | False |

### tailwindcss-escape-classname-blast-radius (estimate_blast_radius)

| condition | rep | quality (acc+comp/2) | grounded | input_tokens | tool_calls | oxide_calls | files | wall_s | error |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 0 | 2.5 | False | 212905 | 6 | 0 | 7 | 78.1 | False |
| baseline | 1 | 3.5 | False | 201189 | 5 | 0 | 8 | 76.9 | False |
| baseline | 2 | 2.5 | False | 327886 | 10 | 0 | 8 | 79.3 | False |
| oxide | 0 | 2.0 | False | 174351 | 5 | 0 | 7 | 69.7 | False |
| oxide | 1 | 2.5 | False | 122761 | 4 | 0 | 2 | 50.2 | False |
| oxide | 2 | 3.5 | False | 255961 | 6 | 0 | 8 | 70.3 | False |

## Paired per-task median deltas (oxide - baseline)

| task | quality | input_tokens | tool_calls_total | unique_files_inspected | wall_s |
|---|---|---|---|---|---|
| flask-propagate-exceptions | +0.5 | -20673.0 | -1.0 | +0.0 | -5.3 |
| flask-propagation-test | +0.0 | +58469.0 | +2.0 | +0.0 | +6.3 |
| seaborn-lineplot-call-flow | +1.0 | +25429.0 | +1.0 | +0.0 | +0.8 |
| darkreader-devtools-storage-implementors | +0.5 | +2978.0 | +1.0 | +0.0 | +6.0 |
| tailwindcss-variants-config-to-impl | +0.0 | +50145.0 | +1.0 | -3.0 | +2.2 |
| tailwindcss-escape-classname-blast-radius | +0.0 | -38554.0 | -1.0 | -1.0 | -8.4 |

## Overall (median of per-task deltas)

**Regressions to look at first:** input_tokens, tool_calls_total, wall_s

| metric | median delta (oxide - baseline) |
|---|---|
| quality | +0.25 |
| input_tokens | +14203.50 |
| tool_calls_total | +1.00 |
| unique_files_inspected | +0.00 |
| wall_s | +1.50 |

## Indexing cost (one-time, excluded from agent-time comparisons above)

| repo | wall_s | peak_rss_kb | index_db_bytes | symbols |
|---|---|---|---|---|
| tailwindlabs/tailwindcss | 32.4 | 25720 | 1830912 | 267 |

_Repos not listed above were already indexed from prior OXIDE eval work on this machine -- their cold-index cost was not re-measured (would require deleting and rebuilding a perfectly good index for no benefit but a number)._

## Amortized end-to-end view (indexing cost spread over N tasks)

| tasks | one-time index cost | + agent time (median oxide run) | amortized index_s/task |
|---|---|---|---|
| 1 | 32s | 53s | 32.4s |
| 5 | 32s | 53s | 6.5s |
| 20 | 32s | 53s | 1.6s |

## Methodology problems discovered

- **The core comparison never happened.** OXIDE was available but unused in every oxide-condition run (see headline finding above) -- these 6 tasks, on well-known/memorized open-source code, apparently didn't read as needing discovery tools to this model via Codex. Candidate fixes for a next pass: tasks against less-memorized/newer/larger repos where the model can't answer from pretraining alone, an explicit mention in the system/task framing that a code-search tool is available (still without naming or forcing OXIDE specifically), or trying a model/harness combination less biased toward its own native shell tools.
- Run order was NOT randomized/interleaved as the brief specified -- `run_pilot.py` runs all baseline reps then all oxide reps per task, sequentially. A time-of-day or API-load effect could confound condition with when it ran; a future pass should interleave.
- "Unique files inspected" under Codex is a regex heuristic over raw shell command strings (Codex has no structured read-file tool like opencode's), so it under/over-counts relative to a true file-access log.
- Cold index time/RSS was only measured for tailwindcss (freshly indexed); flask/seaborn/darkreader were already indexed from prior OXIDE eval work, so their cold-start cost is not in this report.
- Several tasks are well-known open-source code (Flask's exception handling, seaborn's plot pipeline) that a large model may answer correctly from pretraining alone without reading the repo at all -- low tool-call counts on some baseline runs may reflect memorization, not efficient comprehension. A harder future pilot should weight toward less-memorized/newer code.
- No superiority claim is made from this pilot (6 tasks, single model, single machine) -- see the brief.
