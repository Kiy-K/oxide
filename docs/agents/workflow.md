# OXIDE development workflow

Commands and verification for OXIDE v2, loaded on demand from the root
`AGENTS.md` routing table. Older docs and code comments that cite
"`AGENTS.md`" for a command or harness detail mean this file.

## Commands

[Mise](https://mise.jdx.dev/installing-mise.html) is the one entrypoint;
`mise.toml` pins uv, and `.python-version` pins the development Python (uv
installs it). CI (`.github/workflows/ci.yml`) runs exactly `mise run verify`.

```bash
mise run bootstrap     # uv sync --locked + `oxide prepare` (pinned FTS extension, sha256-checked)
                       #   + ContextBench corpora at pinned commits (git, cached)
mise run verify        # everything, in order, stopping at the first failure:
mise run lint          #   ruff check + ruff format --check
mise run typecheck     #   mypy --strict over src/ and tests/python/
mise run test          #   pytest (tests/python/)
```

Use `uv run …` for anything else (`uv run oxide index <path>`, `uv run pytest -k name`).
`uv run oxide mcp --repo <path>` serves the MCP tools over stdio (`--http` for
stateless HTTP on loopback); `docs/public-api.md` § MCP has the contract.
`uv run python scripts/bench.py <corpus>` is the reproducible latency and
peak-RSS benchmark: fresh processes and private data, cold and warm definitions
in its docstring, and a JSON report with the corpus identity and environment.
Run it under the limits below; recorded runs are in `docs/migration-audit/bench/`.
`uv run python tests/python/contextbench.py run [--out DIR]` is the context-quality
harness (entry-point, routing and packing metrics at 256/1,024/4,096 units);
`tests/python/test_contextbench.py` gates it in `mise run verify` against the latest
frozen `docs/phase-6/baseline-vN/` (`contextbench.BASELINE`); v1's README is the
protocol (splits, gate rules, how to freeze a new version) and each later README states
its amendments. Gold is audited (`gold-audit.json`); only verified gold is gated.
Develop on the dev split; never tune on held-out.
`tests/python/contextbench_ts.py` is the same ruler for TypeScript (protocol and frozen
baseline in `docs/phase-6/typescript-v1/`). Its scorer cases run in `verify`; the gate
needs ~2 GB of corpora and ~15 minutes, so it is `mise run contextbench-ts`, not part of
`verify`. Run it under the limits below before and after any TypeScript retrieval,
resolution, routing or packing change.
Dependencies live in `pyproject.toml` with a committed `uv.lock`; add them
with `uv add`, never by hand-editing the lock. Keep heavy local runs within
`CPUQuota=200%`/`MemoryMax=3G` (`systemd-run --user --scope`).

Tests never write to real user data: `tests/python/conftest.py` gives every
test a private `OXIDE_DATA_DIR` and XDG locations, fails the session if the
real data location changes, and every subprocess receives explicit private
locations (`env()` and `python()` there; `env()` refuses any location outside
the temporary root). Native probes that could crash, and crash injection, run
only in such subprocesses. Lexical tests need the pinned FTS extension
(`uv run oxide prepare`, once), and the context-quality gate the corpora
(`uv run python tests/python/contextbench.py fetch`, once; both are in bootstrap).

## Commits

Commit only when asked. OXIDE v2 work commits on `rewrite/v2` (or a branch
from it, merged back by PR); never commit v2 work to `main`, which keeps the
legacy v1 implementation until the rewrite is merged by explicit decision. Messages use a
lowercase prefix plus an imperative summary: `fix:`, `feat:`, `refactor:`,
`docs:`, `harden:`, `bench:`, `tierb:`.

## Legacy v1 harnesses (reference only)

The sections below describe tooling for the retired v1 implementation. Its
code is no longer on this branch; these harnesses need the legacy `oxide`
binary built from `main` / `ac985b28` and are not part of `mise run verify`.
They stay as evidence until v2 evaluation harnesses replace them (BOOTSTRAP
Phase 3).

### Local embedder and fixtures

- Start/stop the local llama.cpp server with `scripts/embedder.sh start|stop`
  (~0.3 GB RSS with the capped profile; Q4_K_M third-party quants are broken,
  stick to official Q8_0).
- `fixtures/py_repo` and `fixtures/ts_repo` are committed benchmark fixtures —
  they double as manual smoke-test repos (copy to /tmp before indexing).

### Eval harnesses (eval-agent/, scripts/agent_eval/)

- `eval-agent/.venv` is Python **3.11** (`tree-sitter-languages` has no wheels
  ≥3.12); recreate with `uv venv --python 3.11`.
- The ContextBench evaluator is cloned to `eval-agent/third_party/ContextBench`
  (gitignored) on first run of `scripts/agent_eval/contextbench_run.py`.
- Tier A (`contextbench_run.py`) scores retrieval vs human gold contexts;
  results append to `eval-agent/results/cb_results.jsonl` — resumable, keyed by
  (task, condition). Summarize with `summarize_cb.py`.
- Tier B (`tierb_agent_run.py`) runs headless `opencode` per condition. It pins
  `$PWD` to the task-repo copy because opencode trusts PWD over getcwd().
- Long background runs: launch via a script using `setsid ... &` — plain
  backgrounded shells die with the parent. When matching processes, prefer
  `pgrep -fa` + kill-by-PID; `pkill -f somepattern` matches your own command
  line and kills your own shell.

## LadybugDB FTS extension (Linux x86_64)

LadybugDB comes from its Python wheel (`ladybug==0.21.2`), so there is no
native build step. Full-text search is a separate native extension: `uv run
oxide prepare` (part of `mise run bootstrap`) downloads the pinned 0.21.0 file
over HTTPS and checks its SHA-256 into
`$XDG_CACHE_HOME/oxide/ladybug-extensions-0.21.0/` (override with
`OXIDE_FTS_EXTENSION`). It is the only network access OXIDE makes. The
engine re-verifies the digest before every `LOAD EXTENSION` by path, never
`INSTALL`; without the file the lexical channel reports `unavailable` and
structural retrieval and routing continue. No vector extension exists.

## Frozen baselines

`docs/phase-3/baseline-v1/` (Rust retrieval and routing) is a read-only
oracle: `tests/python/test_baseline.py` asserts the Python engine reproduces
it exactly. A pin or version bump that changes it is a new baseline
directory with a recorded comparison, never a regenerated old one.
`docs/phase-4/` and `docs/phase-5/` artifacts and the DecisionBench/JEV
harnesses documented there were produced by the Rust runtime, which was removed
in ADR-0011. They are evidence, reproducible only at commit `f79808a`.
