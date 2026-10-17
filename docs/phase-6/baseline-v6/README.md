# Phase 6 Python context-quality baseline v6 (`contextbench-py-v6`)

This is a versioned re-freeze, not a change in engine quality. Path-hint selection
([../path-hint-utility](../path-hint-utility/README.md)) changes the selector from
`oxide-select-greedy-v3` to `oxide-select-greedy-v4`. That changes `versions.selector`,
so the compatibility gate failed as designed.

The change only acts on `paths` hints. The ContextBench harness passes none, so
everything else is [v5's](../baseline-v5/README.md), and so v3's: the protocol, scorer,
tasks, gold audit, corpora, splits, budgets, tolerance, gated metrics, and every other
engine version.

## What changed, measured

- `results.json` and `metrics.json` are v5's, apart from `versions.selector`. Entries,
  routes, payload hashes at every budget, metrics, loss stages and gold states are all
  identical.
- `manifest.json` differs only in `baseline`, `protocol`, `oxide_source` and
  `versions.selector`.

v1–v5 stay as frozen.
