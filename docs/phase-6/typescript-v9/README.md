# typescript-v9: `oxide-python-v4` identity amendment

The `python` component moves to `oxide-python-v4` (Python cross-file resolution,
[../graph-v1/README.md](../graph-v1/README.md)), which changes the derivation id of every
index. TypeScript, JavaScript, Go and Rust slices and all facts encodings are unchanged.

Measured against [typescript-v8](../typescript-v8/README.md): results are identical except
`versions.derivation`; metrics are byte-identical. Manifest changes are labels, derivation
identities and `environment.python` 3.13.16 → 3.13.17 (a local interpreter update).
Timings and memory in `performance.json` are re-measured: the largest `index_s` ratio is
1.03 (vscode@8c76afad6ccf) and the largest absolute `max_rss_mb` change is −28 MB
(vscode@8ce35fa28ded). This is an identity amendment, not a quality change.
`OXIDE_CONTEXTBENCH_TS=1 uv run --locked pytest tests/python/test_contextbench_ts.py`
passes on the final code (17 passed).
