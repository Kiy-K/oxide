#[path = "../../kernel/tests/common/mod.rs"]
mod common;
use oxide_runtime::storage::LadybugStore;
use std::sync::atomic::{AtomicU64, Ordering};
fn make() -> LadybugStore {
    static NEXT: AtomicU64 = AtomicU64::new(0);
    let path = std::env::temp_dir().join(format!(
        "oxide-store-{}-{}-{}",
        std::process::id(),
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos(),
        NEXT.fetch_add(1, Ordering::Relaxed)
    ));
    LadybugStore::new(&path).unwrap()
}
macro_rules! cases { ($($case:ident),*) => {$(#[test] fn $case(){ common::store_cases::$case(make()); })*}; }
cases!(
    unpublished_generation_is_invisible,
    failed_publication_stays_invisible,
    derivations_are_never_mixed,
    lookup_is_scoped_ordered_and_explicit_about_missing,
    adjacency_is_typed_bounded_and_stable,
    views_stay_pinned_across_publication,
    published_generations_are_immutable,
    publication_is_independent_of_write_order
);

use oxide_kernel::{
    id::*,
    knowledge::*,
    store::{KnowledgeStore, ReadView, StoreError},
};
fn temp(name: &str) -> std::path::PathBuf {
    static NEXT: AtomicU64 = AtomicU64::new(10000);
    std::env::temp_dir().join(format!(
        "oxide-store-{}-{}-{}-{name}",
        std::process::id(),
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos(),
        NEXT.fetch_add(1, Ordering::Relaxed)
    ))
}
#[test]
fn reopen_retains_all_graph_truth() {
    let root = temp("reopen");
    let key = common::key("s", "d");
    {
        let mut store = LadybugStore::new(&root).unwrap();
        common::publish(&mut store, &key, common::batch());
    }
    let store = LadybugStore::new(&root).unwrap();
    let view = store.open(&key).unwrap();
    assert_eq!(
        view.entities(&[common::g()]).unwrap()[0]
            .as_ref()
            .unwrap()
            .name,
        "g"
    );
    assert_eq!(store.current(&key.repo).unwrap(), key);
}
#[test]
fn ownership_is_retained_by_pinned_view() {
    let root = temp("pinned-owner");
    let view = {
        let mut store = LadybugStore::new(&root).unwrap();
        let key = common::key("s", "d");
        common::publish(&mut store, &key, common::batch());
        store.open(&key).unwrap()
    };
    assert!(matches!(
        LadybugStore::new(&root),
        Err(StoreError::OwnershipConflict)
    ));
    assert!(view.entities(&[common::g()]).unwrap()[0].is_some());
    drop(view);
    assert!(LadybugStore::new(&root).is_ok());
}
#[test]
fn startup_removes_incomplete_generation_and_fails_closed_for_bad_current() {
    let root = temp("recover");
    {
        let mut store = LadybugStore::new(&root).unwrap();
        let key = common::key("s", "d");
        common::publish(&mut store, &key, common::batch());
        store
            .begin(common::manifest(common::key("unfinished", "d")))
            .unwrap();
    }
    let store = LadybugStore::new(&root).unwrap();
    assert!(
        std::fs::read_dir(root.join("generations"))
            .unwrap()
            .all(|e| !e
                .unwrap()
                .file_name()
                .to_string_lossy()
                .ends_with(".staging"))
    );
    drop(store);
    std::fs::write(root.join("CURRENT"), "../escape\n").unwrap();
    assert!(matches!(
        LadybugStore::new(&root),
        Err(StoreError::Corrupt(_))
    ));
}
#[test]
fn retained_source_is_exact_and_verified_after_reopen() {
    let root = temp("source");
    let source = temp("capture");
    std::fs::create_dir_all(&source).unwrap();
    let bytes = b"one\r\n\xfftwo\n";
    std::fs::write(source.join("a.py"), bytes).unwrap();
    let capture = oxide_runtime::capture::capture(&source, &Default::default()).unwrap();
    let path = RepoPath::new("a.py").unwrap();
    let digest = capture.files[&path].digest.clone();
    let key = SnapshotKey {
        repo: RepoId::new("fixture").unwrap(),
        snapshot: capture.snapshot.clone(),
        derivation: DerivationId::new("d").unwrap(),
    };
    let manifest = RepositorySnapshot {
        key: key.clone(),
        files: std::collections::BTreeMap::from([(
            path.clone(),
            FileManifest {
                digest: digest.clone(),
                language: "python".into(),
                coverage: Coverage::Complete,
                byte_length: bytes.len() as u64,
            },
        )]),
    };
    let reference = SourceRef {
        file: path.clone(),
        range: ByteRange { start: 3, end: 8 },
        digest,
    };
    {
        let mut store = LadybugStore::new(&root).unwrap();
        store.begin(manifest).unwrap();
        store.retain_source(&capture).unwrap();
        store
            .write(
                &key,
                Batch {
                    entities: vec![
                        common::entity(EntityId::Repository, "repository", "fixture", None),
                        common::entity(EntityId::File(path.clone()), "file", "a.py", None),
                    ],
                    relations: vec![common::contains(EntityId::Repository, EntityId::File(path))],
                },
            )
            .unwrap();
        store.publish(&key).unwrap();
    }
    let store = LadybugStore::new(&root).unwrap();
    let view = store.open(&key).unwrap();
    assert_eq!(view.hydrate(&reference).unwrap(), &bytes[3..8]);
    let source_dir = std::fs::read_dir(root.join("generations"))
        .unwrap()
        .next()
        .unwrap()
        .unwrap()
        .path()
        .join("source");
    let blob = std::fs::read_dir(source_dir)
        .unwrap()
        .next()
        .unwrap()
        .unwrap()
        .path();
    std::fs::write(blob, b"corrupt").unwrap();
    assert!(matches!(
        view.hydrate(&reference),
        Err(StoreError::Corrupt(_))
    ));
}
#[test]
fn cross_process_child() {
    let Ok(root) = std::env::var("OXIDE_STORE_CHILD") else {
        return;
    };
    std::process::exit(
        if matches!(
            LadybugStore::new(std::path::Path::new(&root)),
            Err(StoreError::OwnershipConflict)
        ) {
            7
        } else {
            8
        },
    );
}
#[test]
fn exclusive_ownership_crosses_process_boundary() {
    let root = temp("process");
    let _store = LadybugStore::new(&root).unwrap();
    let status = std::process::Command::new(std::env::current_exe().unwrap())
        .args(["--exact", "cross_process_child", "--test-threads=1"])
        .env("OXIDE_STORE_CHILD", root)
        .status()
        .unwrap();
    assert_eq!(status.code(), Some(7));
}
#[test]
fn serialized_writer_requests_preserve_every_batch() {
    let mut store = make();
    let key = common::key("serial", "d");
    store.begin(common::manifest(key.clone())).unwrap();
    let b = common::batch();
    for entity in b.entities {
        store
            .write(
                &key,
                Batch {
                    entities: vec![entity],
                    relations: vec![],
                },
            )
            .unwrap();
    }
    for relation in b.relations {
        store
            .write(
                &key,
                Batch {
                    entities: vec![],
                    relations: vec![relation],
                },
            )
            .unwrap();
    }
    store.publish(&key).unwrap();
    assert!(
        store
            .open(&key)
            .unwrap()
            .entities(&[common::test_h()])
            .unwrap()[0]
            .as_ref()
            .unwrap()
            .test
    );
}

fn generation_dirs(root: &std::path::Path) -> Vec<std::path::PathBuf> {
    std::fs::read_dir(root.join("generations"))
        .unwrap()
        .map(|e| e.unwrap().path())
        .collect()
}
/// Rewrites the storage format recorded by the CURRENT generation.
fn set_storage_format(root: &std::path::Path, format: u64) {
    let current = std::fs::read_to_string(root.join("CURRENT")).unwrap();
    let path = root
        .join("generations")
        .join(current.trim())
        .join("MANIFEST");
    let mut manifest: serde_json::Value =
        serde_json::from_slice(&std::fs::read(&path).unwrap()).unwrap();
    manifest[0] = serde_json::json!(format);
    std::fs::write(path, manifest.to_string()).unwrap();
}
#[test]
fn older_storage_format_is_discarded_for_rebuild() {
    let root = temp("old-format");
    let key = common::key("format", "d");
    {
        let mut store = LadybugStore::new(&root).unwrap();
        common::publish(&mut store, &key, common::batch());
    }
    set_storage_format(&root, oxide_runtime::storage::STORAGE_FORMAT - 1);
    // Never migrated: the old-format generation is dropped and the key is
    // rebuilt from scratch.
    let mut store = LadybugStore::new(&root).unwrap();
    assert_eq!(store.current(&key.repo), Err(StoreError::MissingSnapshot));
    assert!(generation_dirs(&root).is_empty());
    common::publish(&mut store, &key, common::batch());
    assert_eq!(store.current(&key.repo).unwrap(), key);
}
#[test]
fn newer_storage_format_is_refused_and_kept() {
    let root = temp("new-format");
    let key = common::key("format", "d");
    {
        let mut store = LadybugStore::new(&root).unwrap();
        common::publish(&mut store, &key, common::batch());
        // A superseded generation must survive the refusal too.
        common::publish(&mut store, &common::key("newer", "d"), common::batch());
    }
    let before = generation_dirs(&root).len();
    let current = std::fs::read(root.join("CURRENT")).unwrap();
    set_storage_format(&root, oxide_runtime::storage::STORAGE_FORMAT + 1);
    assert!(matches!(
        LadybugStore::new(&root),
        Err(StoreError::Unsupported(reason)) if reason.contains("newer")
    ));
    assert_eq!(generation_dirs(&root).len(), before);
    assert_eq!(std::fs::read(root.join("CURRENT")).unwrap(), current);
}
#[test]
fn first_seal_interrupted_before_publication_is_discarded() {
    // State of a first build killed after its seal/rename and before CURRENT
    // was written: a valid sealed generation and no pointer.
    let root = temp("first-seal");
    let key = common::key("first", "d");
    {
        let mut store = LadybugStore::new(&root).unwrap();
        common::publish(&mut store, &key, common::batch());
    }
    std::fs::remove_file(root.join("CURRENT")).unwrap();
    let mut store = LadybugStore::new(&root).unwrap();
    assert_eq!(store.current(&key.repo), Err(StoreError::MissingSnapshot));
    assert_eq!(store.open(&key).err(), Some(StoreError::MissingSnapshot));
    assert!(generation_dirs(&root).is_empty());
    common::publish(&mut store, &key, common::batch());
    assert_eq!(store.current(&key.repo).unwrap(), key);
}
#[test]
fn later_seal_interrupted_before_publication_keeps_previous_current() {
    let root = temp("later-seal");
    let good = common::key("good", "d");
    let sealed = common::key("sealed", "d");
    {
        let mut store = LadybugStore::new(&root).unwrap();
        common::publish(&mut store, &good, common::batch());
    }
    let current = std::fs::read(root.join("CURRENT")).unwrap();
    {
        let mut store = LadybugStore::new(&root).unwrap();
        common::publish(&mut store, &sealed, common::batch());
    }
    // Rewind the pointer: `sealed` is now a seal that never became current.
    std::fs::write(root.join("CURRENT"), current).unwrap();
    let store = LadybugStore::new(&root).unwrap();
    assert_eq!(store.current(&good.repo).unwrap(), good);
    assert_eq!(store.open(&sealed).err(), Some(StoreError::MissingSnapshot));
    assert_eq!(generation_dirs(&root).len(), 1);
    drop(store);
    // A pointer to a missing generation still fails closed.
    for dir in generation_dirs(&root) {
        std::fs::remove_dir_all(dir).unwrap();
    }
    assert!(matches!(
        LadybugStore::new(&root),
        Err(StoreError::Corrupt(_))
    ));
}
#[test]
fn concurrent_read_views_remain_immutable_during_publication() {
    let mut store = make();
    let old = common::key("old-read", "d");
    common::publish(&mut store, &old, common::batch());
    let view = store.open(&old).unwrap();
    std::thread::scope(|scope| {
        let workers = (0..4)
            .map(|_| {
                let view = view.clone();
                scope.spawn(move || {
                    for _ in 0..20 {
                        assert_eq!(
                            view.entities(&[common::g()]).unwrap()[0]
                                .as_ref()
                                .unwrap()
                                .name,
                            "g"
                        );
                    }
                })
            })
            .collect::<Vec<_>>();
        let mut batch = common::batch();
        batch
            .entities
            .iter_mut()
            .find(|e| e.id == common::g())
            .unwrap()
            .name = "new-g".into();
        let new = common::key("new-read", "d");
        common::publish(&mut store, &new, batch);
        assert_eq!(
            store.open(&new).unwrap().entities(&[common::g()]).unwrap()[0]
                .as_ref()
                .unwrap()
                .name,
            "new-g"
        );
        for worker in workers {
            worker.join().unwrap();
        }
    });
}
#[test]
fn crash_child() {
    let Ok(root) = std::env::var("OXIDE_CRASH_ROOT") else {
        return;
    };
    let mut store = LadybugStore::new(std::path::Path::new(&root)).unwrap();
    let key = common::key("crashed", "d");
    store.begin(common::manifest(key.clone())).unwrap();
    store.write(&key, common::batch()).unwrap();
    std::process::exit(9);
}
#[test]
fn process_exit_before_seal_preserves_current() {
    let root = temp("crash");
    let key = common::key("last-good", "d");
    {
        let mut store = LadybugStore::new(&root).unwrap();
        common::publish(&mut store, &key, common::batch());
    }
    let status = std::process::Command::new(std::env::current_exe().unwrap())
        .args(["--exact", "crash_child", "--test-threads=1"])
        .env("OXIDE_CRASH_ROOT", &root)
        .status()
        .unwrap();
    assert_eq!(status.code(), Some(9));
    let store = LadybugStore::new(&root).unwrap();
    assert_eq!(store.current(&key.repo).unwrap(), key);
    assert!(matches!(
        store.open(&common::key("crashed", "d")),
        Err(StoreError::MissingSnapshot)
    ));
}

#[test]
fn derivation_manifest_identity_is_verified_on_write_and_reopen() {
    let root = temp("derivation-manifest");
    let components = std::collections::BTreeMap::from([
        ("schema".into(), "ladybug47-v1".into()),
        ("parser".into(), "python-v1".into()),
    ]);
    let mut key = common::key("derived", "d");
    key.derivation = oxide_runtime::derivation::derivation_id(&components);
    {
        let mut store = LadybugStore::new(&root).unwrap();
        store.begin(common::manifest(key.clone())).unwrap();
        assert!(matches!(
            store.retain_derivation(&key, &std::collections::BTreeMap::new()),
            Err(StoreError::InvalidBatch(_))
        ));
        store.retain_derivation(&key, &components).unwrap();
        store.write(&key, common::batch()).unwrap();
        store.publish(&key).unwrap();
    }
    let store = LadybugStore::new(&root).unwrap();
    assert_eq!(store.current(&key.repo).unwrap(), key);
    drop(store);
    let dir = std::fs::read_dir(root.join("generations"))
        .unwrap()
        .next()
        .unwrap()
        .unwrap()
        .path();
    std::fs::write(dir.join("DERIVATION"), b"{}").unwrap();
    assert!(matches!(
        LadybugStore::new(&root),
        Err(StoreError::Corrupt(_))
    ));
}

#[test]
fn failure_inside_native_transaction_rolls_back_earlier_entities() {
    let mut store = make();
    let key = common::key("rollback", "d");
    store.begin(common::manifest(key.clone())).unwrap();
    let mut batch = common::batch();
    batch
        .entities
        .last_mut()
        .unwrap()
        .source
        .as_mut()
        .unwrap()
        .range
        .end = u64::MAX;
    assert!(matches!(
        store.write(&key, batch),
        Err(StoreError::InvalidBatch(_))
    ));
    store.write(&key, common::batch()).unwrap();
    store.publish(&key).unwrap();
    assert!(store.open(&key).unwrap().entities(&[common::g()]).unwrap()[0].is_some());
}
