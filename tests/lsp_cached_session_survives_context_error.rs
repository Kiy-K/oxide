//! Regression test for hardening-pass item #3: a failed `context()` call
//! must not strand or corrupt a cached, reusable LSP session.
//!
//! In its own file/process — not folded into
//! `lsp_mcp_process_cache_reuse.rs` — because this test drives
//! `OXIDE_CONTEXT_FORCE_ERROR`, a process-global env var. Tests in the same
//! binary run concurrently on separate threads by default; a shared
//! process-wide var would risk making a *different*, concurrently-running
//! `--lsp` test spuriously fail. Separate test files are separate
//! processes, so this has no such risk.
//!
//! Skipped, not failed, when `ty` isn't installed.

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

/// SAFETY: this is the only place in this single-threaded-w.r.t.-env-vars
/// test binary that touches process env, and it's set exactly once before
/// any other code in this process reads it.
fn pin_offline_embedder() {
    unsafe { std::env::set_var("OXIDE_EMBED_NATIVE", "hashed") };
}

#[test]
fn cached_session_survives_a_forced_context_building_error() {
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

    // 1. Establish a cached, healthy session. Timed: this is the cold-spawn
    // baseline the recovery check below compares against.
    let t0 = Instant::now();
    let first = service
        .context("run", 2048, RetrievalMode::default(), false, false, true)
        .expect("first --lsp call must establish a session");
    let cold_ms = t0.elapsed().as_secs_f64() * 1e3;
    let first_has_lsp = first
        .items
        .iter()
        .any(|i| i.evidence.reasons.iter().any(|r| r.starts_with("lsp-")));
    assert!(
        first_has_lsp,
        "first call must surface real lsp-* evidence: {first:?}"
    );

    // 2. Force build_context_with to fail while this call holds the
    // cached client (see context.rs's OXIDE_CONTEXT_FORCE_ERROR hook).
    // SAFETY: no other thread in this test binary touches process env.
    unsafe { std::env::set_var("OXIDE_CONTEXT_FORCE_ERROR", "1") };
    let forced_err = service.context("run", 2048, RetrievalMode::default(), false, false, true);
    unsafe { std::env::remove_var("OXIDE_CONTEXT_FORCE_ERROR") };
    assert!(
        forced_err.is_err(),
        "the forced-error call must actually fail, or this test proves nothing"
    );

    // 3. The next valid request must succeed AND reuse the same warm
    // session rather than silently respawning. Correctness alone
    // (is_ok() + lsp-* evidence present) doesn't distinguish reuse from
    // respawn — ty always successfully respawns, so a version of this
    // test without the timing check would pass against the pre-fix bug
    // too. Timing is the established signal this suite already uses
    // (lsp_mcp_process_cache_reuse.rs's own speed-comparison test) for
    // exactly this reason.
    let t1 = Instant::now();
    let recovered = service
        .context("run", 2048, RetrievalMode::default(), false, false, true)
        .expect("cache must still be healthy and usable after the forced error");
    let recovered_ms = t1.elapsed().as_secs_f64() * 1e3;
    eprintln!("cold_ms={cold_ms:.1} recovered_ms={recovered_ms:.1}");
    let recovered_has_lsp = recovered
        .items
        .iter()
        .any(|i| i.evidence.reasons.iter().any(|r| r.starts_with("lsp-")));
    assert!(
        recovered_has_lsp,
        "post-error call must still surface real lsp-* evidence, proving the cache slot \
         wasn't left empty or holding a broken client: {recovered:?}"
    );
    assert!(
        recovered_ms < cold_ms,
        "post-error call must reuse the cached session (fast), not silently respawn (as slow \
         as the first cold spawn): cold={cold_ms:.1}ms recovered={recovered_ms:.1}ms"
    );
}
