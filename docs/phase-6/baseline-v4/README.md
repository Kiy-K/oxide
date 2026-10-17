# Phase 6 Python context-quality baseline v4 (`contextbench-py-v4`)

A versioned re-freeze, not an engine-quality change. The TypeScript language slice
([docs/typescript.md](../../typescript.md)) adds two derivation components
(`typescript`, `typescript-parser`), so every generation's `derivation_id` changed,
and the compatibility gate failed as designed (`versions.derivation`). The protocol,
scorer, tasks, gold audit, corpora, splits, budgets, tolerance and gated metrics are
[v3's](../baseline-v3/README.md), and so is every engine version but the derivation.
v1–v3 stay as frozen.

## What changed, measured

- No corpus contains a TypeScript file (`*.ts`, `*.tsx`, `*.mts`, `*.cts`, counted in
  every pinned checkout), so no entity, relation or lexical document changed.
- Against v3, `results.json` differs only in `versions.derivation` of each task:
  entries, routes and the payload hashes at 256/1,024/4,096 units (`compat`), all
  metrics, loss stages and gold states are identical. `metrics.json` is byte-identical
  to v3's.
- `manifest.json` differs in the corpus `derivation` ids, `oxide_source`, and the
  environment's package list, which now names `tree-sitter-typescript` 0.23.2.
- Two runs gave byte-identical `results.json`, `metrics.json` and `manifest.json`.

Results are therefore v3's tables unchanged.
