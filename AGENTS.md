# AGENTS.md

OXIDE v2 (branch `rewrite/v2`): a structural context engine for agents. One Python package, `oxide`, in `src/oxide/` (ADR-0011); the Rust/Bun implementation it replaced is recoverable at `f79808a`.
`CLAUDE.md` is a symlink to this file, so Claude and Codex share one root policy.

The architecture authority is `docs/spec/SPEC.md`, with phases and exit gates in `docs/BOOTSTRAP.md` and decisions in `docs/adr/` (Proposed ADRs are proposals, not Accepted decisions; never change an ADR's status unasked). When they disagree with anything else in the repo, they win.

This file is always loaded; keep it short. New commands go in `docs/agents/workflow.md`; the public API is documented in `docs/public-api.md`.

## Layout and ownership

- `src/oxide/api.py`, `models.py`: the public API and its typed results/errors, the stable product boundary.
- `src/oxide/core/`: repository intelligence and deterministic policy (Python slice, derivation, retrieval, TreeRouter, selection/packing). No filesystem or database access except through the read view it is given.
- `src/oxide/capture.py`, `src/oxide/storage/`: worktree capture; LadybugDB ownership, generations, publication and queries. Nothing else touches LadybugDB.
- `src/oxide/cli.py`, `src/oxide/mcp_server.py` (FastMCP) and future editor adapters: Python clients of `oxide.api` only. They must not parse source, route, select or pack.
- Vectorless: no embeddings, vector indexes or model runners. Judges, if ever added, are optional external services.
- Toolchain: Mise is the entrypoint (`mise run verify`, which CI runs); uv manages Python and dependencies (`pyproject.toml`, committed `uv.lock`); pytest, Ruff, mypy `--strict`. Rust, Bun/TypeScript, pnpm, Deno and `packages/*` are retired; don't reintroduce them without an ADR.

## How to work

- For fully-autonomous tasks, don't poll background tasks; they will send you their output once they're done.
- Keep it simple. Build only what the current phase needs: no speculative abstractions, flags or features.
- Don't duplicate logic, policy or docs. Still, don't add an abstraction just to remove a few repeated lines.
- Keep changes small and cohesive. Don't clean up unrelated code during a focused task.
- Reuse existing patterns and dependencies before adding new ones. A new package or dependency needs a reuse, packaging or API reason (SPEC § System architecture).
- Keep the public API and versioned formats (derivation components, store schema, payload/units versions) compatible unless the task says to change them; bump the version when you do. Legacy v1 compatibility (SQLite indexes, symbol ids, CLI/MCP/JSON surface) and the retired Rust/TS protocol are not requirements (SPEC § Compatibility policy).
- Fix root causes. Don't weaken tests, regenerate goldens or re-baseline just to make a failure go away.
- Measure before optimizing. Keep negative results in the eval docs.
- Read the code, tests and docs before stating how something works. Label each claim as measured or inferred.
- If missing or ambiguous information could change the implementation, check the repo first. If that doesn't settle it, ask one focused question instead of guessing. Don't invent permanent ID/schema/transport/model policy to unblock scaffolding (BOOTSTRAP rule 4).
- Before finishing, read the full diff for accidental changes in scope, public API surface (`oxide/__init__.py`), dependencies (`pyproject.toml`, `uv.lock`) or generated files.
- Tests never touch real user data: every test and subprocess gets a private data location (`tests/python/conftest.py`).
- Don't commit, push, tag, release, edit GitHub issues or roadmaps, or start the next phase unless asked.

## Read before touching

| Area | Read first |
|---|---|
| Anything architectural | `docs/spec/SPEC.md`, `docs/BOOTSTRAP.md`, `docs/adr/` |
| Python engine, storage, removed/retained features | `docs/adr/0011-python-engine-vectorless.md`, `docs/public-api.md` |
| Build, test, lint, commit format | `docs/agents/workflow.md` |
| Issues, triage labels, domain docs | `docs/agents/` (`issue-tracker.md`, `triage-labels.md`, `domain.md`) |
| Legacy v1 reference only (evidence, not instructions) | `docs/agents/invariants.md`, `docs/canonical-baseline.md`, `docs/agent-usage-policy.md`, `skills/oxide-code-context/`, `docs/legacy/README-v1.md`, eval reports under `docs/`. They describe the retired implementation (root Rust crate, SQLite, legacy CLI/MCP); port lessons and semantic assertions, never their layout or APIs |
| v2 review policy | `docs/review/` — reviewer-facing invariants for the Python engine (public API, storage/generations, core/retrieval, capture/language, evidence/benchmarks). Architecture authority remains `docs/spec/SPEC.md` and `docs/BOOTSTRAP.md`. |

## Always-on rules

- Legacy code and docs are historical evidence, not a template (SPEC, BOOTSTRAP rule 2). The old implementation is recoverable from `main` / `ac985b28`; don't port it into `src/oxide/` without a stated SPEC requirement.
- Retrieval, routing and packing changes are evaluated per SPEC § Evaluation against the frozen baselines (`tests/python/test_baseline.py` checks Phase 3 exactly); never regenerate a frozen artifact to make a failure pass. Legacy numbers such as `docs/canonical-baseline.md` are historical comparators, not v2 gates.
- Models judge; deterministic code decides. Keep JEV and learned judges optional; there are no embeddings. The deterministic offline path needs no model or network (only `oxide prepare` fetches the pinned FTS extension, explicitly).
- Let native libraries do recursive and latency-sensitive work (tree-sitter queries, LadybugDB queries); Python holds bounded policy.

<!-- CODEGRAPH_START -->
## CodeGraph

In repositories indexed by CodeGraph (a `.codegraph/` directory exists at the repo root), reach for it BEFORE grep/find or reading files when you need to understand or locate code:

- **MCP tool** (when available): `codegraph_explore` answers most code questions in one call — the relevant symbols' verbatim source plus the call paths between them, including dynamic-dispatch hops grep can't follow. Name a file or symbol in the query to read its current line-numbered source. If it's listed but deferred, load it by name via tool search.
- **Shell** (always works): `codegraph explore "<symbol names or question>"` prints the same output.

If there is no `.codegraph/` directory, skip CodeGraph entirely — indexing is the user's decision.
<!-- CODEGRAPH_END -->
