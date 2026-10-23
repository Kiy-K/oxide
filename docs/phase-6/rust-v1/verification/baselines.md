# Rust support baseline verification

Commands were run under the requested user scope limits with `TMPDIR=~/Projects/oxide-eval-data/oxide-rust-eval/tmp`.

## Python ContextBench

- Command: `uv run --locked python tests/python/contextbench.py run --out docs/phase-6/baseline-v11`
- Comparison: `cmp` passed for `metrics.json`; normalized comparison passed for `results.json` (only `versions.derivation`; 1 allowed field category) and `manifest.json` (only `baseline`, `protocol`, `oxide_source`, `derivation`; 4 allowed field categories). No other differences.
- Derivation: baseline-v10 `sha256:0b15e6052dc84111a848d7bc5d0262e84a55490899426bc3bb4a9536e83b8bb2`; baseline-v11 `sha256:92088ff7b036404c90a61d316d011cc34f503fd985ed4dbee83f9dbcc0a5d5ab`.
- Result: PASS.

## TypeScript ContextBench

- Command: `uv run --locked python tests/python/contextbench_ts.py run --out docs/phase-6/typescript-v8`
- Comparison: 82 performance rows. `cmp` passed for `metrics.json`; normalized comparison passed for `results.json` (only `versions.derivation`) and `manifest.json` (only `baseline`, `protocol`, `oxide_source`, `derivation`). No other differences.
- Performance variation: largest `index_s` ratio (v8/v7) was `0.949541` for `mui__material-ui@f432f8a73351` (0.436 s to 0.414 s). Largest absolute `max_rss_mb` change was +30 MB for `microsoft__vscode@384b03f26310` (1198 MB to 1228 MB).
- Derivation: TypeScript-v7 `sha256:0b15e6052dc84111a848d7bc5d0262e84a55490899426bc3bb4a9536e83b8bb2`; TypeScript-v8 `sha256:92088ff7b036404c90a61d316d011cc34f503fd985ed4dbee83f9dbcc0a5d5ab`.
- Command: `OXIDE_CONTEXTBENCH_TS=1 uv run --locked pytest tests/python/test_contextbench_ts.py`
- Result: PASS, 17 passed in 622.47 s.

Logs: full run output is in `~/Projects/oxide-eval-data/oxide-rust-eval/step-1a.log` and `step-1b-run.log`; comparison/test logs are in `step-1a-compare.log` and `step-1b-compare-test.log` in the same scratch directory.
