# baseline-v16: `c` component identity amendment

The derivation components gain `c` (`oxide-c-v1`) and `c-parser`
(`tree-sitter-c=0.24.2`), and `FACTS_FORMAT` gains `oxide-c-facts-v1`, for the C language
slice ([../../c.md](../../c.md), evidence in [../c-v1/README.md](../c-v1/README.md)). The
derivation id hashes every component version, so every index's id changes. The pinned
corpora's results show no C effect, and the Python, TypeScript, JavaScript, Go, Rust and
Java slices and their facts encodings are unchanged.

## Measured against baseline-v15

Same tasks, gold audit, scorer, budgets, corpora and tolerance.

| File | Difference from v15 |
| --- | --- |
| `metrics.json` | byte-identical |
| `results.json` | 44 differences, all `versions.derivation` (`sha256:04c2…` → `sha256:a3c0…`); every task's entries, routed set and payload are identical |
| `manifest.json` | 38 corpus `derivation` ids, `versions.derivation`, `oxide_source`, and the `baseline`/`protocol` labels (set to v16 by hand, as in v15) |

This is an identity amendment, not a quality change. `test_output_is_compatible`
reported only the derivation change before the freeze.

Command: `uv run --locked python tests/python/contextbench.py run --out docs/phase-6/baseline-v16`
(run once before the `BASELINE` switch, under a 200% CPU / 3 GB cap).
