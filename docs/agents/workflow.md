# OXIDE development workflow

Commands and verification for OXIDE v2, loaded on demand from the root
`AGENTS.md` routing table. Older docs and code comments that cite
"`AGENTS.md`" for a command or harness detail mean this file.

## Commands

[Mise](https://mise.jdx.dev/installing-mise.html) is the one entrypoint;
`mise.toml` pins uv, and `.python-version` pins the development Python (uv
installs it). CI (`.github/workflows/ci.yml`) runs exactly `mise run verify`;
`.github/workflows/platforms.yml` runs the suite and an installed-artifact smoke on
Linux, Windows and both macOS architectures ([docs/platforms.md](../platforms.md)).

```bash
mise run bootstrap     # uv sync --locked + `oxide prepare` (pinned FTS extension, sha256-checked)
                       #   + ContextBench corpora at pinned commits (git, cached)
mise run verify        # everything, in order, stopping at the first failure:
mise run lint          #   ruff check + ruff format --check
mise run typecheck     #   mypy --strict over src/ and tests/python/
mise run test          #   pytest (tests/python/)
```

`uv run pyrefly check` is an optional second type checker (`[tool.pyrefly]` in
`pyproject.toml`). It is not part of `verify` or CI; mypy `--strict` stays the gate.
On 2026-10-31 it reported 32 errors.

Use `uv run …` for anything else (`uv run oxide index <path>`, `uv run pytest -k name`).
`uv run oxide mcp --repo <path>` serves the single `oxide_search` tool over stdio (`--http` for
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
`tests/python/contextbench_ts.py` is the same ruler for TypeScript (protocol in
`docs/phase-6/typescript-v1/`, corpora amendment in `typescript-v2/`, latest frozen
baseline `typescript-v15/`; corpora in `~/.cache/oxide/contextbench-ts/corpora-v2/`, and the
sources-only `corpora/` only reproduces v1). Its scorer cases run in `verify`; the gate
needs ~2 GB of corpora and ~15 minutes, so it is `mise run contextbench-ts`, not part of
`verify`. Run it under the limits below before and after any TypeScript retrieval,
resolution, routing or packing change.
Dependencies live in `pyproject.toml` with a committed `uv.lock`; add them
with `uv add`, never by hand-editing the lock. Keep heavy local runs within
`CPUQuota=200%`/`MemoryMax=3G` (`systemd-run --user --scope`).
To verify another supported Python, set `UV_PYTHON` and a separate
`UV_PROJECT_ENVIRONMENT` for the entire check. `uv sync --python` alone does not
carry that interpreter choice into a later `uv run`.

OXIDE's engine, CLI, MCP server and distribution need no Node.js, Bun or Rust
toolchain. Native Python dependencies are installed as wheels; the Platforms
workflow enforces wheel-only third-party installation and runs the installed
smoke with only the environment's scripts on `PATH`. That smoke also checks
that MCP advertises exactly `oxide_search`. Optional agent tooling (CodeGraph,
Context7, archify, external MCP servers) and GitHub Actions have their own runtimes;
they are not OXIDE dependencies. See the
[legacy infrastructure audit](../migration-audit/legacy-infrastructure.md) for
the inventory and process ancestry.

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

## Historical reproduction

Retained `eval-agent/`, `scripts/agent_eval/`, `scripts/perf.sh` and Rust
regression/conformance fixtures are historical evidence, not current build steps.
Use the matching pinned tree (`ac985b28` for v1, `f79808a` for the replaced
Rust/Bun v2 runtime), its manifests and original report commands. The legacy
ContextBench evaluator requires Python 3.11; old TS/TSX task verification uses Bun.
See the [infrastructure decision summary](../migration-audit/legacy-infrastructure.md)
for the retention boundary and pinned original audit. Do not apply v1 CLI,
embedding or SQLite contracts to the Python engine.

## LadybugDB FTS extension

LadybugDB comes from its Python wheel (`ladybug==0.21.0`, matched to the FTS build; [docs/platforms.md](../platforms.md)), so there is no
native build step. Full-text search is a separate native extension: `uv run
oxide prepare` (part of `mise run bootstrap`) downloads this platform's pinned
0.21.0 build ([docs/platforms.md](../platforms.md)) over HTTPS and checks its SHA-256 into
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
Earlier Phase 4/5 reports and DecisionBench/JEV harness instructions are in the
[retired runtime tree](https://github.com/Kiy-K/oxide/tree/f79808a/docs), not local
`docs/phase-4/` or `docs/phase-5/` directories. Reproduction requires that tree.

JavaScript/JSX conformance runs in `mise run verify` (`test_javascript.py`). The bounded
real-repository and ContextBench diagnostic is reproducible via
`scripts/eval_javascript.py`; protocol and results: `docs/phase-6/javascript-v1/`.

Go conformance runs in `mise run verify` (`test_go.py`). The bounded real-repository
and ContextBench diagnostic is reproducible via `scripts/eval_go.py`; protocol and
results: `docs/phase-6/go-v1/`.

Rust conformance runs in `mise run verify` (`test_rust.py`). The bounded real-repository,
oracle and ContextBench diagnostic is reproducible via `scripts/eval_rust.py`; protocol and
results: `docs/phase-6/rust-v1/`.
