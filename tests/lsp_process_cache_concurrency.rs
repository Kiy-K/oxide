//! Regression test for hardening-pass item #2: concurrent same-root
//! LSP-backed `RepositoryService::context()` calls must not race into
//! spawning redundant sessions, corrupt state, or deadlock.
//!
//! Skipped, not failed, when `ty` isn't installed.

use oxide::embeddings::HashedEmbedder;
use oxide::index::update_index;
use oxide::retrieval::RetrievalMode;
use oxide::service::RepositoryService;
use oxide::storage::SqliteStore;
use std::path::Path;
use std::sync::mpsc;
use std::time::{Duration, Instant};

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

/// SAFETY: the only env write in this test binary, before any other code
/// in this process reads it.
fn pin_offline_embedder() {
    unsafe { std::env::set_var("OXIDE_EMBED_NATIVE", "hashed") };
}

#[test]
fn concurrent_same_root_lsp_calls_serialize_instead_of_duplicating_the_session() {
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
    let root_str = root.to_str().unwrap().to_string();

    // Cold baseline: one call, no contention, establishes the session and
    // measures the real spawn+initialize cost against this fixture/machine.
    let service = RepositoryService::discover(Some(&root_str))
        .unwrap()
        .with_process_cache();
    let t_cold = Instant::now();
    service
        .context("run", 2048, RetrievalMode::default(), false, false, true)
        .expect("baseline call must succeed");
    let cold_ms = t_cold.elapsed().as_secs_f64() * 1e3;

    // N concurrent calls for the SAME root. If each raced into spawning
    // its own session instead of serializing on the held per-root guard
    // (the bug this item fixes), total wall time would scale toward
    // N * cold_ms (N independent cold spawns, contending for the same
    // spawn_blocking pool besides). Run on a watchdog-bounded background
    // thread so a real deadlock fails this test explicitly instead of
    // hanging the whole suite.
    const N: usize = 6;
    let (tx, rx) = mpsc::channel();
    std::thread::spawn(move || {
        let outcome = std::thread::scope(|scope| {
            let handles: Vec<_> = (0..N)
                .map(|_| {
                    let root_str = root_str.clone();
                    scope.spawn(move || {
                        let service = RepositoryService::discover(Some(&root_str))
                            .unwrap()
                            .with_process_cache();
                        service.context("run", 2048, RetrievalMode::default(), false, false, true)
                    })
                })
                .collect();
            handles
                .into_iter()
                .map(|h| h.join().expect("worker thread must not panic"))
                .collect::<Vec<_>>()
        });
        let _ = tx.send(outcome);
    });

    let t_concurrent = Instant::now();
    let results = rx
        .recv_timeout(Duration::from_secs(30))
        .expect("N concurrent same-root --lsp calls must complete within 30s (no deadlock)");
    let concurrent_ms = t_concurrent.elapsed().as_secs_f64() * 1e3;

    // No corruption / deterministic usable result: every concurrent call
    // must succeed and carry real lsp-* evidence, not a degraded/partial
    // result from a session two threads stepped on each other inside.
    for (i, r) in results.iter().enumerate() {
        let pack = r
            .as_ref()
            .unwrap_or_else(|e| panic!("concurrent call {i} failed: {e}"));
        let has_lsp = pack
            .items
            .iter()
            .any(|item| item.evidence.reasons.iter().any(|r| r.starts_with("lsp-")));
        assert!(
            has_lsp,
            "concurrent call {i} must carry real lsp-* evidence: {pack:?}"
        );
    }

    // No duplicate persistent session: N serialized reuses should cost
    // nowhere near N independent cold spawns. A generous threshold —
    // half of N cold-spawns — comfortably separates "serialized reuse"
    // from "each thread spawned its own session".
    let duplicate_spawn_threshold_ms = cold_ms * (N as f64) * 0.5;
    eprintln!(
        "cold_ms={cold_ms:.1} concurrent_total_ms={concurrent_ms:.1} \
         threshold={duplicate_spawn_threshold_ms:.1} (N={N})"
    );
    assert!(
        concurrent_ms < duplicate_spawn_threshold_ms,
        "N={N} concurrent same-root calls took {concurrent_ms:.1}ms, too close to \
         N * cold-spawn ({:.1}ms) — looks like duplicate sessions were spawned instead of \
         one session being serialized/reused",
        cold_ms * N as f64
    );
}
