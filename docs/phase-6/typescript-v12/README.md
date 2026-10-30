# typescript-v12: `java` component identity amendment

The derivation components gain `java` (`oxide-java-v1`) and `java-parser`
(`tree-sitter-java=0.23.5`), for the Java language slice ([../../java.md](../../java.md),
evidence in [../java-v1/README.md](../java-v1/README.md)). The derivation id hashes every
component version, so every index's id changes. The pinned corpora hold no Java, and the
TypeScript, JavaScript, Python, Go and Rust slices and their facts encodings are unchanged.

## Measured against typescript-v11

| File | Difference from v11 |
| --- | --- |
| `metrics.json` | byte-identical |
| `results.json` | 89 differences, all `versions.derivation` |
| `manifest.json` | 82 corpus `derivation` ids, `versions.derivation`, `oxide_source`. The `baseline`/`protocol` labels are fixed strings and unchanged, as in v11 |

This is an identity amendment, not a quality change.

`performance.json` is re-measured, under the 200% CPU cap:
- `index_s` is 0.94–1.41× v11's (median 1.12×) on all 82 corpora;
- `max_rss_mb` is 3 to 135 MB higher.

The two runs were in different sessions; this table is a diagnostic, not a like-for-like
cost of the change, which parses no file of these corpora as Java.

Command: `OXIDE_CONTEXTBENCH_TS=1 uv run --locked python tests/python/contextbench_ts.py run --out docs/phase-6/typescript-v12`.
