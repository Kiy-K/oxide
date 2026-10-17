# TypeScript context-quality baseline v3 (`contextbench-ts-v3`)

This is a versioned re-freeze, not a change in engine quality. Path-hint selection
([../path-hint-utility](../path-hint-utility/README.md)) changes the selector from
`oxide-select-greedy-v3` to `oxide-select-greedy-v4`. That changes `versions.selector`,
so the compatibility gate failed as designed.

The change only acts on `paths` hints. The ContextBench harness passes none, so
everything else is [v2's](../typescript-v2/README.md): the v1 protocol with the v2
corpora amendment, the scorer, tasks, gold audit, budgets, tolerance, gated metrics,
and every other engine version.

## What changed, measured

- `results.json` and `metrics.json` are v2's, apart from `versions.selector`. Entries,
  routes, payload hashes at every budget, metrics, loss stages and gold states are all
  identical.
- `manifest.json` differs only in `baseline`, `protocol`, `oxide_source` and
  `versions.selector`.
- `performance.json` is the new run's timings.

v1 and v2 stay as frozen.
