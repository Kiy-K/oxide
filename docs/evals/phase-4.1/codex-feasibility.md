# Codex CLI hook feasibility (sourced, no prototype built)

Roadmap #9 scope: "keep other agents as independently validated adapters" —
Codex gets a sourced feasibility verdict here, not a running prototype.
Primary sources fetched 2026-09-20; `developers.openai.com/codex/*` 308-redirects
to `learn.chatgpt.com/docs/*` (OpenAI's own docs, not a third-party mirror).

## This corrects a stale finding in this repo

`docs/evals/phase-3.1/client-compatibility.md` states "Codex has no Skill
mechanism." **That is no longer true as of 2026-09-20.** Codex has since
shipped both a hooks system and a Skills system. Anything in this repo that
still cites "Codex has no Skill mechanism" as a current fact should be read
as historically accurate for phase-3.1's date, not for today.

## Hook mechanism: real, and mechanically close to Claude Code's

- Twelve lifecycle events (`learn.chatgpt.com/docs/hooks`,
  `learn.chatgpt.com/docs/config-file/config-reference`): `SessionStart`,
  `SessionEnd`, `UserPromptSubmit`, `PreToolUse`, `PermissionRequest`,
  `PostToolUse`, `PreCompact`/`PostCompact`, `Stop`, `SubagentStart`/
  `SubagentStop`, `Interrupt`.
- Context injection is real, not permission-only: `additionalContext` (and
  plain stdout) is folded into model-visible context specifically for
  `SessionStart`, `SubagentStart`, and `UserPromptSubmit` — the same three-
  event allowlist shape as Claude Code's four-event one
  (`UserPromptSubmit`/`UserPromptExpansion`/`SessionStart`/`PostModelSwitch`).
  `UserPromptSubmit` is present in both allowlists, so the Claude Code
  design (suggest via `UserPromptSubmit`, never block) ports directly.
- Exit-code contract matches Claude Code's shape closely: exit 0 + no output
  = continue; exit 2 + stderr = block/deny; exit 0 + JSON = structured
  decision (`{"continue": true, ...}` at the top level,
  `{"hookSpecificOutput": {...}}` per event, mirroring Claude Code's own
  `hookSpecificOutput.additionalContext` shape closely enough that the
  translation is field-renaming, not a redesign).
- Timeout: 600s default per hook (1s for `SessionEnd`/`Interrupt`) —
  looser than Claude Code's 30s `UserPromptSubmit`-specific default, so a
  ported hook should still set its own short `timeout` explicitly rather
  than rely on the default.
- Trust model differs from Claude Code's folder-level dialog: Codex requires
  **explicit human review via `/hooks`** for any non-managed hook, pinned to
  the hook's exact content hash — any edit re-triggers review. This is
  *stricter* than Claude Code's one-time-per-folder trust dialog, and would
  need to be called out in any install docs (`--dangerously-bypass-hook-
  trust` exists for scripted/CI use, mirroring Claude Code's
  `disableAllHooks`/`--bare` escape hatches).

## Skills: also real and current

`SKILL.md` packages under `.agents/skills` (repo), `$HOME/.agents/skills`
(user), `/etc/codex/skills` (admin); explicit (`$name`) and implicit
(description-match) invocation. This means Codex could alternatively receive
OXIDE's existing `skills/oxide-code-context/SKILL.md` design nearly as-is
(same shape as Claude Code's Skill mechanism) instead of, or alongside, a
hook — a second, lower-effort adapter path worth more weight than a hook
port if Codex-specific effort is ever prioritized.

## Verdict

**Feasible today, not prototyped this phase.** A `UserPromptSubmit` hook
shelling out to the same trigger heuristic as `oxide_suggest.py` and
returning Codex's `additionalContext` JSON shape would port with event-name
and field-name translation, not a redesign. The Codex-specific costs to
budget for if this is picked up: the stricter hash-pinned hook-trust review
flow (more setup friction than Claude Code's one-time folder trust), and a
still-unconfirmed exact GA date/version for Codex hooks (two independent
third-party blogs estimate "GA 2026-05-14" / "12 events as of 0.150.1,
2026-08-27"; no official changelog entry was found confirming this, so
treat the precise version/date as secondary-sourced, not primary-confirmed).

Sources: `learn.chatgpt.com/docs/hooks`,
`learn.chatgpt.com/docs/config-file/config-reference`,
`learn.chatgpt.com/codex/build-skills`, `developers.openai.com/codex/mcp`,
`learn.chatgpt.com/docs/custom-prompts`.
