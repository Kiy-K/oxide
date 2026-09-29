# OXIDE suggestion hook for Claude Code (opt-in, unshipped)

This is a prototype from `docs/evals/phase-4.1/` (roadmap #9: hook-based
agent activation), not a released OXIDE feature. Nothing in `oxide install`
writes this file or touches your hook config — you opt in by hand.

## What it does

A `UserPromptSubmit` hook (fires once, before Claude sees your prompt) that:

1. Reads the submitted prompt text and the session's working directory.
2. Skips silently (exit 0, no output) unless `<cwd>/.oxide/index.db` already
   exists — it never tells you to index, and never suggests OXIDE where
   there is nothing local to query.
3. Skips silently unless the prompt reads like discovery work (a symptom/bug
   description or a "where/what" question with no file already named) and
   is not shaped like an exact-file, tiny-edit request (the two heuristic
   pattern lists in `oxide_suggest.py`).
4. Skips silently if it already suggested OXIDE once this session.
5. Otherwise, adds `docs/agent-usage-policy.md`'s existing, evidence-backed
   AGENTS.md snippet as `additionalContext` — restated verbatim, not
   reworded.

It never sets `"decision": "block"`, never uses a non-zero exit code, and
never touches tool calls (`PreToolUse`/`PostToolUse`) — the only thing it can
do is add ~50 tokens of context to a prompt that already looks like
unfamiliar-repository discovery. Any parse or lookup failure fails open
(silent, exit 0), never fails closed.

## Why UserPromptSubmit and not a `PreToolUse` gate

`PreToolUse` can allow/deny/rewrite a tool call, but the phase brief asked
for a *suggestion*, not an interception — gating actual tool calls risks
exactly the "forcing calls" / "blocking normal tools" failure mode the
roadmap item explicitly rules out. `UserPromptSubmit` is the one Claude Code
event that can add model-visible context without being able to block or
rewrite anything (`code.claude.com/docs/en/hooks`, verified 2026-09-20:
"`UserPromptSubmit`: can't replace the prompt; it only injects
`additionalContext` alongside it").

## Local-first, no daemon

The hook does one filesystem `stat`-equivalent check (`.oxide/index.db`
existence, walking up at most 4 parent directories and stopping at a `.git`
boundary) and a handful of `re.search` calls over the prompt text. It never
shells out to `oxide`, never opens a network connection, and starts no
background process. Measured overhead: see
`docs/evals/phase-4.1/results.md` §hook-overhead.

## Installing (opt-in)

Merge `settings.snippet.json`'s `hooks` block into your own
`.claude/settings.json` (project, shareable) or `~/.claude/settings.json`
(user, all projects), replacing the placeholder path with this file's real
absolute path. Claude Code's own workspace-trust dialog gates whether a
project-committed hook ever runs in an interactive session — this hook adds
no additional trust step beyond that one.

To turn it off in one session without editing settings:
`claude --settings '{"disableAllHooks": true}'`.

## Known limitations (see `docs/evals/phase-4.1/recommendation.md`)

- Pure text-pattern matching — no semantic understanding of the prompt. It
  will miss discovery-shaped prompts phrased outside its pattern list, and
  the tension between missing valid Bucket-A prompts and false-firing on
  Bucket-C ones is exactly what phase-4.1's held-out evaluation measures.
- Per-session suggestion cap of one. A long session with multiple distinct
  discovery tasks only gets suggested once.
- `--bare` and `disableAllHooks` (correctly) turn it off entirely, including
  for genuinely unfamiliar tasks in those sessions.
