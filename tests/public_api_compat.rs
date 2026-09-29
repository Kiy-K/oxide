//! Pre-#34-S2 public paths that S2 kept as compatibility surface. S2 moved
//! the storage contract to `IndexRead`/`IndexWrite` and snapshot assembly
//! to `retrieval::snapshot`; whether these old names stay, are deprecated
//! or go was #34 S6's decision: all are kept, and this test pins them.

use oxide::embeddings::HashedEmbedder;
use oxide::index::{update_index, IndexRead, SqliteStore};
use oxide::structural_relations::{load_lean_symbols_with_relations, load_symbols_with_relations};

/// The compatibility and facade paths #34 kept (S2-S6), resolved at compile
/// time: storage types at their historical `oxide::index::*` paths, the
/// public embedding facades, the coordinator's pre-S3 public shapes and the
/// no-longer-consulted `RetrievalMode::rerank`.
#[allow(dead_code)]
fn kept_paths() {
    let _: Option<(oxide::index::SqliteStore, oxide::index::IndexStats)> = None;
    let _: fn(&dyn oxide::index::IndexRead) = |_| {};
    let _: fn(&mut dyn oxide::index::IndexWrite) = |_| {};
    let _: Option<oxide::embedding_cache::SharedEmbeddingCache> = None;
    let _ = oxide::remote_embed::normalize_provider;
    let _: Option<oxide::evidence::EvidenceCandidate> = None;
    let _: Option<oxide::evidence::coordinator::CollectOutput> = None;
    let _ = oxide::evidence::scope::scope_files_from_seeds;
    let _ = oxide::retrieval::RetrievalMode::rerank;
}

/// Both old import paths still name the combined capability.
fn takes_index_backend(_: &mut dyn oxide::index::IndexBackend) {}
fn takes_storage_backend(_: &dyn oxide::storage::IndexBackend) {}

#[test]
fn pre_s2_public_paths_still_resolve() {
    let tmp = tempfile::tempdir().unwrap();
    std::fs::write(
        tmp.path().join("a.py"),
        "def helper():\n    return 1\n\n\ndef caller():\n    return helper()\n",
    )
    .unwrap();
    let mut store = SqliteStore::open(&tmp.path().join(".oxide/index.db")).unwrap();
    update_index(tmp.path(), &mut store, &HashedEmbedder::default()).unwrap();
    takes_index_backend(&mut store);
    takes_storage_backend(&store);

    // The lean path forwards to the snapshot owner: lean rows, same order,
    // with the same merged relations as the complete load.
    let full = load_symbols_with_relations(&store).unwrap();
    let lean = load_lean_symbols_with_relations(&store).unwrap();
    let lean_rows = store.all_symbols_lean().unwrap();
    assert_eq!(lean.len(), lean_rows.len());
    assert_eq!(full.len(), lean.len());
    assert!(
        full.iter().any(|s| !s.calls.is_empty()),
        "fixture has a call"
    );
    for ((f, l), r) in full.iter().zip(&lean).zip(&lean_rows) {
        assert_eq!((f.id(), l.id()), (r.id(), r.id()));
        assert!(f.is_complete() && !l.is_complete());
        assert_eq!((&f.calls, &f.bases), (&l.calls, &l.bases));
    }
}
