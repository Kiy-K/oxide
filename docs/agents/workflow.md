# OXIDE development workflow

Commands, verification order and harness details, loaded on demand from the
root `AGENTS.md` routing table. Older docs and code comments that cite
"`AGENTS.md`" for a command or harness detail mean this file.

## Commands

`mise.toml` pins every non-Rust dev tool (Python 3.11, uv, shellcheck, jq,
cargo-llvm-cov, Node, pnpm) and wraps the checks below in tasks matching CI's
current commands step-for-step, so there is one place either can drift from
once CI itself is migrated to call `mise run` directly (not done yet for the
Rust jobs — pending verification on a real runner; they still run the raw
commands below). See
`mise.toml`'s own comments for why Rust itself stays pinned only in
`rust-toolchain.toml`. With
[mise](https://mise.jdx.dev/installing-mise.html) installed (prefer a system
package, e.g. `pacman -S mise`/`brew install mise`, over piping an installer
script):

```bash
mise run bootstrap    # pinned Rust toolchain/components + mise tools + TS workspace install
mise run lint         # cargo fmt --check + clippy -D warnings
mise run test         # full unit + integration suite
mise run bench        # release build + the canonical fixture benchmark
mise run verify:rust  # lint, lint/test --no-default-features, test, bench, installer checks, in order
mise run verify:ts    # frozen-lockfile pnpm install, then Turbo lint/typecheck/test/build
mise run ts:integration  # @oxide/client + @oxide/mcp parity against the real binary ($OXIDE_BIN, default target/release/oxide)
mise run mcp:compile  # deno compile @oxide/mcp into packages/mcp/dist/oxide-mcp
mise run native:build # build the @oxide/native addon (packages/native, Linux x64); ts:integration runs it
mise run lint:native  # cargo fmt --check + clippy -D warnings for packages/native
mise run verify       # verify:rust, verify:ts, then ts:integration — the single full-repo entrypoint
```

TypeScript workspace (#36): pnpm owns dependencies (`pnpm-workspace.yaml`,
committed `pnpm-lock.yaml`), Turbo owns the TS task graph (`turbo.json`) and is
a root devDependency, not a mise tool. `ts:install`/`ts:lint`/`ts:typecheck`/
`ts:test`/`ts:build` wrap single steps; root `package.json` has no scripts, so
mise stays the one entrypoint. Turbo never wraps Cargo. Packages:
`packages/protocol` (`@oxide/protocol`), `packages/client`
(`@oxide/client`) and `packages/mcp` (`@oxide/mcp`, a reference TS MCP server;
the Rust `oxide mcp` stays canonical). Shared dev tooling (TypeScript, Biome,
`@types/node`) is declared once in the root `package.json`. Deno (pinned in
`mise.toml`) is used only to `deno compile` `@oxide/mcp`; the root
`package.json` `"workspaces"` exists for Deno and must mirror
`pnpm-workspace.yaml`. `deno.lock` pins Deno's npm resolution for that compile
(`--frozen-lockfile`); after any dependency change run
`deno install --lockfile-only` at the root. `packages/native` (`@oxide/native`)
is the Node-API addon behind `@oxide/client`'s `backend: "native"`: its own
Cargo project (not a root workspace member) with its own `Cargo.lock`, seeded
from the root lock; after a root dependency change, re-sync it (command in
`packages/native/README.md`) so shared crates stay on the same versions. CI's `typescript` job runs `mise run verify:ts`; its
`client-integration` job, the only one needing both toolchains, builds the
release binary, runs `mise run lint:native`, then `mise run ts:integration`. Neither has a `needs` link
with the Rust jobs, and Turbo never caches the integration task.

`mise run protocol:fixtures` rewrites `fixtures/protocol/` from the real
binary (`tests/protocol_fixtures.rs` with `OXIDE_PROTOCOL_FIXTURES=update`).
Run it only after an intended JSON change, review the diff, then run
`verify:ts`. See `packages/protocol/README.md`.

Without mise, the same checks run directly — this is what CI's `quality`/
`test`/`no-default-features`/`retrieval-gate` jobs currently run:

```bash
cargo test -j 2                 # all tests; keep -j 2 (laptop)
cargo test -j 2 --lib retrieval # one module
RUST_TEST_THREADS=2 cargo test -j 2   # if integration tests contend
cargo fmt && cargo clippy -j 2 --all-targets   # clippy must be warning-free
cargo build --release -j 2      # CLI used by eval scripts lives here
./target/release/oxide eval --config fixtures/benchmark.json   # committed fixture benchmark
scripts/perf.sh 200             # perf harness on synthetic repo (build release first)
```

Order matters only for commits: fmt → clippy → test → benchmark gate.

`tests/benchmark_gate.rs` is semantic, not mechanical: it fails unless hybrid
retrieval ≥ vector-only recall@5 on `fixtures/benchmark.json`. If a ranking
change fails it, fix the ranking or honestly re-baseline both numbers.

## Commits

Commit only when asked. Local commits go straight to `main`; work from remote
sessions lands through a PR from its own branch. Messages use a
lowercase prefix plus an imperative summary: `fix:`, `feat:`, `refactor:`,
`docs:`, `harden:`, `bench:`, `tierb:`.

## Local embedder and fixtures

- Start/stop the local llama.cpp server with `scripts/embedder.sh start|stop`
  (~0.3 GB RSS with the capped profile; Q4_K_M third-party quants are broken,
  stick to official Q8_0).
- `fixtures/py_repo` and `fixtures/ts_repo` are committed benchmark fixtures —
  they double as manual smoke-test repos (copy to /tmp before indexing).

## Eval harnesses (eval-agent/, scripts/agent_eval/)

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
