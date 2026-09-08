# OXIDE Maintainability Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `executing-plans` to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Make the existing Rust codebase smaller and easier to change without altering OXIDE retrieval, freshness, provenance, CLI, or benchmark behavior.

**Architecture:** Preserve the single crate and current domain terms. Extract only cohesive, already-real concepts from `index.rs` and `retrieval.rs`: canonical embedding text, SQLite storage, structural relations, and lexical retrieval. The existing `IndexBackend` trait remains the storage seam; `RelationGraph` remains an OXIDE structural-relation type; no generic graph/backend/provider framework is added.

**Tech Stack:** Rust 2021, rusqlite, tree-sitter, existing test fixtures, GitHub Actions.

**Spec:** Approved maintainability-refactor design in the 2026-09-08 user request and Plan A approval.

## Global Constraints

- Preserve all `AGENTS.md` load-bearing invariants, including persisted ID/hash composition and atomic migration/meta-write contracts.
- Do not change lexical/semantic fusion, structural expansion bounds, embedder selection, persisted schema, JSON shape, CLI/MCP behavior, or benchmark fixtures.
- Do not add dependencies, language extractors, storage backends, FTS5, Turso, Zvec, SCIP, or embedding experiments.
- Keep `RepositoryService::validate_index` separate from write-side compatibility policy: its read-only stable-error behavior is intentional.
- Preserve user-owned untracked `.agents/`, `.codex/`, and `docs/storage-backend-eval/spike/Cargo.lock`.
- Commit each completed slice only after its named focused checks pass.

---

### Task 1: Record the frozen baseline and consolidate embedding text

**Files:**
- Create: `docs/maintainability-refactor-baseline.md`
- Modify: `src/embeddings.rs`, `src/index.rs`, `src/context.rs`, `src/retrieval.rs`, `tests/provider_migration_recovery.rs`

**Interfaces:**
- `embeddings::symbol_embed_text(&Symbol) -> String` becomes the only formatter for text sent to embedders and hashed as vector cache identity.
- Delete `index::embed_text`; every current caller imports the canonical formatter.

- [x] Record the observed pre-refactor fixture and synthetic benchmark values with command provenance; mark them as comparison data, not new thresholds.
- [x] Replace each `index::embed_text` import/call with `embeddings::symbol_embed_text` without changing formatter field order or separators.
- [x] Delete the duplicate formatter only after all callers have migrated.
- [x] Run `cargo test -j 2 --test embedding_staleness`, `cargo test -j 2 --test full_incremental_parity`, and `cargo test -j 2 --test provider_migration_recovery`.
- [x] Build release and run `./target/release/oxide eval --config fixtures/benchmark.json`; compare output to the recorded fixture baseline.
- [x] Commit `refactor: centralize canonical embedding text`.

### Task 2: Extract the SQLite storage adapter

**Files:**
- Create: `src/storage.rs`
- Modify: `src/index.rs`, `src/lib.rs`, tests only if a visibility/import migration requires it.

**Interfaces:**
- Move the existing `IndexBackend` trait and `SqliteStore` implementation unchanged into `storage`.
- `index` continues to own `update_index`, freshness policy, and indexing orchestration; it imports `storage::{IndexBackend, SqliteStore}`.
- Preserve transaction boundaries and `u64`/`i64` SQLite bit-casts exactly.

- [x] Move the contiguous storage declarations and implementation from `index.rs` to `storage.rs`; do not alter SQL, error propagation, transaction scopes, or method signatures.
- [x] Migrate all in-crate imports through `storage`; retain any necessary public re-export only if an existing external callsite requires the `index` path.
- [x] Run `cargo test -j 2 --test provider_migration_recovery`, `cargo test -j 2 --test interrupted_index_recovery`, and `cargo test -j 2 --test cli_e2e`.
- [x] Run `cargo test -j 2 --test full_incremental_parity` and `cargo build --release -j 2`.
- [x] Commit `refactor: separate SQLite storage from indexing`.

### Task 3: Extract structural-relation traversal

**Files:**
- Create: `src/relations.rs`
- Modify: `src/retrieval.rs`, `src/context.rs`, `src/review.rs`, `src/lib.rs`

**Interfaces:**
- Move `RelationGraph` and its reverse-index construction to `relations` unchanged.
- Public callers use `relations::RelationGraph`; bounded file-scope filtering remains in the caller, especially `context`.

- [ ] Move `RelationGraph` without changing `callers_of`, `implementors_of`, `uses`, ordering, or lazy reverse-index behavior.
- [ ] Update imports in retrieval, context, and review; do not introduce a generic graph trait or new query interface.
- [ ] Run focused context/review/retrieval tests, then `cargo test -j 2 --test determinism`.
- [ ] Build release and run the fixture eval; investigate before committing if any recall value changes.
- [ ] Commit `refactor: isolate structural relation traversal`.

### Task 4: Extract lexical retrieval

**Files:**
- Create: `src/lexical.rs`
- Modify: `src/retrieval.rs`, `src/lib.rs`

**Interfaces:**
- Move `LexicalIndex` and its private token/document construction into `lexical` unchanged.
- `RetrievalEngine` continues to coordinate concurrent lexical and semantic search and owns fusion/expansion ordering.

- [ ] Move `LexicalIndex` with identical name/signature/body weighting and repository-root body loading.
- [ ] Keep `RetrievalEngine::search`'s scoped thread concurrency and narrow captures intact; only update imports.
- [ ] Run `cargo test -j 2 --lib retrieval`, `cargo test -j 2 --test determinism`, and fixture eval.
- [ ] Commit `refactor: isolate lexical retrieval index`.

### Task 5: Remove stale documentation and tighten CI dependencies

**Files:**
- Modify: `docs/auto-indexing-watcher-constraints/README.md`, `docs/testing/ci.md`, `.github/workflows/ci.yml`, only the architecture document(s) whose authority is superseded by this extraction.

**Interfaces:**
- `AGENTS.md`, `docs/review/*`, `docs/testing/ci.md`, `docs/canonical-baseline.md`, and `docs/perf-baseline-v0.1.md` remain the authoritative contracts/evidence.
- CI retains `quality`, `tests`, `no-default`, `retrieval`, and `coverage`; expensive ContextBench stays non-required.

- [ ] Correct the watcher document to describe the implemented watcher rather than a future design.
- [ ] State one authority per current architecture/frozen decision/evidence/future-candidate category; preserve rejected-experiment evidence in place.
- [ ] Make non-quality CI jobs depend on quality; remove the redundant standalone MCP rerun already included in `cargo test` and document that coverage.
- [ ] Review the workflow diff for retained `OXIDE_EMBED_*` hermetic environment clearing, cache keys, failure artifacts, no-default coverage, retrieval gate, and informational coverage/performance behavior.
- [ ] Run `cargo fmt && cargo clippy -j 2 --all-targets` and `cargo test -j 2`; commit `chore: clarify refactor evidence and CI gates`.

### Task 6: Run canonical verification and independent review

**Files:**
- Modify only if a verified review finding requires correction.

**Interfaces:**
- Product behavior and stored-index compatibility remain unchanged; all comparisons are against Task 1's recorded baseline.

- [ ] Run `cargo build --release -j 2`, fixture eval, and `scripts/perf.sh 200`; record cold CLI latency, indexing/single-edit/search/context latency, RSS, and index disk usage next to the pre-refactor values.
- [ ] Run `cargo fmt && cargo clippy -j 2 --all-targets`, `RUST_TEST_THREADS=2 cargo test -j 2`, and `cargo test -j 2 --no-default-features`.
- [ ] Request independent Codex review against `docs/review/README.md`; address BLOCKER/MAJOR findings in a separate tested commit.
- [ ] Report exact comparisons, remaining debt, and readiness for separately-scoped SQLite/FTS5 and code-intelligence-provider experiments.
