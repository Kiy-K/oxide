# Phase 2.1 Results

## Screen actually run

The primary screen contains 18 one-repetition runs: six pinned tasks
(`A2,A3,B1,B2,C2,C3`) × three conditions. A separate three-run confirmation
used OpenCode + `nemotron-3-ultra-free` on A2. A separate three-run edit screen
used OpenCode + `ling-3.0-flash-fin-free` on C1. A separate six-run scale screen
covered seaborn (larger Python) and deepseek-harness (medium TypeScript).
This is a cost-bounded screen, not the planned 2–3 repetition study.

The first A1 smoke attempt (three runs) hit the Clinepass monthly quota before
agent calls and is infrastructure failure. It is retained in
`raw/aborted-screen/` and excluded from behavioral counts.

## Conditions

- A: native tools only
- B: `context`/`search` available, instructions removed
- C: same tools plus frozen compact instructions

## Observed call metrics

| screen | A | B | C |
|---|---:|---:|---:|
| primary 6 tasks | 6 | 6 | 6 |
| mean OXIDE context calls | 0 | 0 | 0 |
| mean OXIDE search calls | 0 | 0 | 0 |
| mean native search calls | 2.3 | 2.0 | 2.2 |
| mean reads | 3.8 | 3.0 | 3.5 |
| mean total calls | 7.2 | 6.5 | 6.7 |
| confirmation A2 (Nemotron) OXIDE calls | 0 | 0 | 0 |
| edit C1 total calls | 1 | 1 | 3 |

Raw rows are authoritative in `results.jsonl`, `nemotron.jsonl`, and
`edit.jsonl`.

## Outcome and utilization adjudication

- A2, A3, B1, B2, C2, and C3 completed with status `ok` under all three
  Ling runs, but the model used native exploration in every condition.
- The Nemotron A2 confirmation also used native grep/read in all conditions.
- C1: A and B read the exact file without editing; C performed the requested
  minimal rename edit and reread the file. No test command was run by the
  agent, so coding correctness beyond the logged edit is unverified.
- Scale screen A5 and A6 timed out at 30s after substantial native exploration.
  A6/B made one `context` call; other scale runs made zero OXIDE calls.
- Retrieved-and-used OXIDE evidence: one A6/B run (context evidence was
  followed by native reads). Retrieved-and-ignored: none observed. Not
  retrieved: all other rows.

## Activation and tool-selection findings

For this screen, appropriate activation was not demonstrated by the Ling
model: OXIDE activation was zero in A/B/C, including unfamiliar tasks. That is
evidence of model/client selection behavior, not evidence that the tools are
unavailable; the A6/B run proves a real call occurred. The Nemotron
confirmation independently showed the same native-tool preference on A2.

The known-file controls behaved correctly: C2 and C3 made one direct read in
all conditions; C1 only made an extra reread after its C edit. No admin/status
attempts or search→context redundancy occurred in this screen.

Phase 2's earlier authenticated/available OpenCode screen remains the positive
control: compact instructions produced a single context call, while
no-instruction use produced redundant search→context. Together with the
Phase 2.1 screen, this makes the instruction effect directional and
model-sensitive, not general.

## Metadata utilization

The small behavioral sample does not justify deleting response fields. The
observed classification is:

| field group | classification | evidence |
|---|---|---|
| `file`, line range, symbol identity, snippet | observably useful | successful Phase 2/2.1 navigation and follow-up reads cite paths, lines, and symbols |
| `kind`, `language`, `role`, `score`, `reasons[]` | possibly useful | supports ranking/provenance interpretation, but not independently required by the sampled model |
| `est_tokens`, `budget_tokens` / per-result accounting | apparently unused in this sample | no logged agent decision depended on these values |

“Apparently unused” is a sample observation, not a removal recommendation.
Keep the frozen service DTO and defer any agent projection DTO until repeated
authenticated runs expose enough packs for a stronger utilization study.


## Success and evidence limits

No task-specific automated acceptance test was executed by the runner after
agent turns; navigation outcomes are judged from saved final text/tool logs.
The C1 edit log proves an edit occurred but the runner deletes temporary
workspaces, so no patch/test artifact is available. This is a harness
limitation and a follow-up for Phase 2.2, not a coding success claim.

## Failure attribution

| class | observations |
|---|---|
| AGENT ACTIVATION | most unfamiliar tasks: zero OXIDE calls |
| TOOL SELECTION | native grep/glob/read preferred even with C instructions |
| RETRIEVAL | no isolated retrieval failure established |
| ALLOCATION | no isolated allocation failure established |
| UTILIZATION | one retrieved context was followed by native reads; no ignored pack proven |
| FRESHNESS | no freshness failure |
| TRANSPORT | no protocol failure; Claude/Codex auth blocked behavioral runs |
| CODING/REASONING | not established by navigation tasks |
| VERIFICATION | C1 did not run tests; A5/A6 timed out |

## Search hypothesis

`search` remains justified but weakly revalidated. The screen produced no
search calls, so it supplies no new positive evidence. Phase 2's
context→search caller follow-up remains the only observed distinct utility.
Do not remove `search` without a controlled one-tool arm.
