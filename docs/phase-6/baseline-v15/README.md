# baseline-v15: `java` component identity amendment

The derivation components gain `java` (`oxide-java-v1`) and `java-parser`
(`tree-sitter-java=0.23.5`), and `FACTS_FORMAT` gains `oxide-java-facts-v1`, for the Java
language slice ([../../java.md](../../java.md), evidence in
[../java-v1/README.md](../java-v1/README.md)). The derivation id hashes every component
version, so every index's id changes. The pinned corpora hold no Java, and the Python,
TypeScript, JavaScript, Go and Rust slices and their facts encodings are unchanged.

## Measured against baseline-v14

Same tasks, gold audit, scorer, budgets, corpora and tolerance.

| File | Difference from v14 |
| --- | --- |
| `metrics.json` | byte-identical |
| `results.json` | 44 differences, all `versions.derivation` (`sha256:d89f…` → `sha256:04c2…`); every task's entries, routed set and payload are identical |
| `manifest.json` | 38 corpus `derivation` ids, `versions.derivation`, `oxide_source`, and the `baseline`/`protocol` labels (set to v15 by hand, as in v14) |

This is an identity amendment, not a quality change. `test_output_is_compatible`
reported only the derivation change before the freeze.

Command: `uv run --locked python tests/python/contextbench.py run --out docs/phase-6/baseline-v15`
(run once before the `BASELINE` switch).
