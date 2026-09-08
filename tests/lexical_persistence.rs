//! Persisted BM25 postings: score parity against the in-memory index, and
//! the generation/completeness invariant that keeps a partially built or
//! upgraded index from ever being scored as if it were current.
//!
//! The crash simulations here reach past `IndexBackend` and edit `index.db`
//! with raw SQL on purpose. A backfill killed halfway leaves exactly this
//! shape — some files covered, some not, every covered file's `content_hash`
//! already matching — and there is no public API that can produce it,
//! because no correct code path ever should.

use oxide::embeddings::HashedEmbedder;
use oxide::index::{update_index, IndexBackend, SqliteStore, LEXICAL_INDEX_KEY};
use oxide::lexical::LexicalIndex;
use oxide::retrieval::{RetrievalEngine, SearchMode, SearchOptions};
use std::collections::HashMap;
use std::path::{Path, PathBuf};

const QUERIES: &[&str] = &[
    "retry policy attempts",
    "connect timeout backoff",
    "parse user record",
    "handler",
    "handler handler retry",
    "totally absent gibberish zzz",
    "service compute value cache lookup",
];

fn repo(root: &Path) {
    let files: &[(&str, &str)] = &[
        (
            "src/retry.py",
            "import time\n\n\
             class RetryPolicy:\n\
             \x20   def should_retry(self, attempts):\n\
             \x20       return attempts < self.max_attempts\n\n\
             \x20   def backoff(self, attempts):\n\
             \x20       return 2 ** attempts\n",
        ),
        (
            "src/client.py",
            "from retry import RetryPolicy\n\n\
             def connect(host, timeout):\n\
             \x20   policy = RetryPolicy()\n\
             \x20   while policy.should_retry(0):\n\
             \x20       pass\n\
             \x20   return host\n",
        ),
        (
            "src/records.py",
            "def parse_user_record(line):\n\
             \x20   parts = line.split(',')\n\
             \x20   return {'user': parts[0]}\n\n\
             def handler(event):\n\
             \x20   return parse_user_record(event)\n",
        ),
        (
            "src/cache.py",
            "class Cache:\n\
             \x20   def lookup(self, key):\n\
             \x20       return self.store.get(key)\n\n\
             \x20   def compute_value(self, key):\n\
             \x20       return len(key)\n",
        ),
    ];
    for (p, body) in files {
        let path = root.join(p);
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(path, body).unwrap();
    }
}

fn db(root: &Path) -> PathBuf {
    root.join(".oxide/index.db")
}

fn indexed() -> (tempfile::TempDir, PathBuf) {
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path().to_path_buf();
    repo(&root);
    let mut store = SqliteStore::open(&db(&root)).unwrap();
    update_index(&root, &mut store, &HashedEmbedder::default()).unwrap();
    (tmp, root)
}

/// Raw connection to the index, for simulating a crash mid-backfill.
fn raw(root: &Path) -> rusqlite::Connection {
    rusqlite::Connection::open(db(root)).unwrap()
}

fn persisted_scores(root: &Path, query: &str) -> HashMap<u64, (f32, usize, f32)> {
    let store = SqliteStore::open_read_only(&db(root)).unwrap();
    let n = store.all_symbols().unwrap().len();
    let q = oxide::lexical::prepare_from_store(&store, n, query).unwrap();
    oxide::lexical::score(&q, 1.5, 0.75).0
}

fn memory_scores(root: &Path, query: &str) -> HashMap<u64, (f32, usize, f32)> {
    let store = SqliteStore::open_read_only(&db(root)).unwrap();
    let symbols = store.all_symbols().unwrap();
    LexicalIndex::build(&symbols, Some(root))
        .search(query, 1.5, 0.75)
        .0
}

/// Bit-exact, not merely rank-equivalent: the two paths share one scorer, so
/// any difference here is a difference in the postings themselves.
#[test]
fn persisted_postings_score_identically_to_the_in_memory_index() {
    let (_tmp, root) = indexed();
    for q in QUERIES {
        let mem = memory_scores(&root, q);
        let per = persisted_scores(&root, q);
        assert_eq!(
            mem.len(),
            per.len(),
            "query {q:?}: different number of scored documents"
        );
        for (id, m) in &mem {
            let p = per
                .get(id)
                .unwrap_or_else(|| panic!("query {q:?}: doc {id} missing from persisted scores"));
            assert_eq!(
                m.0.to_bits(),
                p.0.to_bits(),
                "query {q:?}: BM25 score differs for doc {id}: {} vs {}",
                m.0,
                p.0
            );
            assert_eq!(m.1, p.1, "query {q:?}: term coverage count differs");
            assert_eq!(
                m.2.to_bits(),
                p.2.to_bits(),
                "query {q:?}: coverage idf sum differs"
            );
        }
    }
}

/// Every symbol gets a `lexical_docs` row, including ones that contribute no
/// terms — otherwise BM25 length normalization silently substitutes the
/// corpus average for them.
#[test]
fn every_symbol_has_a_document_length_row() {
    let (_tmp, root) = indexed();
    let store = SqliteStore::open_read_only(&db(&root)).unwrap();
    let symbols = store.all_symbols().unwrap();
    let (docs, _) = store.lexical_totals().unwrap();
    assert_eq!(
        docs,
        symbols.len(),
        "lexical_docs must hold one row per symbol"
    );
}

#[test]
fn a_full_index_publishes_the_generation_key() {
    let (_tmp, root) = indexed();
    let store = SqliteStore::open_read_only(&db(&root)).unwrap();
    assert_eq!(
        store.get_meta(LEXICAL_INDEX_KEY).unwrap().as_deref(),
        Some(oxide::index::LEXICAL_INDEX_VERSION.to_string().as_str())
    );
}

/// An index built before the lexical tables existed: no key, no rows. The
/// next full run must backfill it and publish, without the caller doing
/// anything special.
#[test]
fn an_old_index_is_backfilled_on_the_next_run() {
    let (_tmp, root) = indexed();
    // Rewind to the pre-feature shape.
    {
        let c = raw(&root);
        c.execute("DELETE FROM lexical_postings", []).unwrap();
        c.execute("DELETE FROM lexical_docs", []).unwrap();
        c.execute("DELETE FROM meta WHERE key = ?1", [LEXICAL_INDEX_KEY])
            .unwrap();
    }
    {
        let store = SqliteStore::open_read_only(&db(&root)).unwrap();
        assert!(store.get_meta(LEXICAL_INDEX_KEY).unwrap().is_none());
        assert_eq!(store.lexical_totals().unwrap().0, 0);
    }

    let mut store = SqliteStore::open(&db(&root)).unwrap();
    let r = update_index(&root, &mut store, &HashedEmbedder::default()).unwrap();
    assert_eq!(
        r.reparsed_files, 0,
        "postings are derivable from stored symbols plus the source already \
         read this run, so the backfill must not reparse unchanged files"
    );
    drop(store);

    let store = SqliteStore::open_read_only(&db(&root)).unwrap();
    assert_eq!(
        store.lexical_totals().unwrap().0,
        store.all_symbols().unwrap().len()
    );
    assert!(store.get_meta(LEXICAL_INDEX_KEY).unwrap().is_some());
}

/// A backfill killed partway leaves some files covered and some not, and
/// every covered file's content_hash already matches — so nothing
/// incremental would ever revisit them. Readers must refuse the partial
/// tables outright, and the next run must repair them.
#[test]
fn an_interrupted_backfill_is_never_read_and_is_repaired() {
    let (_tmp, root) = indexed();
    let full = memory_scores(&root, "retry policy attempts");

    // Simulate the crash: postings for one file dropped, key never written.
    let dropped: Vec<u64> = {
        let store = SqliteStore::open_read_only(&db(&root)).unwrap();
        store
            .all_symbols()
            .unwrap()
            .iter()
            .filter(|s| s.file == "src/retry.py")
            .map(|s| s.id())
            .collect()
    };
    assert!(!dropped.is_empty());
    {
        let c = raw(&root);
        c.execute("DELETE FROM meta WHERE key = ?1", [LEXICAL_INDEX_KEY])
            .unwrap();
        for id in &dropped {
            c.execute(
                "DELETE FROM lexical_postings WHERE symbol_id = ?1",
                [*id as i64],
            )
            .unwrap();
            c.execute(
                "DELETE FROM lexical_docs WHERE symbol_id = ?1",
                [*id as i64],
            )
            .unwrap();
        }
    }

    // The tables exist and hold plenty of rows — table existence proves
    // nothing. Retrieval must still see the complete corpus, via fallback.
    let store = SqliteStore::open_read_only(&db(&root)).unwrap();
    assert!(store.lexical_totals().unwrap().0 > 0, "partial rows remain");
    let emb = HashedEmbedder::default();
    let engine = RetrievalEngine::new(&store, &emb);
    let hits = engine
        .search(
            "retry policy attempts",
            &SearchOptions {
                limit: 10,
                mode: SearchMode::LexicalOnly,
                ..Default::default()
            },
        )
        .unwrap();
    assert!(
        hits.iter().any(|h| h.symbol.file == "src/retry.py"),
        "a partially-backfilled index must fall back, not hide src/retry.py"
    );
    drop(store);

    // And the next run repairs it back to exact parity.
    let mut store = SqliteStore::open(&db(&root)).unwrap();
    update_index(&root, &mut store, &HashedEmbedder::default()).unwrap();
    drop(store);
    let repaired = persisted_scores(&root, "retry policy attempts");
    assert_eq!(repaired.len(), full.len());
    for (id, m) in &full {
        assert_eq!(m.0.to_bits(), repaired.get(id).unwrap().0.to_bits());
    }
}

/// Editing one file while the index is mid-migration must not publish a
/// half-built index, and editing one after migration must keep the published
/// index exactly consistent with a from-scratch build.
#[test]
fn single_file_edits_stay_consistent_during_and_after_migration() {
    let (_tmp, root) = indexed();

    // --- during: key cleared, then one file edited and indexed.
    {
        let c = raw(&root);
        c.execute("DELETE FROM lexical_postings", []).unwrap();
        c.execute("DELETE FROM lexical_docs", []).unwrap();
        c.execute("DELETE FROM meta WHERE key = ?1", [LEXICAL_INDEX_KEY])
            .unwrap();
    }
    std::fs::write(
        root.join("src/cache.py"),
        "class Cache:\n\
         \x20   def lookup(self, key):\n\
         \x20       return self.store.get(key)\n\n\
         \x20   def evict_entry(self, key):\n\
         \x20       return self.store.pop(key)\n",
    )
    .unwrap();
    let mut store = SqliteStore::open(&db(&root)).unwrap();
    update_index(&root, &mut store, &HashedEmbedder::default()).unwrap();
    drop(store);

    // The run completed, so it both migrated and absorbed the edit.
    let store = SqliteStore::open_read_only(&db(&root)).unwrap();
    assert!(store.get_meta(LEXICAL_INDEX_KEY).unwrap().is_some());
    assert_eq!(
        store.lexical_totals().unwrap().0,
        store.all_symbols().unwrap().len()
    );
    drop(store);
    for q in ["evict entry", "lookup key"] {
        let mem = memory_scores(&root, q);
        let per = persisted_scores(&root, q);
        assert_eq!(mem.len(), per.len(), "query {q:?} after mid-migration edit");
        for (id, m) in &mem {
            assert_eq!(m.0.to_bits(), per.get(id).unwrap().0.to_bits());
        }
    }

    // --- after: a further edit on an already-published index.
    std::fs::write(
        root.join("src/records.py"),
        "def parse_user_record(line):\n\
         \x20   return {'user': line}\n\n\
         def rename_marker_symbol(event):\n\
         \x20   return parse_user_record(event)\n",
    )
    .unwrap();
    let mut store = SqliteStore::open(&db(&root)).unwrap();
    update_index(&root, &mut store, &HashedEmbedder::default()).unwrap();
    drop(store);

    let store = SqliteStore::open_read_only(&db(&root)).unwrap();
    assert_eq!(
        store.lexical_totals().unwrap().0,
        store.all_symbols().unwrap().len(),
        "the removed symbol's document row must go, and the new one's must arrive"
    );
    drop(store);
    for q in ["rename marker symbol", "handler", "parse user record"] {
        let mem = memory_scores(&root, q);
        let per = persisted_scores(&root, q);
        assert_eq!(
            mem.len(),
            per.len(),
            "query {q:?} after post-migration edit"
        );
        for (id, m) in &mem {
            assert_eq!(
                m.0.to_bits(),
                per.get(id).unwrap().0.to_bits(),
                "query {q:?}: stale posting survived an edit"
            );
        }
    }
}

/// A generation the running binary does not recognize is as unusable as no
/// generation at all — the rows were written under different tokenizer or
/// weight rules.
#[test]
fn an_unrecognized_generation_is_rebuilt_not_trusted() {
    let (_tmp, root) = indexed();
    {
        let c = raw(&root);
        c.execute(
            "UPDATE meta SET value = '999' WHERE key = ?1",
            [LEXICAL_INDEX_KEY],
        )
        .unwrap();
        // Rows deliberately left in place and wrong.
        c.execute("DELETE FROM lexical_postings WHERE tf > 1", [])
            .unwrap();
    }
    let store = SqliteStore::open_read_only(&db(&root)).unwrap();
    let emb = HashedEmbedder::default();
    let engine = RetrievalEngine::new(&store, &emb);
    let hits = engine
        .search(
            "retry policy attempts",
            &SearchOptions {
                limit: 10,
                mode: SearchMode::LexicalOnly,
                ..Default::default()
            },
        )
        .unwrap();
    assert!(
        hits.iter().any(|h| h.symbol.file == "src/retry.py"),
        "an unknown generation must fall back to the in-memory build"
    );
    drop(store);

    let mut store = SqliteStore::open(&db(&root)).unwrap();
    update_index(&root, &mut store, &HashedEmbedder::default()).unwrap();
    drop(store);
    let store = SqliteStore::open_read_only(&db(&root)).unwrap();
    assert_eq!(
        store.get_meta(LEXICAL_INDEX_KEY).unwrap().as_deref(),
        Some(oxide::index::LEXICAL_INDEX_VERSION.to_string().as_str())
    );
}
