# typescript-v14: `cpp` component identity amendment

The derivation components gain `cpp` (`oxide-cpp-v1`) and `cpp-parser`
(`tree-sitter-cpp=0.23.4`), for the C++ language slice ([../../cpp.md](../../cpp.md),
evidence in [../cpp-v1/README.md](../cpp-v1/README.md)). The derivation id hashes every
component version, so every index's id changes. The pinned corpora's results show no C++
effect, and the TypeScript, JavaScript, Python, Go, Rust, Java and C slices and their
facts encodings are unchanged.

## Measured against typescript-v13

| File | Difference from v13 |
| --- | --- |
| `metrics.json` | byte-identical |
| `results.json` | 89 differences, all `versions.derivation` |
| `manifest.json` | 82 corpus `derivation` ids, `versions.derivation`, `oxide_source`. The `baseline`/`protocol` labels are fixed strings and unchanged, as in v13 |

This is an identity amendment, not a quality change.

`performance.json` is re-measured, under the 200% CPU / 3 GB cap:
- `index_s` is 0.84–1.16× v13's (median 0.99×) on all 82 corpora;
- `max_rss_mb` is 36 MB lower to 78 MB higher.

The two runs were in different sessions; this table is a diagnostic, not a like-for-like
cost of the change.

Command: `OXIDE_CONTEXTBENCH_TS=1 uv run --locked python tests/python/contextbench_ts.py run --out docs/phase-6/typescript-v14`.
