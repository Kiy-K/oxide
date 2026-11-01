# baseline-v16: identity amendment

`python` `oxide-python-v8` (a class attribute assigned anywhere no longer resolves a
direct `C.m()` call). The derivation id hashes every component version, so every
index's id changes.

Against baseline-v15 (same tasks, gold audit, scorer, budgets, corpora, tolerance):
`metrics.json` byte-identical; `results.json` differs only in 89 `versions.derivation`
values; `manifest.json` in 83 `derivation` ids, `oxide_source` and `protocol`.

Command: `OXIDE_CONTEXTBENCH_TS=1 uv run --locked python tests/python/contextbench_ts.py run --out fixtures/eval/contextbench-ts/baseline-v16`
(under a 200% CPU / 3 GB cap). v15 is recoverable at `5bb0b2e`.
