# Compact JSON output integrity and truncation: preregistration (2026-10-21)

Frozen before any agent call of this study. `$DATA/prereg.sha256` records its digest.
`$DATA` is `~/Projects/oxide-eval-data/oxide-integrity-eval`.

## Question

Can agents recognize incomplete or deliberately limited OXIDE results better, while
Compact JSON keeps its size, correctness and compatibility? Four failure sources are kept
apart and measured separately:

1. **Generation:** OXIDE emits an incomplete response.
2. **Client handling:** an MCP client clips or reshapes what OXIDE emitted.
3. **Deliberate limits:** the server's `limit`, `budget` and hint caps leave matches out.
   This is correct behavior, and the result already declares it.
4. **Interpretation:** the agent misreads a complete, limited or clipped text.

The TOON study (REJECT, `../toon-serialization/`) motivates this one. Compact JSON cut at an
entry boundary was called complete in 4/4 calls. Those cuts were synthetic.

## What is known before scoring (read in code, or probed once with no scoring)

- `mode=search` text is FastMCP's compact JSON of `SearchResult`. Its key order is
  `snapshot, query, entries, lexical, hints`. `lexical.returned` and `lexical.truncated`
  are exact: the store fetches `limit + 1` rows. So the server-limit metadata already
  exists, but it comes **after** the entries.
- `mode=context` text is a summary line (`used/budget`, item count, omitted count) plus the
  payload. The structured content is `asdict(ContextBundle)`.
- OpenCode 2.0.32 clips every tool output at 2,000 lines or 51,200 bytes. It keeps whole
  lines only, then appends `[showing lines 1-N of M; full output saved to …]`. A
  single-line text over the byte limit is therefore delivered as zero lines plus that marker.
- OpenCode's default "code mode" calls MCP tools from model-written JS. The model then sees
  the returned value pretty-printed (structured content, not OXIDE's text). One probe with
  limit 100 on requests showed 1,581 of 1,907 lines; the tail status was lost.
- In the direct path (`codemode: false`), the model sees OXIDE's text. One probe called a
  complete 44 KB result "not complete" because of `"truncated": true`.

## Arms (`scripts/render.py`)

Search: **C** production compact JSON. **CM** is C with leading `entries_returned` and
`more_available`. These are additive and computed from existing fields. `more_available`
means the lexical limit was hit or a symbol hint was capped. **CL** is C with a line break
around each entry; it is still valid JSON. **P** is `indent=2`. **E** is C between a header
line and an end line. The header reads `# oxide search: N entries; more matches exist
beyond the result limit` (or `no more matches`), followed by `; the complete output ends
with the line "# end of oxide search: …"`. The end line is
`# end of oxide search: N entries`.

Context: **C** production. **E** is C between `# the complete output ends with the line
"# end of oxide context: …"` and `# end of oxide context: N items`. Without the
announcement, a reader of a clipped text never learns that an end line is missing. (A
first draft without it was found to be identical to C in every cut condition, and was
corrected before freezing.)

Promotable candidates: **CM** for search and **E** for context. P (size) and E for search
(its text is no longer JSON) are comparators, never promoted. CL only enters the
deterministic and live stages. Under the controlled byte cuts its text is identical in
kind to C, and its only mechanism is line-granular clipping.

## Outputs and conditions (`scripts/build.py`)

The outputs come from every real dev task of both ContextBench suites: requests ×6,
seaborn, flask, code-server ×2, darkreader ×2 and vscode ×14, so 26 tasks. An `exhaust`
case exists only where an identifier among the first 10 entries has 3–100 matches (15 of
26 tasks). The query is
the issue text and retrieval is production. The TOON edge repository adds Unicode paths
and special characters. Each cut sits at the same point of the value in every arm.

| condition | what it is | gold `cut` |
|---|---|---|
| search `full` | `limit=20`; the server's limit is usually hit | false |
| search `exhaust` | an identifier query with `limit` = its exact match count: exactly `limit` entries and no more matches (looks limited, is complete) | false |
| `cut_entry` | ends inside entry k's name (interrupted mid-string). In the edge repository it ends inside a multi-byte character (malformed UTF-8 → U+FFFD) | true |
| `cut_nested` | ends inside the nested `source.digest` of the first located entry ≥ k | true |
| `cut_boundary` | ends right after entry k−1's closing brace | true |
| `elided` | entries 3…n−4 replaced by `…[N characters omitted]…` | true |
| bundle `full` | budget 2,048: budget omissions, header-only `(header)` items (looks cut, is complete) | false |
| bundle `cut_line` | ends mid-line inside item j | true |
| bundle `cut_boundary` | ends right before item j's heading | true |
| `nav` | issue + search `full` or `cut_boundary`; choose up to 3 files | – |

k is drawn in [n/3, 2n/3] and j in [m/3, 2m/3], seeded per task.

## Questions and gold

Search: q1 cut (bool). q2 more results beyond the limit (true/false/null). The gold is
`more_available` if the arm's visible text still carries the status, else null. For C, P
and CL, the status is lost in every `cut_*`. For CM and E it is never lost. q3 complete
entries visible (int). q4 the source path of a named, uniquely-named visible entry.
Bundles: q1 cut. q2 omitted to fit the budget (the header always survives). q3 complete
`###` sections. q4 copy one visible source line exactly. Nav: gold files from the task;
scored by hit@3 and gold-file recall.

## Agent

OpenCode 2.0.32, `openai/gpt-6-luna`, no tools, isolated config, one turn, two
repetitions. This is the TOON study's harness (`agent.py`), reused. Billed prompt tokens
come from the session export. An invalid reply scores 0 on every question.

## Live MCP stage (`scripts/live.py`)

This stage uses real `oxide mcp` (C) or `variant_mcp.py` (the production server plus a
result-rewriting middleware) inside OpenCode, with its own isolated config and server. For
each of the 26 tasks, the agent is told to call `oxide_search` once with the issue text:
`mode=search, limit=100` and `mode=context, budget=32768`. These are the maxima, which
exercise client clipping. Direct path: search C, CM, CL and E; context C and E. Code mode:
search C and CM; context C and E. That is one call each. From the session export the
study records what the client delivered (bytes, clipped or not, marker), the agent's
answers (cut, more, count, first file) and billed tokens. This stage is descriptive. It
bounds what the README may claim about downstream clipping and gates nothing.

## Deterministic measurements (`scripts/measure.py`)

- A real MCP stdio client checks every arm for every output: text and structured content
  against the renderer and the API value.
- Size: tokens (o200k, cl100k), bytes, lines, and units, at the default and maximum limit
  and budget. Also the fraction over OpenCode's thresholds per arm and path.
- Render time and MCP round-trip latency, C vs candidates.
- The `oxide-units-v1` scope question: units against o200k on payloads and on JSON texts.

## Acceptance criteria (per mode: CM vs C for search, E vs C for context)

The bootstrap resamples tasks (10,000 resamples, seed 20261021), pairing arms within a
task.

- **R1 detection:** balanced accuracy of q1 rises by ≥ +0.10, with a 95 % CI lower bound
  > 0. Balanced accuracy is the mean of recall on cut conditions and specificity on
  complete conditions.
- **R2 server-limit reading:** the q2 accuracy difference is ≥ −0.02, with a CI lower
  bound ≥ −0.08.
- **Q1 task quality:** pooled q3 + q4 accuracy difference ≥ −0.03, with a CI lower bound
  ≥ −0.08.
- **Q2 navigation (search only):** hit and file-recall differences ≥ −0.05.
- **T tokens:** median o200k overhead on complete outputs ≤ +2 %, and median paired billed
  prompt-token overhead ≤ +2 %.
- **V validity:** ≤ 5 % invalid replies per arm.
- **K compatibility:** the candidate's structured content minus its added fields equals
  production, and it validates against the production output schema. Search text is JSON
  equal to the structured content. Context text contains the production text intact, after the announcing line. The
  extra render time has p95 ≤ 1 ms.

**Decision per mode:** PASS if every criterion holds. REJECT if the R1 difference is
< +0.05, the Q1 difference is < −0.08, or T or K fails. Otherwise INCONCLUSIVE.
**Overall:** PASS if any mode passes; only that mode's change may then be promoted, as a
separate commit. REJECT if both modes reject. Otherwise INCONCLUSIVE.

Recall, precision, the false completeness rate (cut called complete), the false alarm
rate, per-condition results and per-failure attribution are reported for every arm,
including the comparators.
