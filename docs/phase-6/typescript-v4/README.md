# TypeScript context-quality baseline v4 (`contextbench-ts-v4`)

This is a versioned re-freeze, not a change in engine quality. The ladybug pin moved
from 0.21.2 to 0.21.0 to match the only published FTS extension build
([../../platforms.md](../../platforms.md) § Why ladybug is pinned at 0.21.0). The
`ladybug` derivation component is part of every derivation id, so the compatibility
gate failed as designed.

Everything else is [v3's](../typescript-v3/README.md): the v1 protocol with the v2
corpora amendment, the scorer, tasks, gold audit, budgets, tolerance, gated metrics,
and every other engine version.

## What changed, measured

- `results.json` is v3's apart from each task's `versions.derivation`. Entries, routes,
  payload hashes at every budget, loss stages and gold states are all identical.
  `metrics.json` is identical.
- `manifest.json` differs only in `baseline`, `protocol`, `oxide_source`,
  `environment.packages.ladybug` and the derivation ids. `baseline` and `protocol`
  were set to v4's labels after the run, which was made with v3's constants.
- `performance.json` is the new run's timings. Peak RSS is 1,252 MB (v3: 1,221 MB),
  including every pinned VS Code commit, so the load pool sized on 0.21.2
  (`docs/large-repositories.md`) still holds on 0.21.0.

v1–v3 stay as frozen.
