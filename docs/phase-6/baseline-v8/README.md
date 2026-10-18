# Phase 6 Python context-quality baseline v8 (`contextbench-py-v8`)

This is a versioned re-freeze, not a change in engine quality. Both language slices
now refuse a file whose syntax tree is deeper than tree-sitter's query cursor can
match ([../../parser-limits.md](../../parser-limits.md)): the `python` and
`typescript` derivation components moved to `oxide-python-v3` and
`oxide-typescript-v3`, which changes every derivation id, so the compatibility gate
failed as designed.

Everything else is [v7's](../baseline-v7/README.md), and so v3's: the protocol, scorer,
tasks, gold audit, corpora, splits, budgets, tolerance, gated metrics, and every other
engine version.

## What changed, measured

- `results.json` is v7's apart from each task's `versions.derivation`. Entries, routes,
  payload hashes at every budget, loss stages and gold states are all identical.
  `metrics.json` is identical.
- `manifest.json` differs only in `baseline`, `protocol`, `oxide_source` and the
  derivation ids. `baseline` and `protocol` were set to v8's labels after the run,
  which was made with v7's constants.

v1–v7 stay as frozen.
