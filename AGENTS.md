# AGENTS.md

OXIDE: local incremental code index and hybrid retrieval (Rust, single crate).
Product docs are in `README.md`. `CLAUDE.md` is a symlink to this file, so Claude and Codex share one root policy.

This file is always loaded; keep it short. Detail lives in the docs below. Read them when a task touches their area, and don't copy them here: new invariants go in `docs/agents/invariants.md`, new commands in `docs/agents/workflow.md`.
Docs and code comments that cite "`AGENTS.md`" for an invariant, command or harness detail mean `docs/agents/invariants.md` or `docs/agents/workflow.md`.

## How to work

- Keep it simple. Build only what the task needs: no speculative abstractions, flags or features.
- Don't duplicate logic, policy or docs. Still, don't add an abstraction just to remove a few repeated lines.
- Keep changes small and cohesive. Don't clean up unrelated code during a focused task.
- Reuse existing patterns and dependencies before adding new ones.
- Keep behavior and compatibility (on-disk index, symbol ids, JSON output, CLI/MCP surface) unless the task says to change them.
- Fix root causes. Don't weaken tests, regenerate goldens or re-baseline just to make a failure go away.
- Measure before optimizing. Keep negative results in the eval docs.
- Read the code, tests and docs before stating how something works. Label each claim as measured or inferred.
- If missing or ambiguous information could change the implementation, check the repo first. If that doesn't settle it, ask one focused question instead of guessing. Don't ask what the code or docs already answer.
- Before finishing, read the full diff for accidental changes in scope, `pub` visibility, dependencies (`Cargo.toml`/`Cargo.lock`) or generated files.
- Don't commit, push, tag, release, edit GitHub issues or roadmaps, or start follow-up work unless asked.

## Read before touching

| Area | Read first |
|---|---|
| Build, test, lint, benchmark gate, commit order and format, eval harnesses | `docs/agents/workflow.md` |
| Symbol ids, parser dedup, SQLite casts/transactions/query plans, index generation, lexical/BM25 persistence | `docs/agents/invariants.md` § Load-bearing invariants |
| `src/retrieval/`, `src/context.rs`, `src/config.rs` weights, `RetrievalMode` | invariants § Load-bearing invariants, then `docs/review/retrieval-and-config.md` |
| Embedding providers, fingerprints, native session pool | invariants § Embeddings / providers, then `docs/review/embeddings-and-index.md` |
| Languages, tags extraction, structural relations, `blast_radius.rs`, storage backend | invariants § Repo layout facts, then `docs/review/structural-and-language.md` |
| Terminal output, telemetry, `--json`/MCP output | invariants § Terminal output and telemetry and § JSON output contracts, then `docs/review/api-surface.md` |
| Reviewing any change | `docs/review/README.md` |
| Issues, triage labels, domain docs | `docs/agents/` (`issue-tracker.md`, `triage-labels.md`, `domain.md`) |

## Always-on rules

- Retrieval ranking is benchmark-gated. Before changing ranking, scoring, fusion weights or tie-breaks, compare against `docs/canonical-baseline.md`. Treat any difference in results as a regression to explain.
- `docs/agent-usage-policy.md` is the only canonical guide for how agents consume OXIDE. The Skill, MCP instructions and consumer snippets restate it; they never fork it. `skills/oxide-code-context/` is the bundled skill for downstream users and is not the same as `.claude/skills/` or `.agents/skills/`, which are used to develop OXIDE.
