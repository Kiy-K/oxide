# Phase 2.2 recommendation

Governing rule (unchanged from the brief): **use the weakest instruction
layer that makes OXIDE reliably useful without turning OXIDE itself into
context pollution.**

## Errata (added at merge time): §6 and §7 below are stale

§6 and §7 were written against this branch's git history, which had no
MCP implementation. By the time this branch merged back to `main`, a
parallel workstream had already shipped one there (`src/mcp.rs`,
`tests/mcp_e2e.rs`, frozen per `docs/mcp-phase-2-report.md` §8) and run a
real Phase 2.1 agent evaluation (`docs/evals/phase-2.1/`) — see
`protocol.md`'s own errata for the full explanation. Two corrections:

- **§6 ("all conclusions invalidated") is wrong as a claim about the
  project.** It was accurate about this branch's history, not about
  whether real MCP evidence existed anywhere. It did, on `main`, merged
  from a sibling branch off the same base commit. Read `docs/mcp-phase-2-
  report.md` §6 for the real MCP-transport agent-behavior findings
  instead of treating the original brief's Phase 2.1 citations as
  baseless.
- **§7's premise — "use `rmcp` rather than a hand-written adapter" for a
  future MCP pass — is moot.** A hand-written adapter already exists,
  already passes a real protocol/lifecycle test suite
  (`tests/mcp_e2e.rs`), and was already evaluated against real agents
  (OpenCode connected and called both tools correctly; Claude Code and
  Codex CLI verified at the transport/config level). `docs/mcp-phase-2-
  report.md` §8 explicitly recommends freezing it as-is. Any future
  decision to migrate to `rmcp` should weigh that migration's cost
  against a working, tested, already-shipped adapter — not start from
  "build one with `rmcp`" as if nothing exists yet. §7's other bullets
  (reuse `RepositoryService`, keep the tool count at 2, pair tool
  descriptions with persistent instructions, re-run rather than port
  activation numbers) are still reasonable general guidance and happen to
  match what the real adapter already does, but were derived without
  knowing that.

## 1. Which instruction layer produced the best appropriate activation

**E — `SKILL.md` + tiny `AGENTS.md`.** 54% Bucket-A activation, 72%
overall appropriate-activation, and it's the only condition where
`oxide` was ever the literal first repository-discovery action (2/13
Bucket-A runs). Every single-mechanism condition underperformed it:

```
E (skill+AGENTS.md)  54% Bucket-A activation, 72% overall appropriate
B (bare mention)      40%                      65%
C (skill only)        31%                      52%
D (AGENTS.md only)    25%                      50%
A (baseline)           0%                      31%
```

Neither the skill alone nor the AGENTS.md rule alone is sufficient — both
underperform even the zero-guidance "bare mention" condition (B). This is
the single most actionable finding of the phase: **do not ship the tiny
AGENTS.md rule as a standalone integration story.** Ship the skill and
the AGENTS.md rule together, or don't bother with either alone.

Caveat carried from `activation-results.md`: 54% is not "reliable" by any
normal reading of that word, and n=13 valid Bucket-A runs is a small
sample even after the 5-rep extension. Read this as "E is the best of
five imperfect options on tiny fixture repos," not "E solves activation."

## 2. False-positive activation on trivial tasks

**0% in every condition, 40/40 Bucket-C runs.** No instruction layer,
including the two that actively teach OXIDE usage (C, D, and their
combination E), caused a single unnecessary OXIDE call on an exact-file,
trivial-edit task. This is good news independent of which layer wins on
Bucket A: the "don't turn OXIDE into a reflex" property `docs/agent-
usage-policy.md` and `skills/oxide-code-context/SKILL.md` already
prescribe held up empirically at every tested strength of instruction.

## 3. Whether `search` still has distinct follow-up utility

Yes, conditionally. Under E (the condition that actually teaches the
`context → search` workflow), the dominant pattern among oxide-using runs
is using *both* tools (7 of 10 oxide-using Bucket-A+B+C runs), not
`search` alone. Under B (bare mention, no workflow guidance), the model
defaults to `search` alone 2x as often as using `context` at all — it
treats `search` as a generic one-shot lookup, not part of a two-step
flow, when nothing tells it otherwise. Conclusion: `search`'s "distinct
follow-up role" is real, but it is an effect of instruction, not an
intrinsic property of the tool surface — keep `search` (per the brief's
own "don't remove it" instruction) but don't expect it to earn its keep
without the workflow being taught somewhere.

## 4. CLI instruction/context overhead

See `context-cost.md` for the full breakdown. Summary: E's persistent
footprint is ~162 tokens (skill frontmatter ~99 + AGENTS.md rule ~63)
plus an on-demand ~803-token skill body loaded only when actually
invoked. That is a small, bounded, and mostly avoidable-when-unused cost
for the best-performing condition — it does not turn OXIDE into
permanent context pollution, satisfying the phase's governing rule.

## 5. Agent/client-specific quirks

The dominant one: **`opencode run` has a ~20% chance of a session
dying after exactly one tool call** — a permission-denied
`read(filePath="/")` that the client doesn't recover from (see
`failures.md`). This is unrelated to OXIDE, unrelated to condition, and
consumed a meaningful fraction of this phase's run budget (32 navigation
runs + several coding-outcome runs had to be excluded/re-run because of
it). Also: `opencode run --pure` (the documented way to get a plugin-free
baseline) hangs with zero output in this environment, so a truly clean
"no ambient plugins" arm (brief §18) could not be produced here — every
condition carries the same `ponytail`-plugin + `codegraph`-MCP confound,
constant across conditions and therefore not fatal to the *relative*
comparison, but worth fixing in a future pass with a properly isolated
`opencode` config that doesn't depend on `--pure`.

## 6. Which previous MCP conclusions were invalidated

All of them, because none of them were ever real evidence in this repo.
See `protocol.md` §0: no MCP server exists in this codebase's history,
`README.md` describes MCP as future work, and the six "Phase 2.1"
findings the original brief cited (OpenCode ignoring OXIDE despite MCP
visibility, known-file controls correctly avoiding OXIDE via MCP, etc.)
have no artifacts anywhere in `docs/`, `eval-agent/`, or `scripts/
agent_eval/`. The only real prior evidence, `docs/compact-toolset-
evaluation.md`, was itself CLI-based (n=1/cell) — consistent in spirit
with this phase's finding that a compact surface beats a full one, but
not an MCP result either. Nothing here should be read as "MCP activation
was worse/better than CLI activation" — that comparison was never run and
still hasn't been.

## 7. Requirements for a future proper MCP implementation using `rmcp`

Not built in this phase (out of scope per the user's mid-session
correction). What this phase's evidence implies a future MCP pass should
carry over, using `modelcontextprotocol/rust-sdk` (`rmcp`) rather than a
hand-written adapter:

- **Reuse `RepositoryService` as the MCP tool boundary**, exactly as
  `README.md`'s "Future MCP reuse audit" already describes — `context`
  and `search` map directly to two MCP tools with the same JSON contract
  the CLI's `--json` output already produces (`docs/agent-usage-
  policy.md`'s flattened `path#QualifiedName` shape). No new response
  schema design needed.
- **Ship the tool descriptions as the SKILL.md-equivalent, not a
  separate document.** This phase's finding that skill-alone
  underperforms skill+AGENTS.md suggests an MCP tool description alone
  (the MCP analog of the skill) will likely need the same pairing with a
  short persistent client-side instruction — test conditions analogous to
  C/D/E again once MCP exists, don't assume tool-schema presence alone is
  enough (this phase directly measured that assumption failing for the
  CLI-transport analog).
- **Keep the tool count at 2** (`context`, `search`) — nothing in this
  phase's evidence argues for more MCP tools than the CLI already
  exposes; Bucket-C's 0% false-positive rate held with only two tools
  available across every instruction layer.
- **Budget for the same client-side reliability gap.** This phase's
  biggest single confound (the `opencode` permission-denial session-death
  bug) was a transport-adjacent client issue, not a protocol issue — but
  it's a reminder that whatever MCP client is used for a future pass
  needs the same "verify actual delivery, don't assume the schema being
  sent means the model uses it" discipline this phase applied to
  AGENTS.md/SKILL.md (§16 in `client-instruction-paths.md`).
- **Re-run the activation comparison, don't port these numbers.** CLI
  tool-selection (an `oxide` shell invocation among many other bash
  commands) and MCP tool-selection (a first-class tool in the model's
  tool-call list) are different affordances with plausibly different
  activation dynamics — this phase's CLI numbers are a floor/prior, not a
  substitute for measuring MCP directly.

## 8. Whether hooks are justified

**No.** Per the brief's own escalation rule ("only recommend a hook for a
future phase if MCP + persistent instructions demonstrably fail"): they
haven't been shown to fail here so much as shown to be *imperfect* — E
gets appropriate activation on most runs and zero false positives on all
40 trivial-task runs. A hook would trade that clean 0% false-positive
record for the false-positive risk hooks are documented to carry
(brief §4: "false positives, language-specific trigger problems,
platform/path failures, unsolicited context injection"). Ship E-style
guidance (skill + tiny AGENTS.md rule) as the production recommendation;
revisit hooks only if a future, larger-sample pass shows E's ~50%
Bucket-A activation rate isn't moving with better task/repo realism (this
phase's fixture repos are 7 files each — a bigger, more realistic
"unfamiliar" repo may itself raise activation without any instruction
change, since native grep/read stops being competitively fast).

## Production recommendation

Ship **`SKILL.md` (already exists, unchanged) + the tiny `AGENTS.md`
block** together as the default integration story for consumers of the
`oxide` CLI. Do not ship the AGENTS.md rule without the skill, or the
skill without some persistent nudge — both underperformed the combination
in this phase's evidence. Do not build a hook. Do not build MCP as part
of closing out this phase; when a future phase does build it, follow §7
above and re-measure rather than assume these numbers transfer.
