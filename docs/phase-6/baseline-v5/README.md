# Phase 6 Python context-quality baseline v5 (`contextbench-py-v5`)

This is a versioned re-freeze, not a change in engine quality. TypeScript module
resolution through `tsconfig.json` ([docs/typescript.md](../../typescript.md)) changes
the `typescript` derivation component from `oxide-typescript-v1` to `oxide-typescript-v2`.
That changes every generation's `derivation_id`, so the compatibility gate failed as
designed (`versions.derivation`).

Everything else is [v4's](../baseline-v4/README.md), and so [v3's](../baseline-v3/README.md):
- the protocol, scorer, tasks and gold audit;
- the corpora, splits, budgets, tolerance and gated metrics;
- every engine version except the derivation.

v1–v4 stay as frozen.

## What changed, measured

- No corpus contains a TypeScript source or a `tsconfig.json`, so no entity, relation
  or lexical document changed.
- Against v4, `results.json` differs only in `versions.derivation` of each task. These
  are identical:
  - entries and routes;
  - the payload hashes at 256, 1,024 and 4,096 units (`compat`);
  - all metrics, loss stages and gold states.
- `metrics.json` is byte-identical to v4's.
- `manifest.json` differs only in `baseline`, `protocol`, `oxide_source`, `versions`
  and the corpus `derivation` ids.
- Two runs gave byte-identical `results.json`, `metrics.json` and `manifest.json`.

The results are therefore v3's tables, unchanged.
