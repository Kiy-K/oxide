# RESUME: Tier B downstream evidence (paused for refactor)

## Where we stopped (2026-08-28)
- All Tier B / harness smoke runs were killed or invalidated; nothing live.
- Switched MODEL to `opencode/muse-spark-1.2-contributor-free` in
  `scripts/agent_eval/tierb_agent_run.py` (uncommitted) — earlier smokes with
  this model had valid opencode output but baseline was `dead_test` due to
  missing repo-specific test deps.
- Earlier `cline-pass/cline-pass/minimax-m3` 13-task small expanded was
  killed; its partial workdirs and logs remain under
  `eval-agent/results/tierb_expanded/` (preserved).

## What's known to be broken / missing
- `scripts/agent_eval/tierb_solver.py::run_pytest` previously returned
  `no_tests` (None,None) on repos without `git ls-files tests/ test/`
  hits; now always runs `pytest -x`. Verified by re-anchored read.
- eval venv at `eval-agent/.venv` is missing pytest entirely; installed via
  `uv pip install --python eval-agent/.venv/bin/python pytest pytest-timeout`.
- Per-repo test deps are not pre-installed: matplotlib for seaborn, astroid
  + isort for pylint, etc. Without these, `baseline_pytest` is `dead_test`
  on every task for those repos.
- Clinepass (opencode model) hits "Error 429: monthly Clinepass limit" on
  some tasks; detected as `provider_failed` in
  `scripts/agent_eval/tierb_solver.py:128-130` (added 2026-08-27).

## Negative results (preserve)
- `psf/requests` is dead_test on Python 3.11 (uses `collections.MutableMapping`,
  removed in 3.10). Use a different task or 3.9 venv.
- `mwaskom/seaborn`: even with `matplotlib/pandas/numpy` installed, full
  `pytest tests/ -x` collection error in `tests/test_core.py`
  (`Marks cannot be applied to fixtures` on pytest 9.1.1). `dead_test`
  on the actual solver's full-suite run; the `--ignore=tests/test_core.py`
  preflight is NOT solver evidence.
- `pylint-dev/pylint` requires pinned `astroid<2.7` + `isort`; latest
  astroid is 4.3.1. Don't treat the env's latest-astroid preflight as
  baseline evidence.

## What to do when refactor lands
1. Pick a Tier B candidate repo where the eval venv can run the baseline
   pytest to completion. `astropy`, `django`, `sympy`, `transformers`
   were not yet preflighted; check first.
2. Stage only the intended refactor commits — do not commit
   `tierb_solver_smoke*` logs or workdirs.
3. The MODEL change in `scripts/agent_eval/tierb_agent_run.py` is
   uncommitted; verify whether the new model is still valid before
   re-running, or revert to `cline-pass/cline-pass/minimax-m3`.
4. After picking a runnable repo, launch 13-task small expanded the same
   way (counterbalanced, seed=42) and append to
   `eval-agent/results/tierb_expanded/agent_results.jsonl` (resume-safe
   by `(task, condition)`).
5. `tool_calls_proxy` is still 0.0 because the harness counts "\n$ "
   which current opencode doesn't emit; not a blocker for gold/bad/wall/ctx.

## Env reminders
- llama server: `scripts/embedder.sh start` (was up, port 8191, Q8_0).
- eval venv: `eval-agent/.venv/bin/python` (3.11).
- Tier B SOLVE runner: `scripts/agent_eval/tierb_solver.py`
  (`--limit-per-repo N --repos ... --conditions stock,budgeted --out ...`).
- Kill stale process groups: `pkill -f tierb_solver` is enough for the
  Python parent; orphaned `timeout`+`opencode` children need explicit
  `kill -9 <pid>` or `kill -- -<pgid>`.
- Background jobs: `setsid ... &` via wrapper script; kill by exact PID
  (`pgrep -fa` + kill), never `pkill -f <pattern>` (self-match kills own shell).
- Laptop resource-conscious: `-j 2` everywhere.
