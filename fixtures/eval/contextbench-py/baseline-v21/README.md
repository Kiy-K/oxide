# baseline-v21: lexical index in its own database

Store schema `oxide-ladybug-py-v4`: the FTS documents and index move from `graph.lbug` to
a per-generation `lexical.lbug`, built while the graph loads and hard-linked from the
previous generation when the documents are unchanged. Only the derivation id changes
(it includes the store schema).

Against baseline-v20: `results.json` identical after substituting the derivation id,
`metrics.json` byte-identical. `manifest.json` differs in `derivation`, `oxide_source`
and the `baseline`/`protocol` labels (set by hand).

Command: `uv run --locked python tests/python/contextbench.py run --out fixtures/eval/contextbench-py/baseline-v21`
(under a 200% CPU / 3 GB cap). v20 is recoverable at `665e97f`.

Pin relabel (2026-11-11): `manifest.json` `derivation` (the `versions` entry and the 38
corpus labels) changed from `sha256:ff500e5a…` to `sha256:daa271b3…`. Verified against
HEAD: the only changed derivation components are `storage-schema` (`oxide-ladybug-py-v4` →
`v5`) and the new `blast` component. `results.json` keeps the id each task was produced with,
and its compatibility fields are unchanged (`test_output_is_compatible` passes), so
`results.json` and `metrics.json` were not regenerated.
