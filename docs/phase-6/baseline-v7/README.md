# Phase 6 Python context-quality baseline v7 (`contextbench-py-v7`)

This is a versioned re-freeze, not a change in engine quality. The ladybug pin moved
from 0.21.2 to 0.21.0 to match the only published FTS extension build
([../../platforms.md](../../platforms.md) § Why ladybug is pinned at 0.21.0). The
`ladybug` derivation component is part of every derivation id, so the compatibility
gate failed as designed.

Everything else is [v6's](../baseline-v6/README.md), and so v3's: the protocol, scorer,
tasks, gold audit, corpora, splits, budgets, tolerance, gated metrics, and every other
engine version.

## What changed, measured

- `results.json` is v6's apart from each task's `versions.derivation`. Entries, routes,
  payload hashes at every budget, loss stages and gold states are all identical.
  `metrics.json` is identical.
- `manifest.json` differs only in `baseline`, `protocol`, `oxide_source`,
  `environment.packages.ladybug` and the derivation ids. `baseline` and `protocol`
  were set to v7's labels after the run, which was made with v6's constants.

v1–v6 stay as frozen.
