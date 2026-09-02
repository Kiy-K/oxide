//! `pending_embedding_count` must never diverge from what `update_embeddings`
//! would actually (re)compute — a watcher reporting "0 pending" while a real
//! run would still find work is exactly the "stale/pending embeddings
//! presented as current" bug the auto-indexing watcher must avoid.

use oxide::embeddings::HashedEmbedder;
use oxide::index::{
    pending_embedding_count, update_base, update_embeddings, IndexBackend, IndexOptions,
    IndexReport, SqliteStore,
};
use std::path::Path;

fn write(path: &Path, content: &str) {
    std::fs::create_dir_all(path.parent().unwrap()).unwrap();
    std::fs::write(path, content).unwrap();
}

fn seeded_repo() -> (tempfile::TempDir, SqliteStore) {
    let tmp = tempfile::tempdir().unwrap();
    write(&tmp.path().join("src/a.py"), "def a():\n    return 1\n");
    write(&tmp.path().join("src/b.py"), "def b():\n    return 2\n");
    let mut store = SqliteStore::open(Path::new(":memory:")).unwrap();
    update_base(tmp.path(), &mut store, &IndexOptions::default()).unwrap();
    (tmp, store)
}

#[test]
fn freshly_based_but_never_embedded_index_reports_every_symbol_pending() {
    let (_tmp, store) = seeded_repo();
    let emb = HashedEmbedder::default();
    let n = pending_embedding_count(&store, &emb).unwrap();
    assert_eq!(n, store.all_symbols().unwrap().len());
    assert!(n > 0);
}

#[test]
fn fully_embedded_index_reports_zero_pending() {
    let (tmp, mut store) = seeded_repo();
    let emb = HashedEmbedder::default();
    let mut report = IndexReport::default();
    update_embeddings(
        tmp.path(),
        &mut store,
        &emb,
        &IndexOptions::default(),
        &mut report,
    )
    .unwrap();
    assert_eq!(pending_embedding_count(&store, &emb).unwrap(), 0);
}

#[test]
fn editing_one_file_after_embedding_makes_exactly_its_symbols_pending() {
    let (tmp, mut store) = seeded_repo();
    let emb = HashedEmbedder::default();
    let mut report = IndexReport::default();
    update_embeddings(
        tmp.path(),
        &mut store,
        &emb,
        &IndexOptions::default(),
        &mut report,
    )
    .unwrap();
    assert_eq!(pending_embedding_count(&store, &emb).unwrap(), 0);

    write(&tmp.path().join("src/a.py"), "def a():\n    return 999\n");
    update_base(tmp.path(), &mut store, &IndexOptions::default()).unwrap();
    // At least a.py's function symbol changed content_hash (its body
    // changed); b.py's did not. The module symbol's coarse hash (imports +
    // first line) may or may not change depending on what moved, so this
    // checks the real embeddings table rather than assuming a fixed count —
    // it can't silently drift from `pending_embedding_count`'s actual
    // contract if that coarse-hash behavior ever changes.
    let pending = pending_embedding_count(&store, &emb).unwrap();
    assert!(pending > 0, "edited file's symbols must be pending");
    let embeddings = store.all_embeddings().unwrap();
    let expected_pending = store
        .all_symbols()
        .unwrap()
        .iter()
        .filter(|s| match embeddings.get(&s.id()) {
            Some((old_hash, _)) => *old_hash != s.content_hash,
            None => true,
        })
        .count();
    assert_eq!(pending, expected_pending);
    assert!(
        !store
            .all_symbols()
            .unwrap()
            .iter()
            .filter(|s| s.file == "src/b.py")
            .any(|s| embeddings.get(&s.id()).map(|(h, _)| *h) != Some(s.content_hash)),
        "b.py must not have any stale embeddings"
    );
}

#[test]
fn switching_embedder_makes_everything_pending_even_if_content_hashes_match() {
    let (tmp, mut store) = seeded_repo();
    let hashed = HashedEmbedder::default();
    let mut report = IndexReport::default();
    update_embeddings(
        tmp.path(),
        &mut store,
        &hashed,
        &IndexOptions::default(),
        &mut report,
    )
    .unwrap();
    assert_eq!(pending_embedding_count(&store, &hashed).unwrap(), 0);

    // A different embedder (different fingerprint) must report everything
    // pending, exactly like `update_embeddings` would wipe-and-reembed all.
    let other = HashedEmbedder::new(64);
    let pending = pending_embedding_count(&store, &other).unwrap();
    assert_eq!(pending, store.all_symbols().unwrap().len());
}

#[test]
fn pending_count_matches_what_update_embeddings_actually_embeds() {
    // The load-bearing property: predicted pending count == actual embedded
    // count on the very next `update_embeddings` call.
    let (tmp, mut store) = seeded_repo();
    let emb = HashedEmbedder::default();
    let predicted = pending_embedding_count(&store, &emb).unwrap();
    let mut report = IndexReport::default();
    update_embeddings(
        tmp.path(),
        &mut store,
        &emb,
        &IndexOptions::default(),
        &mut report,
    )
    .unwrap();
    assert_eq!(report.embedded_symbols, predicted);
}
