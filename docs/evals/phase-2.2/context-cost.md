# Persistent activation-cost by condition

No MCP tool schemas exist in this repo (see `protocol.md` §0), so there is
nothing to compare against the brief's "MCP tool schemas" line item. The
real, measurable persistent costs for the CLI-transport substitution are:

| Layer | Persistent cost (every turn) | On-demand cost (only if invoked) |
|---|---|---|
| **A — baseline** | 0 | 0 |
| **B — bare mention** | ~15 tokens (one added sentence in the task prompt; not actually a standing/persistent cost since it's per-prompt, not per-turn config) | 0 |
| **C — SKILL.md** | ~99 tokens (frontmatter `name` + `description` only, registered in opencode's `<available_skills>` list) | ~803 tokens (full `SKILL.md` body, loaded only when the `skill` tool is called) |
| **D — AGENTS.md** | ~63 tokens (the block is small enough it undercuts the ~50–100 token target in the user's instruction) | 0 (no on-demand tier; it's always-resident) |
| **E — SKILL.md + AGENTS.md** | ~162 tokens (99 + 63) | ~803 tokens (skill body, same as C) |

Measured by `wc -c` on the actual files (`docs/evals/phase-2.2/raw/analyze_results.py`
inputs), chars/4 estimate per the phase brief's own convention. Not run
through a real tokenizer; the CLI has no tokenizer dependency to borrow one
from, and the gap between a chars/4 estimate and a real BPE count at this
size (60–160 tokens) is not going to change which layer wins.

## Actual per-run token totals (from `step_finish` events, real model-side
accounting, not the chars/4 estimate above)

See `activation-results.md` "tool-call discipline" table — the observed
`tokens_total` per run ranges ~100k–170k across conditions. This is
**almost entirely repo/tool-schema/system-prompt overhead intrinsic to
`opencode` itself** (the `ponytail` plugin, ~30 registered skills, MCP
tool registrations for `codegraph`, etc. — see `protocol.md` §6), not
OXIDE's marginal cost. The condition-to-condition delta in this number
(e.g. C's 100,007 vs E's 142,454) is a noisier, weaker signal than the
char-counted persistent-cost table above and should not be read as "OXIDE
costs 40k tokens" — it mixes in real variance from how much the model
explored and how long its own reasoning ran per condition.

## Reading

The AGENTS.md rule (D) is the cheapest non-zero intervention by a wide
margin — cheaper even than the skill's frontmatter stub — and per
`activation-results.md` it is *not* sufficient alone. The condition that
actually produces useful activation (E) costs ~162 persistent tokens plus
an ~803-token on-demand load only on the runs where the skill is actually
invoked. That is a genuinely small permanent footprint for a coding
agent's context window, and is the basis for the production
recommendation in `recommendation.md`.
