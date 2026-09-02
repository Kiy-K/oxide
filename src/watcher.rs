//! `oxide watch`: keep an index fresh via native filesystem events instead
//! of re-running `oxide index` by hand. See
//! `docs/auto-indexing-watcher-constraints/README.md` for the design this
//! implements and the seams it opens in `src/index.rs`.
//!
//! Architecture: a raw `notify::RecommendedWatcher` callback filters out
//! `EventKind::Access` (opens/closes/reads — see below) and drops surviving
//! paths into a shared `Mutex<HashSet<PathBuf>>`, signaling a plain `mpsc`
//! channel. A single worker loop (`run`) waits on that channel; on the first
//! signal after idle it sleeps one [`DEBOUNCE`] window (a flat settle delay,
//! not a per-path timer — simpler, and sufficient for the burst shapes this
//! needs to coalesce: a single editor save, an atomic-rename save, a
//! multi-file refactor written in one pass) before draining *everything*
//! currently pending into one `HashSet<PathBuf>` and processing it — so a
//! batch that arrives while a previous (possibly slow, embeddings-bound)
//! pass is still running is never lost and never processed one settle-window
//! at a time.
//!
//! This does not use `notify-debouncer-mini` even though it exists for
//! exactly this purpose: that crate discards each raw event's `EventKind`
//! before a caller ever sees it, and its internals are private, so there is
//! no way to filter `EventKind::Access` through it. That filter is not
//! optional here — `notify`'s Linux backend unconditionally includes
//! `WatchMask::OPEN` in its inotify mask (no `notify::Config` knob disables
//! it), so merely *reading* a file — which `update_base_for_files` does for
//! every candidate on every batch — raises a fresh Access event on that
//! exact path. Left unfiltered, the watcher would perpetually reprocess its
//! own reads: a livelock, not just wasted work, discovered by measuring idle
//! CPU after a real edit rather than only before one.
//!
//! Each batch runs through [`process_batch`], which is deliberately decoupled
//! from `notify` so it can be unit-tested with a synthesized path set: filter
//! through an [`IgnoreCache`], then reuse the existing scoped base-update
//! (`update_base_for_files`) and embedding (`update_embeddings`) primitives —
//! the same convergence guarantee those functions already carry over from
//! `update_base`/`update_index` extends to the watcher for free.

use crate::embeddings::EmbeddingProvider;
use crate::index::{
    update_base, update_base_for_files, update_embeddings, update_index, IndexBackend,
    IndexOptions, IndexReport,
};
use crate::scanner;
use anyhow::{Context, Result};
use notify::{Event, EventKind, RecommendedWatcher, RecursiveMode, Watcher};
use std::collections::HashSet;
use std::fs::OpenOptions;
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::sync::mpsc::RecvTimeoutError;
use std::sync::{Arc, Mutex};
use std::time::Duration;

/// Flat settle delay after the first post-idle event before a batch is
/// drained and processed. Coalesces the several raw events one editor save
/// or atomic-rename save produces into a single update pass.
pub const DEBOUNCE: Duration = Duration::from_millis(400);

/// `.oxide/watch.pid` heartbeat interval: how often an idle watcher (no fs
/// events at all) refreshes the lock file's mtime.
pub const LOCK_HEARTBEAT: Duration = Duration::from_secs(5);

/// How often the main loop wakes on its own (no fs event) to check the
/// `stop` flag and whether a heartbeat is due. Short enough that `run`
/// reacts to `stop` quickly (tests don't wait multiple seconds to shut a
/// watcher down); still just a timed park-and-wake with no work on most
/// wakeups, so it doesn't register as meaningful idle CPU.
const IDLE_POLL: Duration = Duration::from_millis(250);

/// A lock file untouched for this long is presumed to belong to a dead
/// process (crash, `kill -9`), not a live watcher, and may be reclaimed.
/// Kept at 4x the heartbeat so a watcher merely busy on one slow update pass
/// is never mistaken for dead.
pub const LOCK_STALE_AFTER: Duration = Duration::from_secs(20);

fn changes_ignore_rules(root: &Path, path: &Path) -> bool {
    let Ok(rel) = path.strip_prefix(root) else {
        return false;
    };
    matches!(
        rel.file_name().and_then(|n| n.to_str()),
        Some(".gitignore") | Some(".ignore")
    ) || rel == Path::new(".git/info/exclude")
}

fn is_oxide_internal_path(root: &Path, path: &Path) -> bool {
    path.starts_with(root.join(".oxide"))
}

/// Caches `scan_repo`'s indexable-file set so a debounced batch can be
/// filtered against it in O(1) per path instead of re-deriving gitignore
/// semantics per event (seam #1 in the constraints doc).
pub struct IgnoreCache {
    indexable: HashSet<String>,
}

impl IgnoreCache {
    pub fn build(root: &Path) -> Result<Self> {
        Ok(Self {
            indexable: scan_set(root)?,
        })
    }

    /// Decide whether `path` (must be under `root`) is worth passing to a
    /// base-update pass, returning its repo-relative slash path if so.
    ///
    /// Three outcomes, cheapest first:
    /// - Already a known-indexable path: always a candidate, no rescan.
    /// - Not shaped like a source file OXIDE indexes at all — unrecognized
    ///   extension, or under a denylisted/hidden ancestor directory
    ///   (`target/`, `node_modules/`, `.venv/`, ...): never a candidate, no
    ///   rescan. This is what stops a build process writing continuously
    ///   into an ignored tree from ever triggering a `scan_repo` rescan —
    ///   the entire point of caching the indexable set instead of asking
    ///   `ignore` about every event.
    /// - Extension-shaped but unknown to the cache (a plausible new source
    ///   file, or the cache is stale after a `.gitignore`/`.ignore` edit):
    ///   refresh once and recheck. If it's still not indexable after a
    ///   fresh scan, it's either a live ignore-rule exclusion (must never be
    ///   indexed, so rejected) or a path that no longer exists on disk —
    ///   whether that's a real deletion of a previously-tracked file is
    ///   `update_base_for_files`'s own NotFound+tracked check to make, so
    ///   it is passed through rather than decided here.
    pub fn candidate(&mut self, root: &Path, path: &Path) -> Result<Option<String>> {
        let Ok(rel) = path.strip_prefix(root) else {
            return Ok(None);
        };
        if rel.as_os_str().is_empty() {
            return Ok(None);
        }
        let rel_str = rel.display().to_string();
        if self.indexable.contains(&rel_str) {
            return Ok(Some(rel_str));
        }
        if !changes_ignore_rules(root, path)
            && (scanner::language_for_path(rel).is_none() || scanner::has_denied_ancestor(rel))
        {
            return Ok(None);
        }
        self.refresh(root)?;
        if self.indexable.contains(&rel_str) {
            return Ok(Some(rel_str));
        }
        // Still not indexable after a fresh scan: only a candidate if the
        // path is gone (a possible deletion of a previously-tracked file —
        // update_base_for_files decides for real). If it still exists, a
        // current ignore rule excludes it and it must never be indexed.
        Ok(if !path.exists() { Some(rel_str) } else { None })
    }

    fn refresh(&mut self, root: &Path) -> Result<()> {
        self.indexable = scan_set(root)?;
        Ok(())
    }
}

fn scan_set(root: &Path) -> Result<HashSet<String>> {
    Ok(scanner::scan_repo(root)?
        .into_iter()
        .map(|p| p.display().to_string())
        .collect())
}

/// One full update pass over a batch of changed absolute paths: filter
/// through the ignore cache, then run the scoped base stage followed by
/// embeddings. This is the single code path both the real fs-event loop
/// (`run`) and tests use — "does the watcher converge to manual indexing" is
/// a property of this one function, exercisable without any real filesystem
/// events.
pub fn process_batch(
    root: &Path,
    store: &mut dyn IndexBackend,
    embedder: &dyn EmbeddingProvider,
    ignore_cache: &mut IgnoreCache,
    changed: &HashSet<PathBuf>,
) -> Result<IndexReport> {
    // An ignore-rule edit can both remove already-indexed files and reveal
    // files that were previously ignored. A scoped update cannot discover
    // either set from the rule file's path alone, so reconcile once here.
    if changed.iter().any(|path| changes_ignore_rules(root, path)) {
        let mut report = update_base(root, store, &IndexOptions::default())?;
        update_embeddings(root, store, embedder, &IndexOptions::default(), &mut report)?;
        ignore_cache.refresh(root)?;
        return Ok(report);
    }
    let mut rel_paths = Vec::with_capacity(changed.len());
    for path in changed {
        if let Some(rel) = ignore_cache.candidate(root, path)? {
            rel_paths.push(rel);
        }
    }
    if rel_paths.is_empty() {
        return Ok(IndexReport::default());
    }
    let mut report = update_base_for_files(root, store, &IndexOptions::default(), &rel_paths)?;
    update_embeddings(root, store, embedder, &IndexOptions::default(), &mut report)?;
    Ok(report)
}

/// Advisory single-instance lock for `oxide watch`, held at
/// `<root>/.oxide/watch.pid`. Not a substitute for SQLite's own WAL-based
/// concurrent-writer safety (already relied on elsewhere in this codebase
/// for `index.db` itself); its only job is to stop two `oxide watch`
/// processes from racing each other's debounce/coalesce state for the same
/// repository. A stale lock (untouched for `LOCK_STALE_AFTER`) is reclaimed
/// by mtime rather than checked for PID liveness, so this needs no extra
/// dependency and still recovers automatically after a crash. Manual
/// `oxide index` never consults this lock — it must remain a valid recovery
/// path regardless of watcher state.
pub struct WatchLock {
    path: PathBuf,
    token: String,
    heartbeat_stop: std::sync::mpsc::Sender<()>,
    heartbeat_thread: Option<std::thread::JoinHandle<()>>,
}

impl WatchLock {
    pub fn acquire(root: &Path) -> Result<Self> {
        let dir = root.join(".oxide");
        std::fs::create_dir_all(&dir)
            .with_context(|| format!("cannot create {}", dir.display()))?;
        let path = dir.join("watch.pid");
        let token = format!(
            "{}:{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap_or_default()
                .as_nanos()
        );
        loop {
            match OpenOptions::new().write(true).create_new(true).open(&path) {
                Ok(mut file) => {
                    file.write_all(token.as_bytes())
                        .with_context(|| format!("cannot write {}", path.display()))?;
                    let (heartbeat_stop, heartbeat_rx) = std::sync::mpsc::channel();
                    let heartbeat_path = path.clone();
                    let heartbeat_token = token.clone();
                    let heartbeat_thread = std::thread::spawn(move || loop {
                        match heartbeat_rx.recv_timeout(LOCK_HEARTBEAT) {
                            Ok(()) | Err(std::sync::mpsc::RecvTimeoutError::Disconnected) => break,
                            Err(std::sync::mpsc::RecvTimeoutError::Timeout) => {
                                if refresh_lock(&heartbeat_path, &heartbeat_token).is_err() {
                                    break;
                                }
                            }
                        }
                    });
                    return Ok(Self {
                        path,
                        token,
                        heartbeat_stop,
                        heartbeat_thread: Some(heartbeat_thread),
                    });
                }
                Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => {
                    let meta = match std::fs::metadata(&path) {
                        Ok(meta) => meta,
                        Err(e) if e.kind() == std::io::ErrorKind::NotFound => continue,
                        Err(e) => {
                            return Err(e)
                                .with_context(|| format!("cannot inspect {}", path.display()))
                        }
                    };
                    let age = meta
                        .modified()
                        .ok()
                        .and_then(|m| m.elapsed().ok())
                        .unwrap_or(Duration::ZERO);
                    if age < LOCK_STALE_AFTER {
                        let holder = std::fs::read_to_string(&path).unwrap_or_default();
                        anyhow::bail!(
                            "another `oxide watch` appears to be running for this repository (pid {}); \
                             if that process is gone, wait ~{}s for the lock to go stale, or remove {}",
                            holder.trim(),
                            LOCK_STALE_AFTER.as_secs(),
                            path.display()
                        );
                    }
                    if let Err(e) = std::fs::remove_file(&path) {
                        if e.kind() != std::io::ErrorKind::NotFound {
                            return Err(e)
                                .with_context(|| format!("cannot reclaim {}", path.display()));
                        }
                    }
                }
                Err(e) => {
                    return Err(e).with_context(|| format!("cannot create {}", path.display()))
                }
            }
        }
    }

    pub fn heartbeat(&self) -> Result<()> {
        refresh_lock(&self.path, &self.token)
    }
}

impl Drop for WatchLock {
    fn drop(&mut self) {
        let _ = self.heartbeat_stop.send(());
        if let Some(thread) = self.heartbeat_thread.take() {
            let _ = thread.join();
        }
        if std::fs::read_to_string(&self.path).ok().as_deref() == Some(self.token.as_str()) {
            let _ = std::fs::remove_file(&self.path);
        }
    }
}

fn refresh_lock(path: &Path, token: &str) -> Result<()> {
    let mut file = OpenOptions::new()
        .read(true)
        .write(true)
        .open(path)
        .with_context(|| format!("cannot refresh {}", path.display()))?;
    let mut holder = String::new();
    file.read_to_string(&mut holder)
        .with_context(|| format!("cannot read {}", path.display()))?;
    anyhow::ensure!(
        holder == token,
        "watch lock was replaced by another process: {}",
        path.display()
    );
    file.set_modified(std::time::SystemTime::now())
        .with_context(|| format!("cannot refresh {}", path.display()))
}

/// Run the auto-indexing watcher loop for `root` until the process is
/// killed or the filesystem watcher itself fails. It registers the native
/// watch before a normal full index pass, so startup changes are either in
/// that reconciliation or retained in the pending set for the next batch.
///
/// A filesystem-watcher error or channel disconnect is treated as fatal:
/// this returns `Err` rather than silently degrading, so the failure is
/// visible (non-zero exit, printed message) and the recovery path — rerun
/// `oxide watch`, or fall back to manual `oxide index` — is the user's to
/// take deliberately rather than something papered over here.
///
/// `stop` lets a caller (tests; an embedder of this function) end the loop
/// cleanly and get `Ok(())` back — the real CLI never sets it, since a
/// terminal `oxide watch` session is stopped by process signal (ctrl-C)
/// instead, which this loop doesn't need to know about.
pub fn run(
    root: &Path,
    store: &mut dyn IndexBackend,
    embedder: &dyn EmbeddingProvider,
    lock: &WatchLock,
    stop: &std::sync::atomic::AtomicBool,
    mut on_batch: impl FnMut(&IndexReport),
) -> Result<()> {
    use std::sync::atomic::Ordering;

    let pending: Arc<Mutex<HashSet<PathBuf>>> = Arc::new(Mutex::new(HashSet::new()));
    // One queued wakeup is enough: `pending` holds the actual work. Keeping
    // this bounded prevents a build burst from allocating one mpsc message
    // per raw event while the worker is embedding a previous batch.
    let (signal_tx, signal_rx) = std::sync::mpsc::sync_channel::<()>(1);
    let pending_for_cb = pending.clone();
    let watcher_error = Arc::new(Mutex::new(None));
    let watcher_error_for_cb = watcher_error.clone();
    let root_for_cb = root.to_path_buf();
    let mut watcher = RecommendedWatcher::new(
        move |res: notify::Result<Event>| match res {
            Ok(event) => {
                // Access(Open/Close/Read) is not a content change — it's
                // exactly what our own file reads generate while processing
                // a batch. See the module doc comment: unfiltered, this is a
                // livelock, not just wasted work.
                if matches!(event.kind, EventKind::Access(_)) {
                    return;
                }
                let paths: Vec<PathBuf> = event
                    .paths
                    .into_iter()
                    .filter(|path| !is_oxide_internal_path(&root_for_cb, path))
                    .collect();
                if !paths.is_empty() {
                    pending_for_cb.lock().unwrap().extend(paths);
                    let _ = signal_tx.try_send(());
                }
            }
            Err(e) => {
                *watcher_error_for_cb.lock().unwrap() = Some(e.to_string());
                let _ = signal_tx.try_send(());
            }
        },
        notify::Config::default(),
    )
    .context("failed to start filesystem watcher")?;
    watcher
        .watch(root, RecursiveMode::Recursive)
        .with_context(|| format!("failed to watch {}", root.display()))?;

    // Register before reconciling: an edit during startup is then either
    // included in this full pass or retained in `pending` for the next one.
    let initial = update_index(root, store, embedder)?;
    on_batch(&initial);
    let mut ignore_cache = IgnoreCache::build(root)?;

    let mut last_heartbeat = std::time::Instant::now();
    while !stop.load(Ordering::Relaxed) {
        match signal_rx.recv_timeout(IDLE_POLL) {
            Ok(()) => {
                if let Some(e) = watcher_error.lock().unwrap().take() {
                    anyhow::bail!("filesystem watcher reported an error: {e}");
                }
                // Settle window: let the rest of a burst (save + rename +
                // chmod, or several files touched by one refactor) land
                // before draining. A flat sleep rather than a rolling/
                // per-path deadline — simpler, and sufficient here since a
                // burst still finishing when this fires just produces
                // another (harmless, correctness-preserving) batch on the
                // next wakeup rather than one that misses nothing.
                std::thread::sleep(DEBOUNCE);
                while signal_rx.try_recv().is_ok() {}
                if let Some(e) = watcher_error.lock().unwrap().take() {
                    anyhow::bail!("filesystem watcher reported an error: {e}");
                }
                let changed: HashSet<PathBuf> = {
                    let mut set = pending.lock().unwrap();
                    std::mem::take(&mut *set)
                };
                if !changed.is_empty() {
                    let report = process_batch(root, store, embedder, &mut ignore_cache, &changed)?;
                    on_batch(&report);
                }
                lock.heartbeat()?;
                last_heartbeat = std::time::Instant::now();
            }
            Err(RecvTimeoutError::Timeout) => {
                if let Some(e) = watcher_error.lock().unwrap().take() {
                    anyhow::bail!("filesystem watcher reported an error: {e}");
                }
                if last_heartbeat.elapsed() >= LOCK_HEARTBEAT {
                    lock.heartbeat()?;
                    last_heartbeat = std::time::Instant::now();
                }
            }
            Err(RecvTimeoutError::Disconnected) => {
                anyhow::bail!("filesystem watcher stopped unexpectedly")
            }
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::embeddings::HashedEmbedder;
    use crate::index::{content_stale_embedding_count, update_index, IndexBackend, SqliteStore};

    fn write(path: &Path, content: &str) {
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(path, content).unwrap();
    }

    fn seeded_repo() -> (tempfile::TempDir, SqliteStore) {
        let tmp = tempfile::tempdir().unwrap();
        write(&tmp.path().join("src/a.py"), "def a():\n    return 1\n");
        write(&tmp.path().join("src/b.py"), "def b():\n    return 2\n");
        let mut store = SqliteStore::open(Path::new(":memory:")).unwrap();
        let emb = HashedEmbedder::default();
        update_index(tmp.path(), &mut store, &emb).unwrap();
        (tmp, store)
    }

    fn paths(tmp: &tempfile::TempDir, rels: &[&str]) -> HashSet<PathBuf> {
        rels.iter().map(|r| tmp.path().join(r)).collect()
    }

    #[test]
    fn edit_burst_collapses_to_one_up_to_date_symbol() {
        let (tmp, mut store) = seeded_repo();
        let emb = HashedEmbedder::default();
        let mut cache = IgnoreCache::build(tmp.path()).unwrap();
        // Simulate a burst of saves to the same file landing in one batch.
        write(&tmp.path().join("src/a.py"), "def a():\n    return 999\n");
        let report = process_batch(
            tmp.path(),
            &mut store,
            &emb,
            &mut cache,
            &paths(&tmp, &["src/a.py"]),
        )
        .unwrap();
        assert_eq!(report.reparsed_files, 1);
        assert_eq!(
            crate::index::content_stale_embedding_count(&store).unwrap(),
            0,
            "the batch must leave embeddings fully caught up, not just the base layer"
        );
    }

    #[test]
    fn create_delete_rename_in_one_batch() {
        let (tmp, mut store) = seeded_repo();
        let emb = HashedEmbedder::default();
        let mut cache = IgnoreCache::build(tmp.path()).unwrap();
        write(&tmp.path().join("src/c.py"), "def c():\n    return 3\n");
        std::fs::remove_file(tmp.path().join("src/b.py")).unwrap();
        std::fs::rename(
            tmp.path().join("src/a.py"),
            tmp.path().join("src/renamed.py"),
        )
        .unwrap();
        let report = process_batch(
            tmp.path(),
            &mut store,
            &emb,
            &mut cache,
            &paths(
                &tmp,
                &["src/c.py", "src/b.py", "src/a.py", "src/renamed.py"],
            ),
        )
        .unwrap();
        assert_eq!(report.removed_files, 2, "b.py deleted, a.py renamed away");
        let symbols = store.all_symbols().unwrap();
        assert!(!symbols.iter().any(|s| s.file == "src/a.py"));
        assert!(!symbols.iter().any(|s| s.file == "src/b.py"));
        assert!(symbols.iter().any(|s| s.file == "src/c.py"));
        assert!(symbols
            .iter()
            .any(|s| s.file == "src/renamed.py" && s.name == "a"));
    }

    #[test]
    fn atomic_editor_save_temp_file_is_never_indexed() {
        // Many editors write file.py.tmp then rename it over file.py. The
        // temp path must never reach update_base_for_files as its own
        // symbol — language_for_path already rejects the ".tmp" extension,
        // so no special-case code is needed, only this regression pin.
        let (tmp, mut store) = seeded_repo();
        let emb = HashedEmbedder::default();
        let mut cache = IgnoreCache::build(tmp.path()).unwrap();
        write(&tmp.path().join("src/a.py.tmp"), "def a():\n    return 1\n");
        let report = process_batch(
            tmp.path(),
            &mut store,
            &emb,
            &mut cache,
            &paths(&tmp, &["src/a.py.tmp"]),
        )
        .unwrap();
        assert_eq!(report.scanned_files, 0, "temp file must be filtered out");
    }

    #[test]
    fn ignored_tree_never_triggers_a_rescan_or_indexing() {
        let (tmp, mut store) = seeded_repo();
        let emb = HashedEmbedder::default();
        let mut cache = IgnoreCache::build(tmp.path()).unwrap();
        write(
            &tmp.path().join("node_modules/pkg/index.py"),
            "def noise():\n    return 1\n",
        );
        let candidate = cache
            .candidate(tmp.path(), &tmp.path().join("node_modules/pkg/index.py"))
            .unwrap();
        assert_eq!(candidate, None, "denylisted ancestor must reject cheaply");
        let report = process_batch(
            tmp.path(),
            &mut store,
            &emb,
            &mut cache,
            &paths(&tmp, &["node_modules/pkg/index.py"]),
        )
        .unwrap();
        assert_eq!(report.scanned_files, 0);
    }

    #[test]
    fn gitignored_file_is_never_indexed_even_with_a_recognized_extension() {
        let (tmp, mut store) = seeded_repo();
        write(&tmp.path().join(".gitignore"), "src/generated.py\n");
        std::process::Command::new("git")
            .args(["init", "-q"])
            .current_dir(tmp.path())
            .status()
            .unwrap();
        let emb = HashedEmbedder::default();
        let mut cache = IgnoreCache::build(tmp.path()).unwrap();
        write(
            &tmp.path().join("src/generated.py"),
            "def generated():\n    return 1\n",
        );
        let report = process_batch(
            tmp.path(),
            &mut store,
            &emb,
            &mut cache,
            &paths(&tmp, &["src/generated.py"]),
        )
        .unwrap();
        assert_eq!(
            report.scanned_files, 0,
            "a live .gitignore exclusion must never be indexed just because \
             it has a recognized extension"
        );
    }

    #[test]
    fn new_gitignore_rule_is_picked_up_via_the_ignore_file_itself() {
        let (tmp, mut store) = seeded_repo();
        std::process::Command::new("git")
            .args(["init", "-q"])
            .current_dir(tmp.path())
            .status()
            .unwrap();
        let emb = HashedEmbedder::default();
        let mut cache = IgnoreCache::build(tmp.path()).unwrap();
        // b.py starts out indexable and is already in the cache.
        assert!(cache
            .candidate(tmp.path(), &tmp.path().join("src/b.py"))
            .unwrap()
            .is_some());
        write(&tmp.path().join(".gitignore"), "src/b.py\n");
        // Changing the .gitignore itself must force a refresh...
        let after_gitignore_edit = cache
            .candidate(tmp.path(), &tmp.path().join(".gitignore"))
            .unwrap();
        assert_eq!(
            after_gitignore_edit, None,
            ".gitignore itself is never a symbol source"
        );
        // ...so a subsequent edit to the now-ignored file is rejected.
        let report = process_batch(
            tmp.path(),
            &mut store,
            &emb,
            &mut cache,
            &paths(&tmp, &["src/b.py"]),
        )
        .unwrap();
        assert_eq!(report.scanned_files, 0);
    }

    #[test]
    fn ignore_rule_change_reconciles_files_hidden_and_revealed_by_the_rule() {
        let (tmp, mut store) = seeded_repo();
        std::process::Command::new("git")
            .args(["init", "-q"])
            .current_dir(tmp.path())
            .status()
            .unwrap();
        write(&tmp.path().join(".gitignore"), "src/hidden.py\n");
        let emb = HashedEmbedder::default();
        let mut cache = IgnoreCache::build(tmp.path()).unwrap();
        write(
            &tmp.path().join("src/hidden.py"),
            "def hidden():\n    return 1\n",
        );
        write(&tmp.path().join(".gitignore"), "src/b.py\n");

        process_batch(
            tmp.path(),
            &mut store,
            &emb,
            &mut cache,
            &paths(&tmp, &[".gitignore"]),
        )
        .unwrap();

        let symbols = store.all_symbols().unwrap();
        assert!(!symbols.iter().any(|s| s.file == "src/b.py"));
        assert!(symbols.iter().any(|s| s.file == "src/hidden.py"));
    }

    #[test]
    fn offline_changes_are_recovered_by_reconciliation_not_the_watcher() {
        // The watcher process is never involved here: this pins the
        // contract that `update_index` alone (what `oxide watch`'s startup
        // reconciliation calls) is sufficient to catch up on changes made
        // while nothing was watching — correctness must never depend on the
        // watcher having been running.
        let (tmp, mut store) = seeded_repo();
        write(&tmp.path().join("src/a.py"), "def a():\n    return 777\n");
        write(
            &tmp.path().join("src/new_offline.py"),
            "def z():\n    return 1\n",
        );
        std::fs::remove_file(tmp.path().join("src/b.py")).unwrap();
        let emb = HashedEmbedder::default();
        update_index(tmp.path(), &mut store, &emb).unwrap();
        let symbols = store.all_symbols().unwrap();
        assert!(!symbols.iter().any(|s| s.file == "src/b.py"));
        assert!(symbols.iter().any(|s| s.name == "z"));
        assert_eq!(content_stale_embedding_count(&store).unwrap(), 0);
    }

    #[test]
    fn concurrent_manual_index_while_watcher_state_is_open_does_not_corrupt() {
        // Simulates "manual `oxide index` while watcher is active": two
        // independent SqliteStore connections against the same on-disk DB,
        // relying on the same WAL-based concurrent-writer safety the rest
        // of this codebase already depends on (see AGENTS.md). No watcher
        // code needs to know about this — it's a property of SqliteStore.
        let tmp = tempfile::tempdir().unwrap();
        write(&tmp.path().join("src/a.py"), "def a():\n    return 1\n");
        let db_path = tmp.path().join(".oxide").join("index.db");
        let emb = HashedEmbedder::default();
        {
            let mut watcher_store = SqliteStore::open(&db_path).unwrap();
            update_index(tmp.path(), &mut watcher_store, &emb).unwrap();
        }
        write(&tmp.path().join("src/a.py"), "def a():\n    return 2\n");
        let mut manual_store = SqliteStore::open(&db_path).unwrap();
        let report = update_index(tmp.path(), &mut manual_store, &emb).unwrap();
        assert_eq!(report.reparsed_files, 1);
        assert_eq!(content_stale_embedding_count(&manual_store).unwrap(), 0);
    }

    #[test]
    fn stale_lock_is_reclaimed_but_a_fresh_one_blocks() {
        let tmp = tempfile::tempdir().unwrap();
        std::fs::create_dir_all(tmp.path().join(".oxide")).unwrap();
        let lock_path = tmp.path().join(".oxide").join("watch.pid");

        // A fresh lock blocks a second acquire.
        let held = WatchLock::acquire(tmp.path()).unwrap();
        assert!(WatchLock::acquire(tmp.path()).is_err());
        drop(held);
        assert!(
            !lock_path.exists(),
            "dropping the lock must remove the file"
        );

        // A stale lock (old mtime, backdated directly rather than by
        // sleeping — deterministic, no timing flakiness) is reclaimed.
        std::fs::write(&lock_path, "999999").unwrap();
        let old = std::time::SystemTime::now() - LOCK_STALE_AFTER - Duration::from_secs(1);
        set_mtime(&lock_path, old);
        assert!(
            WatchLock::acquire(tmp.path()).is_ok(),
            "a stale lock must be reclaimable"
        );
    }

    #[test]
    fn concurrent_acquire_admits_exactly_one_watcher() {
        let tmp = tempfile::tempdir().unwrap();
        let start = std::sync::Arc::new(std::sync::Barrier::new(3));
        let release = std::sync::Arc::new(std::sync::Barrier::new(3));
        let (tx, rx) = std::sync::mpsc::channel();
        let mut threads = Vec::new();
        for _ in 0..2 {
            let root = tmp.path().to_path_buf();
            let start = start.clone();
            let release = release.clone();
            let tx = tx.clone();
            threads.push(std::thread::spawn(move || {
                start.wait();
                let lock = WatchLock::acquire(&root).ok();
                tx.send(lock.is_some()).unwrap();
                release.wait();
                drop(lock);
            }));
        }
        start.wait();
        let acquired = [rx.recv().unwrap(), rx.recv().unwrap()]
            .into_iter()
            .filter(|ok| *ok)
            .count();
        release.wait();
        for thread in threads {
            thread.join().unwrap();
        }
        assert_eq!(acquired, 1);
    }

    fn set_mtime(path: &Path, time: std::time::SystemTime) {
        let file = std::fs::File::open(path).unwrap();
        file.set_modified(time).unwrap();
    }

    #[test]
    fn converges_to_the_same_state_as_a_manual_index_across_a_mixed_changeset() {
        // The commit gate: apply a mixed changeset (edit + create + delete +
        // rename + a write inside an ignored dir) through the watcher's
        // process_batch, and separately through plain update_index on an
        // identically-seeded repo, and assert identical final symbol sets
        // and identical outstanding-embedding counts.
        let (tmp_w, mut store_w) = seeded_repo();
        write(&tmp_w.path().join("src/a.py"), "def a():\n    return 42\n");
        write(&tmp_w.path().join("src/c.py"), "def c():\n    return 1\n");
        std::fs::remove_file(tmp_w.path().join("src/b.py")).unwrap();
        write(
            &tmp_w.path().join("node_modules/noise.py"),
            "def noise():\n    return 1\n",
        );
        let emb = HashedEmbedder::default();
        let mut cache = IgnoreCache::build(tmp_w.path()).unwrap();
        process_batch(
            tmp_w.path(),
            &mut store_w,
            &emb,
            &mut cache,
            &paths(
                &tmp_w,
                &["src/a.py", "src/c.py", "src/b.py", "node_modules/noise.py"],
            ),
        )
        .unwrap();

        let (tmp_m, mut store_m) = seeded_repo();
        write(&tmp_m.path().join("src/a.py"), "def a():\n    return 42\n");
        write(&tmp_m.path().join("src/c.py"), "def c():\n    return 1\n");
        std::fs::remove_file(tmp_m.path().join("src/b.py")).unwrap();
        write(
            &tmp_m.path().join("node_modules/noise.py"),
            "def noise():\n    return 1\n",
        );
        update_index(tmp_m.path(), &mut store_m, &emb).unwrap();

        let mut names_w: Vec<String> = store_w
            .all_symbols()
            .unwrap()
            .iter()
            .map(|s| format!("{}#{}", s.file, s.qualified_name))
            .collect();
        let mut names_m: Vec<String> = store_m
            .all_symbols()
            .unwrap()
            .iter()
            .map(|s| format!("{}#{}", s.file, s.qualified_name))
            .collect();
        names_w.sort();
        names_m.sort();
        assert_eq!(names_w, names_m);
        assert_eq!(
            content_stale_embedding_count(&store_w).unwrap(),
            content_stale_embedding_count(&store_m).unwrap()
        );
    }
}
