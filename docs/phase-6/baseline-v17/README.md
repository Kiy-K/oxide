# baseline-v17: `cpp` component identity amendment

The derivation components gain `cpp` (`oxide-cpp-v1`) and `cpp-parser`
(`tree-sitter-cpp=0.23.4`), and `FACTS_FORMAT` gains `oxide-cpp-facts-v1`, for the C++
language slice ([../../cpp.md](../../cpp.md), evidence in
[../cpp-v1/README.md](../cpp-v1/README.md)). The derivation id hashes every component
version, so every index's id changes. The pinned corpora's results show no C++ effect,
and the Python, TypeScript, JavaScript, Go, Rust, Java and C slices and their facts
encodings are unchanged.

## Measured against baseline-v16

Same tasks, gold audit, scorer, budgets, corpora and tolerance.

| File | Difference from v16 |
| --- | --- |
| `metrics.json` | byte-identical |
| `results.json` | 44 differences, all `versions.derivation` (`sha256:a3c0…` → `sha256:1b2f…`); every task's entries, routed set and payload are identical |
| `manifest.json` | 39 `derivation` ids (38 corpora and `versions`), `oxide_source`, and the `baseline`/`protocol` labels (set to v17 by hand, as in v16) |

This is an identity amendment, not a quality change. `test_output_is_compatible`
reported only the derivation change before the freeze.

Command: `uv run --locked python tests/python/contextbench.py run --out docs/phase-6/baseline-v17`
(run once before the `BASELINE` switch, under a 200% CPU / 3 GB cap).
