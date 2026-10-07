---
name: verify
description: Runs OXIDE v2's full verification (`mise run verify`: Rust fmt, clippy, tests in oxide_kernel/, then Bun install, lint, typecheck, tests) and reports pass/fail. Use before committing any change to this repo, or when asked to "verify", "check everything passes", or "run the checklist".
---

Run the one canonical check from the repo root (CI runs the same command):

```bash
mise run verify
```

It runs, in order, stopping at the first failure (`docs/agents/workflow.md`):
`rust:lint` (cargo fmt --check, clippy -D warnings in `oxide_kernel/`),
`rust:test`, `ts:install` (bun install --frozen-lockfile), `ts:lint` (Biome),
`ts:typecheck` (tsc), `ts:test` (builds `oxide-runtime`, then `bun test`,
including every shared contract case through the real binary).

Report the first failing step and its output; do not run later steps against
code you know is broken. If `cargo fmt --check` fails, run `cargo fmt` in
`oxide_kernel/`; if Biome reports formatting, run `bunx biome check --write`.
Don't hand-edit whitespace.

Report each step's pass/fail plainly. Don't claim success for a step you
didn't actually run. There is no legacy benchmark gate in v2; retrieval
baselines arrive with Phase 3 (`docs/BOOTSTRAP.md`).
