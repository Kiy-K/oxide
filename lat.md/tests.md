# Tests and verification

Quality is gated by pytest, static checks and frozen benchmarks; `mise run verify` is the single entrypoint CI also runs.

## Verification

`mise run verify` runs Ruff lint and format, mypy `--strict` and pytest; `mise run pythons` adds CI's other Python versions before a push.

See `mise.toml`. `mise run tier` selects the review rigor a change needs ([[docs/agents/workflow.md]]).

## Test isolation

Every test and subprocess gets a private data location from `tests/python/conftest.py`, so tests never touch real user data.

## Frozen baselines

`tests/python/test_baseline.py` checks Phase 3 retrieval exactly against frozen artifacts.

Retrieval, routing and packing changes are judged against ContextBench corpora (`tests/python/contextbench*.py`, protocol in [[fixtures/eval/README.md]]); artifacts are never regenerated to pass.

## Fixtures

`fixtures/<lang>_repo` are small per-language repositories; `fixtures/eval/` holds ContextBench, agent-swe and structural benchmarks.

## Agent evaluation

`scripts/agent_eval/` and `tests/python/test_agent_*.py` run and score end-to-end agent tasks; `scripts/bench.py` measures speed.
