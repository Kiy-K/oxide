# OXIDE development workflow

Commands and verification for OXIDE v2, loaded on demand from the root
`AGENTS.md` routing table. Older docs and code comments that cite
"`AGENTS.md`" for a command or harness detail mean this file.

## Default loop

Read the code, change it, add or adjust a test, run `mise run verify`. That is the
whole process for features, fixes, language work and refactors. Only changes that
claim a retrieval/routing/packing quality or performance improvement also need the
ContextBench or `scripts/bench.py` runs below (see `AGENTS.md`).

## Review tiers

Run `mise run tier` on the final diff. It prints `tier=N` and the applicable rubric filenames.
Tiers combine by maximum: unclassified paths default to tier 1; Markdown paths are tier 0 unless a tier-2 path rule matches.

| Tier | Scope | Required before commit |
|---|---|---|
| 0 | Documentation changes, except agent/review/spec policy | `mise run verify` |
| 1 | Tests, ordinary source/configuration paths, and unknown paths | Tier 0 plus an independent zero-context diff review against the printed `docs/review/` rubrics |
| 2 | Public API, capture/storage/core policy, frozen evaluation fixtures, test harness, dependencies, CI/agent configuration, review classifier and architecture authorities | Tier 1 plus a short intent spec in `docs/superpowers/specs/`, reviewed by a zero-context agent before implementation for features/behavior changes. Bug fixes restoring documented behavior do not need a new spec. |

Cursor reviews pull requests. There is no Claude Code review check in CI.
`mise run tier` is local guidance before commit; it is not a GitHub gate.
CI runs `mise run verify` on every PR. `Verify` is the required status check. Agents retain autonomous implementation and commits; no per-change human approval is added.

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
Run it under the limits below; recorded runs are in `docs/migration-audit/bench/` at commit `013e791`.
`uv run python tests/python/contextbench.py run [--out DIR]` is the context-quality
harness (entry-point, routing and packing metrics at 256/1,024/4,096 units);
`tests/python/test_contextbench.py` gates it in `mise run verify` against the latest
frozen `fixtures/eval/contextbench-py/baseline-vN/` (`contextbench.BASELINE`); the protocol (splits, gate
rules, how to freeze a new version) is in [`fixtures/eval/`](../../fixtures/eval/README.md),
and the frozen README states the latest amendment. Gold is audited (`gold-audit.json`); only verified gold is gated.
Develop on the dev split; never tune on held-out.
`tests/python/contextbench_ts.py` is the same ruler for TypeScript (tasks and gold
audit in `fixtures/eval/contextbench-ts/`, latest frozen baseline `baseline-v16/`; corpora in `~/.cache/oxide/contextbench-ts/corpora-v2/`, and the
sources-only `corpora/` only reproduces v1). Its scorer cases run in `verify`; the gate
needs ~2 GB of corpora and ~15 minutes, so it is `mise run contextbench-ts`, not part of
`verify`. Run it under the limits below before and after any TypeScript retrieval,
resolution, routing or packing change.
`uv run python scripts/agent_eval {verify,run,report,diagnose}` is the paired task-success eval (the
same agent fixes real bugs with and without `oxide mcp`); protocol, pinned model and the
pass/fail rule are in [`fixtures/eval/agent-swe/`](../../fixtures/eval/agent-swe/README.md). It
makes billed model calls and needs `opencode` with an API-backed provider. `run` takes
`--max-tokens` (a projected spend cap) and `--split dev|heldout`; a held-out set needs `--final`
and is opened once. `diagnose RESULTS` classifies OXIDE-arm runs from their retained traces.
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
[legacy infrastructure audit](https://github.com/Kiy-K/oxide/blob/013e7910d6fe8b9df9ba753bd68e2b6468bb4e51/docs/migration-audit/legacy-infrastructure.md) for
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

Commit only when asked. OXIDE v2 lives on `main`; commit there or on a branch
from it merged back by PR. Messages use a
lowercase prefix plus an imperative summary: `fix:`, `feat:`, `refactor:`,
`docs:`, `harden:`, `bench:`, `tierb:`.

## Release

OXIDE ships to PyPI as `oxide-agent` (PyPI's `oxide` is another project); the import
package and the CLI are still `oxide`. Pushing a `v<version>` tag runs
`.github/workflows/release.yml`. It runs the whole Platforms matrix, Intel macOS
included, and then uploads the wheel and sdist that linux-x86_64 built and smoke-tested.
The upload uses PyPI trusted publishing (OIDC), so no token is stored anywhere.

One-time setup, before the first tag:

1. On PyPI, go to *Your account → Publishing → Add a new pending publisher* and enter:
   PyPI project `oxide-agent`, owner `Kiy-K`, repository `oxide`, workflow
   `release.yml`, environment `pypi`. The project is created on the first upload.
2. On GitHub, go to *Settings → Environments → New environment* and create `pypi`.
   Add yourself as a required reviewer, and limit deployment to tags matching `v*`.

Each release: set `version` in `pyproject.toml`, run `uv lock`, `mise run verify` and
`mise run pythons`, merge to `main`, then tag that commit `v<version>` and push the tag
(only when the release is approved). The publish job fails unless the tag matches the
built version.

## Historical reproduction

Legacy harnesses (`eval-agent/`, `scripts/agent_eval/`, `scripts/perf.sh`) and
completed study reports are not in this checkout. Run them from their pinned tree:
`013e791` for the Phase 6 studies and the last copy of the legacy harnesses,
`ac985b28` for v1, `f79808a` for the replaced Rust/Bun v2 runtime. The legacy
ContextBench evaluator requires Python 3.11; old TS/TSX task verification uses Bun.
Do not apply v1 CLI, embedding or SQLite contracts to the Python engine.

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

`fixtures/eval/structural-v1/` (Rust retrieval and routing) is a read-only
oracle: `tests/python/test_baseline.py` asserts the Python engine reproduces
it exactly. A pin or version bump that changes it is a new baseline
directory with a recorded comparison, never a regenerated old one.
Earlier Phase 4/5 reports and DecisionBench/JEV harness instructions are in the
[retired runtime tree](https://github.com/Kiy-K/oxide/tree/f79808a/docs), not local
`docs/phase-4/` or `docs/phase-5/` directories. Reproduction requires that tree.

Each language's conformance suite runs in `mise run verify` (`tests/python/test_<language>.py`).
Where a bounded real-repository and ContextBench diagnostic exists, it is `scripts/eval_<language>.py`, with
protocol and results in the pinned `docs/phase-6/<language>-v1/` at `013e791`. Rerun it only when a change claims to
move that language's resolution quality; ordinary fixes need only `verify`.
