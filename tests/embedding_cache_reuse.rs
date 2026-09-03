//! End-to-end regression test for `oxide::embedding_cache`'s shared,
//! content-addressed cache (docs/term-coverage-eval/README.md's
//! harness-reuse follow-up): exercises it through the SAME `update_index`
//! entry point `examples/term_coverage_index.rs` uses, across two fully
//! separate repositories with their own separate `.oxide/index.db` files —
//! not just the unit-level `SharedEmbeddingCache` API in
//! `src/embedding_cache.rs`'s own test module.
//!
//! Proves the two properties that matter together: identical content
//! reuses its embedding across the two independently-indexed stores, AND
//! no symbol or structural-relation state ever crosses between them —
//! each store only ever reports its own repository's own symbols.

use oxide::embedding_cache::SharedEmbeddingCache;
use oxide::embeddings::open_embedder;
use oxide::index::{update_index, IndexBackend, SqliteStore};
use std::path::Path;

fn write(path: &Path, content: &str) {
    std::fs::create_dir_all(path.parent().unwrap()).unwrap();
    std::fs::write(path, content).unwrap();
}

const SHARED_PY: &str = "def shared_helper():\n    return 1\n";

#[test]
fn identical_files_reuse_embeddings_across_commits_without_leaking_symbols() {
    let repo_a = tempfile::tempdir().unwrap();
    let repo_b = tempfile::tempdir().unwrap();
    // `shared.py` is byte-identical in both repos (same relative path, same
    // content) — this is the file two nearby pinned commits of the same
    // real repo would share unchanged. `only_a.py`/`only_b.py` are unique
    // to each, standing in for the file each commit actually touched.
    write(&repo_a.path().join("shared.py"), SHARED_PY);
    write(
        &repo_a.path().join("only_a.py"),
        "def only_in_a():\n    return 'a'\n",
    );
    write(&repo_b.path().join("shared.py"), SHARED_PY);
    write(
        &repo_b.path().join("only_b.py"),
        "def only_in_b():\n    return 'b'\n",
    );

    let cache_db = tempfile::NamedTempFile::new().unwrap();
    let db_a = tempfile::NamedTempFile::new().unwrap();
    let db_b = tempfile::NamedTempFile::new().unwrap();

    let mut store_a = SqliteStore::open(db_a.path()).unwrap();
    let embedder_a =
        SharedEmbeddingCache::open(open_embedder(None).unwrap(), cache_db.path()).unwrap();
    update_index(repo_a.path(), &mut store_a, &embedder_a).unwrap();
    // First-ever indexing of this content: nothing to reuse yet.
    assert_eq!(embedder_a.hits(), 0);
    assert!(embedder_a.misses() > 0);

    let mut store_b = SqliteStore::open(db_b.path()).unwrap();
    let embedder_b =
        SharedEmbeddingCache::open(open_embedder(None).unwrap(), cache_db.path()).unwrap();
    update_index(repo_b.path(), &mut store_b, &embedder_b).unwrap();

    // --- reuse: shared.py's content must not be re-embedded from scratch ---
    assert!(
        embedder_b.hits() > 0,
        "indexing a second repo/commit sharing unchanged files must reuse \
         at least one embedding from the shared cache (hits={}, misses={})",
        embedder_b.hits(),
        embedder_b.misses()
    );

    let symbols_a = store_a.all_symbols().unwrap();
    let symbols_b = store_b.all_symbols().unwrap();
    let embeddings_a = store_a.all_embeddings().unwrap();
    let embeddings_b = store_b.all_embeddings().unwrap();

    let shared_fn_a = symbols_a
        .iter()
        .find(|s| s.file == "shared.py" && s.qualified_name.contains("shared_helper"))
        .expect("shared_helper symbol in repo_a");
    let shared_fn_b = symbols_b
        .iter()
        .find(|s| s.file == "shared.py" && s.qualified_name.contains("shared_helper"))
        .expect("shared_helper symbol in repo_b");
    let vec_a = &embeddings_a.get(&shared_fn_a.id()).unwrap().1;
    let vec_b = &embeddings_b.get(&shared_fn_b.id()).unwrap().1;
    assert_eq!(
        vec_a, vec_b,
        "the reused embedding must be byte-identical to the original, not just \"present\""
    );

    // --- no leakage: each store reports ONLY its own repo's symbols ---
    assert!(
        symbols_a
            .iter()
            .all(|s| s.file == "shared.py" || s.file == "only_a.py"),
        "repo_a's store must never contain a symbol from repo_b: {:?}",
        symbols_a.iter().map(|s| &s.file).collect::<Vec<_>>()
    );
    assert!(
        symbols_b
            .iter()
            .all(|s| s.file == "shared.py" || s.file == "only_b.py"),
        "repo_b's store must never contain a symbol from repo_a: {:?}",
        symbols_b.iter().map(|s| &s.file).collect::<Vec<_>>()
    );
    assert!(
        symbols_a
            .iter()
            .all(|s| !s.qualified_name.contains("only_in_b")),
        "repo_a's store must never contain repo_b's unique symbol"
    );
    assert!(
        symbols_b
            .iter()
            .all(|s| !s.qualified_name.contains("only_in_a")),
        "repo_b's store must never contain repo_a's unique symbol"
    );

    // --- changed/unique content is still freshly embedded, not skipped ---
    let only_b_fn = symbols_b
        .iter()
        .find(|s| s.file == "only_b.py" && s.qualified_name.contains("only_in_b"))
        .expect("only_in_b symbol present");
    assert!(
        embeddings_b.contains_key(&only_b_fn.id()),
        "repo_b's unique symbol must have its own real embedding, not a missing/reused one"
    );
}
