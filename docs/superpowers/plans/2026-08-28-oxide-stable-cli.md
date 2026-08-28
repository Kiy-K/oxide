# OXIDE Stable CLI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development or executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Expose stable status, index, search, and context contracts for coding agents while preserving retrieval behavior.

**Architecture:** Add a small `service` module between CLI parsing and existing index/retrieval/context modules. The service validates repository/index state, maps results to explicit DTOs, and classifies actionable errors. CLI remains a parser/renderer and `main` owns exit behavior.

**Tech Stack:** Rust 2021, Clap 4, serde/serde_json, anyhow, existing SQLite/index/retrieval modules, std::process integration tests.

**Spec:** `docs/superpowers/specs/2026-08-28-oxide-cli-design.md`

## Global Constraints

- Do not change retrieval ranking, expansion, fusion constants, embedding models, or benchmark fixtures.
- Read commands must not create `.oxide/index.db`.
- JSON stdout must contain only the requested result or structured error; human diagnostics belong on stderr.
- Stable evidence identity is repository-relative `path#qualified_name`.
- Runtime errors are non-zero; malformed Clap invocations retain exit code 2.
- Context output must omit the Qwen-specific internal query prefix.

---

### Task 1: Define service DTOs and repository lifecycle

**Files:**
- Create: `src/service.rs`
- Modify: `src/lib.rs`

**Interfaces:**
- `RepositoryService::new(root: PathBuf) -> Result<Self, ServiceError>` validates/canonicalizes a directory.
- `RepositoryService::index(embedder_url: Option<&str>) -> Result<IndexResult, ServiceError>` opens/updates the index.
- `RepositoryService::status() -> Result<StatusResult, ServiceError>` reads status without creating files or probing the network.
- `RepositoryService::search(query: &str, options: SearchRequest) -> Result<Vec<Evidence>, ServiceError>`.
- `RepositoryService::context(task: &str, budget_tokens: usize) -> Result<ContextResult, ServiceError>`.
- `Evidence` is the shared explicit wire record with `id`, `file`, `qualified_name`, `name`, `kind`, `language`, `start_line`, `end_line`, `score`, `reasons`, and `snippet`.

- [ ] Add tests first for evidence identity and missing-index error classification.
- [ ] Run the focused test and observe the expected missing symbols/types failure.
- [ ] Implement DTOs, error codes, root validation, existing-index opening, and conversion helpers.
- [ ] Run focused service tests.

### Task 2: Add status and structured index/search/context execution

**Files:**
- Modify: `src/service.rs`, `src/index.rs` only where metadata/status helpers are required
- Modify: `src/embeddings.rs` only where runtime provider health must be exposed

**Interfaces:**
- `StatusResult` exposes root, index existence, currentness, counts, indexed embedder, supported languages, and schema version.
- `IndexResult` is a narrow serialized index summary retaining existing count semantics.
- Runtime failures map to stable codes such as `repository_not_found`, `index_missing`, `no_source_files`, `embedder_unavailable`, `search_failed`, and `context_failed`.

- [ ] Add failing service tests for current/stale status, empty source repo, provider failure, and budgeted context conversion.
- [ ] Implement currentness by scanning and comparing file content hashes; do not alter ranking code.
- [ ] Implement index/search/context service methods using existing functions.
- [ ] Run focused tests and confirm deterministic evidence ordering and budget limits.

### Task 3: Wire the CLI and process exit behavior

**Files:**
- Modify: `src/cli.rs`
- Modify: `src/main.rs`

**Interfaces:**
- Add `status [PATH]` and `--json` to index/status/search/context.
- Preserve existing human output and search mode flags.
- JSON runtime failures print one structured error object to stdout and exit non-zero; human failures print stderr diagnostics. Clap parsing remains unchanged.

- [ ] Add failing real-binary tests for status/index/search/context JSON, missing index, malformed invocation, and stdout/stderr separation.
- [ ] Implement dispatch through `RepositoryService`; remove direct retrieval orchestration from CLI.
- [ ] Implement compact human rendering from explicit DTOs.
- [ ] Run the CLI integration test binary.

### Task 4: Add full CLI regression coverage

**Files:**
- Create: `tests/cli_e2e.rs`

**Interfaces:**
- Tests invoke `env!("CARGO_BIN_EXE_oxide")` against isolated temporary repositories.
- Assertions validate stable required keys, array/object top-level forms, exit codes, and no ANSI/log text in JSON stdout.

- [ ] Cover index → status → search → context.
- [ ] Cover repeated no-change indexing and deleted/renamed files.
- [ ] Cover no-result search, missing index, empty/no-source repo, malformed budget/invocation, and unavailable embedder.
- [ ] Cover context `used_tokens <= budget_tokens` and repeated output ordering.
- [ ] Cover old documented search/context JSON fields that remain intentionally compatible.
- [ ] Run `cargo test -j 2 --test cli_e2e`.

### Task 5: Document the stable agent interface

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-08-28-oxide-cli-design.md` only if implementation decisions materially differ

- [ ] Document status/index/search/context commands with JSON examples.
- [ ] Document stable evidence identity, error shape, exit-code behavior, and read-command index requirements.
- [ ] Add a short note identifying `src/service.rs` as the future MCP reuse boundary without implementing MCP.
- [ ] Run the relevant CLI smoke examples and inspect JSON validity.

### Task 6: Run final quality and compatibility gates

**Files:**
- No source changes expected unless verification exposes a defect.

- [ ] Run `cargo fmt --check`.
- [ ] Run `cargo clippy -j 2 --all-targets -- -D warnings`.
- [ ] Run `cargo test -j 2`.
- [ ] Run `cargo run --quiet -- eval --config fixtures/benchmark.json --json` and compare aggregate metrics to the captured baseline.
- [ ] Run cold, no-change, single-edit, deletion/rename indexing smoke scenarios.
- [ ] Review the diff for accidental ranking changes and oversized JSON fields.
- [ ] Commit the complete Phase 1 change separately with a descriptive message.
