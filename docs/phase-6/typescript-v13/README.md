# typescript-v13: `c` component identity amendment

The derivation components gain `c` (`oxide-c-v1`) and `c-parser` (`tree-sitter-c=0.24.2`),
for the C language slice ([../../c.md](../../c.md), evidence in
[../c-v1/README.md](../c-v1/README.md)). The derivation id hashes every component version,
so every index's id changes. The pinned corpora's results show no C effect, and the
TypeScript, JavaScript, Python, Go, Rust and Java slices and their facts encodings are
unchanged.

## Measured against typescript-v12

| File | Difference from v12 |
| --- | --- |
| `metrics.json` | byte-identical |
| `results.json` | 89 differences, all `versions.derivation` |
| `manifest.json` | 82 corpus `derivation` ids, `versions.derivation`, `oxide_source`. The `baseline`/`protocol` labels are fixed strings and unchanged, as in v12 |

This is an identity amendment, not a quality change.

`performance.json` is re-measured, under the 200% CPU / 3 GB cap:
- `index_s` is 0.71–1.11× v12's (median 0.97×) on all 82 corpora;
- `max_rss_mb` is 17 MB lower to 23 MB higher.

The two runs were in different sessions; this table is a diagnostic, not a like-for-like
cost of the change.

Command: `OXIDE_CONTEXTBENCH_TS=1 uv run --locked python tests/python/contextbench_ts.py run --out docs/phase-6/typescript-v13`.
