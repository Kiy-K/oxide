# Baseline identity verification

- `git rev-parse HEAD`: `3492b388eebf3c7b60973f8080bece664a7c2e1a`.
- Python recursive normalization check: each `derivation` value is replaced; in manifests, `baseline`, `protocol`, and `oxide_source` are replaced too. The normalized JSON structures were equal.
- `baseline-v14` → `baseline-v15`: results normalized 88 derivation values; manifests normalized 78 derivation values and 2 each of baseline, protocol, and oxide_source. `metrics.json` is byte-identical.
- `typescript-v11` → `typescript-v12`: results normalized 178 derivation values; manifests normalized 166 derivation values and 2 each of baseline, protocol, and oxide_source. Across 82 paired records, the largest `index_s` ratio is 1.40899 (`darkreader__darkreader@a787eb511f45`, 0.467s → 0.658s); `max_rss_mb` changes range from +3 to +135 MB.
- Derivation IDs: baseline-v14 `sha256:d89f4dac0d5e4fc27fa3956b519644b1add4d1ac540cdfa440575023092b5478`; baseline-v15 `sha256:04c247de3dee90ded12945ccc46fe05dc9b56cb9a27086c93329dc80828a27b4`.
- `OXIDE_CONTEXTBENCH_TS=1 uv run --locked pytest tests/python/test_contextbench_ts.py`: **PASS**, 17 passed.

Commands/results log: `verification/contextbench-ts.log`. No differences outside the normalized identity fields were found.
