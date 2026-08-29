# Client instruction-delivery findings (`gpt-5.6-luna` via opencode 1.18.25)

Phase 2.2 confirmed AGENTS.md/SKILL.md delivery for `muse-spark`. This
phase re-checked the same questions for `gpt-5.6-luna`, since Phase 2.2
found delivery mechanics are worth verifying per model, not assumed.

## AGENTS.md is (almost always) auto-injected, same as Phase 2.2

Only **1 of 120** main-batch logs shows an explicit `read` tool call on
`AGENTS.md` (`C1-E2-r2.jsonl` — see `activation-results.md` §4 for that
transcript). Every other run that used the rule's guidance did so without
ever opening the file as a tool call, meaning the content reaches the
model via system-prompt injection, same mechanism as `muse-spark`. The
one exception shows the model *can* explicitly re-read it out of
curiosity mid-task, which `muse-spark` was never observed doing in Phase
2.2 (though absence of evidence there isn't strong — Phase 2.2's sample
had less total volume).

## `.opencode/skills/oxide-code-context/SKILL.md` discovery confirmed again, plus a stronger signal this time

Every one of 120 Bucket-A runs called a `skill` tool as its first action,
and `oxide-code-context` specifically was that first skill in 6–9 of 12
runs per variant (`activation-results.md` §3) — much stronger and more
direct confirmation of discovery than Phase 2.2 had (which only saw one
`first_action="skill"` run out of 13 valid E-condition Bucket-A runs).
This model reaches for the skill mechanism far more readily than
`muse-spark` did by default.

## New finding this phase: a competing skill genuinely competes

`codebase-memory` (CodeGraph's bundled skill, present because CodeGraph
is installed in this environment as an MCP server + skill pair) is called
first on 3–6 of 12 Bucket-A runs per variant — a real, measured rival to
`oxide-code-context` for "which code-context skill does the model reach
for," not a theoretical confound. Phase 2.2 could only note that
`codegraph_explore` (the MCP tool form) appeared as a first action in 2
runs total; this phase's skill-vs-skill framing makes the same dynamic
far more visible and quantifiable. See `activation-results.md` §3 for the
full breakdown — this is the actual mechanism the E1–E4 variants are
tuning.

## `--auto` requirement is a per-model/client discovery, not a general opencode fact

`muse-spark` runs (Phase 2.2 and this phase's abandoned partial batch)
never needed `--auto` — read/bash/edit tool calls were auto-approved by
default. `gpt-5.6-luna` hangs indefinitely without it (`failures.md`).
Neither behavior is documented anywhere obvious in `opencode --help`
beyond the flag's one-line description ("auto-approve permissions that
are not explicitly denied (dangerous!)") — a future pass switching models
again should treat this as something to verify empirically with a cheap
trivial-task probe before running any real batch, exactly as this phase
did after hitting the hang.

## `--pure` (clean/isolated config) works for this model, unlike `muse-spark`

Phase 2.2 could not get a plugin-free baseline because `opencode run
--pure` hung with zero output for `muse-spark` (plausibly because
provider auth was itself plugin-delivered for that model). `gpt-5.6-luna`
uses the `openai` provider directly and responds normally under
`--pure` — see `protocol.md` §7 / this phase's `interference.jsonl` for
the resulting real clean-vs-normal-config comparison, something Phase 2.2
explicitly could not produce.
