//! End-to-end incremental indexing tests against a real temp repository.

use oxide::embeddings::HashedEmbedder;
use oxide::index::{update_index, IndexBackend, SqliteStore};
use std::path::Path;

fn write(path: &Path, content: &str) {
    std::fs::create_dir_all(path.parent().unwrap()).unwrap();
    std::fs::write(path, content).unwrap();
}

const FOO_V1: &str = "def foo():\n    return 1\n\ndef bar():\n    return foo() + 1\n";
const FOO_V2: &str = "def foo():\n    return 42\n\ndef bar():\n    return foo() + 1\n";

#[test]
fn unchanged_files_are_not_reparsed_and_embeddings_reused() {
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path();
    write(&root.join("src/thing.py"), FOO_V1);

    let mut store = SqliteStore::open(Path::new(":memory:")).unwrap();
    let emb = HashedEmbedder::default();

    let r1 = update_index(root, &mut store, &emb).unwrap();
    assert_eq!(r1.reparsed_files, 1);
    assert_eq!(r1.embedded_symbols, r1.new_symbols);
    assert_eq!((r1.new_symbols, r1.changed_symbols), (3, 0)); // module + foo + bar

    // No-change reindex: zero reparse, zero embed.
    let r2 = update_index(root, &mut store, &emb).unwrap();
    assert_eq!(r2.reparsed_files, 0);
    assert_eq!(r2.unchanged_files, 1);
    assert_eq!(r2.embedded_symbols, 0);
    assert_eq!(r2.reused_embeddings, 3);
}

#[test]
fn changing_one_symbol_only_reembeds_that_symbol() {
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path();
    write(&root.join("src/thing.py"), FOO_V1);

    let mut store = SqliteStore::open(Path::new(":memory:")).unwrap();
    let emb = HashedEmbedder::default();
    update_index(root, &mut store, &emb).unwrap();

    write(&root.join("src/thing.py"), FOO_V2); // only foo()'s body changed
    let r = update_index(root, &mut store, &emb).unwrap();

    assert_eq!(
        r.reparsed_files, 1,
        "file content changed so it must reparse"
    );
    assert_eq!((r.changed_symbols, r.new_symbols), (1, 0));
    assert_eq!(
        r.embedded_symbols, 1,
        "only the modified symbol may be re-embedded"
    );
    assert_eq!(r.reused_embeddings, 2, "bar and module keep embeddings");

    // The updated value is visible through retrieval.
    let engine = oxide::retrieval::RetrievalEngine::new(&store, &emb);
    let hits = engine
        .search(
            "foo",
            &oxide::retrieval::SearchOptions {
                limit: 3,
                mode: oxide::retrieval::SearchMode::LexicalOnly,
                expand: false,
            },
        )
        .unwrap();
    assert_eq!(hits[0].symbol.name, "foo");
}

#[test]
fn deleted_files_and_symbols_are_removed_from_persistent_index() {
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path();
    write(&root.join("a.py"), "def gone():\n    pass\n");
    write(&root.join("b.py"), "def stays():\n    return 1\n");

    let mut store = SqliteStore::open(Path::new(":memory:")).unwrap();
    let emb = HashedEmbedder::default();
    update_index(root, &mut store, &emb).unwrap();
    assert_eq!(store.all_symbols().unwrap().len(), 4); // 2 files × (module + fn)

    std::fs::remove_file(root.join("a.py")).unwrap();
    let r = update_index(root, &mut store, &emb).unwrap();
    assert_eq!(r.removed_files, 1);
    assert_eq!(r.deleted_symbols, 2);

    let names: Vec<String> = store
        .all_symbols()
        .unwrap()
        .into_iter()
        .map(|s| s.name)
        .collect();
    assert!(!names.contains(&"gone".to_string()));
    assert!(names.contains(&"stays".to_string()));

    let stats = store.stats().unwrap();
    assert_eq!(stats.files, 1);
    assert_eq!(stats.symbols, 2);
    assert_eq!(stats.embeddings, 2, "stale embeddings purged");
}

#[test]
fn renaming_a_symbol_within_a_still_present_file_counts_as_deleted() {
    // A symbol can disappear without its file being deleted -- e.g. a
    // rename, or a function removed while its siblings stay. `replace_file`
    // deletes the whole file's symbol rows and reinserts the fresh set, so
    // this is a real deletion, not just a "changed" symbol; the report must
    // count it under deleted_symbols, not silently fold it into new_symbols.
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path();
    write(&root.join("src/thing.py"), FOO_V1); // foo, bar

    let mut store = SqliteStore::open(Path::new(":memory:")).unwrap();
    let emb = HashedEmbedder::default();
    update_index(root, &mut store, &emb).unwrap();

    write(
        &root.join("src/thing.py"),
        "def foo_renamed():\n    return 1\n\ndef bar():\n    return foo_renamed() + 1\n",
    );
    let r = update_index(root, &mut store, &emb).unwrap();

    assert_eq!(r.new_symbols, 1, "foo_renamed is a new symbol id");
    assert_eq!(
        r.deleted_symbols, 1,
        "the old foo symbol id is gone, not just changed"
    );

    let names: Vec<String> = store
        .all_symbols()
        .unwrap()
        .into_iter()
        .map(|s| s.name)
        .collect();
    assert!(!names.contains(&"foo".to_string()));
    assert!(names.contains(&"foo_renamed".to_string()));
}

#[test]
fn unreadable_file_is_counted_as_errored_not_silently_dropped() {
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path();
    write(&root.join("src/good.py"), "def good():\n    return 1\n");
    // 0xFF is never a valid UTF-8 lead byte, so read_to_string fails on this
    // file; it contains no NUL byte so the scanner's binary sniff still
    // accepts it (it must reach the indexing pipeline to exercise the bug).
    std::fs::write(root.join("src/bad.py"), b"def bad():\n    x = \xff\n").unwrap();

    let mut store = SqliteStore::open(Path::new(":memory:")).unwrap();
    let emb = HashedEmbedder::default();
    let r = update_index(root, &mut store, &emb).unwrap();

    assert_eq!(r.scanned_files, 2, "both files must be discovered");
    assert_eq!(
        r.errored_files, 1,
        "the unreadable file must be counted, not silently folded into unchanged"
    );
    assert_eq!(r.reparsed_files, 1, "only the readable new file is parsed");
    assert_eq!(
        r.scanned_files,
        r.unchanged_files + r.reparsed_files + r.errored_files,
        "accounting invariant must hold: discovered == unchanged + reparsed + errored"
    );

    let names: Vec<String> = store
        .all_symbols()
        .unwrap()
        .into_iter()
        .map(|s| s.name)
        .collect();
    assert!(names.contains(&"good".to_string()));
    assert!(
        !names.iter().any(|n| n == "bad"),
        "unreadable file must not silently appear in the index"
    );

    // A later run over an unchanged unreadable file keeps reporting it as
    // errored (not unchanged): it was never actually indexed.
    let r2 = update_index(root, &mut store, &emb).unwrap();
    assert_eq!(r2.errored_files, 1);
    assert_eq!(r2.reparsed_files, 0);
    assert_eq!(r2.unchanged_files, 1);
}

#[test]
fn index_survives_reopen_from_disk() {
    let tmp = tempfile::tempdir().unwrap();
    let db = tmp.path().join(".oxide/index.db");
    let src_root = tmp.path().join("repo_src");
    write(&src_root.join("m.py"), "def persist_me():\n    pass\n");

    {
        let mut store = SqliteStore::open(&db).unwrap();
        update_index(&src_root, &mut store, &HashedEmbedder::default()).unwrap();
    }
    // Reopen: no work needed, symbols still queryable.
    let mut store = SqliteStore::open(&db).unwrap();
    let emb = HashedEmbedder::default();
    let r = update_index(&src_root, &mut store, &emb).unwrap();
    assert_eq!(r.unchanged_files, 1);
    assert_eq!(r.reused_embeddings, 2);
    let names: Vec<String> = store
        .all_symbols()
        .unwrap()
        .into_iter()
        .map(|s| s.name)
        .collect();
    assert!(names.contains(&"persist_me".to_string()));
}
