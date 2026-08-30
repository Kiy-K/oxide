# CI verification

OXIDE v0.1 uses Rust `1.98.0`, declared in `rust-toolchain.toml`. Rustup
installs `rustfmt`, `clippy`, and `llvm-tools-preview` with that toolchain.

The required GitHub Actions checks run for pull requests and pushes to `main`:

- **Rust / Quality**: `cargo fmt --check` and warning-free clippy.
- **Rust / Tests**: the complete unit and integration suite, followed by the
  explicit MCP protocol gate in `tests/mcp_e2e.rs`.
- **OXIDE / Retrieval Gate**: the release binary runs the committed,
  deterministic `fixtures/benchmark.json` evaluation.
- **Rust / Coverage**: a `cargo-llvm-cov` report and artifact. Coverage is
  informational; report generation itself must succeed.

Run the equivalent local gate before pushing:

```bash
cargo fmt --check
cargo clippy -j 2 --all-targets -- -D warnings
cargo test -j 2
cargo test -j 2 --test mcp_e2e
cargo build --release -j 2
./target/release/oxide eval --config fixtures/benchmark.json
cargo install cargo-llvm-cov --version 0.6.19 --locked # once
cargo llvm-cov --workspace --all-targets --html --output-dir coverage/html
git diff --check
```

The fixture benchmark is a retrieval-quality gate, not a timing benchmark.
It uses the default deterministic hashed embedder; the reference aggregate is
vector-only Recall@5 `0.818`, hybrid Recall@5 `0.909`.

## Reproducibility and network policy

CI starts from a checkout with no repository `.oxide` state and clears
`OXIDE_EMBED_URL` and `OXIDE_EMBED_MODEL` for test, benchmark, and coverage
commands. The required test suite uses temporary repositories and committed
fixtures. It does not require a llama.cpp server, a model API, an agent
configuration, CodeGraph, or a developer home-directory path.

Cargo may access crates.io only to fetch Rust dependencies and the pinned
coverage tool. After that setup, OXIDE verification is offline. The only
HTTP-embedder test path uses a loopback endpoint to assert structured failure;
it does not call an external service. Real-agent evaluations are research
artifacts and are deliberately excluded from per-PR CI.

The cache contains Cargo registries, git dependencies, and `target/`; its key
includes the lockfile, manifest, pinned toolchain, Cargo configuration, and
build scripts. A cache miss is expected to perform a clean successful build.
For a fresh local build, use a fresh clone or set `CARGO_TARGET_DIR` to a new
temporary directory; do not rely on an existing `.oxide` index.

Coverage and benchmark artifacts are retained for 14 days. They diagnose
failures; neither is release automation.
