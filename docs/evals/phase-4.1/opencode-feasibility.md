# OpenCode plugin/hook feasibility (sourced, no prototype built)

Primary sources fetched 2026-09-20. OpenCode's mechanism is called
"Plugins," not "hooks" — different enough mechanically from Claude Code's
shell-command hooks that it is documented here as a feasibility finding, per
roadmap #9's "keep other agents as independently validated adapters," not
ported as a second prototype this phase.

## Two live doc surfaces, one confirmed live source

v1 (stable, `opencode.ai/docs/plugins/`) and a v2 preview
(`opencode.ai/v2/docs/build/plugins`) coexist, with different hook names
(v1 flat strings like `chat.message`; v2 namespaced groups like
`session.prompt`). Everything below is v1 unless marked otherwise, and was
cross-checked against the actual TypeScript source on
`github.com/anomalyco/opencode` (`sst/opencode` now redirects there — an org
rename, not a fork) rather than taken from prose docs alone, because the
richest hooks are undocumented on the prose page (see below).

## Mechanically different from Claude Code in three ways

1. **In-process JS/TS function, not a spawned shell process.** A plugin is
   loaded into OpenCode's own runtime and receives a full SDK client plus
   shell access (`Bun.$`) — strictly more privileged than Claude Code's
   out-of-process, stdin/stdout, JSON-contract hook.
2. **Object mutation, not a JSON return value.** The closest analog to
   `UserPromptSubmit` is the `"chat.message"` hook, which mutates
   `output.parts`/`output.message` in place rather than returning an
   `additionalContext` string. A richer, closer analog —
   `"experimental.chat.system.transform"` (append/replace system-prompt
   text every turn) — exists in the type source
   (`packages/plugin/src/index.ts`) but **is not on the official prose docs
   page at all**; building on it means tracking the source directly, not
   the docs site.
3. **No harness-enforced timeout or sandbox.** Hook *invocation* runs
   through a non-catching path (`Effect.promise` in
   `packages/opencode/src/plugin/index.ts`); a thrown error propagates (the
   docs' own example throws to block a tool call), and there is no timeout
   wrapper at all. A slow or hanging OXIDE call would block the pipeline
   rather than being killed the way Claude Code's 30s `UserPromptSubmit`
   default protects against — a real operational difference a port would
   need to defend against itself (an explicit timeout inside the plugin
   code, since the runtime won't provide one).

## Confirms prior phase-3.1 findings, still current

MCP servers (`mcp` block in `opencode.json`, `type: "local"|"remote"`) and
Skills (`SKILL.md`, discovered at `.opencode/skills/`,
`~/.config/opencode/skills/`, **and `.claude/skills/`**) both still match
phase-3.1's setup. The `.claude/skills/` discovery path means OXIDE's
existing `skills/oxide-code-context/SKILL.md`, unmodified, is already
usable by OpenCode today without a separate OpenCode-specific skill file.

## Verdict

**Feasible, with a different engineering shape than a Claude Code hook
port.** A plugin hooking `"chat.message"` (documented) or
`"experimental.chat.system.transform"` (undocumented but real, sourced from
`packages/plugin/src/index.ts`) could inject the same suggestion text OXIDE
already ships. The two costs specific to OpenCode: no timeout protection
(the plugin must self-limit), and building on an API surface that exists in
source but not in prose docs, so it needs to be re-verified against
`packages/plugin/src/index.ts` on future OpenCode upgrades rather than
trusted to stay documented. Given OpenCode is also mid-migration to a v2
namespaced hook API, a real port should expect a rename window.

Sources: `opencode.ai/docs/plugins/`, `opencode.ai/v2/docs/build/plugins`,
`opencode.ai/docs/mcp-servers/`, `opencode.ai/docs/skills/`,
`github.com/anomalyco/opencode/blob/dev/packages/plugin/src/index.ts`,
`github.com/anomalyco/opencode/blob/dev/packages/opencode/src/plugin/index.ts`.
