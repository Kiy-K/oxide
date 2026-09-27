# OXIDE development workflow

Commands, verification order and harness details, loaded on demand from the
root `AGENTS.md` routing table. Older docs and code comments that cite
"`AGENTS.md`" for a command or harness detail mean this file.

## Commands

`mise.toml` pins every non-Rust dev tool (Python 3.11, uv, shellcheck, jq,
cargo-llvm-cov) and wraps the checks below in tasks matching CI's current
commands step-for-step, so there is one place either can drift from once CI
itself is migrated to call `mise run` directly (not done yet — pending
verification on a real runner; CI still runs the raw commands below). See
`mise.toml`'s own comments for why Rust itself stays pinned only in
`rust-toolchain.toml`. With
[mise](https://mise.jdx.dev/installing-mise.html) installed (prefer a system
package, e.g. `pacman -S mise`/`brew install mise`, over piping an installer
script):

```bash
mise run bootstrap  # installs the pinned Rust toolchain/components + mise tools
mise run lint       # cargo fmt --check + clippy -D warnings
mise run test       # full unit + integration suite
mise run bench      # release build + the canonical fixture benchmark
mise run verify     # lint, lint/test --no-default-features, test, bench, installer checks, in order
```

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

Commit only when asked. Commits go straight to `main` (no PRs). Messages use a
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
