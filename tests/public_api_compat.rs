//! Pre-#34-S2 public paths that S2 kept as compatibility surface. S2 moved
//! the storage contract to `IndexRead`/`IndexWrite` and snapshot assembly
//! to `retrieval::snapshot`; whether these old names stay, are deprecated
//! or go is #34 S6's decision, so S2 must not drop them by accident.

use oxide::embeddings::HashedEmbedder;
use oxide::index::{update_index, IndexRead, SqliteStore};
use oxide::structural_relations::{load_lean_symbols_with_relations, load_symbols_with_relations};

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
