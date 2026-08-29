# OXIDE Agent Usage Policy

This is the single source of truth for how a coding agent should use OXIDE.
It is transport-independent: the CLI Agent Skill, the MCP server's
instructions, AGENTS.md snippets, and any installer-generated integration
must all be restatements of this document, not independent copies of OXIDE
philosophy.

## What OXIDE is

> Given a coding task and repository state, OXIDE provides the smallest
> high-signal working set that helps a coding agent begin useful work
> quickly.

OXIDE is a **context supplier**, not a coding agent. It indexes code at the
symbol level and returns ranked, explained evidence. It does not read files
end to end, does not reason about correctness, does not edit, and does not
verify anything. The agent still does all of that. OXIDE's job ends the
moment it hands back a context pack or a search result.

**OXIDE is never authoritative or complete.** Its evidence is a starting
point assembled by lexical + semantic retrieval plus a few structural
heuristics (see `reasons[]` on every result — some are exact matches, some
are name-based heuristics with no scope analysis). Treat every result as a
lead to verify by reading the actual file, never as a fact about the
codebase.

## When to use OXIDE

Reach for OXIDE when the task requires **discovery** — figuring out *where*
relevant code lives before you can start reading it with intent:

- An unfamiliar coding task in a repository you have not explored yet.
- Multi-file repository discovery ("what touches X", "where is Y handled").
- Localizing an implementation from a behavioral description or bug report
  (you have symptoms, not a file path).
- Finding related code around a behavior you're about to change (callers,
  tests, structural neighbors).
- Building a bounded context before editing, so you don't have to grep
  blind or read the whole tree.

## When to prefer normal tools

Skip OXIDE and just read/search/edit directly when:

- You already know the exact file and location (a stack trace, a line
  number, a prior search result already pointed you there).
- The task is a tiny, isolated edit (rename, typo, one-line fix) with a
  known target.
- An exact literal string search (`grep`/`ripgrep`) already answers the
  question — OXIDE's retrieval is for concept/behavior queries, not exact
  string lookup.
- You are about to edit code and need to verify current source — always
  read the file directly before editing; never edit from an OXIDE snippet
  alone.
- OXIDE has already returned weak or empty evidence for this task. Do not
  retry with rephrased queries hoping for a better answer — fall back to
  normal repository exploration (grep, directory listing, following
  imports) instead.

A skill or integration that makes an agent call OXIDE before *every*
operation, including these cases, is a regression, not a feature.

## Core workflow

```text
task
 ↓
OXIDE context for unfamiliar repository work
 ↓
read actual relevant source
 ↓
targeted OXIDE search only if a specific follow-up question remains
 ↓
normal repository exploration for anything OXIDE didn't cover
 ↓
edit
 ↓
tests / build / lint
```

`oxide context` is the default entry point for an unfamiliar task — it
returns a budgeted, ranked, deduplicated pack in one call instead of many
exploratory searches. `oxide search` is for a narrower follow-up question
once you already have a task-level context pack and need one more specific
thing (e.g., "where else is this function called").

Do not treat OXIDE calls as a required checklist step. Skip straight to
reading/editing when the task doesn't need discovery.

## Index and freshness behavior

- OXIDE's read commands (`status`, `search`, `context`) never index, never
  repair, and never mutate the index, even when it is missing or stale.
  A missing index fails with a clear `index_missing` error and an `index`
  action hint — the agent (or the human driving it) decides whether and
  when to run `oxide index`, OXIDE never does it silently.
- Do not reflexively call `oxide status` or `oxide index` before every
  `search`/`context` call. Index once per session (or when you know the
  repository changed since the last index), not per query.
- Every JSON error carries a stable `code` and an `action`
  (`index` / `repair` / `retry` / `fall_back` / `stop`) — use `action` to
  decide what to do next instead of pattern-matching the message string.

## What OXIDE does not promise

- It does not guarantee finding the right code — it optimizes for a small,
  high-signal pack, which means it can miss things a broader search would
  find. If evidence looks thin, broaden with normal tools.
- Retrieval reasons are not all equally strong: `lexical=`/`semantic=` are
  direct matches to the query; `parent←`/`child←`/`imported-definition←`
  are structurally resolved relations; `uses←`/`test←` are name-based
  heuristics with no scope analysis and can occasionally link unrelated
  same-named symbols. Weight your trust in a result accordingly.
- It never edits code, runs tests, or verifies its own output. That
  responsibility never leaves the agent.

## Recommended AGENTS.md snippet

A consumer project wiring OXIDE into a coding agent's persistent
instructions (its own `AGENTS.md`, `CLAUDE.md`, or equivalent) should
paste this snippet verbatim rather than write a new one — it is a
restatement of this document, kept intentionally small, and should not be
expanded with example CLI syntax (that belongs in `README.md`'s
"Using OXIDE from a coding agent" section and the bundled
`skills/oxide-code-context/SKILL.md`, not in always-resident context):

```markdown
## OXIDE

For unfamiliar repository work where the implementation path is not
already known, use `oxide context` before broad grep/read exploration.
Use `oxide search` for focused follow-up discovery. For exact known-file
or literal tasks, use normal tools directly. Read source before editing.
```

This exact wording was chosen over several tested alternatives —
including a version spelling out literal command syntax and a version
rewording only the bundled Skill's activation description — in a
real-agent evaluation comparing activation behavior across variants; see
`docs/evals/phase-2.3/recommendation.md` for the evidence and
`docs/evals/phase-2.3/policy-variants.md` for what was tried and
rejected. Do not hand-edit this snippet's wording without similar
evidence that a change helps.
