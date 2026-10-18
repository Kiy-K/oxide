# TypeScript context-quality baseline v5 (`contextbench-ts-v5`)

This is a versioned re-freeze, not a change in engine quality. Both language slices
now refuse a file whose syntax tree is deeper than tree-sitter's query cursor can
match ([../../parser-limits.md](../../parser-limits.md)): the `python` and
`typescript` derivation components moved to `oxide-python-v3` and
`oxide-typescript-v3`, which changes every derivation id, so the compatibility gate
failed as designed.

Everything else is [v4's](../typescript-v4/README.md): the v1 protocol with the v2
corpora amendment, the scorer, tasks, gold audit, budgets, tolerance, gated metrics,
and every other engine version.

## What changed, measured

- `results.json` is v4's apart from each task's `versions.derivation`. Entries, routes,
  payload hashes at every budget, loss stages and gold states are all identical.
  `metrics.json` is identical.
- `manifest.json` differs only in `baseline`, `protocol`, `oxide_source` and the
  derivation ids. `baseline` and `protocol` were set to v5's labels after the run,
  which was made with v4's constants.
- `performance.json` is the new run's timings. Peak RSS is 1,253 MB (v4: 1,252 MB).

v1–v4 stay as frozen.
