# Client instruction-delivery findings (opencode 1.18.25)

Phase brief §16 asks: verify the client/model actually *receives* each
instruction mechanism before trusting a comparison built on it. Findings:

## AGENTS.md (condition D/E)

- **Discovery path confirmed**: a repo-root `AGENTS.md` in the directory
  passed via `opencode run --dir <path>` is picked up — condition D/E runs
  do reference oxide (`oxide_context`/`oxide_search` calls appear only in
  conditions D/E/B/C, never in A, and their *rate* differs meaningfully
  from B/C — see `activation-results.md`), so the content is reaching the
  model in some form.
- **It is never `read` as a tool call.** Grepped all condition-D/E logs
  for a `read` tool call whose `filePath` ends in `AGENTS.md`: zero hits
  across 47+ logs. This means opencode injects `AGENTS.md` content
  directly into the system/context prompt rather than requiring the model
  to open it as a file — consistent with how `CLAUDE.md`/`AGENTS.md`
  conventions generally work, and worth confirming here rather than
  assuming, since the whole comparison depends on it.
- **Delivered ≠ followed.** The instruction reaching the model's context
  does not mean the model prioritizes it. Bucket-A activation under D
  alone (AGENTS.md, no skill) was the *weakest* non-baseline condition at
  25% (see `activation-results.md`) — lower than B (bare mention, no
  guidance at all, 40%). The rule is present but easy for the model to
  deprioritize in favor of just reading files, especially on these small
  (7-file) fixture repos where native exploration is already fast.

## SKILL.md (condition C/E)

- **Discovery path confirmed empirically and via binary inspection.**
  `skills/oxide-code-context/SKILL.md` was copied to
  `.opencode/skills/oxide-code-context/SKILL.md` in each task-repo copy.
  `strings` on the installed `opencode` binary
  (`/home/khoi/.bun/install/global/node_modules/opencode-ai/bin/opencode.exe`)
  contains the literal path convention `.opencode/skills` alongside the
  skill-loading machinery (`DirectorySource`, `SkillDiscovery`,
  `<available_skills>`/`</available_skills>` tags). At least one Bucket-A
  run under condition C shows `first_action = "skill"` — the model
  explicitly invoked the `skill` tool before doing anything else,
  confirming the skill was both discovered and reachable.
- **Persistent vs on-demand cost is real and matters** (see
  `context-cost.md`): only the skill's `name`+`description` frontmatter
  (~99 tokens) sits in context by default; the full ~803-token body loads
  only when the model calls the `skill` tool. This is the same
  discovery-then-expand shape MCP tool listings use, achieved here without
  any MCP code.
- **Skill alone underperforms skill+AGENTS.md.** Condition C's Bucket-A
  activation (31%) trails E (54%). The skill teaches *how* to use OXIDE
  well once invoked, but doesn't reliably make the model reach for it in
  the first place as strongly as having the tiny always-resident AGENTS.md
  reminder alongside it.

## No MCP `initialize`-time instructions exist to test

The phase brief's condition C ("MCP + initialize instructions") has no
analog in this repo — there is no MCP server, so nothing was substituted
for it beyond the SKILL.md condition above. See `protocol.md` §0.

## Client/environment quirks discovered along the way (not instruction-path bugs, but relevant to trusting any of this)

- `opencode run --pure` (disables external plugins) reliably hung with
  zero stdout/stderr for the full timeout in this environment — plausibly
  because provider auth (`ClinePass`) is itself plugin-delivered, so
  `--pure` breaks the ability to reach the model at all rather than giving
  a clean baseline. A genuinely plugin-free "clean config" arm (brief §18)
  was not achievable here as a result.
- The dominant reliability issue found in this phase — a permission-denied
  `read("/")` call that kills 20% of sessions outright — is a client bug,
  not an instruction-delivery bug. See `failures.md`.
- Global `~/.config/opencode/AGENTS.md` (this machine's ambient dev config)
  only injects CodeGraph guidance gated on a `.codegraph/` directory
  existing in the target repo; the fixture copies never have one, so it is
  inert here and not a source of oxide-specific bias.
