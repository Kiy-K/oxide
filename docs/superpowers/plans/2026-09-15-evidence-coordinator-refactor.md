# Evidence Coordinator Refactor + Post-LSP Audit Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the two MAJOR audit findings (`review --diff` swallowing errors, LSP `ProcessCache` staleness) and move independent evidence collection (structural, Git, LSP, blast-radius) behind an isolated async worker layer, keeping CLI/MCP/retrieval/allocator fully synchronous.

**Architecture:** New `src/evidence/` module owns one process-lifetime Tokio runtime (`rt-multi-thread`, 2 workers, `max_blocking_threads(4)`). `EvidenceCoordinator::collect()` is a synchronous entry point: it runs structural + blast-radius (both borrow non-`Send` `RelationGraph`/`IndexBackend` state) on a `std::thread::scope`-spawned OS thread, while concurrently driving Git-I/O and LSP-I/O (both fully owned/`Send` once handed an `Arc<[Symbol]>` and an owned `LspClient`) as `spawn_blocking` tasks joined via `tokio::join!` on the calling thread. Git's `graph.neighbors`/`callers_of` enrichment runs synchronously afterward, back on the calling thread, using the async phase's `GitContext` result. All four sources fold into `context.rs`'s existing `order_note`/allocator in one fixed order, never insertion/completion order.

**Tech Stack:** Rust, `tokio` (`rt-multi-thread` feature, already `rt`-only in `Cargo.toml`), existing `anyhow`/`serde_json`/`lsp_types`/`clap`.

**Spec:** `docs/superpowers/specs/2026-09-15-evidence-coordinator-refactor-design.md`

## Global Constraints

- `RetrievalMode`/ranking constants in `src/config.rs` are NOT touched — no weight or allocator constant changes anywhere in this plan.
- `oxide eval --config fixtures/benchmark.json` must report the unchanged `hybrid recall@5 0.909 ≥ vector-only recall@5 0.818` after every commit.
- `cargo fmt --check` and `cargo clippy --all-targets -- -D warnings` must be clean after every commit (run with `-j 2` per this repo's shared-machine convention).
- No new Tokio feature beyond `rt-multi-thread`; no `tokio::process`, no `tokio::time`, no `tokio::net` (none needed — see spec).
- `git-changed`/`git-caller-of-changed`/`git-cochange`/`lsp-caller`/`lsp-reference`/`lsp-implementation`/`lsp-diagnostic`/`ast-grep-caller`/`blast-radius` reason-tag strings and score fractions (`GIT_CHANGED_SCORE_FRACTION` etc.) are preserved byte-for-byte — this refactor moves code, it does not re-derive scoring.
- Only Python/`ty` gets a real `oxide lsp install` registry entry; no other language profile is added.

This plan has 15 tasks in four groups matching the required commit structure: Tasks 1-3 (audit correctness fixes), Tasks 4-10 (coordinator refactor), Tasks 11-13 (lifecycle/CI hardening), Task 14 (docs), Task 15 (perf + final report). See the spec doc for full architectural rationale — this plan does not repeat it, only the parts an implementer needs inline.

---

### Task 1: `gitctx::build_git_context` propagates git errors instead of swallowing them

**Files:**
- Modify: `src/gitctx.rs:112-147` (`build_git_context`)
- Modify: `src/review.rs:47` (`build_review_context`'s call site)
- Modify: `src/context.rs:372` (temporary `.unwrap_or_default()` shim — replaced by the coordinator in Task 7)
- Test: `src/gitctx.rs` (new `#[cfg(test)]` cases), `tests/review_e2e.rs`

**Interfaces:**
- Produces: `pub fn build_git_context(repo_root: &Path, symbols: &[Symbol], range: &str) -> anyhow::Result<GitContext>` (was `-> GitContext`). For `range == "HEAD~1"` (the CLI's own default, and the only value where "doesn't resolve" always means "no parent commit"): `"no prior commit to diff against (repository has only one commit); pass an explicit --diff range instead"`. For any other unresolvable range: `"invalid diff range '{range}': {underlying git stderr}"`.

- [ ] **Step 1: Write the failing tests in `src/gitctx.rs`**

```rust
#[test]
fn invalid_range_returns_an_error_not_an_empty_context() {
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path();
    git(root, &["init", "-q"]);
    std::fs::write(root.join("a.py"), "a\n").unwrap();
    git(root, &["add", "."]);
    commit(root, "init");
    let err = build_git_context(root, &[], "nonexistent-garbage-range-zzz").unwrap_err();
    assert!(err.to_string().contains("invalid diff range"), "{err}");
}

#[test]
fn fresh_single_commit_repo_head_tilde_1_gets_a_truthful_message() {
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path();
    git(root, &["init", "-q"]);
    std::fs::write(root.join("a.py"), "a\n").unwrap();
    git(root, &["add", "."]);
    commit(root, "only commit");
    let err = build_git_context(root, &[], "HEAD~1").unwrap_err();
    assert!(err.to_string().contains("no prior commit to diff against"), "{err}");
}

#[test]
fn valid_range_still_returns_ok() {
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path();
    git(root, &["init", "-q"]);
    std::fs::write(root.join("a.py"), "a\n").unwrap();
    git(root, &["add", "."]);
    commit(root, "first");
    std::fs::write(root.join("a.py"), "a\nb\n").unwrap();
    let ctx = build_git_context(root, &[], "").unwrap();
    assert!(!ctx.evidence.changed_files.is_empty());
}
```

- [ ] **Step 2: Run to verify they fail**

Run: `cargo test -j 2 --lib gitctx:: -- --nocapture`
Expected: compile error (`build_git_context` still returns `GitContext`, not `Result`).

- [ ] **Step 3: Change `build_git_context`'s signature and swallow point**

```rust
pub fn build_git_context(repo_root: &Path, symbols: &[Symbol], range: &str) -> anyhow::Result<GitContext> {
    if !gitutil::is_repo(repo_root) {
        return Ok(GitContext::default());
    }
    let text = gitutil::diff_text(repo_root, range).map_err(|e| {
        if range == "HEAD~1" {
            anyhow::anyhow!(
                "no prior commit to diff against (repository has only one commit); pass an explicit --diff range instead"
            )
        } else {
            anyhow::anyhow!("invalid diff range '{range}': {e}")
        }
    })?;
    let deltas = gitutil::parse_unified(&text);

    let mut changed_files: Vec<String> = deltas.iter().map(|d| d.file.clone()).collect();
    changed_files.extend(gitutil::deleted_files(&text));
    changed_files.sort();
    changed_files.dedup();

    let recent_commits = if range.is_empty() {
        gitutil::recent_commits(repo_root, GIT_RECENT_COMMITS_LIMIT)
    } else {
        gitutil::recent_commits_in_range(repo_root, range, GIT_RECENT_COMMITS_LIMIT)
    }
    .unwrap_or_default();

    let co_change = co_change_for(repo_root, &deltas);
    let changed_symbols = changed_symbols_for(&deltas, symbols);

    Ok(GitContext {
        evidence: GitEvidence {
            range: if range.is_empty() { "HEAD".into() } else { range.into() },
            changed_files,
            recent_commits,
            co_change,
        },
        changed_symbols,
    })
}
```

Update the existing `non_git_repo_degrades_to_empty_context` test (`gitctx.rs:314-321`) to `.unwrap()` the now-`Result` return.

- [ ] **Step 4: Update `review.rs`'s call site to propagate via `?`**

In `build_review_context` (`review.rs:47`), change:
```rust
    let git_ctx = gitctx::build_git_context(repo_root, symbols, range)?;
```
`build_review_context` already returns `anyhow::Result<ReviewContext>`, so this compiles with no other signature change. `service.rs:916-917` already maps any `Err` here to `ErrorCode::ReviewFailed` (`"review_failed"`) — no `service.rs` change needed for this task.

- [ ] **Step 5: Update `context.rs`'s temporary call site to keep degrading**

In `build_context_with`, `context.rs:372`, change to:
```rust
            let git_ctx = gitctx::build_git_context(root, symbols, "").unwrap_or_default();
```
`context.rs`'s `--git` opt-in path always passes `range: ""`. A truly fresh no-commit repo still fails `git diff --unified=0 --no-color HEAD` in that case, so this shim keeps degrading exactly as before. It is temporary — Task 7 replaces it with the coordinator's `Outcome::Degraded` handling. `GitContext` already derives `Default` (`gitctx.rs:60`), so `.unwrap_or_default()` compiles with no other change.

- [ ] **Step 6: Run tests to verify they pass**

Run: `cargo test -j 2 --lib gitctx:: && cargo test -j 2 --test review_e2e`
Expected: PASS.

- [ ] **Step 7: Add CLI-level regression tests in `tests/review_e2e.rs`**

```rust
#[test]
fn invalid_diff_range_fails_with_review_failed() {
    let repo = init_repo_with_one_commit();
    let mut cmd = oxide_cmd(&repo);
    cmd.args(["review", "--diff", "nonexistent-garbage-range-zzz", "--json"]);
    let out = cmd.output().unwrap();
    assert!(!out.status.success());
    let stderr = String::from_utf8_lossy(&out.stderr);
    assert!(stderr.contains("review_failed"), "{stderr}");
}

#[test]
fn fresh_single_commit_repo_default_diff_fails_truthfully() {
    let repo = init_repo_with_one_commit();
    let mut cmd = oxide_cmd(&repo);
    cmd.args(["review", "--json"]); // default --diff is HEAD~1
    let out = cmd.output().unwrap();
    assert!(!out.status.success());
    let stderr = String::from_utf8_lossy(&out.stderr);
    assert!(stderr.contains("no prior commit"), "{stderr}");
}
```
Reuse whatever `init_repo_with_one_commit`/`oxide_cmd` helpers `tests/review_e2e.rs` already defines — do not add a second harness.

Run: `cargo test -j 2 --test review_e2e`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
cargo fmt && cargo clippy -j 2 --all-targets -- -D warnings
git add src/gitctx.rs src/review.rs src/context.rs tests/review_e2e.rs
git commit -m "fix: propagate invalid git diff ranges as review_failed instead of an empty review"
```

---

### Task 2: LSP `ensure_open` sends `didChange` instead of staying silently stale

**Files:**
- Modify: `src/lsp/client.rs:29-35` (`LspClient` struct), `:172-197` (`ensure_open`)
- Test: `src/lsp/client.rs` (new unit test), `tests/lsp_process_cache_staleness.rs` (new)

**Interfaces:**
- Produces: `LspClient::ensure_open(&mut self, rel_path: &str) -> Result<Uri>` — same signature; never-opened → `didOpen`; opened + unchanged content → no-op; opened + changed content → full-document `didChange` with incremented version.

- [ ] **Step 1: Change `opened` to track a content hash + version**

```rust
pub struct LspClient {
    transport: Transport,
    capabilities: ServerCapabilities,
    root: std::path::PathBuf,
    /// Per-open-document state: content hash last sent and the
    /// didOpen/didChange version, so a file edited between two calls
    /// against a cached session (see `ProcessCache`) gets a
    /// `textDocument/didChange` with fresh content instead of silently
    /// serving the server's stale copy — see `ensure_open`.
    opened: HashMap<String, (u64, i32)>,
    timeout: Duration,
}
```
Replace `use std::collections::HashSet;` with `use std::collections::HashMap;` at `client.rs:20` (`HashSet` is unused after this change).

- [ ] **Step 2: Rewrite `ensure_open`**

```rust
    /// Open `rel_path` if not already open this session (`didOpen`), or
    /// resend its current on-disk content via a full-document `didChange` if
    /// it was opened before but the content has since changed on disk — the
    /// spec-correct signal (LSP 3.17 `TextDocumentSyncKind::Full`; a second
    /// `didOpen` without an intervening `didClose` is not permitted).
    /// Detecting "changed" by content hash rather than mtime means an
    /// external touch with no byte change never triggers a needless resync.
    pub fn ensure_open(&mut self, rel_path: &str) -> Result<Uri> {
        let uri = file_uri(&self.root, rel_path)?;
        let key = uri.as_str().to_string();
        let text = std::fs::read_to_string(self.root.join(rel_path))
            .with_context(|| format!("reading {rel_path} to open in lsp"))?;
        let hash = content_hash(&text);
        match self.opened.get(&key).copied() {
            None => {
                self.transport
                    .notify(
                        "textDocument/didOpen",
                        serde_json::json!({
                            "textDocument": {"uri": key, "languageId": "python", "version": 1, "text": text}
                        }),
                    )
                    .map_err(transport_err)?;
                self.opened.insert(key, (hash, 1));
            }
            Some((last_hash, version)) if last_hash != hash => {
                let next_version = version + 1;
                self.transport
                    .notify(
                        "textDocument/didChange",
                        serde_json::json!({
                            "textDocument": {"uri": key, "version": next_version},
                            "contentChanges": [{"text": text}],
                        }),
                    )
                    .map_err(transport_err)?;
                self.opened.insert(key, (hash, next_version));
            }
            Some(_) => {} // already open, content unchanged: no-op
        }
        Ok(uri)
    }
```

Add below `percent_decode`:
```rust
/// Cheap, non-cryptographic content fingerprint — a collision would only
/// skip one `didChange`, caught on the next `ensure_open` once content
/// actually differs from what's cached, not a new failure mode.
fn content_hash(text: &str) -> u64 {
    use std::hash::{Hash, Hasher};
    let mut h = std::collections::hash_map::DefaultHasher::new();
    text.hash(&mut h);
    h.finish()
}
```

- [ ] **Step 3: Write the failing unit test, then run, then implement (Steps 1-2 above already are the implementation — run now to confirm)**

Add to `client.rs`'s `#[cfg(test)]` module:
```rust
#[test]
fn ensure_open_tracks_content_hash_and_bumps_version_on_change() {
    let hash_v1 = content_hash("v1\n");
    let hash_v2 = content_hash("v2\n");
    assert_ne!(hash_v1, hash_v2);

    let mut opened: HashMap<String, (u64, i32)> = HashMap::new();
    let key = "file:///a.py".to_string();
    opened.insert(key.clone(), (hash_v1, 1));
    assert_eq!(opened[&key], (hash_v1, 1));

    let (last_hash, version) = opened[&key];
    assert_ne!(last_hash, hash_v2, "must detect the change");
    opened.insert(key.clone(), (hash_v2, version + 1));
    assert_eq!(opened[&key], (hash_v2, 2), "version must increment on change");
}
```
Run: `cargo test -j 2 --lib lsp::client:: -- --nocapture`
Expected: PASS once Steps 1-2 are in place (compile error before).

- [ ] **Step 4: Add the real-server integration regression test**

New file `tests/lsp_process_cache_staleness.rs`:
```rust
//! Requires `ty` on `$PATH` (`oxide lsp install ty`, Task 11 of this plan).
//! Reproduces the audit's bug directly: a file edited between two calls
//! against the same cached `LspClient` must not keep serving the first
//! call's `didOpen` snapshot.

use oxide::lsp::LspClient;
use std::time::Duration;

fn ty_available() -> bool {
    std::process::Command::new("ty").arg("--version").output().map(|o| o.status.success()).unwrap_or(false)
}

#[test]
fn edit_between_two_ensure_open_calls_is_visible_to_the_server() {
    if !ty_available() {
        eprintln!("skipping: `ty` not on PATH");
        return;
    }
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path();
    std::fs::write(root.join("a.py"), "def old_name():\n    pass\n").unwrap();

    let mut client = LspClient::spawn("ty", root, Duration::from_secs(10), Duration::from_secs(3))
        .expect("ty must spawn");

    let uri = client.ensure_open("a.py").unwrap();
    let pos = oxide::lsp::client::symbol_position("def old_name():\n    pass\n", 1, "old_name").unwrap();
    let first = client.definition(&uri, pos).unwrap();
    assert!(!first.is_empty(), "must resolve old_name before the edit");

    std::fs::write(root.join("a.py"), "def new_name():\n    pass\n").unwrap();
    client.ensure_open("a.py").unwrap(); // must send didChange, not a no-op

    let new_pos = oxide::lsp::client::symbol_position("def new_name():\n    pass\n", 1, "new_name").unwrap();
    let after_edit = client.definition(&uri, new_pos).unwrap();
    assert!(!after_edit.is_empty(), "server must see post-edit content, not stale pre-edit text");

    client.close();
}
```
Run: `cargo test -j 2 --test lsp_process_cache_staleness -- --nocapture` (`ty` is already installed on this machine; CI wiring lands in Task 13).
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cargo fmt && cargo clippy -j 2 --all-targets -- -D warnings
git add src/lsp/client.rs tests/lsp_process_cache_staleness.rs
git commit -m "fix: LSP client resyncs changed documents via didChange instead of staying silently stale"
```

---

### Task 3: Bounded, configurable LSP session cache in `ProcessCache`

**Files:**
- Modify: `src/config.rs` (new constant)
- Modify: `src/service.rs:325-343` (`ProcessCache` struct), `:827-851` (`context()`'s LSP-slot lookup)
- Test: `src/service.rs` (new `#[cfg(test)]` case)

**Interfaces:**
- Produces: `LSP_MAX_CACHED_SESSIONS: usize` (`config.rs`, default `4`), overridable via `$OXIDE_LSP_MAX_CACHED_SESSIONS` (same precedence convention as `context.rs::resolve_context_max_primaries`).

- [ ] **Step 1: Add the config constant**

In `src/config.rs`, after the existing `LSP_*` block (after line 174):
```rust
/// Cap on live `ty server` (or other LSP) child processes `oxide mcp`'s
/// `ProcessCache` keeps warm across repository roots — each one is a real OS
/// process (~90MB RSS measured for `ty`, docs/lsp-enrichment-eval/README.md),
/// unlike other MCP caches, so this needs an explicit bound. Overridden via
/// `$OXIDE_LSP_MAX_CACHED_SESSIONS`; unset is this default.
pub(crate) const LSP_MAX_CACHED_SESSIONS: usize = 4;
```

- [ ] **Step 2: Write the failing test in `service.rs`**

```rust
#[test]
fn lsp_session_cache_evicts_the_least_recently_used_idle_slot_over_the_cap() {
    let cache = ProcessCache::default();
    let roots: Vec<PathBuf> = (0..(LSP_MAX_CACHED_SESSIONS + 2))
        .map(|i| PathBuf::from(format!("/repo-{i}")))
        .collect();
    for root in &roots {
        let _ = cache.lsp_client_slot(root);
    }
    let live = cache.lsp_clients.lock().unwrap();
    assert!(live.len() <= LSP_MAX_CACHED_SESSIONS, "got {}", live.len());
    assert!(!live.contains_key(&roots[0]));
    assert!(!live.contains_key(&roots[1]));
    assert!(live.contains_key(roots.last().unwrap()));
}
```

- [ ] **Step 3: Run to verify it fails**

Run: `cargo test -j 2 --lib service:: -- lsp_session_cache_evicts --nocapture`
Expected: FAIL/compile error — `lsp_client_slot` isn't a bound method yet (current code inlines the lookup unbounded in `context()`).

- [ ] **Step 4: Add last-used tracking and an eviction method to `ProcessCache`**

```rust
#[derive(Default)]
struct ProcessCache {
    snapshots: Mutex<HashMap<PathBuf, Arc<CachedSnapshot>>>,
    embedder: Mutex<Option<(String, Arc<dyn EmbeddingProvider + Send + Sync>)>>,
    /// One LSP session per repository root, bounded to
    /// `LSP_MAX_CACHED_SESSIONS` live slots. `last_used` is checked lazily
    /// on access, not by a background reaper — avoids a supervision thread
    /// for what a few lines of eviction logic already cover.
    lsp_clients: Mutex<HashMap<PathBuf, Arc<Mutex<Option<LspClient>>>>>,
    lsp_last_used: Mutex<HashMap<PathBuf, std::time::Instant>>,
}

impl ProcessCache {
    /// Return this root's LSP session slot, creating one if absent. If the
    /// cache is at capacity and `root` is new, evicts the least-recently-used
    /// root whose slot is currently idle (not locked by another in-flight
    /// call) before inserting.
    fn lsp_client_slot(&self, root: &Path) -> Arc<Mutex<Option<LspClient>>> {
        let mut clients = self.lsp_clients.lock().unwrap_or_else(|e| e.into_inner());
        let mut last_used = self.lsp_last_used.lock().unwrap_or_else(|e| e.into_inner());
        last_used.insert(root.to_path_buf(), std::time::Instant::now());
        if let Some(existing) = clients.get(root) {
            return existing.clone();
        }
        let max = resolve_lsp_max_cached_sessions();
        if clients.len() >= max {
            let victim = last_used
                .iter()
                .filter(|(r, _)| clients.contains_key(*r) && *r != root)
                .filter(|(r, _)| clients.get(*r).map(|slot| slot.try_lock().is_ok()).unwrap_or(false))
                .min_by_key(|(_, t)| **t)
                .map(|(r, _)| r.clone());
            if let Some(victim) = victim {
                clients.remove(&victim);
                last_used.remove(&victim);
            }
            // If every existing slot is busy, none are evicted this round —
            // the new root falls back to spawn-per-call for this one
            // request, the existing "cache miss" behavior, not a new one.
        }
        let slot = Arc::new(Mutex::new(None));
        if clients.len() < max {
            clients.insert(root.to_path_buf(), slot.clone());
        }
        slot
    }
}

/// `$OXIDE_LSP_MAX_CACHED_SESSIONS` override — same precedence convention as
/// `context.rs::resolve_context_max_primaries`: any parse failure, including
/// unset, falls back to the shipped default.
fn resolve_lsp_max_cached_sessions() -> usize {
    std::env::var("OXIDE_LSP_MAX_CACHED_SESSIONS")
        .ok()
        .and_then(|v| v.parse::<usize>().ok())
        .unwrap_or(crate::config::LSP_MAX_CACHED_SESSIONS)
}
```

- [ ] **Step 5: Replace `context()`'s inline lookup**

In `context()` (`service.rs:827-851`), replace the `lsp_slot`/inline-`HashMap` construction with:
```rust
        let lsp_slot = (lsp && self.use_process_cache)
            .then(|| process_cache().lsp_client_slot(&self.root));
```
using the existing `process_cache()` accessor already used elsewhere in this file. The rest of `context()` (the `is_alive()` respawn check, `build_context_with` call) is unchanged by this task.

- [ ] **Step 6: Run tests**

Run: `cargo test -j 2 --lib service::`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
cargo fmt && cargo clippy -j 2 --all-targets -- -D warnings
git add src/config.rs src/service.rs
git commit -m "fix: bound oxide mcp's cached LSP sessions to LSP_MAX_CACHED_SESSIONS with LRU eviction"
```

---

### Task 4: Performance baseline capture (before the refactor)

**Files:**
- Create: `docs/evidence-coordinator-refactor/README.md` (started here, finished in Task 15)
- Create: `docs/evidence-coordinator-refactor/before.json`

**Interfaces:**
- Produces: `docs/evidence-coordinator-refactor/before.json`, read back by Task 15's after-capture.

- [ ] **Step 1: Build the release binary at the current commit (after Tasks 1-3)**

Run: `cargo build --release -j 2`

- [ ] **Step 2: Capture the four-way matrix against `fixtures/py_repo`**

```bash
mkdir -p docs/evidence-coordinator-refactor
for cond in base git lsp git_lsp; do
  flags=""
  [ "$cond" = "git" ] && flags="--git"
  [ "$cond" = "lsp" ] && flags="--lsp"
  [ "$cond" = "git_lsp" ] && flags="--git --lsp"
  ./target/release/oxide query "where is retry logic" --json $flags \
    --path fixtures/py_repo > "docs/evidence-coordinator-refactor/before_${cond}.json"
  /usr/bin/time -v ./target/release/oxide query "where is retry logic" $flags \
    --path fixtures/py_repo 2> "docs/evidence-coordinator-refactor/before_${cond}_time.txt" >/dev/null
done
```

- [ ] **Step 3: Write `before.json` with the real captured numbers (5 repeats, median) and the exact commit SHA this was captured at**

- [ ] **Step 4: Commit**

```bash
git add docs/evidence-coordinator-refactor/
git commit -m "bench: capture pre-refactor evidence-collection baseline (wall time, RSS, process count)"
```

---

### Task 5: Evidence contract types + isolated Tokio runtime

**Files:**
- Create: `src/evidence/mod.rs`, `src/evidence/contract.rs`, `src/evidence/runtime.rs`
- Modify: `Cargo.toml:39`, `src/lib.rs`
- Test: inline in `contract.rs`/`runtime.rs`

**Interfaces:**
- Produces: `evidence::contract::{EvidenceSource, EvidenceCandidate, DegradeReason, Degraded, Outcome<T>}`; `evidence::runtime::runtime() -> &'static tokio::runtime::Runtime`.

- [ ] **Step 1: Add `rt-multi-thread` to Cargo.toml**

```toml
tokio = { version = "1", features = ["rt", "rt-multi-thread"] }
```

- [ ] **Step 2: Write `src/evidence/contract.rs`**

```rust
//! Shared shape and execution controls for evidence sources the
//! `EvidenceCoordinator` collects — standardizes failure reporting, not
//! scoring: each source keeps its own reason-tag vocabulary and score
//! fractions (`config.rs`).

use crate::symbols::Symbol;
use std::time::Duration;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum EvidenceSource {
    Structural,
    Git,
    Lsp,
    BlastRadius,
}

impl EvidenceSource {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Structural => "structural",
            Self::Git => "git",
            Self::Lsp => "lsp",
            Self::BlastRadius => "blast_radius",
        }
    }
}

/// One piece of evidence, already in `context.rs`'s `Candidate` shape so the
/// coordinator's output folds directly into the existing allocator with no
/// extra conversion step.
#[derive(Debug, Clone)]
pub struct EvidenceCandidate {
    pub symbol: Symbol,
    pub score: f32,
    pub reasons: Vec<String>,
}

#[derive(Debug)]
pub enum DegradeReason {
    Timeout { elapsed: Duration, deadline: Duration },
    Unavailable { detail: String },
    ProtocolError { detail: String },
    SubprocessError { exit_code: Option<i32>, stderr_tail: Option<String> },
    Cancelled,
}

#[derive(Debug)]
pub struct Degraded {
    pub source: EvidenceSource,
    pub reason: DegradeReason,
    pub elapsed: Duration,
}

/// An evidence source either produced candidates, or degraded — never a hard
/// error the coordinator propagates. `review --diff` is a separate,
/// non-`Outcome` path (Task 1).
pub enum Outcome<T> {
    Ready(T),
    Degraded(Degraded),
}
```

- [ ] **Step 3: Write `src/evidence/runtime.rs`**

```rust
//! The one isolated Tokio runtime evidence collection uses — built once,
//! process-lifetime, safe for both a one-shot CLI call and `oxide mcp`'s
//! long-lived process; never rebuilt per query.
//!
//! Only `rt-multi-thread` is enabled. Git/LSP evidence collection dispatches
//! existing blocking `std::process`/`std::thread` code via `spawn_blocking`
//! rather than moving to `tokio::process`, so no `time`/`process`/`net`
//! feature is needed. `max_blocking_threads(4)`: at most two blocking
//! sources (Git, LSP) run concurrently today, well under Tokio's default
//! 512 — matches this repo's shared-machine CPU-budget convention.

use std::sync::OnceLock;
use tokio::runtime::Runtime;

static EVIDENCE_RUNTIME: OnceLock<Runtime> = OnceLock::new();

pub fn runtime() -> &'static Runtime {
    EVIDENCE_RUNTIME.get_or_init(|| {
        tokio::runtime::Builder::new_multi_thread()
            .worker_threads(2)
            .max_blocking_threads(4)
            .thread_name("oxide-evidence")
            .build()
            .expect("evidence runtime must build: only rt-multi-thread is required")
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn runtime_is_a_singleton_across_calls() {
        let a = runtime() as *const Runtime;
        let b = runtime() as *const Runtime;
        assert_eq!(a, b);
    }

    #[test]
    fn runtime_can_join_two_concurrent_blocking_tasks() {
        let rt = runtime();
        let (a, b) = rt.block_on(async {
            let t1 = tokio::task::spawn_blocking(|| {
                std::thread::sleep(std::time::Duration::from_millis(20));
                1
            });
            let t2 = tokio::task::spawn_blocking(|| {
                std::thread::sleep(std::time::Duration::from_millis(20));
                2
            });
            tokio::join!(t1, t2)
        });
        assert_eq!((a.unwrap(), b.unwrap()), (1, 2));
    }
}
```

- [ ] **Step 4: Write `src/evidence/mod.rs`**

```rust
//! Isolated async evidence collection: independent, optional evidence
//! sources (structural, Git, LSP, blast-radius) run concurrently behind one
//! synchronous entry point, `EvidenceCoordinator::collect()`. The rest of
//! OXIDE — CLI, MCP service API, retrieval core, SQLite/indexing path,
//! allocator — stays fully synchronous; see
//! docs/superpowers/specs/2026-09-15-evidence-coordinator-refactor-design.md.

pub mod contract;
pub mod coordinator;
pub mod runtime;
pub mod scope;

pub use contract::{Degraded, DegradeReason, EvidenceCandidate, EvidenceSource, Outcome};
pub use coordinator::EvidenceCoordinator;
```

- [ ] **Step 5: Register in `src/lib.rs`**

Add `pub mod evidence;` alongside the other top-level module declarations.

- [ ] **Step 6: Stub `coordinator.rs`/`scope.rs` so the crate builds**

`src/evidence/scope.rs`: `//! Filled in by Task 6.`
`src/evidence/coordinator.rs`:
```rust
//! Filled in by Task 7.
pub struct EvidenceCoordinator;
```

- [ ] **Step 7: Run**

Run: `cargo build -j 2 && cargo test -j 2 --lib evidence::`
Expected: builds clean, both `runtime.rs` tests PASS.

- [ ] **Step 8: Commit**

```bash
cargo fmt && cargo clippy -j 2 --all-targets -- -D warnings
git add Cargo.toml Cargo.lock src/lib.rs src/evidence/
git commit -m "feat: evidence contract types + isolated Tokio runtime (src/evidence/)"
```

---

### Task 6: Shared seed-pool file-scoping helper (fixes audit MINOR-1)

**Files:**
- Modify: `src/evidence/scope.rs`

**Interfaces:**
- Produces: `pub fn scope_files_from_seeds(seeds: &[SearchHit], max_files: usize) -> Vec<String>` — replaces the three independent implementations at `context.rs:288-296` (structural), `:380-385` (git), `:491-496` (LSP).

- [ ] **Step 1: Write the failing test**

```rust
use crate::retrieval::SearchHit;
use crate::symbols::{Language, Symbol, SymbolKind};

fn hit(file: &str, score: f32) -> SearchHit {
    SearchHit {
        symbol: Symbol {
            file: file.to_string(), name: "x".into(), qualified_name: "x".into(),
            kind: SymbolKind::Function, language: Language::Python,
            start_line: 1, end_line: 2, content_hash: 0, signature: String::new(),
            imports: vec![], exported: false, parent: None,
            references: vec![], calls: vec![], bases: vec![],
        },
        score, reasons: vec![], snippet: String::new(),
    }
}

#[test]
fn caps_to_max_files_preserving_seed_order_and_dedups() {
    let seeds = vec![hit("a.py", 3.0), hit("b.py", 2.0), hit("a.py", 1.9), hit("c.py", 1.0)];
    let scoped = scope_files_from_seeds(&seeds, 2);
    assert_eq!(scoped, vec!["a.py".to_string(), "b.py".to_string()]);
}

#[test]
fn empty_seeds_yields_empty_scope() {
    assert!(scope_files_from_seeds(&[], 5).is_empty());
}
```

- [ ] **Step 2: Run to verify it fails**

Run: `cargo test -j 2 --lib evidence::scope:: -- --nocapture`
Expected: compile error — function doesn't exist yet.

- [ ] **Step 3: Implement**

```rust
//! One shared "which files does this seed pool touch" helper, replacing the
//! three independent implementations that used to live inline in
//! `context.rs` for structural/Git/LSP evidence (audit MINOR-1: they had
//! silently diverging bounds despite a comment claiming parity). A source
//! that needs a different bound passes a different `max_files` and
//! documents why at its call site.

use crate::retrieval::SearchHit;

pub fn scope_files_from_seeds(seeds: &[SearchHit], max_files: usize) -> Vec<String> {
    let mut files = Vec::new();
    for h in seeds {
        if files.len() >= max_files {
            break;
        }
        if !files.contains(&h.symbol.file) {
            files.push(h.symbol.file.clone());
        }
    }
    files
}

#[cfg(test)]
mod tests {
    use super::*;
    // (test bodies from Step 1)
}
```

- [ ] **Step 4: Run**

Run: `cargo test -j 2 --lib evidence::scope::`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cargo fmt && cargo clippy -j 2 --all-targets -- -D warnings
git add src/evidence/scope.rs
git commit -m "feat: shared scope_files_from_seeds helper for evidence file-scoping"
```

(Wired into its three call sites in Task 7/8, alongside the coordinator itself.)

---

### Task 7: `EvidenceCoordinator` — structural/blast-radius on a scoped OS thread, Git/LSP I/O via `spawn_blocking`, fixed-order merge

The largest task — split into sub-steps that each compile independently.

**Files:**
- Modify: `src/evidence/coordinator.rs`
- Test: inline, plus `tests/evidence_coordinator_determinism.rs` (new)

**Interfaces:**
- Consumes: `crate::gitctx::build_git_context` (Task 1's `Result` signature), `crate::lsp::{LspClient, enrich_seeds}`, `crate::relations::RelationGraph`, `crate::blast_radius::compute`, `crate::retrieval::SearchHit`, `crate::symbols::Symbol`, `crate::evidence::scope::scope_files_from_seeds`, `crate::evidence::runtime::runtime`.
- Produces: `EvidenceCoordinator::collect(CollectInput) -> CollectOutput`.

- [ ] **Step 1: Define `CollectInput`/`CollectOutput` and the skeleton**

```rust
//! Orchestrates the four opt-in evidence sources concurrently:
//! - structural (AST-precise callers) and blast-radius run on a scoped OS
//!   thread — both borrow `RelationGraph<'a>`, not `Send`/`'static` (it
//!   borrows `symbols: &'a [Symbol]`, itself borrowed from the caller's
//!   `RetrievalEngine` snapshot), so they cannot become Tokio tasks.
//! - Git and LSP *I/O* run as `spawn_blocking` tasks, `tokio::join!`ed so
//!   their waits overlap. Both need only owned/`Send` data: Git needs
//!   `Arc<[Symbol]>` + the repo root; LSP needs the same `Arc<[Symbol]>`
//!   plus full ownership of the `LspClient` (moved in, moved back out via
//!   the task's return value — `LspClient`/`Transport` are `Send`, only the
//!   *borrow* a caller normally holds isn't `'static`).
//! - Git's `graph.neighbors`/`callers_of` enrichment (raw `GitContext` →
//!   scored candidates) is NOT part of the async task — it needs `graph`,
//!   so it runs synchronously afterward, back on the calling thread.
//!
//! All four sources fold into the output in one fixed order
//! (Structural → Git → LSP → BlastRadius) regardless of completion order —
//! see `tests/evidence_coordinator_determinism.rs`.

use crate::blast_radius;
use crate::config::{
    BLAST_RADIUS_CONTEXT_ITEMS, BLAST_RADIUS_MAX_SEEDS, BLAST_RADIUS_SCORE_FRACTION,
    GIT_CHANGED_CONTEXT_ITEMS, GIT_CHANGED_SCORE_FRACTION, GIT_COCHANGE_SCORE_FRACTION,
    GIT_COCHANGE_SYMBOLS_PER_FILE, GIT_NEIGHBOR_HITS_PER_CHANGED, GIT_NEIGHBOR_SCORE_FRACTION,
    LSP_MAX_SEEDS, LSP_PER_SEED_ITEMS, LSP_SCORE_FRACTION,
};
use crate::evidence::contract::{Degraded, DegradeReason, EvidenceCandidate, EvidenceSource, Outcome};
use crate::evidence::runtime::runtime;
use crate::evidence::scope::scope_files_from_seeds;
use crate::gitctx;
use crate::lsp::{enrich_seeds, LspClient};
use crate::relations::RelationGraph;
use crate::retrieval::SearchHit;
use crate::symbols::{Language, Symbol, SymbolKind};
use std::collections::HashMap;
use std::path::Path;
use std::sync::Arc;
use std::time::Instant;

pub struct CollectInput<'a> {
    pub root: &'a Path,
    pub symbols: &'a [Symbol],
    pub graph: &'a RelationGraph<'a>,
    pub seeds: &'a [SearchHit],
    pub structural_max_seeds: usize,
    pub structural_max_files: usize,
    pub blast_radius: bool,
    pub git: bool,
    pub lsp: bool,
    /// `Some` only on the MCP `ProcessCache` path (Task 9); moved in and
    /// handed back in the output so the caller restores it into the cache
    /// slot regardless of whether this call used it.
    pub lsp_client: Option<LspClient>,
}

pub struct CollectOutput {
    pub candidates: Vec<EvidenceCandidate>,
    pub degraded: Vec<Degraded>,
    pub git_evidence: Option<crate::gitctx::GitEvidence>,
    pub lsp_client: Option<LspClient>,
}

pub struct EvidenceCoordinator;
```

- [ ] **Step 2: Port structural + blast-radius evidence verbatim (from `context.rs:259-357`, unchanged scoring)**

```rust
fn structural_evidence(
    graph: &RelationGraph<'_>,
    seeds: &[SearchHit],
    max_seeds: usize,
    max_files: usize,
) -> Vec<EvidenceCandidate> {
    const STRUCTURAL_CALLER_HITS_PER_SEED: usize = 2;
    let scope_files = scope_files_from_seeds(seeds, max_files);
    let mut out = Vec::new();
    for seed in seeds.iter().take(max_seeds) {
        let callers = graph.callers_of(&seed.symbol.name);
        let scoped = callers
            .into_iter()
            .filter(|c| scope_files.contains(&c.file))
            .filter(|c| c.id() != seed.symbol.id())
            .take(STRUCTURAL_CALLER_HITS_PER_SEED);
        for caller in scoped {
            out.push(EvidenceCandidate {
                symbol: caller.clone(),
                score: seed.score * 0.4,
                reasons: vec![format!("ast-grep-caller←{}", seed.symbol.qualified_name)],
            });
        }
    }
    out
}

fn blast_radius_evidence(graph: &RelationGraph<'_>, seeds: &[SearchHit]) -> Vec<EvidenceCandidate> {
    let anchors: Vec<&Symbol> = seeds.iter().take(BLAST_RADIUS_MAX_SEEDS).map(|h| &h.symbol).collect();
    let by_qname: HashMap<&str, f32> =
        seeds.iter().map(|h| (h.symbol.qualified_name.as_str(), h.score)).collect();
    blast_radius::compute(graph, &anchors, true)
        .into_iter()
        .take(BLAST_RADIUS_CONTEXT_ITEMS)
        .map(|(item, sym)| {
            let seed_score = by_qname.get(item.via.as_str()).copied().unwrap_or(0.0);
            EvidenceCandidate {
                symbol: sym.clone(),
                score: seed_score * BLAST_RADIUS_SCORE_FRACTION,
                reasons: vec![format!("blast-radius:{}←{}", item.relation, item.via)],
            }
        })
        .collect()
}
```

- [ ] **Step 3: Write a unit test pinning that the port matches pre-refactor reason tags/scores exactly**

Mirror `context.rs`'s existing `#[cfg(test)]` fixture-construction helper (its `seed()`/`SqliteStore::open(":memory:")`-based setup, `context.rs:1009` onward) into this module's test suite rather than re-deriving fixture construction. Assert `structural_evidence`'s output reason tag is exactly `"ast-grep-caller←{qualified_name}"` and score is exactly `seed.score * 0.4`, and `blast_radius_evidence`'s score is exactly `seed_score * BLAST_RADIUS_SCORE_FRACTION`.

- [ ] **Step 4: Run**

Run: `cargo test -j 2 --lib evidence::coordinator:: -- --nocapture`
Expected: PASS.

- [ ] **Step 5: Implement Git evidence's async I/O phase**

```rust
/// Runs entirely on a `spawn_blocking` task — pure subprocess I/O +
/// `symbols`-slice filtering (`gitctx::build_git_context`), no
/// `RelationGraph`. `range` is always `""` (worktree vs HEAD) — matches
/// today's opt-in git-evidence call; `review --diff`'s explicit-range path
/// (Task 1) is a separate, non-coordinator call.
async fn git_io(root: std::path::PathBuf, symbols: Arc<[Symbol]>) -> Outcome<gitctx::GitContext> {
    let start = Instant::now();
    let handle = tokio::task::spawn_blocking(move || gitctx::build_git_context(&root, &symbols, ""));
    match handle.await {
        Ok(Ok(ctx)) => Outcome::Ready(ctx),
        Ok(Err(e)) => Outcome::Degraded(Degraded {
            source: EvidenceSource::Git,
            reason: DegradeReason::ProtocolError { detail: e.to_string() },
            elapsed: start.elapsed(),
        }),
        Err(join_err) => Outcome::Degraded(Degraded {
            source: EvidenceSource::Git,
            reason: DegradeReason::Unavailable { detail: join_err.to_string() },
            elapsed: start.elapsed(),
        }),
    }
}
```
No `tokio::time::timeout` here deliberately — the runtime excludes the `time` feature (Task 5), and a `git` subprocess call has no cooperative cancellation point mid-flight to time out into anyway (`gitutil::run_git` doesn't support killing a running child); this returns whenever the subprocess call itself returns.

- [ ] **Step 6: Implement LSP evidence's async I/O phase**

```rust
/// Runs on a `spawn_blocking` task. Takes ownership of `client` and hands it
/// back in the return tuple regardless of outcome, so the caller can close
/// it (CLI) or restore it to the cache (MCP) either way. `py_seeds_owned`/
/// `py_seed_scores` are owned copies (same length/order) of the top Python
/// seeds — `enrich_seeds` needs data borrowed from something this task
/// itself owns, not the caller's borrowed `symbols`/`seeds`.
async fn lsp_io(
    mut client: LspClient,
    root: std::path::PathBuf,
    symbols: Arc<[Symbol]>,
    py_seeds_owned: Vec<Symbol>,
    py_seed_scores: Vec<f32>,
    scope_files: Vec<String>,
) -> (Option<LspClient>, Outcome<Vec<EvidenceCandidate>>) {
    let start = Instant::now();
    let handle = tokio::task::spawn_blocking(move || {
        let py_seeds: Vec<&Symbol> = py_seeds_owned.iter().collect();
        let by_qname: HashMap<&str, f32> = py_seeds_owned
            .iter()
            .zip(py_seed_scores.iter())
            .map(|(s, score)| (s.qualified_name.as_str(), *score))
            .collect();
        let top_score = py_seed_scores.first().copied().unwrap_or(0.0);
        let evidence = enrich_seeds(
            &mut client, &root, &symbols, &py_seeds, &scope_files, LSP_MAX_SEEDS, LSP_PER_SEED_ITEMS,
        );
        let candidates: Vec<EvidenceCandidate> = evidence
            .into_iter()
            .map(|ev| {
                let seed_score = ev.via.and_then(|v| by_qname.get(v).copied()).unwrap_or(top_score);
                EvidenceCandidate {
                    symbol: ev.symbol.clone(),
                    score: seed_score * LSP_SCORE_FRACTION,
                    reasons: vec![ev.reason],
                }
            })
            .collect();
        (client, candidates)
    });
    match handle.await {
        Ok((client, candidates)) => (Some(client), Outcome::Ready(candidates)),
        Err(join_err) => (
            None, // panicked task: nothing to hand back; caller respawns next time, same as today's is_alive()-false path
            Outcome::Degraded(Degraded {
                source: EvidenceSource::Lsp,
                reason: DegradeReason::Unavailable { detail: join_err.to_string() },
                elapsed: start.elapsed(),
            }),
        ),
    }
}
```

- [ ] **Step 7: Implement `collect()`'s body**

```rust
impl EvidenceCoordinator {
    pub fn collect(input: CollectInput<'_>) -> CollectOutput {
        let CollectInput {
            root, symbols, graph, seeds, structural_max_seeds, structural_max_files,
            blast_radius, git, lsp, lsp_client,
        } = input;

        let mut degraded = Vec::new();
        let symbols_arc: Arc<[Symbol]> = Arc::from(symbols);

        let py_seeds_owned: Vec<Symbol> = seeds
            .iter().filter(|h| h.symbol.language == Language::Python)
            .take(LSP_MAX_SEEDS).map(|h| h.symbol.clone()).collect();
        let py_seed_scores: Vec<f32> = seeds
            .iter().filter(|h| h.symbol.language == Language::Python)
            .take(LSP_MAX_SEEDS).map(|h| h.score).collect();
        let lsp_scope_files = scope_files_from_seeds(seeds, usize::MAX);

        let mut structural_out = Vec::new();
        let mut blast_out = Vec::new();
        let mut git_result: Option<gitctx::GitContext> = None;
        let mut git_degraded = None;
        let mut returned_lsp_client: Option<LspClient> = None;
        let mut lsp_out: Vec<EvidenceCandidate> = Vec::new();
        let mut lsp_degraded = None;

        std::thread::scope(|s| {
            s.spawn(|| { structural_out = structural_evidence(graph, seeds, structural_max_seeds, structural_max_files); });
            if blast_radius {
                s.spawn(|| { blast_out = blast_radius_evidence(graph, seeds); });
            }

            let rt = runtime();
            let (git_res, lsp_res) = rt.block_on(async {
                let git_fut = async {
                    if git { Some(git_io(root.to_path_buf(), symbols_arc.clone()).await) } else { None }
                };
                let lsp_fut = async {
                    if lsp {
                        if let Some(client) = lsp_client {
                            let (client_back, outcome) = lsp_io(
                                client, root.to_path_buf(), symbols_arc.clone(),
                                py_seeds_owned, py_seed_scores, lsp_scope_files,
                            ).await;
                            Some((client_back, outcome))
                        } else {
                            None // CLI spawn-per-call path — wired in Task 8
                        }
                    } else { None }
                };
                tokio::join!(git_fut, lsp_fut)
            });

            git_result = git_res.and_then(|o| match o {
                Outcome::Ready(ctx) => Some(ctx),
                Outcome::Degraded(d) => { git_degraded = Some(d); None }
            });
            if let Some((client, outcome)) = lsp_res {
                returned_lsp_client = client;
                match outcome {
                    Outcome::Ready(c) => lsp_out = c,
                    Outcome::Degraded(d) => lsp_degraded = Some(d),
                }
            }
        });

        // ---- fixed-order fold: Structural → Git → LSP → BlastRadius ----
        let mut candidates = Vec::new();
        candidates.extend(structural_out);

        let mut git_evidence = None;
        if let Some(ctx) = git_result {
            let top_seed_score = seeds.first().map(|h| h.score).unwrap_or(0.0);
            let git_scope_files = scope_files_from_seeds(seeds, usize::MAX);
            candidates.extend(git_graph_enrichment(graph, &ctx, &git_scope_files, symbols, top_seed_score));
            git_evidence = Some(ctx.evidence);
        }
        if let Some(d) = git_degraded { degraded.push(d); }

        candidates.extend(lsp_out);
        if let Some(d) = lsp_degraded { degraded.push(d); }

        candidates.extend(blast_out);

        CollectOutput { candidates, degraded, git_evidence, lsp_client: returned_lsp_client }
    }
}
```

- [ ] **Step 8: Implement `git_graph_enrichment` (ported from `context.rs:386-470`, unchanged scoring)**

```rust
fn git_graph_enrichment(
    graph: &RelationGraph<'_>,
    ctx: &gitctx::GitContext,
    git_scope_files: &[String],
    symbols: &[Symbol],
    top_seed_score: f32,
) -> Vec<EvidenceCandidate> {
    let mut out = Vec::new();
    for cs in ctx.changed_symbols.iter().take(GIT_CHANGED_CONTEXT_ITEMS) {
        out.push(EvidenceCandidate {
            symbol: cs.symbol.clone(),
            score: top_seed_score * GIT_CHANGED_SCORE_FRACTION,
            reasons: vec![format!("git-changed(+{})←{}", cs.added_lines, cs.symbol.file)],
        });
        let mut hits = 0usize;
        for (rel, n) in graph.neighbors(&cs.symbol) {
            if hits >= GIT_NEIGHBOR_HITS_PER_CHANGED { break; }
            if rel != "test" && rel != "uses" { continue; }
            hits += 1;
            out.push(EvidenceCandidate {
                symbol: n.clone(),
                score: top_seed_score * GIT_NEIGHBOR_SCORE_FRACTION,
                reasons: vec![format!("git-caller-of-changed←{}", cs.symbol.qualified_name)],
            });
        }
        for caller in graph.callers_of(&cs.symbol.name).into_iter()
            .filter(|c| git_scope_files.iter().any(|f| f == &c.file))
            .filter(|c| c.id() != cs.symbol.id())
            .take(GIT_NEIGHBOR_HITS_PER_CHANGED - hits.min(GIT_NEIGHBOR_HITS_PER_CHANGED))
        {
            out.push(EvidenceCandidate {
                symbol: caller.clone(),
                score: top_seed_score * GIT_NEIGHBOR_SCORE_FRACTION,
                reasons: vec![format!("git-caller-of-changed←{}", cs.symbol.qualified_name)],
            });
        }
    }
    for entry in &ctx.evidence.co_change {
        for s in symbols.iter()
            .filter(|s| s.file == entry.co_changed_with && s.kind != SymbolKind::Module)
            .take(GIT_COCHANGE_SYMBOLS_PER_FILE)
        {
            out.push(EvidenceCandidate {
                symbol: s.clone(),
                score: top_seed_score * GIT_COCHANGE_SCORE_FRACTION * entry.strength.min(1.0),
                reasons: vec![format!("git-cochange({} commits)←{}", entry.count, entry.file)],
            });
        }
    }
    out
}
```

- [ ] **Step 9: Run the full evidence module test suite**

Run: `cargo build -j 2 && cargo test -j 2 --lib evidence::`
Expected: builds clean (`grep -n "todo!\|unreachable!" src/evidence/coordinator.rs` returns nothing), all tests PASS.

- [ ] **Step 10: Write the determinism test**

New file `tests/evidence_coordinator_determinism.rs`:
```rust
//! Pins the audit's determinism requirement: workers may finish in any
//! order, but the merged output must be byte-identical regardless.

use std::process::Command;

fn run_query(repo: &str, extra_env: &[(&str, &str)]) -> String {
    let mut cmd = Command::new(env!("CARGO_BIN_EXE_oxide"));
    cmd.args(["query", "where is retry logic", "--json", "--path", repo]).env("OXIDE_EMBED_NATIVE", "hashed");
    for (k, v) in extra_env { cmd.env(k, v); }
    let out = cmd.output().expect("oxide query must run");
    assert!(out.status.success(), "{}", String::from_utf8_lossy(&out.stderr));
    String::from_utf8_lossy(&out.stdout).into_owned()
}

#[test]
fn completion_order_never_changes_final_json_output() {
    let baseline = run_query("fixtures/py_repo", &[]);
    let git_slow = run_query("fixtures/py_repo", &[("OXIDE_EVIDENCE_ARTIFICIAL_DELAY_MS", "git=40,lsp=0")]);
    let lsp_slow = run_query("fixtures/py_repo", &[("OXIDE_EVIDENCE_ARTIFICIAL_DELAY_MS", "git=0,lsp=40")]);
    assert_eq!(baseline, git_slow, "completion order must not change output");
    assert_eq!(baseline, lsp_slow, "completion order must not change output");
}
```

Add the `cfg(test)`-only delay hook (compiles out of release builds entirely) to `coordinator.rs`:
```rust
#[cfg(test)]
fn artificial_delay_ms(source: &str) -> u64 {
    std::env::var("OXIDE_EVIDENCE_ARTIFICIAL_DELAY_MS").ok()
        .and_then(|spec| spec.split(',').find_map(|pair| pair.split_once('=').filter(|(k, _)| *k == source)))
        .and_then(|(_, v)| v.parse::<u64>().ok())
        .unwrap_or(0)
}
#[cfg(not(test))]
fn artificial_delay_ms(_source: &str) -> u64 { 0 }
```
Call `std::thread::sleep(std::time::Duration::from_millis(artificial_delay_ms("git")))` as the first line inside `git_io`'s `spawn_blocking` closure, and the `"lsp"` equivalent inside `lsp_io`'s.

Run: `cargo test -j 2 --test evidence_coordinator_determinism -- --nocapture` (fully exercised once Task 8 wires `context.rs` to the coordinator — re-run again at the end of that task).

- [ ] **Step 11: Commit**

```bash
cargo fmt && cargo clippy -j 2 --all-targets -- -D warnings
git add src/evidence/coordinator.rs tests/evidence_coordinator_determinism.rs
git commit -m "feat: EvidenceCoordinator — concurrent structural/git/lsp/blast-radius collection, fixed-order deterministic merge"
```

---

### Task 8: Wire `context.rs` to call `EvidenceCoordinator::collect()`

**Files:**
- Modify: `src/context.rs:145-154` (`build_context`), `:167-553` (`build_context_with`)

**Interfaces:**
- `build_context_with`'s signature changes: `lsp_client: Option<&mut crate::lsp::LspClient>` becomes `lsp_client: Option<crate::lsp::LspClient>` (owned), and the return type becomes `Result<(ContextPack, Option<crate::lsp::LspClient>)>` — the client is handed back so no caller silently drops a live session it owned. `build_context` (the `None`-in convenience wrapper) absorbs this internally and keeps its own old signature (`-> Result<ContextPack>`), closing any returned client itself.

- [ ] **Step 1: Replace lines 259-552 with the coordinator call**

The always-on `RelationGraph::neighbors` expansion at `context.rs:217-257` (computing `seen_seeds`/`graph`) stays untouched — it's core retrieval expansion, not one of the four pluggable sources. Replace everything from the `STRUCTURAL_CALLER_HITS_PER_SEED` comment (`:259`) through the LSP block's closing `}` (`:552`) with:

```rust
        if let Some((max_seeds, max_files)) = opts.retrieval_mode.structural_budget() {
            let output = crate::evidence::EvidenceCoordinator::collect(
                crate::evidence::coordinator::CollectInput {
                    root, symbols, graph: &graph, seeds: &seeds,
                    structural_max_seeds: max_seeds, structural_max_files: max_files,
                    blast_radius: opts.blast_radius, git: opts.git, lsp: opts.lsp,
                    lsp_client,
                },
            );
            for c in output.candidates {
                let role = if is_test_symbol(&c.symbol) { Role::Test } else { Role::Dependency };
                order_note(Candidate { symbol: c.symbol, score: c.score, reasons: c.reasons, role });
            }
            git_evidence = output.git_evidence;
            returned_lsp_client = output.lsp_client;
        } else {
            returned_lsp_client = lsp_client;
        }
```

- [ ] **Step 2: Change `build_context_with`'s signature to own (not borrow) the LSP client and hand it back**

```rust
pub fn build_context_with(
    root: &Path,
    engine: &RetrievalEngine<'_>,
    task: &str,
    opts: &ContextOptions,
    lsp_client: Option<crate::lsp::LspClient>,
) -> Result<(ContextPack, Option<crate::lsp::LspClient>)> {
```
Declare `let mut returned_lsp_client: Option<crate::lsp::LspClient> = None;` near the top of the function body (alongside the existing `let mut git_evidence: Option<GitEvidence> = None;` at `:215`), and at every existing `Ok(ContextPack { ... })` return point in the function, wrap as `Ok((ContextPack { ... }, returned_lsp_client))`. When `seeds.is_empty()` (the `if !seeds.is_empty() { ... }` block at `:219` never runs), `returned_lsp_client` stays `None` unless explicitly set to the original `lsp_client` — set `returned_lsp_client = lsp_client;` immediately before that `if` block so the caller-owned client is never silently dropped on the empty-seeds path either.

- [ ] **Step 3: Update `build_context` to keep its old, simpler contract**

```rust
pub fn build_context(
    root: &Path, store: &dyn IndexBackend, embedder: &dyn EmbeddingProvider,
    task: &str, opts: &ContextOptions,
) -> Result<ContextPack> {
    let engine = RetrievalEngine::new(store, embedder);
    let (pack, client) = build_context_with(root, &engine, task, opts, None)?;
    if let Some(client) = client {
        client.close(); // this call owned it (CLI spawn-per-call path) — matches pre-refactor behavior
    }
    Ok(pack)
}
```

- [ ] **Step 4: Add `diagnostics` to `ContextPack` (previously-invisible degraded-source reporting)**

Add to `ContextPack` (`context.rs:80-96`):
```rust
    /// Evidence sources that degraded during this call (timed out,
    /// unavailable, protocol error) — empty unless something actually
    /// failed; never causes this call itself to fail.
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub diagnostics: Vec<String>,
```
In Step 1's block, set `diagnostics: output.degraded.iter().map(|d| format!("{}: {:?}", d.source.as_str(), d.reason)).collect()` for the new `ContextPack` field added in this step (default to `Vec::new()` on the `else` branch and the empty-seeds path).

- [ ] **Step 5: Fix `service.rs`'s call site to compile against the new tuple/owned-client signature (full ownership restore is Task 9 — for now, unwrap and discard)**

```rust
        let (pack, _client) = build_context_with(
            &self.root, &engine, task,
            &ContextOptions { budget_tokens, retrieval_mode, blast_radius, git, lsp, ..ContextOptions::default() },
            lsp_guard.as_mut().and_then(|g| g.take()),
        )
        .map_err(|e| ServiceError::from_error(ErrorCode::ContextFailed, e))?;
```
(Task 9 replaces this to restore `_client` into the cache slot — this step exists only so the crate compiles between Task 8 and Task 9's commits.)

- [ ] **Step 6: Run the full existing `context.rs` test suite — must stay green with zero test-body changes**

Run: `cargo test -j 2 --lib context::`
Expected: PASS unchanged. Any failure means the port drifted from pre-refactor scoring/reason tags — diff against `context.rs`'s pre-Task-8 git history, don't adjust the test.

- [ ] **Step 7: Re-run the determinism test from Task 7**

Run: `cargo test -j 2 --test evidence_coordinator_determinism -- --nocapture`
Expected: PASS.

- [ ] **Step 8: Run the committed benchmark gate**

Run: `cargo build --release -j 2 && ./target/release/oxide eval --config fixtures/benchmark.json`
Expected: `hybrid recall@5 0.909 ≥ vector-only recall@5 0.818`, unchanged.

- [ ] **Step 9: Commit**

```bash
cargo fmt && cargo clippy -j 2 --all-targets -- -D warnings
git add src/context.rs src/service.rs
git commit -m "refactor: context.rs delegates structural/git/lsp/blast-radius evidence to EvidenceCoordinator"
```

---

### Task 9: `service.rs` hands LSP session ownership to the coordinator and restores it after

**Files:**
- Modify: `src/service.rs:808-867` (`context()`)

- [ ] **Step 1: Replace the borrow-based guard pattern with take/restore**

```rust
        let lsp_slot = (lsp && self.use_process_cache).then(|| process_cache().lsp_client_slot(&self.root));
        let owned_client: Option<LspClient> = match &lsp_slot {
            Some(slot) => {
                let mut guard = slot.lock().unwrap_or_else(|e| e.into_inner());
                let needs_spawn = match guard.as_mut() {
                    Some(client) => !client.is_alive(),
                    None => true,
                };
                if needs_spawn {
                    let server = std::env::var("OXIDE_LSP_SERVER").unwrap_or_else(|_| "ty".to_string());
                    *guard = LspClient::spawn(
                        &server, &self.root,
                        Duration::from_millis(LSP_INIT_TIMEOUT_MS), Duration::from_millis(LSP_REQUEST_TIMEOUT_MS),
                    ).ok();
                }
                guard.take()
            }
            None => None,
        };

        let (pack, returned_client) = build_context_with(
            &self.root, &engine, task,
            &ContextOptions { budget_tokens, retrieval_mode, blast_radius, git, lsp, ..ContextOptions::default() },
            owned_client,
        )
        .map_err(|e| ServiceError::from_error(ErrorCode::ContextFailed, e))?;

        if let Some(slot) = &lsp_slot {
            let mut guard = slot.lock().unwrap_or_else(|e| e.into_inner());
            *guard = returned_client; // None here (panicked spawn_blocking) means the next call respawns, same as today's is_alive()-false path
        }
```
This replaces both the old inline spawn/respawn block and Task 8's temporary discard shim.

- [ ] **Step 2: Add a regression test proving state survives the round trip**

Extend `tests/lsp_mcp_process_cache_reuse.rs` with a correctness assertion (the existing test in this file already proves the second call is faster; this proves session *state* — from Task 2's content-hash tracking — persists across two `service.context()` calls, not just speed):
```rust
#[test]
fn cached_lsp_session_state_survives_the_ownership_round_trip_through_the_coordinator() {
    if !ty_available() {
        eprintln!("skipping: `ty` not on PATH");
        return;
    }
    let (service, _tmp) = service_with_two_file_repo(); // reuse this file's existing fixture helper
    let first = service.context("target function", 2048, RetrievalMode::default(), false, false, true).unwrap();
    let second = service.context("target function", 2048, RetrievalMode::default(), false, false, true).unwrap();
    assert!(!first.items.is_empty() || !second.items.is_empty());
}
```

- [ ] **Step 3: Run**

Run: `cargo test -j 2 --lib service:: && cargo test -j 2 --test lsp_mcp_process_cache_reuse -- --nocapture`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
cargo fmt && cargo clippy -j 2 --all-targets -- -D warnings
git add src/service.rs tests/lsp_mcp_process_cache_reuse.rs
git commit -m "refactor: service.rs transfers LSP session ownership into the coordinator and restores it after"
```

---

### Task 10: Compatibility-gate fixtures (byte-identical default/git/lsp paths)

**Files:**
- Create: `tests/evidence_coordinator_compat.rs`, `fixtures/evidence_compat/`

- [ ] **Step 1: Capture pinned expected outputs (once, reviewed before committing)**

```bash
cargo build --release -j 2
mkdir -p fixtures/evidence_compat
for cond in default git_disabled lsp_disabled; do
  ./target/release/oxide query "where is retry logic" --json --path fixtures/py_repo \
    > "fixtures/evidence_compat/${cond}.json"
done
./target/release/oxide query "where is retry logic" --git --json --path fixtures/py_repo \
  > fixtures/evidence_compat/git_enabled.json
./target/release/oxide query "where is retry logic" --lsp --json --path fixtures/py_repo \
  > fixtures/evidence_compat/lsp_enabled.json
```

- [ ] **Step 2: Write the test**

```rust
//! Byte-identical compatibility gates for the evidence-coordinator refactor.
//! `lsp_enabled.json` requires `ty` on PATH; the other four do not touch LSP.

use std::process::Command;

fn run(args: &[&str]) -> String {
    let out = Command::new(env!("CARGO_BIN_EXE_oxide")).args(args).env("OXIDE_EMBED_NATIVE", "hashed")
        .output().expect("oxide must run");
    assert!(out.status.success(), "{}", String::from_utf8_lossy(&out.stderr));
    String::from_utf8_lossy(&out.stdout).into_owned()
}

fn expect_fixture(name: &str, args: &[&str]) {
    let expected = std::fs::read_to_string(format!("fixtures/evidence_compat/{name}.json"))
        .unwrap_or_else(|_| panic!("missing fixture fixtures/evidence_compat/{name}.json"));
    assert_eq!(run(args).trim(), expected.trim(), "condition `{name}` must stay byte-identical");
}

#[test]
fn default_query_is_byte_identical() {
    expect_fixture("default", &["query", "where is retry logic", "--json", "--path", "fixtures/py_repo"]);
}
#[test]
fn git_disabled_is_byte_identical() {
    expect_fixture("git_disabled", &["query", "where is retry logic", "--json", "--path", "fixtures/py_repo"]);
}
#[test]
fn lsp_disabled_is_byte_identical() {
    expect_fixture("lsp_disabled", &["query", "where is retry logic", "--json", "--path", "fixtures/py_repo"]);
}
#[test]
fn git_enabled_is_byte_identical() {
    expect_fixture("git_enabled", &["query", "where is retry logic", "--git", "--json", "--path", "fixtures/py_repo"]);
}
#[test]
fn lsp_enabled_is_byte_identical() {
    if !std::process::Command::new("ty").arg("--version").output().map(|o| o.status.success()).unwrap_or(false) {
        eprintln!("skipping: `ty` not on PATH");
        return;
    }
    expect_fixture("lsp_enabled", &["query", "where is retry logic", "--lsp", "--json", "--path", "fixtures/py_repo"]);
}
```

- [ ] **Step 3: Run**

Run: `cargo test -j 2 --test evidence_coordinator_compat -- --nocapture`
Expected: PASS. Before committing, inspect the captured fixtures from Step 1 for any non-deterministic field (timestamps, absolute paths); normalize such a field out of the comparison rather than shipping a flaky pinned fixture. `fixtures/py_repo` may not itself be a git repo — if `--git` degrades to an empty block there, pin that real result, not an assumed one.

- [ ] **Step 4: Commit**

```bash
git add tests/evidence_coordinator_compat.rs fixtures/evidence_compat/
git commit -m "test: byte-identical compatibility gates for default/git/lsp query paths"
```

---

### Task 11: `oxide lsp install <name>` subcommand (ty-only registry)

**Files:**
- Create: `src/lsp_install.rs`
- Modify: `src/cli.rs`, `src/lib.rs`
- Test: inline, `tests/cli_e2e.rs`

- [ ] **Step 1: Write the failing test**

```rust
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn unsupported_name_returns_a_clear_error_not_a_silent_noop() {
        let err = plan_install("pyright").unwrap_err();
        assert!(err.to_string().contains("not supported yet"), "{err}");
        assert!(err.to_string().contains("pyright"), "{err}");
    }
    #[test]
    fn ty_resolves_to_the_pinned_uv_tool_install_command() {
        let plan = plan_install("ty").unwrap();
        assert_eq!(plan.program, "uv");
        assert_eq!(plan.args, vec!["tool", "install", format!("ty=={}", TY_PINNED_VERSION)]);
    }
}
```

- [ ] **Step 2: Run to verify it fails**

Run: `cargo test -j 2 --lib lsp_install:: -- --nocapture`
Expected: compile error — module doesn't exist yet.

- [ ] **Step 3: Implement `src/lsp_install.rs`**

```rust
//! `oxide lsp install <name>` — a small registry of known LSP-server
//! installers. Only `ty` (Python) has a real entry today, matching that only
//! Python has a working `LspClient` profile in this codebase. Other names
//! return a clear "not supported yet" error rather than installing a binary
//! OXIDE cannot use, or silently no-op-ing.

/// Pinned so `oxide lsp install ty` and CI's `lsp-integration` job install
/// the exact version the real-server tests were written against.
pub const TY_PINNED_VERSION: &str = "0.0.80";

pub struct InstallPlan {
    pub program: String,
    pub args: Vec<String>,
}

#[derive(Debug)]
pub struct LspInstallError(String);

impl std::fmt::Display for LspInstallError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result { write!(f, "{}", self.0) }
}
impl std::error::Error for LspInstallError {}

pub fn plan_install(name: &str) -> Result<InstallPlan, LspInstallError> {
    match name {
        "ty" => Ok(InstallPlan {
            program: "uv".to_string(),
            args: vec!["tool".into(), "install".into(), format!("ty=={TY_PINNED_VERSION}")],
        }),
        other => Err(LspInstallError(format!(
            "LSP server '{other}' is not supported yet — only 'ty' (Python) has a working OXIDE integration today"
        ))),
    }
}

/// Runs the resolved install plan; separate from `plan_install` so planning
/// logic is testable without `uv` installed.
pub fn install(name: &str) -> Result<(), LspInstallError> {
    let plan = plan_install(name)?;
    let status = std::process::Command::new(&plan.program).args(&plan.args).status()
        .map_err(|e| LspInstallError(format!("failed to run `{} {}`: {e}", plan.program, plan.args.join(" "))))?;
    if !status.success() {
        return Err(LspInstallError(format!("`{} {}` exited with {status}", plan.program, plan.args.join(" "))));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    // (test bodies from Step 1)
}
```

- [ ] **Step 4: Register the module and CLI subcommand**

`src/lib.rs`: add `pub mod lsp_install;`.

`src/cli.rs`: new `Cmd` variant near `Review`/`Mcp`:
```rust
    /// Manage optional LSP servers used by `--lsp` semantic enrichment.
    Lsp { #[command(subcommand)] action: LspAction },
```
```rust
#[derive(Subcommand)]
pub enum LspAction {
    /// Install a supported LSP server (currently: `ty`, for Python).
    Install { name: String },
}
```
Dispatch arm (alongside the existing `Cmd::Review { .. } => cmd_review(...)`):
```rust
        Cmd::Lsp { action: LspAction::Install { name } } => cmd_lsp_install(&name),
```
```rust
fn cmd_lsp_install(name: &str) -> Result<(), CliError> {
    crate::lsp_install::install(name).map_err(|e| CliError::generic(e, false))?;
    println!("Installed LSP server '{name}'.");
    Ok(())
}
```
(Reuse `CliError::generic`'s existing signature — see `cmd_review`'s `CliError::generic(e, true)` call for the pattern.)

- [ ] **Step 5: Run**

Run: `cargo test -j 2 --lib lsp_install:: && cargo build -j 2`
Expected: PASS, builds clean.

- [ ] **Step 6: Add a CLI-level test**

In `tests/cli_e2e.rs`:
```rust
#[test]
fn lsp_install_unsupported_name_fails_clearly() {
    let out = std::process::Command::new(env!("CARGO_BIN_EXE_oxide")).args(["lsp", "install", "pyright"]).output().unwrap();
    assert!(!out.status.success());
    assert!(String::from_utf8_lossy(&out.stderr).contains("not supported yet"));
}
```
Run: `cargo test -j 2 --test cli_e2e -- lsp_install`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
cargo fmt && cargo clippy -j 2 --all-targets -- -D warnings
git add src/lsp_install.rs src/cli.rs src/lib.rs tests/cli_e2e.rs
git commit -m "feat: oxide lsp install <name> — ty-only registry, clear error for unsupported servers"
```

---

### Task 12: Capability-fallback protocol-fixture test (fixes audit MINOR-3)

**Files:**
- Create: `fixtures/fake_lsp_no_references/server.py`, `tests/lsp_capability_fallback.rs`
- Modify: `src/lsp/client.rs` (`spawn` → delegates to new `spawn_raw`)

- [ ] **Step 1: Write the fake LSP server fixture**

`fixtures/fake_lsp_no_references/server.py`:
```python
#!/usr/bin/env python3
"""Minimal fake LSP server: answers `initialize` with every capability
OXIDE's client requests EXCEPT `referencesProvider`, so the capability-
fallback test can assert a clean degrade instead of a crash."""
import sys, json

def read_message():
    headers = {}
    while True:
        line = sys.stdin.buffer.readline()
        if not line or line in (b"\r\n", b"\n"):
            break
        if b":" in line:
            k, v = line.decode().split(":", 1)
            headers[k.strip().lower()] = v.strip()
    length = int(headers.get("content-length", 0))
    return json.loads(sys.stdin.buffer.read(length))

def write_message(obj):
    body = json.dumps(obj).encode()
    sys.stdout.buffer.write(f"Content-Length: {len(body)}\r\n\r\n".encode() + body)
    sys.stdout.buffer.flush()

while True:
    msg = read_message()
    if msg is None:
        break
    method = msg.get("method")
    if method == "initialize":
        write_message({"jsonrpc": "2.0", "id": msg["id"], "result": {"capabilities": {
            "positionEncoding": "utf-8", "definitionProvider": True,
            "callHierarchyProvider": True, "implementationProvider": True,
            "diagnosticProvider": {"interFileDependencies": False, "workspaceDiagnostics": False},
        }}})
    elif method == "textDocument/references":
        write_message({"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32601, "message": "not supported"}})
    elif method == "exit":
        break
    elif "id" in msg:
        write_message({"jsonrpc": "2.0", "id": msg["id"], "result": None})
```

- [ ] **Step 2: Add `LspClient::spawn_raw`**

In `src/lsp/client.rs`, refactor `spawn` to delegate:
```rust
    pub fn spawn(program: &str, root: &Path, init_timeout: Duration, request_timeout: Duration) -> Result<Self> {
        Self::spawn_raw(program, &["server"], root, init_timeout, request_timeout)
    }

    /// As [`Self::spawn`], with an explicit argument list — used by tests
    /// pointing at something other than `<program> server`.
    pub fn spawn_raw(program: &str, args: &[&str], root: &Path, init_timeout: Duration, request_timeout: Duration) -> Result<Self> {
        let mut transport = Transport::spawn(program, args, root)
            .with_context(|| format!("spawning `{program} {}`", args.join(" ")))?;
        // ... move the rest of the existing `spawn` body here verbatim, operating on `transport` ...
    }
```

- [ ] **Step 3: Write the test**

`tests/lsp_capability_fallback.rs`:
```rust
//! Covers the audit's capability-fallback gap with a deterministic fake
//! server instead of depending on `ty`'s specific capability set.

use oxide::lsp::LspClient;
use std::time::Duration;

fn python3_available() -> bool {
    std::process::Command::new("python3").arg("--version").output().map(|o| o.status.success()).unwrap_or(false)
}

#[test]
fn references_call_against_a_server_lacking_the_capability_degrades_cleanly() {
    if !python3_available() {
        eprintln!("skipping: python3 not on PATH");
        return;
    }
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path();
    std::fs::write(root.join("a.py"), "def f():\n    pass\n").unwrap();

    let script = concat!(env!("CARGO_MANIFEST_DIR"), "/fixtures/fake_lsp_no_references/server.py");
    let mut client = LspClient::spawn_raw("python3", &[script], root, Duration::from_secs(5), Duration::from_secs(2))
        .expect("fake server must spawn and complete initialize");

    let uri = client.ensure_open("a.py").unwrap();
    let pos = oxide::lsp::client::symbol_position("def f():\n    pass\n", 1, "f").unwrap();
    let result = client.references(&uri, pos);
    assert!(result.is_err(), "the fake server's -32601 must surface as Err, not a fabricated empty Ok");
    // `enrich_seeds` already wraps every call in `if let Ok(...)` — this
    // pins the client-level contract that sits on: a missing-capability
    // error is a plain `Err`, indistinguishable in kind from a timeout, so
    // existing degrade-and-continue handling covers it with no new branch.

    client.close();
}
```

- [ ] **Step 4: Run**

Run: `cargo test -j 2 --test lsp_capability_fallback -- --nocapture`
Expected: PASS (skips cleanly if `python3` is absent).

- [ ] **Step 5: Commit**

```bash
cargo fmt && cargo clippy -j 2 --all-targets -- -D warnings
git add fixtures/fake_lsp_no_references/ tests/lsp_capability_fallback.rs src/lsp/client.rs
git commit -m "test: deterministic capability-fallback coverage via a fake LSP server fixture"
```

---

### Task 13: `lsp-integration` CI job

**Files:**
- Modify: `.github/workflows/ci.yml`

- [ ] **Step 1: Add the job**

```yaml
  lsp-integration:
    name: OXIDE / LSP integration (real ty server)
    needs: quality
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - name: Install pinned Rust toolchain
        run: rustup toolchain install 1.98.0 --profile minimal --component rustfmt --component clippy --component llvm-tools-preview
      - name: Restore Cargo cache
        uses: actions/cache@v6
        with:
          path: |
            ~/.cargo/registry
            ~/.cargo/git
            target
          key: ${{ runner.os }}-cargo-${{ hashFiles('Cargo.lock', 'Cargo.toml', 'rust-toolchain.toml', '.cargo/config.toml', '**/build.rs') }}
      - name: Install uv (for oxide lsp install ty)
        run: curl -LsSf https://astral.sh/uv/install.sh | sh
      - name: Build oxide
        run: cargo build -j 2
      - name: Install ty via oxide's own installer
        run: |
          export PATH="$HOME/.cargo/bin:$HOME/.local/bin:$PATH"
          ./target/debug/oxide lsp install ty
      - name: Run real-server LSP tests
        run: |
          export PATH="$HOME/.local/bin:$PATH"
          cargo test -j 2 --test lsp_ty_integration --test lsp_scope_regression \
            --test lsp_mcp_process_cache_reuse --test lsp_process_cache_staleness -- --nocapture
```
Runs in parallel with `test`/`retrieval-gate` (both `needs: quality` only), isolated so a slow `uv`/`ty` network install never blocks the fast existing jobs; required (no `continue-on-error`), so CI genuinely fails if these don't pass — closing the audit's "presenting CI as LSP-validated while silently skipping" finding.

- [ ] **Step 2: Verify locally that the four listed tests don't silently skip**

Run: `PATH="$HOME/.local/bin:$PATH" cargo test -j 2 --test lsp_ty_integration --test lsp_scope_regression --test lsp_mcp_process_cache_reuse --test lsp_process_cache_staleness -- --nocapture`
Expected: all PASS, none print `"skipping: ty not on PATH"`.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: add required lsp-integration job — installs ty via oxide lsp install, runs real-server tests"
```

Note in the final report: this job's first real GitHub Actions run is unverified until pushed — local verification (Step 2) is what this plan can confirm from this session.

---

### Task 14: Documentation cleanup

**Files:**
- Modify: `AGENTS.md`, `README.md`, `docs/git-aware-context/README.md`, `docs/lsp-enrichment-eval/README.md`, `docs/canonical-baseline.md`, `docs/evidence-coordinator-refactor/README.md`

- [ ] **Step 1: Fix AGENTS.md's language list**

Change `(currently python, typescript/tsx, javascript/jsx, rust, go, java — see` to `(currently python, typescript/tsx, javascript/jsx, rust, go, java, ruby, php, c, c++ — see`.

- [ ] **Step 2: Add the remote-provider step to AGENTS.md's "Embeddings / providers" section**

After the "Provider selection: explicit `--embedder URL` > ..." bullet, add:
```
  A configured remote provider (Voyage/Jina/OpenAI-compatible via
  `$OXIDE_EMBED_PROVIDER`+API key, or `oxide setup`'s saved config gated on
  `remote_consent_ack`) resolves between the explicit-URL tier and
  `$OXIDE_EMBED_NATIVE` — an unconfigured environment falls through
  untouched to the native/hashed tiers exactly as before remote providers
  existed.
```

- [ ] **Step 3: Finish the in-progress `README.md` rewrite**

Add to the existing "Quick start" section (after `oxide review --diff HEAD~1`):
```
oxide query "fix token refresh" --git       # also weigh the current diff's changed symbols
oxide query "fix token refresh" --lsp       # also pull exact references/diagnostics from a running language server (Python/ty today)
oxide lsp install ty                        # install the optional LSP server --lsp uses
```
Add after "## Privacy and offline use"'s existing paragraph:
```
### Remote embedding providers

OXIDE also supports Voyage AI, Jina AI, and any OpenAI-compatible remote
embedding endpoint as an **explicit, consent-gated opt-in** (`oxide setup`).
Unlike the local/offline paths above, a configured remote provider sends
each symbol's text (never a whole file) to that provider's API over the
network — this is the one way OXIDE's default "your code never leaves the
machine" guarantee changes, and only if you deliberately configure it.
```
Add short "Git-aware context" and "LSP semantic enrichment" subsections near "### Blast radius": `--git`/`--lsp` are opt-in and byte-identical when off; git-aware limitations (untracked files excluded, pure renames may be invisible, co-change is heuristic); LSP is Python/`ty`-only, `oxide lsp install ty` to use it; neither is claimed to improve agent-task outcomes (no agent-level benchmark exists for either).

- [ ] **Step 4: Add missing Limitations to `docs/git-aware-context/README.md`**

```
- Untracked files are excluded from all git-aware evidence — `git diff HEAD`
  never sees a file that was never `git add`ed.
- A pure rename with no content change produces no diff hunk, so the
  renamed file is invisible to `--git`'s evidence.
- Git-enabled retrieval quality has not been validated by any agent-outcome
  benchmark — only correctness/plumbing tests exist today.
```

- [ ] **Step 5: Add the cross-source duplicate-evidence note**

Append to `docs/evidence-coordinator-refactor/README.md` (continuing Task 4's file):
```
## Duplicate evidence across sources

When the same symbol is surfaced by more than one evidence source in the
same call, `context.rs`'s `order_note` sums their scores rather than taking
the max — a property of the merge step itself, not specific to any one
source. Applied to git-vs-structural before this refactor and applies
identically to git/LSP/structural/blast-radius after it; not a regression
introduced here, documented because no single doc stated it generally
before.
```

- [ ] **Step 6: Update `docs/lsp-enrichment-eval/README.md`'s stale one-query-lifetime claim**

Replace the "Accepted as a documented limitation, not fixed" `didOpen`-staleness bullet with:
```
- **Fixed, not just accepted** — `didOpen` staleness: a file edited between
  two calls against the same `LspClient` now triggers a full-document
  `textDocument/didChange` (content-hash-detected) instead of silently
  serving the first `didOpen`'s snapshot. Originally accepted on the
  assumption that a client's lifetime was one query (seconds); that broke
  the same day `ProcessCache` landed, so the fix closes it properly instead
  of re-scoping the old acceptance to a longer window. See
  `src/lsp/client.rs::ensure_open` and
  `docs/superpowers/specs/2026-09-15-evidence-coordinator-refactor-design.md`.
```

- [ ] **Step 7: Add a historical-snapshot header to `docs/canonical-baseline.md`**

Insert above the existing `# Canonical baseline` heading:
```
> **Historical snapshot, not current.** Captured 2026-08-27 at `d1076f5`,
> before git-aware context, LSP enrichment, and remote embedding providers
> existed. `src/retrieval.rs` has had 15+ commits since; the committed
> fixture gate (`oxide eval --config fixtures/benchmark.json`, see
> README.md's Evidence section) has been re-validated multiple times since
> and stays current — this file's ContextBench Tier-A real-repo numbers
> have not been rerun against current `main` and should not be read as such.
```

- [ ] **Step 8: Commit**

```bash
git add AGENTS.md README.md docs/git-aware-context/README.md docs/lsp-enrichment-eval/README.md docs/canonical-baseline.md docs/evidence-coordinator-refactor/README.md
git commit -m "docs: fix language count, document --git/--lsp/remote providers, close stale LSP lifetime claim"
```

---

### Task 15: Performance evaluation (after), final verification, Codex review, final report

**Files:**
- Modify: `docs/evidence-coordinator-refactor/README.md`, `docs/evidence-coordinator-refactor/after.json`

- [ ] **Step 1: Repeat Task 4's exact capture matrix at the final commit**

```bash
cargo build --release -j 2
for cond in base git lsp git_lsp; do
  flags=""
  [ "$cond" = "git" ] && flags="--git"
  [ "$cond" = "lsp" ] && flags="--lsp"
  [ "$cond" = "git_lsp" ] && flags="--git --lsp"
  ./target/release/oxide query "where is retry logic" --json $flags --path fixtures/py_repo \
    > "docs/evidence-coordinator-refactor/after_${cond}.json"
  /usr/bin/time -v ./target/release/oxide query "where is retry logic" $flags --path fixtures/py_repo \
    2> "docs/evidence-coordinator-refactor/after_${cond}_time.txt" >/dev/null
done
```
Also sample live `ty`-process count during the `lsp`/`git_lsp` runs, and diff `before_*.json`/`after_*.json` per condition for output parity.

- [ ] **Step 2: Write the comparison into `docs/evidence-coordinator-refactor/README.md`**

Fill in real measured numbers (wall time, RSS, process count, output-parity result) per condition. State plainly whether `new ≈ max(git_wait, lsp_wait) + overhead` held — only claim it if the `git_lsp` condition's measured wall time is actually closer to `max(git, lsp)` than to `git + lsp`.

- [ ] **Step 3: Run the full verification suite one final time, in order**

`cargo fmt --check`, `cargo clippy -j 2 --all-targets -- -D warnings`, `cargo test -j 2`, `cargo build --release -j 2`, `./target/release/oxide eval --config fixtures/benchmark.json`.
Expected: all clean, benchmark gate unchanged.

- [ ] **Step 4: Commit the performance report**

```bash
git add docs/evidence-coordinator-refactor/
git commit -m "bench: post-refactor evidence-collection performance comparison"
```

- [ ] **Step 5: Run Codex review against the full diff since Task 4's pre-refactor baseline commit**

Focus list (from the original request): runtime leaks, nested Tokio runtimes, blocking calls on executor threads, nondeterministic candidate merge, cancellation/process leaks, stale LSP documents, Git error swallowing, SQLite/thread-safety assumptions, altered scoring from completion order, `ProcessCache` lifetime, shutdown behavior. Address any real finding with a follow-up commit before the final report; do not tag a release.

- [ ] **Step 6: Write the final report** — architecture before/after, audit findings fixed, behavior intentionally unchanged, performance before/after, CI coverage, remaining known limitations, commit list, freeze/hold recommendation — as the closing message of this implementation, not a new file.
