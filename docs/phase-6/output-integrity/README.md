# Compact JSON output integrity and truncation: results (2026-10-22)

**Verdict: PASS** (preregistered rule: both modes pass every criterion;
[preregistration.md](preregistration.md), frozen 2026-10-21T19:55:55+07:00 before any agent call,
sha256 `d23814e2…3f4d4e` in `$DATA/prereg.sha256`).

**Promoted: search CM only** (two leading fields, `entries_returned` and `more_available`, in
both the text and the structured content of `mode=search`; a separate commit). **Context E
passed but is not promoted.** The preregistration allows promotion and does not require it.
In the live stage E's marker never reached the model on OpenCode's default path (code
mode shows the structured content, not the text). On the direct path, C was already 26/26
correct. See [Recommendation](#recommendation).

Compact JSON stays the default, and so do MCP JSON-RPC, the structured content, the single
`oxide_search` tool, retrieval, provenance, budgets and the frozen baselines. TOON stays
rejected ([../toon-serialization/](../toon-serialization/README.md)).

`$DATA` is `~/Projects/oxide-eval-data/oxide-integrity-eval`. Unless a claim is labelled
*inferred*, it is measured by the scripts named in the text.

## Root cause: four sources, measured apart

| Source | Finding | Evidence |
|---|---|---|
| 1. OXIDE generation | **Never incomplete.** In 520/520 real stdio MCP round trips, the text and the structured content were byte-identical to the renderer and the API value. That covers 5 arms × 26 tasks × (search 20/100, context 2,048/32,768). | `measure.py mcp` → `results/mcp.json` |
| 2. Client handling | **The only source of truncation seen.** OpenCode 2.0.32 clips every tool output at 2,000 lines or 51,200 bytes. It keeps whole lines and appends `[showing lines 1-N of M; full output saved to …]`. Compact JSON is a single line, so 14/26 search100 texts arrived as **zero lines** plus the marker (live, direct path: 14/26 for C and CM). In the default code mode the model never sees OXIDE's text: OpenCode pretty-prints the structured content. That clipped 26/26 search100 results, and the status tail (`lexical`, `hints`) was lost in 26/26. | OpenCode bundle `ToolOutput.truncate`; `results/live.json` |
| 3. Deliberate limits | **Always declared, but at the tail.** Every task hit the search limit at 20 and at 100, and every bundle had omissions. `lexical.truncated` is exact (the store fetches `limit+1`), but it sits after `entries`. Any head-keeping clip removes it. | `results/mcp.json` (`server_limited` = 1.0) |
| 4. Interpretation | **Agents misread a lost tail as "complete".** When the text ended at an entry boundary with no marker, the agent called C complete in 54/54 calls. A pre-study probe also called a complete 44 KB result "not complete" because of `"truncated": true`, conflating the server limit with clipping. | `results/agent.json`, failures table below |

So the defect is not in OXIDE's output. The failure is that its completeness status is
placed where downstream clipping removes it, and that agents then read the clipped
remainder as whole.

## Arms

Same value in every arm. A cut is placed at the same point of that value in every arm
(`scripts/render.py`, `scripts/build.py`).

| Arm | Mode | Text |
|---|---|---|
| C | both | production (search: FastMCP compact JSON; context: summary line + payload) |
| **CM** | search | C with two leading fields `entries_returned`, `more_available` (also in structured content) |
| CL | search | C with a line break around each entry (still JSON); deterministic and live only |
| P | search | `indent=2` (comparator, not promotable) |
| E | search | C between a status header that announces an end line, and that end line (comparator: not JSON) |
| **E** | context | C after `# the complete output ends with the line "# end of oxide context: …"`, then `# end of oxide context: N items` |

## Controlled stage (1,908 graded calls)

OpenCode 2.0.32 ran `openai/gpt-6-luna` with no tools, one turn, two repetitions, over 26
real ContextBench dev tasks plus the TOON edge repository (27 bootstrap clusters). The
harness is the TOON study's own `agent.py`. 0 calls failed, 0 calls used a tool, and 1
reply was invalid (C, context).

### Criteria

| | Search: CM − C | Context: E − C | Rule |
|---|---|---|---|
| R1 detection (balanced acc.) | **+0.113** [0.100, 0.125] | **+0.130** [0.079, 0.181] | ≥ +0.10, CI low > 0 |
| R2 server-limit reading | 0.000 [0, 0] | +0.006 [0, 0.019] | ≥ −0.02, CI low ≥ −0.08 |
| Q1 task quality (q3+q4) | −0.002 [−0.023, 0.020] | +0.003 [−0.068, 0.068] | ≥ −0.03, CI low ≥ −0.08 |
| Q2 navigation hit / recall | −0.020 / −0.023 | – | ≥ −0.05 |
| T o200k / billed | +0.31 % / +0.29 % | +1.82 % / +1.40 % | ≤ +2 % each |
| V invalid | 0 / 396 | 0 / 162 | ≤ 5 % |
| K compatible, render p95 | yes, 0.37 ms | yes, 0.03 ms | yes, ≤ 1 ms |
| **Mode verdict** | **PASS** | **PASS** | |

95 % CIs come from a task-cluster bootstrap with 10,000 resamples and seed 20261021.

### Detection per arm (q1: "is this output cut?")

| Arm | Recall | Specificity | Precision | False completeness | False alarm |
|---|---|---|---|---|---|
| search C | 0.745 | 1.000 | 1.000 | 0.255 | 0.000 |
| search **CM** | **0.972** | 1.000 | 1.000 | **0.028** | 0.000 |
| search P | 0.745 | 1.000 | 1.000 | 0.255 | 0.000 |
| search E | 0.660 | 1.000 | 1.000 | 0.340 | 0.000 |
| context C | 0.444 | 0.944 | 0.941 | 0.546 | 0.056 |
| context **E** | **0.648** | 1.000 | 1.000 | **0.352** | 0.000 |

Per condition, q1 accuracy:

| Condition | search C | CM | P | E (search) | | context C | E |
|---|---|---|---|---|---|---|---|
| `full` | 1.00 | 1.00 | 1.00 | 1.00 | `full` | 0.94 | 1.00 |
| `exhaust` (complete, looks limited) | 1.00 | 1.00 | 1.00 | 1.00 | | | |
| `cut_entry` (mid-string / malformed UTF-8) | 1.00 | 1.00 | 1.00 | 1.00 | `cut_line` | 0.85 | 0.89 |
| `cut_nested` | 1.00 | 1.00 | 1.00 | 1.00 | | | |
| `cut_boundary` (valid-looking prefix) | **0.00** | **0.89** | 0.00 | 0.52 | `cut_boundary` | **0.04** | **0.41** |
| `elided` (client-style marker) | 1.00 | 1.00 | 1.00 | **0.12** | | | |

Two of these rows carry the result:

- **Search, `cut_boundary`:** the entire CM gain is here. With the status at the head, the
  agent compares `entries_returned` with the entries it can see. Interrupted strings,
  nested cuts and explicit client markers were detected by every arm.
- **Context, `cut_boundary`:** the only difference between C and E is the announced end
  line. A clip with no marker remains hard for both arms (E recall 0.41 there).

Search E is worse than C on `elided`. When the agent sees a header announcing the
complete result plus the end line, it calls the elided text complete (46 of 52 calls). An
end marker certifies "the tail arrived", not "nothing was removed in the middle". This is
why the end-marker design was not promoted for search.

### Failure attribution (all q1 errors)

| Arm | Errors |
|---|---|
| search C | `cut_boundary` called complete: 54 |
| search CM | `cut_boundary` called complete: 6 |
| search P | `cut_boundary` called complete: 54 |
| search E | `cut_boundary` called complete: 26; `elided` called complete: 46 |
| context C | `cut_boundary` called complete: 52; `cut_line` called complete: 7; no answer: 1; `full` called cut: 3 |
| context E | `cut_boundary` called complete: 32; `cut_line` called complete: 6 |

Every error is interpretation (source 4) on a client-style cut (source 2). There were no
false alarms on search `exhaust` (complete output that looks limited) in any arm.

### Other questions

- **q2, the server limit:** 1.00 in every search arm, 0.99 (C) and 1.00 (E) for context.
  When the status is visible, the agent reads it correctly. When it is lost, the gold is
  "unknown", and the agent said so.
- **q3 counts and q4 source:** these are unchanged across arms (search q3 0.81–0.82; q4
  ≥ 0.997). Context q3 (count of complete `###` sections) is low in both arms (0.34 / 0.36)
  and q4 is 0.49 / 0.48. That is a task-difficulty property and does not separate the arms.
- **Navigation (hit@3 / gold-file recall):** C 0.59/0.40, CM 0.57/0.38, P 0.63/0.43,
  E 0.61/0.42. These are within noise of each other. One task
  (`microsoft__vscode-135805`) has no verified gold file, since all gold is marked drift,
  and is excluded.

## Live MCP stage (260 calls, descriptive)

This stage ran real `oxide mcp` (C) or `variant_mcp.py` (the production server plus a
result-rewriting middleware) inside OpenCode 2.0.32, with an isolated config and server.
For each task the agent called `oxide_search` once at the maxima (search `limit=100`,
context `budget=32768`). Values are fractions of 26 calls. "Status seen" means the
completeness or limit status survived in what the client delivered.

| Path | Mode | Arm | Clipped | Zero lines | Status seen | `more` correct | Claimed complete when clipped | Prompt tokens (median) |
|---|---|---|---|---|---|---|---|---|
| code (default) | search | C | 1.00 | 0 | **0.00** | **0.46** | 0 | 42,103 |
| code (default) | search | **CM** | 1.00 | 0 | **1.00** | **1.00** | 0 | 41,735 |
| code | context | C | 0.46 | 0 | 0.69 | 0.85 | 0 | 39,859 |
| code | context | E | 0.42 | 0 | 0.69 | 0.85 | 0 | 40,555 |
| direct | search | C | 0.44 | 0.44 | 0.56 | 1.00 | 0.09 | 50,521 |
| direct | search | CM | 0.54 | 0.54 | 0.46 | 1.00 | 0 | 50,644 |
| direct | search | CL | 0.56 | 0 | 0.44 | 0.56 | 0.07 | 53,989 |
| direct | search | E | 0.54 | 0 | 1.00 | 1.00 | 0 | 45,322 |
| direct | context | C | 0.15 | 0 | 1.00 | 1.00 | 0 | 44,268 |
| direct | context | E | 0.15 | 0 | 1.00 | 1.00 | 0 | 43,561 |

Direct search C and CL ran 25 of 26 calls. In one task each, the agent called only
`execute` and never `oxide_search`, even after 4 attempts.

What the live stage shows:

- **CM is the only arm whose status survives OpenCode's default path.** Code mode
  pretty-prints the structured content, and OpenCode keeps the head. CM's fields are the
  first keys, so the agent read the server limit correctly in 26/26 calls, against 12/26
  for C.
- **No metadata survives a single-line text over 51,200 bytes on the direct path.** C and
  CM arrive as zero lines plus OpenCode's marker. The agent then saw the marker, said
  "cut" and re-queried. Only the multi-line arms (E, CL) keep a prefix there.
  - CM does not fix this, and no in-band field can (*inferred*: the whole line is dropped).
  - The marker itself made the agent correct, so metadata is not what protects this case.
- **Context E changed nothing live.** In code mode the model never sees the text, and on
  the direct path the summary header (line 1) already survived 26/26.
- 96 % of calls edited the query, although the prompt said to pass the issue text. Live
  gold therefore uses each call's actual arguments, re-run against the server. 35–100 % of
  calls also used other tools (`execute`, `list_mcp_resources`).

**What is not claimed:** metadata does not protect against arbitrary downstream clipping.
CM protects one measured case: a head-keeping clip of the JSON (OpenCode code mode, or any
byte or line cut that keeps the first line). The controlled `cut_boundary` condition
emulates that case without any marker. A client that drops the whole line, rewrites the
structured content, or removes the middle is not covered.

## Tokens and runtime

Median o200k overhead vs C on complete outputs (`measure.py sizes` → `results/sizes.json`):

| Output | CM | CL | P | E |
|---|---|---|---|---|
| search `limit=20` | +0.31 % | +0.60 % | +29.0 % | +1.17 % |
| search `limit=100` | +0.07 % | +0.65 % | +31.8 % | +0.26 % |
| context `budget=2048` | – | – | – | +1.82 % |
| context `budget=32768` | – | – | – | +0.48 % |

Max render p95 is ≤ 0.74 ms in every arm (C itself is 0.74 ms at search100). Median MCP
round trip is 0.09–0.12 s for search and 0.25–0.29 s for context, the same across arms.
Controlled-stage median agent latency is 6.9–7.5 s per call, also the same across arms.

## Compatibility

- **CM:** it is additive. Two keys are added in front, so the structured content minus
  those keys equals production. It validates against the production output schema, which
  does not forbid extra properties (52/52 MCP rows). The text is still compact JSON equal
  to the structured content. Clients that index by key are unaffected. A client that
  compares whole objects, or relies on key order, sees a change.
- **E (context):** it adds a first line in front of `# oxide context: …`. A reader that
  expects the summary on line 1 (one repository test does) would break. This is one more
  reason it was not promoted without a client that needs it.

## `oxide-units-v1` scope

Units per o200k token, as the median over outputs:

| Text | Units per o200k token |
|---|---|
| bundle payload | 1.50–1.53 (min 1.19) |
| compact JSON | 1.42–1.44 |
| context text | 1.47–1.52 |
| pretty JSON | 1.18–1.19 |

Why it varies (*measured on the estimator's formula, `words(len+3)//4 + newlines +
punctuation`*): the estimator ignores whitespace and charges every punctuation mark, so
its ratio to a real tokenizer depends on the format.

*Inferred* from its definition and its only use: this is the intended scope, not a defect.
`oxide-units-v1` is the payload budget unit, a deterministic, tokenizer-free bound for
packing source text. It was never meant to measure serialized JSON or to compare formats,
which is how the TOON study misused it. No accounting change is proposed. Any change
would be a separate, versioned `oxide-units-v2` decision.

## Recommendation

1. **Promote CM for `mode=search`** (done in a separate commit). It moves the server-limit
   status ahead of the entries, where head-keeping clips leave it. The controlled gain is
   +0.11 balanced accuracy (CI excludes 0), and OpenCode's default path read the limit
   correctly in 26/26 calls against 12/26. The cost is +0.3 % tokens, it adds no
   dependency or tool, and the text is still compact JSON.
2. **Keep context text as is.** E passed the controlled criteria, but it had no live
   benefit and costs +0.5–1.8 % tokens. It also changes line 1. Revisit it if a client is
   found that clips text without a marker. It is cheap to promote then; the preregistered
   evidence is here.
3. **Do not pursue** pretty JSON (+29–32 % tokens, no detection gain), line-broken JSON
   (no detection gain controlled; live it keeps a prefix but loses the status), or an
   end-marker envelope for search. That envelope is no longer JSON, and it worsened
   `elided` detection from 1.00 to 0.12.
4. **Client side (not OXIDE's to fix):** compact single-line JSON over 50 KiB is dropped
   whole by OpenCode's direct path. Agents should keep `limit` near the default of 20. At
   the default, 0/26 outputs were clipped on either path.

## Limitations

- One model (`gpt-6-luna`) and one client (OpenCode 2.0.32). Claude Code and Codex were not
  tested, and their clipping rules may differ.
- `exhaust` exists in 15/26 tasks. One navigation task cannot be graded.
- MCP queries are capped at 2,000 characters, which shortened 4 of the 26 issues.
- The live agent edited its query in 96 % of calls. Live gold is recomputed from the actual
  arguments, so it measures the reading of what was returned, not retrieval.
- The controlled cuts are synthetic, placed at the same value offset in every arm. The live
  stage is the only real-client evidence, and it gates nothing.

## Reproduce

```bash
cd ~/Projects/oxide   # needs the indexed stores in $DATA/oxide-data (corpora.py index)
S=docs/phase-6/output-integrity/scripts
CAP="systemd-run --user --scope -q -p MemoryMax=3G -p MemorySwapMax=0 -p CPUQuota=200%"
python3 $S/build.py cases                                   # $DATA/cases.json (281 cases)
uv run --with tiktoken python $S/measure.py mcp             # results/mcp.json
uv run --with tiktoken python $S/measure.py sizes           # results/sizes.json
$CAP python3 $S/agent.py run r1 4 && $CAP python3 $S/agent.py run r2 4
python3 $S/agent.py grade                                   # results/agent.json
$CAP .venv/bin/python $S/live.py run 2 && .venv/bin/python $S/live.py grade   # results/live.json
python3 $S/analyze.py                                       # results/report.json
```
