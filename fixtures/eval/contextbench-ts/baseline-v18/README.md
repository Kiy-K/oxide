# baseline-v18: lexical index in its own database

Store schema `oxide-ladybug-py-v4`: the FTS documents and index move from `graph.lbug` to
a per-generation `lexical.lbug`, built while the graph loads and hard-linked from the
previous generation when the documents are unchanged. Only the derivation id changes
(it includes the store schema).

Against baseline-v17: `results.json` identical after substituting the derivation id,
`metrics.json` byte-identical, no `default_index_failure` (a first run at a 2 B/B
lexical pool failed on all 14 VS Code corpora; the pool is now 10 B/B, measured).
`performance.json`: total cold index 418→352 s (−16%), max RSS 1340→1591 MB.
`manifest.json` differs in `derivation` and `oxide_source`.

Command: `OXIDE_CONTEXTBENCH_TS=1 uv run --locked python tests/python/contextbench_ts.py run --out fixtures/eval/contextbench-ts/baseline-v18`
(under a 200% CPU / 3 GB cap). v17 is recoverable at `665e97f`.
