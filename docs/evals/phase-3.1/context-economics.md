# Phase 3.1 context economics

All chars/4 estimates below are labeled `estimated`; anything captured
directly from a live protocol exchange or client cost/token field is
labeled `measured`. No number here is invented — where a real tokenizer or
client telemetry was unavailable, the value is marked `unavailable` rather
than guessed.

## CLI (condition B / E's CLI half)

| Item | Chars | Tokens (chars/4, estimated) | Label |
|---|---:|---:|---|
| AGENTS.md rule (E1) | 295 | ~74 | measured (chars, `len(E1_AGENTS_CLI)` in `raw/run_matrix.py`), estimated (tokens) — text unchanged from Phase 2.3 |
| Skill frontmatter (loaded eagerly, every session) | 437 | ~109 | measured (chars, `skills/oxide-code-context/SKILL.md` between its `---` markers, byte-identical since Phase 2.2), estimated (tokens) |
| Skill body (loaded only when the `skill` tool is invoked) | 2774 | ~694 | measured (chars, `wc -c` on `SKILL.md` minus frontmatter), estimated (tokens) — on-demand only |
| `oxide context --json` output | varies by task | see per-task `tokens_total` in `results.jsonl` | measured (opencode's own `step_finish.tokens` field, when present) |

CLI persistent cost per session (always paid, regardless of whether the
model ever calls `oxide`): AGENTS.md (295c) + skill frontmatter (437c) =
**732c / ~183 tokens**, matching Phase 2.3's finding that every tested
variant stayed well under that phase's ~250-token persistent ceiling for
the AGENTS.md rule alone; the *skill* frontmatter is a separate, also-small
persistent line item that Phase 2.3's ceiling didn't count. The larger
~694-token skill body is on-demand only, paid solely in sessions where the
model actually invokes the `skill` tool.

## MCP (conditions C/D, and E's MCP half)

Captured live from the running `rmcp` server via `raw/mcp_probe.py`
(`raw/mcp_probe_result.json`) — **measured**, not copied from Phase 2.1's
pre-migration numbers (see protocol.md §1 for why that matters):

| Item | Chars | Tokens (chars/4, estimated) | Label |
|---|---:|---:|---|
| `tools/list` response (both tool schemas) | 590 | ~148 | measured |
| Server `instructions` string (part of `initialize` response) | 280 | ~70 | measured |
| **MCP persistent total** | **870** | **~218** | measured |
| D's AGENTS.md rule (transport-generic wording) | 301 | ~75 | measured (chars, `len(D_AGENTS_MCP)`), estimated (tokens) |
| `context` tool call response, sample query "how does retry backoff work" | 6052 | ~1513 | measured, single sample — response size scales with the task's actual retrieval budget, not fixed |
| `search` tool call response, sample query "retry" | 9582 | ~2396 | measured, single sample |

MCP persistent cost per session (paid on every `initialize`/`tools/list`
regardless of whether the model calls the tools): **870c / ~218 tokens**
for C, **870 + 301 = 1171c / ~293 tokens** for D — this is **higher** than
CLI's ~183-token persistent floor, because the entire tool schema (JSON
Schema `properties`/`required`/`additionalProperties` for both tools) is
sent on every session's `tools/list`, whereas the CLI's persistent cost is
just the short AGENTS.md rule plus a skill *frontmatter* stub — the CLI's
actual usage documentation (the ~803-token skill body) is on-demand, loaded
only if the `skill` tool fires. **MCP has no equivalent on-demand tier**:
the full tool schema is unconditionally part of every session's context,
whether or not the model ever calls `context` or `search`. Condition D
pushes this further above CLI's floor.

## Condition E (both transports)

E's persistent cost is additive, not deduplicated: CLI's ~183 tokens
(AGENTS.md + skill frontmatter) **plus** MCP's ~218 tokens (`tools/list` +
instructions) — since E's AGENTS.md is the unmodified CLI-only E1 text (no
combined/de-duplicated wording), giving **~401 tokens of persistent
overhead** before either transport is ever invoked, the highest of any
condition tested. Whether that buys anything (deduplicated retrieval,
complementary coverage) or is pure waste (redundant retrieval, as the phase
brief warns against) is the transport-selection question — see
`transport-selection.md`, informed by `results.jsonl`'s `transport` field
per condition-E run.

## What this phase does NOT claim

- No real tokenizer was run against these bodies (chars/4 is the estimation
  convention used since Phase 2.1); all "tokens" figures above are
  `estimated` unless stated otherwise.
- OpenCode's own `step_finish.tokens.total` field (when present in
  `results.jsonl`) is a `measured` total-session figure that includes
  everything (system prompt, all tool schemas including native tools,
  conversation) — it is not decomposable into "OXIDE's share" vs. "the
  rest," so it is reported as a whole-session number in `results.md`, not
  substituted for the persistent-cost breakdown above.
- `context`/`search` response sizes above are single live samples, not an
  average across the matrix — a distribution over `results.jsonl`'s actual
  task prompts (which vary in retrieval breadth) is a more representative
  number and is reported in `results.md` where available.
