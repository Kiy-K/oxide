//! `open_repository` / `close_repository` through `Runtime::handle` against
//! real LadybugDB stores prepared by the Phase 2B rebuild pipeline: identity,
//! repeated opens, every repository error code, lock release on close and
//! on drop. src/client.test.ts covers the same through the real process.

use std::fs;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};

use oxide_kernel::id::SnapshotKey;
use oxide_kernel::store::{KnowledgeStore, StoreError};
use oxide_runtime::Runtime;
use oxide_runtime::capture::{Scope, capture};
use oxide_runtime::derivation::{components, derivation_id, derive};
use oxide_runtime::repository::{DataLocation, RepositorySession};
use oxide_runtime::storage::LadybugStore;
use serde_json::{Value, json};

fn temp(name: &str) -> PathBuf {
    static NEXT: AtomicU64 = AtomicU64::new(0);
    let path = std::env::temp_dir().join(format!(
        "oxide-session-{}-{}-{name}",
        std::process::id(),
        NEXT.fetch_add(1, Ordering::Relaxed)
    ));
    let _ = fs::remove_dir_all(&path);
    fs::create_dir_all(&path).unwrap();
    path
}

/// A small Python checkout under `base/<name>`.
fn checkout(base: &Path, name: &str) -> PathBuf {
    let root = base.join(name);
    fs::create_dir_all(root.join("pkg")).unwrap();
    fs::write(root.join("pkg/__init__.py"), "").unwrap();
    fs::write(
        root.join("pkg/auth.py"),
        "def login(user):\n    return user\n",
    )
    .unwrap();
    root
}

/// Registers and fully rebuilds `root`; returns the published key.
fn index(root: &Path, location: &DataLocation) -> SnapshotKey {
    let mut session = RepositorySession::open(root, location).unwrap();
    session.rebuild(&Scope::default()).unwrap().key
}

fn open(runtime: &mut Runtime, path: &Path) -> Value {
    runtime.handle(
        &json!({"version": 1, "id": "o", "op": "open_repository", "params": {"path": path}})
            .to_string(),
    )
}

fn close(runtime: &mut Runtime, handle: &Value) -> Value {
    runtime.handle(
        &json!({"version": 1, "id": "c", "op": "close_repository", "params": {"handle": handle}})
            .to_string(),
    )
}

fn code(answer: &Value) -> &str {
    answer["error"]["code"].as_str().unwrap_or("<ok>")
}

#[test]
fn opens_an_indexed_repository_with_its_published_identity() {
    let base = temp("open");
    let root = checkout(&base, "repo");
    let location = DataLocation(base.join("data"));
    let key = index(&root, &location);
    let mut runtime = Runtime::new(Some(location.clone()));

    let first = open(&mut runtime, &root);
    assert_eq!(first["ok"], true, "{first}");
    let result = &first["result"];
    assert_eq!(result["repo_id"], key.repo.as_str());
    assert_eq!(result["snapshot"], key.snapshot.as_str());
    assert_eq!(result["derivation"], key.derivation.as_str());
    assert_eq!(
        result["root"],
        fs::canonicalize(&root).unwrap().to_str().unwrap()
    );
    assert_eq!(result["handle"].as_str().unwrap().len(), 32);

    // Repeated opens, through any spelling of the root, return the same
    // session; the store is opened once, so no self-conflict.
    let again = open(&mut runtime, &root.join("pkg/.."));
    assert_eq!(again, first);

    // The open session owns the store: another owner is refused until close.
    assert!(matches!(
        RepositorySession::open(&root, &location),
        Err(StoreError::OwnershipConflict)
    ));
    let handle = result["handle"].clone();
    assert_eq!(close(&mut runtime, &handle)["result"], json!({}));
    assert_eq!(code(&close(&mut runtime, &handle)), "unknown_handle");
    drop(RepositorySession::open(&root, &location).unwrap());

    // Reopening after close is a new session on the same snapshot.
    let reopened = open(&mut runtime, &root);
    assert_ne!(reopened["result"]["handle"], handle);
    assert_eq!(reopened["result"]["snapshot"], result["snapshot"]);

    // Dropping the runtime (EOF) releases every session's lock.
    drop(runtime);
    drop(RepositorySession::open(&root, &location).unwrap());
    fs::remove_dir_all(base).unwrap();
}

#[test]
fn handles_belong_to_one_runtime() {
    let base = temp("handles");
    let root = checkout(&base, "repo");
    let location = DataLocation(base.join("data"));
    index(&root, &location);
    let mut first = Runtime::new(Some(location.clone()));
    let handle = open(&mut first, &root)["result"]["handle"].clone();
    drop(first);
    let mut second = Runtime::new(Some(location));
    assert_eq!(code(&close(&mut second, &handle)), "unknown_handle");
    fs::remove_dir_all(base).unwrap();
}

#[test]
fn unregistered_and_unpublished_repositories_are_not_indexed() {
    let base = temp("unindexed");
    let location = DataLocation(base.join("data"));
    let mut runtime = Runtime::new(Some(location.clone()));

    // Nothing registered yet (no data directory at all): lookup only, so
    // nothing is created.
    let stranger = checkout(&base, "stranger");
    assert_eq!(code(&open(&mut runtime, &stranger)), "not_indexed");
    assert!(!location.0.exists());

    // Another repository registered, this one still unknown.
    index(&checkout(&base, "other"), &location);
    let entries = || {
        fs::read_dir(location.0.join("repositories"))
            .unwrap()
            .count()
    };
    let before = entries();
    assert_eq!(code(&open(&mut runtime, &stranger)), "not_indexed");
    assert_eq!(entries(), before, "opening never registers");

    // Registered, but no generation was ever published.
    let registered = checkout(&base, "registered");
    drop(RepositorySession::open(&registered, &location).unwrap());
    assert_eq!(code(&open(&mut runtime, &registered)), "not_indexed");
    fs::remove_dir_all(base).unwrap();
}

#[test]
fn corrupt_state_and_ownership_conflicts_are_reported() {
    let base = temp("corrupt");
    let root = checkout(&base, "repo");
    let location = DataLocation(base.join("data"));
    index(&root, &location);
    let mut runtime = Runtime::new(Some(location.clone()));

    // Another owner holds the store.
    let owner = RepositorySession::open(&root, &location).unwrap();
    let directory = owner.store_directory.clone();
    assert_eq!(code(&open(&mut runtime, &root)), "ownership_conflict");
    drop(owner);

    fs::write(directory.join("CURRENT"), "not-a-generation\n").unwrap();
    assert_eq!(code(&open(&mut runtime, &root)), "repository_unavailable");

    // Data location inside the requested path.
    let mut inside = Runtime::new(Some(DataLocation(root.join(".oxide"))));
    assert_eq!(code(&open(&mut inside, &root)), "invalid_path");
    fs::remove_dir_all(base).unwrap();
}

#[test]
fn a_generation_of_another_derivation_is_incompatible() {
    let base = temp("derivation");
    let root = checkout(&base, "repo");
    let location = DataLocation(base.join("data"));
    // Published through the real pipeline stages, but under derivation
    // components this runtime does not have (another parser version).
    let mut session = RepositorySession::open(&root, &location).unwrap();
    let captured = capture(&root, &Scope::default()).unwrap();
    let (mut manifest, batch, _) = derive(&session.repo_id, &captured).unwrap();
    let mut other = components();
    other.insert("python-parser".into(), "older".into());
    manifest.key.derivation = derivation_id(&other);
    let key = manifest.key.clone();
    let store: &mut LadybugStore = &mut session.store;
    store.begin(manifest).unwrap();
    store.retain_derivation(&key, &other).unwrap();
    store.retain_source(&captured).unwrap();
    store.write(&key, batch).unwrap();
    store.publish(&key).unwrap();
    drop(session);

    let mut runtime = Runtime::new(Some(location));
    let answer = open(&mut runtime, &root);
    assert_eq!(code(&answer), "incompatible_derivation", "{answer}");
    let message = answer["error"]["message"].as_str().unwrap();
    assert!(message.contains(key.derivation.as_str()), "{message}");
    fs::remove_dir_all(base).unwrap();
}
