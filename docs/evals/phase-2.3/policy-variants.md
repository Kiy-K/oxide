# Policy variants tested

All five variants keep the skill body, the CLI, and everything else fixed
— only the `AGENTS.md` text and/or the skill's frontmatter `description`
changes. Full text lives in `raw/run_variants.py` next to the harness;
reproduced here for reference.

## E0 — baseline (Phase 2.2's winning condition, unchanged)

```markdown
## OXIDE

For unfamiliar multi-file coding tasks, use `oxide context` before broad
repository exploration. Use `oxide search` for focused follow-up
discovery. For exact known-file or literal tasks, use normal tools
directly. Read source before editing.
```

~63 tokens (chars/4).

## E1 — stronger first sentence

```markdown
## OXIDE

For unfamiliar repository work where the implementation path is not
already known, use `oxide context` before broad grep/read exploration.
Use `oxide search` for focused follow-up discovery. For exact known-file
or literal tasks, use normal tools directly. Read source before editing.
```

~70 tokens.

## E2 — exact commands spelled out

```markdown
## OXIDE

For unfamiliar multi-file coding tasks, before broad repository
exploration run:

```
oxide context --task "<task>" --json
```

For a focused follow-up question, run:

```
oxide search "<question>" --json
```

For exact known-file or literal tasks, use normal tools directly. Read
source before editing.
```

~95 tokens.

## E3 — explicit decision boundary

```markdown
## OXIDE

Unknown implementation path -> use `oxide context` before broad
grep/read exploration, then `oxide search` for focused follow-up.
Known exact file/literal target -> use normal tools directly.
Read source before editing.
```

~60 tokens.

## E4 — skill metadata refinement (AGENTS.md unchanged, = E0's)

Skill body unchanged; only the frontmatter `description` changes:

Before (E0/E1/E2/E3):
> Get a bounded, ranked working set of relevant code from OXIDE's local
> index before exploring an unfamiliar repository. Use when starting a
> multi-file task, localizing an implementation from a bug report or
> behavior description, or needing related code (callers, tests,
> structural neighbors) before editing in a codebase you haven't explored
> yet.

After (E4):
> Use BEFORE grep/read when starting an unfamiliar multi-file task or
> localizing an implementation from a bug report or behavior description
> — get OXIDE's ranked working set first, then read the actual files it
> points to. Skip for known-file, exact-line, or literal-string tasks.

~99 tokens either way (frontmatter description is what's persistently
resident — see `context-cost.md`).

## Why these four and not others

Per `miss-forensics.md`: every diagnosable Phase 2.2 E0 miss was
`NATIVE_DEFAULT` (model defaults to grep/read, never attempts `oxide`).
E1 and E3 target that directly (make the "unfamiliar → OXIDE first"
branch more salient). E2 and E4 target failure modes (`COMMAND_FRICTION`,
`SKILL_NOT_LOADED`) that did not actually appear in the miss set — tested
anyway per the phase brief's explicit request, with the a priori
expectation (stated in `miss-forensics.md` before any E1–E4 run executed)
that they would help less. See `activation-results.md` for whether that
expectation held.
