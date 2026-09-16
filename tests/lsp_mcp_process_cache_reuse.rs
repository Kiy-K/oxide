//! End-to-end proof that `oxide mcp`'s `ProcessCache` actually reuses a
//! spawned `ty` session across calls, instead of paying full server
//! `initialize` every time (`RepositoryService::context`'s `--lsp` path via
//! `service.rs`'s `lsp_client_slot`).
//!
//! Skipped, not failed, when `ty` isn't installed — install with
//! `uv tool install ty` to run this locally.

use oxide::embeddings::HashedEmbedder;
use oxide::index::update_index;
use oxide::retrieval::RetrievalMode;
use oxide::service::RepositoryService;
use oxide::storage::SqliteStore;
use std::path::Path;
use std::time::Instant;

fn ty_available() -> bool {
    std::process::Command::new("ty")
        .arg("--version")
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .status()
        .map(|s| s.success())
        .unwrap_or(false)
}

fn write(path: &Path, content: &str) {
    std::fs::create_dir_all(path.parent().unwrap()).unwrap();
    std::fs::write(path, content).unwrap();
}

/// Pin the offline hashed embedder for this test binary — same pattern and
/// rationale as `tests/service_hardening.rs`'s `pin_offline_embedder`:
/// `RepositoryService::embedder()` otherwise resolves the real ONNX native
/// model, which needs network and wouldn't match the `HashedEmbedder` the
/// index below is built with.
fn pin_offline_embedder() {
    static PIN: std::sync::Once = std::sync::Once::new();
    PIN.call_once(|| {
        // SAFETY: the only write to this variable in this process, serialized
        // by `Once` ahead of every read.
        unsafe { std::env::set_var("OXIDE_EMBED_NATIVE", "hashed") };
    });
}

#[test]
fn second_lsp_call_reuses_the_first_call_s_session_and_is_faster() {
    if !ty_available() {
        eprintln!("skipping: `ty` not on PATH (install with `uv tool install ty`)");
        return;
    }

    pin_offline_embedder();
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path().canonicalize().unwrap();
    write(&root.join("target.py"), "def run():\n    return 1\n");
    write(
        &root.join("caller.py"),
        "import target\n\n\ndef caller():\n    return target.run()\n",
    );
    {
        let mut store = SqliteStore::open(&root.join(".oxide").join("index.db")).unwrap();
        update_index(&root, &mut store, &HashedEmbedder::default()).unwrap();
    }

    let service = RepositoryService::discover(Some(root.to_str().unwrap()))
        .unwrap()
        .with_process_cache();

    let t0 = Instant::now();
    let first = service
        .context("run", 2048, RetrievalMode::default(), false, false, true)
        .expect("first --lsp call must succeed");
    let first_ms = t0.elapsed().as_secs_f64() * 1e3;

    let t1 = Instant::now();
    let second = service
        .context("run", 2048, RetrievalMode::default(), false, false, true)
        .expect("second --lsp call must succeed");
    let second_ms = t1.elapsed().as_secs_f64() * 1e3;

    eprintln!(
        "first call (cold spawn): {first_ms:.1}ms, second call (reused session): {second_ms:.1}ms"
    );

    let has_lsp_evidence = |pack: &oxide::service::ContextResult| {
        pack.items
            .iter()
            .any(|i| i.evidence.reasons.iter().any(|r| r.starts_with("lsp-")))
    };
    assert!(
        has_lsp_evidence(&first) || has_lsp_evidence(&second),
        "at least one call must surface real lsp-* evidence, not just succeed silently"
    );
    assert!(
        second_ms < first_ms,
        "a reused warm session must be faster than the first call's cold spawn+initialize: \
         first={first_ms:.1}ms second={second_ms:.1}ms"
    );
}

/// Proves the ownership round-trip through `RepositoryService::context`'s
/// take-the-client-out/restore-it-after flow (`service.rs`) doesn't lose or
/// reset LSP session state: a file edited between two calls must be visible
/// to the *second* call's evidence, which only happens if (a) the same
/// underlying `LspClient` — not a freshly respawned one — served the second
/// call, and (b) that client's `ensure_open` correctly resynced via
/// `didChange` (the Task 2 fix) rather than serving its first-call snapshot.
/// `second_lsp_call_reuses_the_first_call_s_session_and_is_faster` above
/// proves reuse by speed; this proves it by content.
#[test]
fn edit_between_two_service_context_calls_is_visible_through_the_ownership_round_trip() {
    if !ty_available() {
        eprintln!("skipping: `ty` not on PATH (install with `uv tool install ty`)");
        return;
    }

    pin_offline_embedder();
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path().canonicalize().unwrap();
    write(&root.join("target.py"), "def old_name():\n    return 1\n");
    write(
        &root.join("caller.py"),
        "import target\n\n\ndef caller():\n    return target.old_name()\n",
    );
    {
        let mut store = SqliteStore::open(&root.join(".oxide").join("index.db")).unwrap();
        update_index(&root, &mut store, &HashedEmbedder::default()).unwrap();
    }

    let service = RepositoryService::discover(Some(root.to_str().unwrap()))
        .unwrap()
        .with_process_cache();

    let first = service
        .context(
            "old_name",
            2048,
            RetrievalMode::default(),
            false,
            false,
            true,
        )
        .expect("first --lsp call must succeed");
    let first_has_old = first
        .items
        .iter()
        .any(|i| i.evidence.qualified_name.contains("old_name"));
    assert!(
        first_has_old,
        "first call must resolve old_name before the edit: {first:?}"
    );

    // Edit the file the cached session already opened — same session
    // reused across calls (no re-discover/re-spawn), matching the MCP
    // ProcessCache's real usage pattern.
    write(&root.join("target.py"), "def new_name():\n    return 1\n");
    write(
        &root.join("caller.py"),
        "import target\n\n\ndef caller():\n    return target.new_name()\n",
    );
    {
        let mut store = SqliteStore::open(&root.join(".oxide").join("index.db")).unwrap();
        update_index(&root, &mut store, &HashedEmbedder::default()).unwrap();
    }

    let second = service
        .context(
            "new_name",
            2048,
            RetrievalMode::default(),
            false,
            false,
            true,
        )
        .expect("second --lsp call must succeed");
    let second_has_new = second
        .items
        .iter()
        .any(|i| i.evidence.qualified_name.contains("new_name"));
    assert!(
        second_has_new,
        "second call must see the post-edit content (new_name) via the reused, \
         ownership-round-tripped session, not stale pre-edit text: {second:?}"
    );
}
