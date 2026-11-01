# baseline-v19: identity amendment

`python` `oxide-python-v8`: a class attribute assigned anywhere (`C.m = print`) no longer
resolves a direct `C.m()` call to the declaration. The derivation id hashes every
component version, so every index's id changes; the path does not fire on the pinned corpora.

Against baseline-v18 (same tasks, gold audit, scorer, budgets, corpora, tolerance):
`metrics.json` byte-identical; `results.json` differs only in 44 `versions.derivation`
values; `manifest.json` in 39 `derivation` ids, `oxide_source`, and the
`baseline`/`protocol` labels (set by hand).

Command: `uv run --locked python tests/python/contextbench.py run --out fixtures/eval/contextbench-py/baseline-v19`
(under a 200% CPU / 3 GB cap). v18 is recoverable at `5bb0b2e`.
