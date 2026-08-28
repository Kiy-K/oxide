---
name: oxide-code-context
description: >-
  Get a bounded, ranked working set of relevant code from OXIDE's local index
  before exploring an unfamiliar repository. Use when starting a multi-file
  task, localizing an implementation from a bug report or behavior
  description, or needing related code (callers, tests, structural
  neighbors) before editing in a codebase you haven't explored yet.
allowed_tools:
  - Bash
  - Read
---

Full policy (transport-independent source of truth): `docs/agent-usage-policy.md`.

## When to activate

Use OXIDE when the task needs **discovery**: you don't yet know which files
are relevant. Skip it entirely when:

- the exact file/location is already known (from a stack trace, a prior
  search, or the user telling you),
- the task is a tiny, isolated edit,
- you need an exact literal string match (use grep/ripgrep instead),
- OXIDE already returned weak/empty evidence for this task — fall back to
  normal repository exploration rather than retrying rephrased queries.

Calling OXIDE for every operation, including these cases, is a misuse of
this skill, not a safer default.

## Normal workflow

For an unfamiliar coding task, start with one context call:

```bash
oxide context --task "<task description>" --budget-tokens 4096 --json
```

Then **read the actual source** of the files/symbols it returns — the
snippets in the pack are for orientation, not for editing from. Never edit
based on an OXIDE snippet alone.

## Targeted follow-up

If a specific question remains after reading the context pack (e.g. "where
else is this called", "is there a similar helper elsewhere"), use a narrower
search instead of another full context call:

```bash
oxide search "<specific repository question>" --json
```

Don't loop `context`/`search` calls hoping for a better answer to the same
question — one context call plus at most a couple of targeted searches is
the normal shape of a task. If results are still weak, switch to normal
repository exploration (grep, follow imports, read directory structure).

## Index behavior

- `oxide index [PATH]` builds or incrementally updates the index. Run it
  once per session, or when you know the repo changed materially since the
  last index — not before every query.
- `status`/`search`/`context` are read-only: they never index, repair, or
  modify anything, even when the index is missing or stale. Don't call
  `oxide status` reflexively before every search — if the index is missing,
  `search`/`context` fail with a clear `index_missing` error telling you to
  index.
- Every JSON error has `code` and `action` (`index`/`repair`/`retry`/
  `fall_back`/`stop`). Use `action` to decide what to do, not the message
  text.

## Anti-patterns

- Calling OXIDE for a task where the file/line is already known.
- Treating an OXIDE snippet as verified source — always read the real file
  before editing it.
- Calling `oxide status` or `oxide index` before every single search.
- Repeating the same/similar query hoping for different results instead of
  falling back to normal exploration.
- Treating OXIDE's results as complete or authoritative — it returns a
  small high-signal set, not an exhaustive one.
