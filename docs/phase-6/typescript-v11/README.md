# typescript-v11: `oxide-python-v6` identity amendment

The `python` component moves to `oxide-python-v6` (facts `oxide-python-facts-v4`), which
narrows the attribute-shadowing rule of Python method dispatch. The graph change and its
evidence are in [../graph-navigation-v2/README.md](../graph-navigation-v2/README.md).
The derivation id hashes every component version, so every index's id changes. The
TypeScript, JavaScript, Go and Rust slices and their facts encodings are unchanged.

## Measured against typescript-v10

| File | Difference from v10 |
| --- | --- |
| `metrics.json` | byte-identical |
| `results.json` | 89 differences, all `versions.derivation` |
| `manifest.json` | 82 corpus `derivation` ids, `versions.derivation`, `oxide_source`. The `baseline`/`protocol` labels are fixed strings and unchanged, as in v10 |

This is an identity amendment, not a quality change.

`performance.json` is re-measured:
- `index_s` is 0.30–0.57× v10's (median 0.47×) on all 82 corpora;
- `max_rss_mb` moves by −2 to +1 MB.

The speed-up is the machine, not the code. v10 was measured on battery in the
`powersave` profile; this run was on AC power, under the 200% CPU cap. Like-for-like
index time for this change is in
[../graph-navigation-v2/README.md](../graph-navigation-v2/README.md) § Acceptance checks
(+0.2% to +4.9% on five Python corpora, the same session).

Command: `OXIDE_CONTEXTBENCH_TS=1 uv run --locked python tests/python/contextbench_ts.py run --out docs/phase-6/typescript-v11`.
