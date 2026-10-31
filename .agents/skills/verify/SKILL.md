---
name: verify
description: Runs OXIDE v2's full verification (`mise run verify`: Ruff lint and format check, mypy --strict, pytest over the Python engine) and reports pass/fail. Use before committing any change to this repo, or when asked to "verify", "check everything passes", or "run the checklist".
---

Run the one canonical check from the repo root (CI runs the same command):

```bash
mise run verify
```

It runs, in order, stopping at the first failure (`docs/agents/workflow.md`):
`lint` (`ruff check` and `ruff format --check` over `src/` and `tests/python/`),
`typecheck` (`mypy --strict`), `test` (`pytest`, including the frozen Phase 3
baseline oracle). Lexical tests need the pinned FTS extension: run
`mise run bootstrap` (or `uv run oxide prepare`) once.

Report the first failing step and its output; do not run later steps against
code you know is broken. If `ruff format --check` fails, run
`uv run ruff format src tests/python`; don't hand-edit whitespace.

Report each step's pass/fail plainly. Don't claim success for a step you
didn't actually run. Never regenerate a frozen baseline to make a test pass.
