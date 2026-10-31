# baseline-v14: `oxide-python-v6` identity amendment

`oxide-python-v6` (facts `oxide-python-facts-v4`) narrows one rule of receiver-aware
Python method dispatch:

- An attribute assignment now hides a method only when the assignment's receiver has a
  known class, and that class is on the dispatched receiver's MRO.
- Assignments through receivers of unknown type no longer hide anything.

The graph change and its evidence are in
[../graph-navigation-v2/README.md](../graph-navigation-v2/README.md). Every edge it adds
has `basis="heuristic"`, and the router does not follow heuristic `calls`/`references`
edges.

## Measured against baseline-v13

Same tasks, gold audit, scorer, budgets, corpora and tolerance.

| File | Difference from v13 |
| --- | --- |
| `metrics.json` | byte-identical |
| `results.json` | 44 differences, all `versions.derivation` (`sha256:8213…` → `sha256:d89f…`); every task's entries, routed set and payload are identical |
| `manifest.json` | 38 corpus `derivation` ids, `versions.derivation`, `oxide_source`, and the `baseline`/`protocol` labels (set to v14 by hand, as in v13) |

This is an identity amendment, not a quality change.
`test_quality_does_not_regress` passed before the freeze. `test_output_is_compatible`
reported only the derivation change.

Command: `uv run --locked python tests/python/contextbench.py run --out docs/phase-6/baseline-v14`
(run once before the `BASELINE` switch).
