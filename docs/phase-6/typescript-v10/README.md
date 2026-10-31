# typescript-v10: `oxide-python-v5` identity amendment

The `python` component moves to `oxide-python-v5` (receiver-aware method dispatch,
[../graph-v2/README.md](../graph-v2/README.md)) and the store schema to
`oxide-ladybug-py-v2`, which changes the derivation id of every index. The TypeScript,
JavaScript, Go and Rust slices and their facts encodings are unchanged (`Nesting` moved to
`core/languages/nesting.py`; the TypeScript slice only imports it from there).

Measured against [typescript-v9](../typescript-v9/README.md): results are identical except
`versions.derivation`; metrics are byte-identical. Manifest changes are derivation
identities and `oxide_source`. This is an identity amendment, not a quality change.

`performance.json` is re-measured: `index_s` is 1.65–3.19× v9's on every corpus, and
`max_rss_mb` moves by −61 to +10 MB. The slowdown is the machine, not the code: the run was
on battery in the `powersave` profile (2.1 of 4.7 GHz). In that same machine state, HEAD
(`1b11ab6`, the v9 code) and this code index darkreader@a787eb511f45 in 1.15–1.22 s and
1.08–1.12 s, with or without the 200% CPU cap. Compare `index_s` across freezes only from
runs under the same power profile.

Command: `OXIDE_CONTEXTBENCH_TS=1 uv run --locked python tests/python/contextbench_ts.py run --out docs/phase-6/typescript-v10`.
