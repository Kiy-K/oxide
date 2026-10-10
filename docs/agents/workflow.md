# OXIDE development workflow

Commands and verification for OXIDE v2, loaded on demand from the root
`AGENTS.md` routing table. Older docs and code comments that cite
"`AGENTS.md`" for a command or harness detail mean this file.

## Commands

[Mise](https://mise.jdx.dev/installing-mise.html) is the one entrypoint;
`mise.toml` pins Bun, and `oxide_kernel/rust-toolchain.toml` pins Rust
(`rustup` assumed present). CI (`.github/workflows/ci.yml`) runs exactly
`mise run verify`.

```bash
mise run bootstrap     # pinned Rust toolchain + Bun + bun install --frozen-lockfile
mise run verify        # everything, in order, stopping at the first failure:
mise run rust:lint     #   cargo fmt --check + clippy -D warnings (in oxide_kernel/)
mise run rust:test     #   cargo test: kernel boundary checks, Rust side of the contract cases
mise run ts:install    #   bun install --frozen-lockfile
mise run ts:lint       #   biome check
mise run ts:typecheck  #   tsc --noEmit over src/
mise run ts:test       #   builds oxide-runtime, then bun test (boundary, decoder, contract cases)
```

```bash
mise run spike:ladybug # LadybugDB feasibility evidence (ADR-0002); not part of verify.
                       # Needs network once (pinned, sha256-checked native archive and
                       # extensions), then runs offline. Small DB memory by design.
```

Rust runs from `oxide_kernel/` (its own Cargo workspace; keep `-j 2` on the
laptop). Bun owns all JS/TS: dependencies (`package.json`, committed
`bun.lock`), runtime and tests (`bun:test`, constrained to `src/` by
`bunfig.toml`). There is no pnpm, Deno, Turbo, root Cargo crate or
`packages/*` workspace in v2.

Order matters for commits: Rust fmt → clippy → test, then the TS checks;
`mise run verify` encodes it.

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

## Phase 2B native preparation (Linux x86_64)

Before the first build, run `mise run native:prepare` (also part of
`mise run bootstrap`). This explicit task caches and SHA-256 verifies the
approved LadybugDB v0.21.2 compat archive, then restores its library and
headers into `oxide_kernel/native/.cache/liblbug-0.21.2`. It also fetches
and verifies the pinned FTS extension (0.21.0) into
`oxide_kernel/native/.cache/extensions-0.21.0`. CI restores this cache and
runs the same preparation before `mise run verify`.

Cargo uses controlled paths in `oxide_kernel/.cargo/config.toml`; normal
builds do not fetch native artifacts. The runtime build checks the exact
library/header digests. A modified cache fails verification; rerun the
preparation task to restore it. The runtime re-verifies the FTS digest and
loads it by path (`LOAD EXTENSION`, never `INSTALL`); without it lexical
retrieval reports unavailable. No vector extension is loaded.

The Phase 3 baseline (`docs/phase-3/baseline-v1/`) is checked by
`cargo test` and never regenerated to make a failure pass. Its manifest pins
every derivation component (lbug, parser, FTS extension, terms version,
schema), so a dependency or version bump fails `mise run verify` by design,
even when quality is unchanged. That change creates a new baseline directory
(`baseline-v2/`), points `runtime/tests/phase3_eval.rs` at it, writes it with
`OXIDE_FREEZE_PHASE3=1`, keeps the old directory, and records the comparison.

## Phase 5 DecisionBench and JEV

`cargo test` checks the fixture DecisionBench
(`docs/phase-5/decisionbench-v1/fixture/`) offline: config C replays the
recorded live JEV exchanges in `fixture/jev/`. Never regenerate it to make a
failure pass. A changed dataset is a new version.

The ContextBench run is opt-in, machine-local and `#[ignore]`d. It needs
the public task repositories checked out at their base commits. Records
and JEV exchanges hold source, so they stay under
`OXIDE_DECISIONBENCH_DATA` (default
`~/Projects/oxide-eval-data/oxide-decisionbench`). Results are cached per
task, so a rerun resumes.

```bash
cd oxide_kernel && cargo test --release -p oxide-runtime --test phase5_contextbench --no-run
systemd-run --user --scope -p MemoryMax=8G -p CPUQuota=200% env \
  OXIDE_CB_WORKTREES=<dir>:<dir> OXIDE_PHASE5_JEV=replay \
  target/release/deps/phase5_contextbench-<hash> --ignored --nocapture
```

`OXIDE_PHASE5_JEV=off|replay|live`. Live mode sends source to the hosted
JEV service, so use it only with explicit authorization for the
repositories involved and a request/spend cap (`common/phase5.rs`:
`LIVE_REQUESTS`, `LIVE_TOKENS`). It reads `TYPESAFE_API_KEY`, which never
enters recordings. The test split stays sealed unless
`OXIDE_PHASE5_UNSEAL_TEST=1` and `docs/phase-5/preregistration.md` exists.

The follow-up (`docs/phase-5-followup.md`) is opt-in the same way and
never calls JEV: `--test phase5_followup -- --ignored` replays the
recordings at their recorded latency, and caches per task under
`OXIDE_DECISIONBENCH_DATA/followup/`. Follow-up 2
(`docs/phase-5-followup-2.md`) works the same way: `--test
phase5_stability -- --ignored` replays recordings only, caching under
`OXIDE_DECISIONBENCH_DATA/followup-2/`. Its rejected routing harness is a
patch to `git apply`. Follow-up 3 (`docs/phase-5-followup-3.md`, dev only):
`--test phase5_budget -- --ignored`, caching under
`OXIDE_DECISIONBENCH_DATA/followup-3/`.
