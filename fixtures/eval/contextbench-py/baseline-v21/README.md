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
