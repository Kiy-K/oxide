# baseline-v18: PR #45 review-fix identity amendment

The review fixes for PR #45 bump versions that feed the frozen identity:
`typescript` `oxide-typescript-v4` (callback and block scopes in TypeScript/TSX) with
`FACTS_FORMAT` `oxide-typescript-facts-v3`, `python` `oxide-python-v7` (re-export
chains bounded at 32), `java` `oxide-java-v2` (inheritance diamonds keep inherited
methods), and `router` `oxide-router-bfs-v3` (store reads bounded by an edge
allowance; a cut layer is deferred with `stopped_by: "edges"`). The derivation id
hashes every component version, so every index's id changes. None of these paths fires
on the pinned corpora.

## Measured against baseline-v17

Same tasks, gold audit, scorer, budgets, corpora and tolerance.

| File | Difference from v17 |
| --- | --- |
| `metrics.json` | byte-identical |
| `results.json` | 88 differences, all `versions.derivation` (`sha256:1b2f…` → `sha256:2f7a…`) and `versions.router` (`bfs-v2` → `bfs-v3`); every task's entries, routed set and payload are identical |
| `manifest.json` | 39 `derivation` ids (38 corpora and `versions`), `versions.router`, `oxide_source`, and the `baseline`/`protocol` labels (set to v18 by hand, as in v17) |

This is an identity amendment, not a quality change. `test_output_is_compatible`
reported only the router and derivation changes before the freeze.

Command: `uv run --locked python tests/python/contextbench.py run --out docs/phase-6/baseline-v18`
(run once before the `BASELINE` switch, under a 200% CPU / 3 GB cap).
