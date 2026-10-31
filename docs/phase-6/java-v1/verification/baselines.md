# Successor baselines: verification (measured)

Script: `oxide-java-eval/scripts/cmpbase.py OLD NEW` (scratch). It asserts `metrics.json` byte equality, and it
asserts equality of `results.json`/`manifest.json` after skipping exactly the allowed fields.

| Pair | `metrics.json` | `results.json` normalized | `manifest.json` normalized | Other differences |
| --- | --- | --- | --- | --- |
| baseline-v14 → baseline-v15 | byte-identical | 44 × `derivation` | 39 × `derivation` (38 corpora + `versions`), 1 each `baseline`, `protocol`, `oxide_source` | 0 |
| typescript-v11 → typescript-v12 | byte-identical | 89 × `derivation` | 83 × `derivation` (82 corpora + `versions`), 1 × `oxide_source` | 0 |

Derivation ids: `sha256:d89f4dac0d5e4fc27fa3956b519644b1add4d1ac540cdfa440575023092b5478` (v14/v11) →
`sha256:04c247de3dee90ded12945ccc46fe05dc9b56cb9a27086c93329dc80828a27b4` (v15/v12).

`typescript-v12/performance.json` vs v11 across all 82 corpora:
- `index_s` ratio: min 0.94, median 1.12, max 1.41;
- `max_rss_mb` change: +3 to +135 MB.

These are two sessions under the same 200% CPU cap, so this is a diagnostic only.

`OXIDE_CONTEXTBENCH_TS=1 uv run --locked pytest tests/python/test_contextbench_ts.py`: **17 passed** in 498 s
([contextbench-ts.log](contextbench-ts.log)).
