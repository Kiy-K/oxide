# AGENTS.md

OXIDE v2 (branch `rewrite/v2`): a structural context engine for agents. Rust kernel and runtime in `oxide_kernel/`; Bun/TypeScript control plane in root `src/`.
`CLAUDE.md` is a symlink to this file, so Claude and Codex share one root policy.

The architecture authority is `docs/spec/SPEC.md`, with phases and exit gates in `docs/BOOTSTRAP.md` and decisions in `docs/adr/` (Proposed ADRs are proposals, not Accepted decisions; never change an ADR's status unasked). When they disagree with anything else in the repo, they win.

This file is always loaded; keep it short. New commands go in `docs/agents/workflow.md`; v2 boundary detail lives in `oxide_kernel/README.md`.

## Layout and ownership

- `oxide_kernel/crates/kernel`: repository intelligence and deterministic policy. No I/O, no crate dependencies (enforced by its `tests/boundary.rs`).
- `oxide_kernel/crates/runtime`: sessions, database/resource ownership, caches, provider clients, scheduling, the versioned service contract.
- `src/`: TypeScript only, run by Bun. CLI, MCP, integrations, configuration UX, process lifecycle, runtime supervision. It must not parse source, index, implement TreeIndex/TreeRouter, retrieve/fuse, select candidates, pack context, or touch LadybugDB (enforced in part by `src/boundary.test.ts`).
- All Rust lives under `oxide_kernel/`. Model execution belongs to external runners (Ollama, llama.cpp, judge services).
- Toolchain: Mise is the entrypoint (`mise run verify`, which CI runs), Bun for all JS/TS (packages, runtime, tests), cargo inside `oxide_kernel/`. pnpm, Deno, Turbo, a root Cargo crate and `packages/*` are retired; don't reintroduce them.

## How to work

- Keep it simple. Build only what the current phase needs: no speculative abstractions, flags or features.
- Don't duplicate logic, policy or docs. Still, don't add an abstraction just to remove a few repeated lines.
- Keep changes small and cohesive. Don't clean up unrelated code during a focused task.
- Reuse existing patterns and dependencies before adding new ones. A new crate or package needs a compilation, reuse, runtime or API reason (SPEC § System architecture).
- Keep versioned v2 contracts (service protocol, contract cases) compatible unless the task says to change them; bump the version when you do. Legacy v1 compatibility (SQLite indexes, symbol ids, CLI/MCP/JSON surface) is not a requirement (SPEC § Compatibility policy).
- Fix root causes. Don't weaken tests, regenerate goldens or re-baseline just to make a failure go away.
- Measure before optimizing. Keep negative results in the eval docs.
- Read the code, tests and docs before stating how something works. Label each claim as measured or inferred.
- If missing or ambiguous information could change the implementation, check the repo first. If that doesn't settle it, ask one focused question instead of guessing. Don't invent permanent ID/schema/transport/model policy to unblock scaffolding (BOOTSTRAP rule 4).
- Before finishing, read the full diff for accidental changes in scope, `pub` visibility, dependencies (`oxide_kernel/Cargo.lock`, `bun.lock`) or generated files.
- Don't commit, push, tag, release, edit GitHub issues or roadmaps, or start the next phase unless asked.

## Read before touching

| Area | Read first |
|---|---|
| Anything architectural | `docs/spec/SPEC.md`, `docs/BOOTSTRAP.md`, `docs/adr/` |
| Kernel/runtime/control-plane boundary, service contract, transport | `oxide_kernel/README.md`, `docs/adr/0003-kernel-runtime-control-plane-boundary.md` |
| Build, test, lint, commit format | `docs/agents/workflow.md` |
| Issues, triage labels, domain docs | `docs/agents/` (`issue-tracker.md`, `triage-labels.md`, `domain.md`) |
| Legacy v1 reference only (evidence, not instructions) | `docs/agents/invariants.md`, `docs/review/`, `docs/canonical-baseline.md`, `docs/agent-usage-policy.md`, `skills/oxide-code-context/`, `README.md`, eval reports under `docs/`. They describe the retired implementation (root Rust crate, SQLite, legacy CLI/MCP); port lessons and semantic assertions, never their layout or APIs |

## Always-on rules

- Legacy code and docs are historical evidence, not a template (SPEC, BOOTSTRAP rule 2). The old implementation is recoverable from `main` / `ac985b28`; don't port it into `oxide_kernel/` or `src/` without a stated SPEC requirement.
- Retrieval, routing and packing changes are evaluated per SPEC § Evaluation and BOOTSTRAP's frozen baselines once those exist (Phase 3+). Legacy numbers such as `docs/canonical-baseline.md` are historical comparators, not v2 gates.
- Models judge; deterministic Rust decides. Keep embeddings, JEV and learned judges optional; the deterministic offline path needs no model, runner or network.
