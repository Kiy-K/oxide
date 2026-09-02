//! Real filesystem-event path for `oxide watch`: exercises actual `notify`
//! events end to end, rather than the synthesized-path `process_batch` seam
//! `src/watcher.rs`'s own unit tests use. Convergence is checked with a
//! bounded poll-until loop (never a fixed sleep-and-hope), so this stays
//! deterministic rather than flaky under load.

use oxide::embeddings::HashedEmbedder;
use oxide::index::{content_stale_embedding_count, update_index, IndexBackend, SqliteStore};
use oxide::symbols::content_hash;
use oxide::watcher::{run, WatchLock};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};

fn write(path: &Path, content: &str) {
    std::fs::create_dir_all(path.parent().unwrap()).unwrap();
    std::fs::write(path, content).unwrap();
}

fn db_path(root: &Path) -> PathBuf {
    root.join(".oxide").join("index.db")
}

/// Repeatedly re-touch the file being waited on (idempotent) while polling,
/// so a slow-to-register OS watch (registration itself is fast, but not
/// instantaneous) can't cause a one-shot edit to be missed and the test to
/// spuriously fail. Real fs-watching production code needs no such retry —
/// the watcher is always registered well before a human's next keystroke —
/// this only compensates for a test triggering the edit as fast as possible
/// right after spawning the watcher thread.
fn poll_until_converged(
    edit_path: &Path,
    new_content: &str,
    check: impl Fn() -> bool,
    timeout: Duration,
) -> bool {
    let deadline = Instant::now() + timeout;
    while Instant::now() < deadline {
        if check() {
            return true;
        }
        write(edit_path, new_content);
        std::thread::sleep(Duration::from_millis(200));
    }
    check()
}

#[test]
fn real_filesystem_edit_is_detected_and_converges() {
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path().canonicalize().unwrap();
    write(&root.join("src/a.py"), "def a():\n    return 1\n");
    let emb = HashedEmbedder::default();
    {
        let mut store = SqliteStore::open(&db_path(&root)).unwrap();
        update_index(&root, &mut store, &emb).unwrap();
    }

    let lock = WatchLock::acquire(&root).unwrap();
    let stop = Arc::new(AtomicBool::new(false));
    let stop_for_thread = stop.clone();
    let root_for_thread = root.clone();
    let handle = std::thread::spawn(move || {
        let mut store = SqliteStore::open(&db_path(&root_for_thread)).unwrap();
        run(
            &root_for_thread,
            &mut store,
            &emb,
            &lock,
            &stop_for_thread,
            |_| {},
        )
    });

    let new_src = "def a():\n    return 999\n";
    let converged = poll_until_converged(
        &root.join("src/a.py"),
        new_src,
        || {
            let check_store = SqliteStore::open(&db_path(&root)).unwrap();
            check_store.file_hashes().unwrap().get("src/a.py").copied()
                == Some(content_hash(new_src))
                && content_stale_embedding_count(&check_store).unwrap() == 0
        },
        Duration::from_secs(15),
    );
    assert!(converged, "real fs edit was not picked up by the watcher");

    stop.store(true, Ordering::Relaxed);
    handle.join().unwrap().unwrap();
}

#[test]
fn shutdown_then_restart_both_converge() {
    // First watcher session: edit, converge, then stop it cleanly.
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path().canonicalize().unwrap();
    write(&root.join("src/a.py"), "def a():\n    return 1\n");
    let emb = HashedEmbedder::default();
    {
        let mut store = SqliteStore::open(&db_path(&root)).unwrap();
        update_index(&root, &mut store, &emb).unwrap();
    }

    {
        let lock = WatchLock::acquire(&root).unwrap();
        let stop = Arc::new(AtomicBool::new(false));
        let stop_for_thread = stop.clone();
        let root_for_thread = root.clone();
        let emb1 = HashedEmbedder::default();
        let handle = std::thread::spawn(move || {
            let mut store = SqliteStore::open(&db_path(&root_for_thread)).unwrap();
            run(
                &root_for_thread,
                &mut store,
                &emb1,
                &lock,
                &stop_for_thread,
                |_| {},
            )
        });

        let first_edit = "def a():\n    return 2\n";
        let converged = poll_until_converged(
            &root.join("src/a.py"),
            first_edit,
            || {
                let s = SqliteStore::open(&db_path(&root)).unwrap();
                s.file_hashes().unwrap().get("src/a.py").copied() == Some(content_hash(first_edit))
            },
            Duration::from_secs(15),
        );
        assert!(converged, "first watcher session did not converge");

        stop.store(true, Ordering::Relaxed);
        handle.join().unwrap().unwrap();
        // `run` returning drops the lock at the end of this scope.
    }

    // Second, independent session against the same repo: a fresh lock
    // acquire must succeed (the first was released), and a further edit
    // made only while the second session is running must also converge —
    // proving the watcher's "shutdown, then restart" story holds without
    // relying on any state carried over in-process.
    let lock2 = WatchLock::acquire(&root).unwrap();
    let stop2 = Arc::new(AtomicBool::new(false));
    let stop2_for_thread = stop2.clone();
    let root_for_thread = root.clone();
    let emb2 = HashedEmbedder::default();
    let handle2 = std::thread::spawn(move || {
        let mut store = SqliteStore::open(&db_path(&root_for_thread)).unwrap();
        run(
            &root_for_thread,
            &mut store,
            &emb2,
            &lock2,
            &stop2_for_thread,
            |_| {},
        )
    });

    let second_edit = "def a():\n    return 3\n";
    let converged = poll_until_converged(
        &root.join("src/a.py"),
        second_edit,
        || {
            let s = SqliteStore::open(&db_path(&root)).unwrap();
            s.file_hashes().unwrap().get("src/a.py").copied() == Some(content_hash(second_edit))
        },
        Duration::from_secs(15),
    );
    assert!(converged, "restarted watcher session did not converge");

    stop2.store(true, Ordering::Relaxed);
    handle2.join().unwrap().unwrap();
}

#[test]
fn oxide_internal_writes_do_not_schedule_watcher_batches() {
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path().canonicalize().unwrap();
    write(&root.join("src/a.py"), "def a():\n    return 1\n");
    let emb = HashedEmbedder::default();
    {
        let mut store = SqliteStore::open(&db_path(&root)).unwrap();
        update_index(&root, &mut store, &emb).unwrap();
    }

    let lock = WatchLock::acquire(&root).unwrap();
    let stop = Arc::new(AtomicBool::new(false));
    let stop_for_thread = stop.clone();
    let root_for_thread = root.clone();
    let batches = Arc::new(std::sync::atomic::AtomicUsize::new(0));
    let batches_for_thread = batches.clone();
    let handle = std::thread::spawn(move || {
        let mut store = SqliteStore::open(&db_path(&root_for_thread)).unwrap();
        run(
            &root_for_thread,
            &mut store,
            &emb,
            &lock,
            &stop_for_thread,
            |_| {
                batches_for_thread.fetch_add(1, Ordering::Relaxed);
            },
        )
    });

    let deadline = Instant::now() + Duration::from_secs(5);
    while batches.load(Ordering::Relaxed) == 0 && Instant::now() < deadline {
        std::thread::sleep(Duration::from_millis(25));
    }
    assert_eq!(
        batches.load(Ordering::Relaxed),
        1,
        "initial reconciliation runs once"
    );
    write(&root.join(".oxide/watcher-noise"), "internal write\n");
    std::thread::sleep(Duration::from_millis(2 * 400));
    assert_eq!(
        batches.load(Ordering::Relaxed),
        1,
        "index/lock-directory writes must not re-enter the batch loop"
    );

    stop.store(true, Ordering::Relaxed);
    handle.join().unwrap().unwrap();
}

#[test]
fn watching_a_nonexistent_path_fails_visibly() {
    // Watcher init failure must surface as a real Err, not a silent no-op —
    // the CLI maps this straight to a non-zero exit with the message shown.
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path().join("does_not_exist");
    let mut store = SqliteStore::open(Path::new(":memory:")).unwrap();
    let emb = HashedEmbedder::default();
    let lock_dir = tmp.path().join("elsewhere");
    std::fs::create_dir_all(&lock_dir).unwrap();
    let lock = WatchLock::acquire(&lock_dir).unwrap();
    let stop = AtomicBool::new(false);
    let result = run(&root, &mut store, &emb, &lock, &stop, |_| {});
    assert!(
        result.is_err(),
        "watching a nonexistent path must fail, not silently do nothing"
    );
}
