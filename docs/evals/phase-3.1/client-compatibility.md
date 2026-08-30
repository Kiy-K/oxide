# Phase 3.1 client compatibility

OpenCode carries the full A–E statistical matrix (`results.jsonl`, §
results.md) — it is the only client available in this environment with
both a cheap Skill mechanism and MCP support, and the model used there
(`opencode/muse-spark-1.2-contributor-free`) is a free tier, making a
~200-run matrix practical. Claude Code and Codex CLI get a small n=1
spot-check subset (`raw/run_client_compat.py`, `client-compat-raw.jsonl`)
covering conditions B (CLI) and D (MCP + guidance) on tasks A1 (Bucket A)
and C1 (Bucket C) — enough to confirm the transport works and surface
client-specific quirks, not enough for a statistical verdict on its own.

**Real API cost note**: the 4 Claude Code spot-check runs used the paid
Anthropic API (`claude-sonnet-5`, `firstParty` billing) and cost a combined
**$1.66** (`total_cost_usd` in each `logs/claude-*.json`), since Claude Code
has no free tier equivalent to OpenCode's. This is why the Claude subset
stayed at n=1 per cell rather than being scaled to match OpenCode's
matrix.

## Claude Code

All 4 spot-check runs (A1×{B,D}, C1×{B,D}) completed successfully
(`stop_reason: end_turn`, correct task output in all 4 — verified by
reading each run's `result` field). Condition B used
`.claude/skills/oxide-code-context/SKILL.md` (Claude Code's own skill
directory, not OpenCode's `.opencode/skills/`) plus the same E1 AGENTS.md
text; condition D used `--mcp-config` pointing at a JSON file registering
`oxide mcp` plus `--strict-mcp-config` (so only the explicitly-listed MCP
server loads, no ambient servers) plus the transport-generic AGENTS.md
text.

**Limitation, stated plainly**: this spot check used `--output-format
json`, which returns only a final summary (cost, token usage, final
result text) — not the per-tool-call event stream that OpenCode's
`--format json` provides. **This means Claude Code's spot-check runs
cannot answer the transport-selection question (CLI vs MCP vs both)** —
only that task completion succeeded under both conditions. A follow-up
spot check with `--output-format stream-json` would be needed to get
tool-call-level data from Claude Code, at the cost of additional real API
spend; not done in this phase given the cost already incurred.

**Gotcha found**: `claude`'s `--add-dir <directories...>` flag is
variadic. The first draft of the harness placed it immediately before the
trailing prompt positional, and Claude Code silently absorbed the prompt
text as an additional directory argument, then failed with `Input must be
provided either through stdin or as a prompt argument when using
--print`. Fixed by moving the prompt argument earlier in the command line
(`raw/run_client_compat.py`). This is a client CLI parsing quirk, not an
OXIDE issue, but worth recording since it silently breaks any script that
puts `--add-dir` last.

## Codex CLI

**Correction made during this phase, recorded rather than silently
fixed**: the first pass reported Codex as "auth-gated" (`codex exec`
failed with repeated `HTTP error: 401 Unauthorized` against
`wss://api.openai.com/v1/responses`) and, following Phase 2.1's own
precedent of treating Codex as transport-only, stopped there. That
conclusion was wrong. `codex-companion.mjs setup --json` (run for an
unrelated `/codex:setup` request later in this session) showed `"auth":
{"loggedIn": true, "authMethod": "chatgpt", ...}` — Codex in this
environment authenticates via **ChatGPT login**, with the credential
stored at `~/.codex/auth.json`. The harness's isolated, from-scratch
`CODEX_HOME` (used to keep MCP/AGENTS.md state clean between conditions,
same rationale as OpenCode's isolated `OPENCODE_CONFIG`) had no such file,
so every model call was unauthenticated — a self-inflicted harness bug,
not an environment-wide auth gap. Fixed by copying `auth.json` into the
isolated `CODEX_HOME` while still isolating `config.toml` (so no ambient
MCP servers leak in); real behavioral runs then succeeded (`rc=0` for all
4 spot-check cells, `raw/run_client_compat.py:run_codex`).

**Real behavioral results (n=1 per cell, from actual tool-call logs in
`logs/codex-*.jsonl`)**:

| Task | Condition | Result |
|---|---|---|
| A1 (Bucket A) | B (CLI) | Called `oxide context "..."` via `bash` (twice, refining the query) before reading source — appropriate activation. |
| A1 (Bucket A) | D (MCP) | Called the `oxide` MCP server's `context` tool directly (`item.type: mcp_tool_call`, `server: oxide`, `tool: context`) before reading source — appropriate activation via the *native* MCP tool-call mechanism, not a bash shim. |
| C1 (Bucket C) | B (CLI) | Used `rg`/`sed` only — correctly did not invoke `oxide`. |
| C1 (Bucket C) | D (MCP) | Used `git diff`/`find`/`sed` only — correctly did not invoke the MCP tool. |

On this one task pair, Codex/GPT correctly activates OXIDE for Bucket A
under *both* transports and correctly avoids it for Bucket C under both —
directionally consistent with the main OpenCode/muse-spark matrix's
qualitative pattern, though n=1 per cell is far too small to compare
percentages against that matrix.

**New confound observed, not previously documented in any prior phase**:
Codex reads from an ambient, non-project skill directory
(`/home/khoi/.agents/skills/*/SKILL.md` — e.g. `diagnosing-bugs`,
`git-workflow-and-versioning`) that isolating `CODEX_HOME` does **not**
remove, structurally the same class of problem as OpenCode's ambient
`codegraph` MCP server (protocol.md §2). In these 4 runs it did not cause
a false positive (the ambient skills read were generic, not competing
code-context tools), but a future Codex-focused phase should account for
it explicitly rather than assume `CODEX_HOME` isolation is complete.

**Structural note**: Codex has no Skill mechanism of its own (the
`~/.agents/skills/` directory above is a separate, ambient convention, not
something `condition B`'s setup provisions). Codex's "condition B" is
therefore AGENTS.md-only — a structurally different B from OpenCode's or
Claude Code's Skill+AGENTS.md B — and is never pooled with those in this
report.

## Model families

Per the phase brief's "at least two model families": OpenCode's
`muse-spark-1.2-contributor-free` and Claude Code's `claude-sonnet-5` are
different model families from different providers, satisfying this
requirement even though Codex (a third, OpenAI-family client) could not
be exercised behaviorally.
