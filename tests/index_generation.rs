//! `index_generation`/`index_id` (storage.rs): the cache key a long-running
//! process uses to decide whether a loaded symbol snapshot still describes
//! the database. Every write path must advance the counter, and the id must
//! change when the database is rebuilt, or a cached snapshot could outlive
//! the content it was built from.

use oxide::embeddings::{EmbeddingProvider, HashedEmbedder};
use oxide::storage::{IndexBackend, SqliteStore, INDEX_GENERATION_KEY, INDEX_ID_KEY};
use oxide::symbols::{content_hash, Language, Symbol, SymbolKind};
use std::path::Path;

fn sym(file: &str, name: &str) -> Symbol {
    Symbol {
        qualified_name: name.into(),
        name: name.into(),
        kind: SymbolKind::Function,
        language: Language::Python,
        file: file.into(),
        start_line: 1,
        end_line: 2,
        content_hash: content_hash(name),
        signature: format!("def {name}():"),
        imports: vec![],
        exported: true,
        parent: None,
        references: vec![],
        calls: vec![],
        bases: vec![],
    }
}

fn generation(store: &SqliteStore) -> u64 {
    store.generation().unwrap().expect("keys present").1
}

#[test]
fn every_write_path_advances_the_generation_exactly_once() {
    let tmp = tempfile::tempdir().unwrap();
    let mut store = SqliteStore::open(&tmp.path().join("index.db")).unwrap();
    let s = sym("a.py", "alpha");
    let postings = || {
        oxide::lexical::compute_file_postings(std::slice::from_ref(&s), "def alpha():\n  pass\n")
    };
    let emb = HashedEmbedder::default();

    // Fresh database: keyable from the first writer open, at generation 0.
    assert!(store.get_meta(INDEX_ID_KEY).unwrap().is_some());
    assert_eq!(
        store.get_meta(INDEX_GENERATION_KEY).unwrap().as_deref(),
        Some("0")
    );
    assert_eq!(generation(&store), 0);

    type Step = Box<dyn Fn(&mut SqliteStore)>;
    let steps: Vec<(&str, Step)> = vec![
        (
            "replace_file",
            Box::new({
                let s = s.clone();
                let postings = postings();
                move |st: &mut SqliteStore| {
                    st.replace_file("a.py", 1, std::slice::from_ref(&s), &[], &postings)
                        .unwrap()
                }
            }),
        ),
        (
            "put_symbol_relations_batch",
            Box::new({
                let id = s.id();
                move |st: &mut SqliteStore| {
                    st.put_symbol_relations_batch(&[(id, vec!["beta".into()], vec![])])
                        .unwrap()
                }
            }),
        ),
        (
            "put_file_lexical",
            Box::new({
                let postings = postings();
                move |st: &mut SqliteStore| {
                    assert!(st.put_file_lexical("a.py", 1, &postings).unwrap())
                }
            }),
        ),
        (
            "put_embedding",
            Box::new({
                let id = s.id();
                let v = emb.embed("alpha");
                move |st: &mut SqliteStore| st.put_embedding(id, &v).unwrap()
            }),
        ),
        (
            "put_embeddings_batch",
            Box::new({
                let id = s.id();
                let v = emb.embed("alpha again");
                move |st: &mut SqliteStore| st.put_embeddings_batch("", &[(id, v.clone())]).unwrap()
            }),
        ),
        (
            "set_meta",
            Box::new(|st: &mut SqliteStore| st.set_meta("root", "/x").unwrap()),
        ),
        (
            "set_meta_all",
            Box::new(|st: &mut SqliteStore| st.set_meta_all("", &[("dim", "1")]).unwrap()),
        ),
        (
            "begin_embedding_migration",
            Box::new(|st: &mut SqliteStore| st.begin_embedding_migration("{}").unwrap()),
        ),
        (
            "remove_files",
            Box::new(|st: &mut SqliteStore| st.remove_files(&["a.py".to_string()]).unwrap()),
        ),
    ];
    let mut expected = 0u64;
    for (name, step) in steps {
        step(&mut store);
        expected += 1;
        assert_eq!(
            generation(&store),
            expected,
            "{name} must bump index_generation by exactly one"
        );
    }
}

#[test]
fn reads_never_advance_the_generation() {
    let tmp = tempfile::tempdir().unwrap();
    let path = tmp.path().join("index.db");
    let mut store = SqliteStore::open(&path).unwrap();
    let s = sym("a.py", "alpha");
    store
        .replace_file("a.py", 1, std::slice::from_ref(&s), &[], &[])
        .unwrap();
    let before = generation(&store);
    let ro = SqliteStore::open_read_only(&path).unwrap();
    let _ = ro.all_symbols().unwrap();
    let _ = ro.symbols_by_ids(&[s.id()]).unwrap();
    let _ = ro.symbol_count().unwrap();
    ro.for_each_embedding(&mut |_, _, _| {}).unwrap();
    let _ = ro.lexical_postings("alpha").unwrap();
    assert_eq!(generation(&ro), before);
    drop(ro);
    // A second writer open of the same database keeps the identity.
    let id_before = store.get_meta(INDEX_ID_KEY).unwrap();
    drop(store);
    let again = SqliteStore::open(&path).unwrap();
    assert_eq!(again.get_meta(INDEX_ID_KEY).unwrap(), id_before);
    assert_eq!(generation(&again), before);
}

#[test]
fn a_rebuilt_database_gets_a_new_identity_even_at_the_same_generation() {
    let tmp = tempfile::tempdir().unwrap();
    let path = tmp.path().join("index.db");
    let s = sym("a.py", "alpha");
    let first = {
        let mut store = SqliteStore::open(&path).unwrap();
        store
            .replace_file("a.py", 1, std::slice::from_ref(&s), &[], &[])
            .unwrap();
        store.generation().unwrap().unwrap()
    };
    std::fs::remove_file(&path).unwrap();
    let _ = std::fs::remove_file(tmp.path().join("index.db-wal"));
    let _ = std::fs::remove_file(tmp.path().join("index.db-shm"));
    let second = {
        let mut store = SqliteStore::open(&path).unwrap();
        store
            .replace_file("a.py", 1, std::slice::from_ref(&s), &[], &[])
            .unwrap();
        store.generation().unwrap().unwrap()
    };
    assert_eq!(first.1, second.1, "same write sequence, same generation");
    assert_ne!(first.0, second.0, "different database, different id");
}

#[test]
fn an_index_that_predates_the_counter_is_not_keyable_until_a_writer_touches_it() {
    let tmp = tempfile::tempdir().unwrap();
    let path = tmp.path().join("index.db");
    {
        let mut store = SqliteStore::open(&path).unwrap();
        store
            .replace_file("a.py", 1, &[sym("a.py", "alpha")], &[], &[])
            .unwrap();
        // Simulate a database written before these keys existed.
        let conn = rusqlite::Connection::open(&path).unwrap();
        conn.execute(
            "DELETE FROM meta WHERE key IN (?1, ?2)",
            [INDEX_ID_KEY, INDEX_GENERATION_KEY],
        )
        .unwrap();
    }
    let ro = SqliteStore::open_read_only(&path).unwrap();
    assert!(
        ro.generation().unwrap().is_none(),
        "no key ⇒ nothing may be cached"
    );
    drop(ro);
    let mut w = SqliteStore::open(&path).unwrap();
    assert!(
        w.get_meta(INDEX_ID_KEY).unwrap().is_some(),
        "writer open restores the id"
    );
    assert_eq!(
        generation(&w),
        0,
        "…and makes the index keyable at generation 0"
    );
    w.set_meta("root", Path::new("/x").to_str().unwrap())
        .unwrap();
    assert_eq!(generation(&w), 1);
}
