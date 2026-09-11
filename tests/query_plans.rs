//! Pins the query plans of every statement on the request path. The
//! persisted-lexical lookup, candidate hydration and meta reads must stay
//! index-driven; the embedding scan is a deliberate full scan. Checked
//! both without statistics (a fresh index) and after `ANALYZE` (what
//! `PRAGMA optimize` would persist), so that changed planner statistics —
//! from a future pragma, a rusqlite bump, or a hand-run `ANALYZE` — cannot
//! silently turn a rowid probe into a table scan.

use oxide::storage::{IndexBackend, SqliteStore};
use oxide::symbols::{content_hash, Language, Symbol, SymbolKind};

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

fn plan(store: &SqliteStore, sql: &str) -> String {
    store.explain_query_plan(sql).unwrap().join(" | ")
}

fn assert_plans(store: &SqliteStore, label: &str) {
    // `lexical_postings`: covering range scan on the (term, symbol_id)
    // primary key, then a rowid probe into lexical_docs.
    let lexical = plan(
        store,
        "SELECT p.symbol_id, p.tf, d.len
         FROM lexical_postings p JOIN lexical_docs d ON d.symbol_id = p.symbol_id
         WHERE p.term = 'alpha'",
    );
    assert!(
        lexical.contains("SEARCH p USING PRIMARY KEY (term=?)")
            && lexical.contains("SEARCH d USING INTEGER PRIMARY KEY (rowid=?)"),
        "{label}: lexical plan degraded: {lexical}"
    );
    assert!(
        !lexical.contains("SCAN"),
        "{label}: lexical plan scans: {lexical}"
    );

    // `symbols_by_ids`: json_each is the driving (virtual) table, symbols
    // is probed by rowid — never scanned.
    let hydrate = plan(
        store,
        "SELECT file FROM symbols WHERE id IN (SELECT value FROM json_each('[1,2]'))",
    );
    assert!(
        hydrate.contains("SEARCH symbols USING INTEGER PRIMARY KEY (rowid=?)"),
        "{label}: hydration plan degraded: {hydrate}"
    );
    assert!(
        !hydrate.contains("SCAN symbols"),
        "{label}: hydration scans symbols: {hydrate}"
    );

    // Meta reads: primary-key probe.
    let meta = plan(store, "SELECT value FROM meta WHERE key = 'root'");
    assert!(
        meta.contains("SEARCH meta USING INDEX sqlite_autoindex_meta_1 (key=?)"),
        "{label}: {meta}"
    );

    // Symbol count uses the smallest covering index, never the row data.
    let count = plan(store, "SELECT COUNT(*) FROM symbols");
    assert!(count.contains("USING COVERING INDEX"), "{label}: {count}");

    // The embedding scan is exhaustive by design (streaming top-K in
    // `RetrievalEngine::semantic_top_k`); pin that it is a plain table
    // scan rather than something the planner routes through an index.
    let scan = plan(store, "SELECT symbol_id, dim, vec FROM embeddings");
    assert_eq!(scan, "SCAN embeddings", "{label}");

    // Cascade paths: symbol delete must hit the covering secondary indexes.
    let cascade = plan(store, "DELETE FROM lexical_postings WHERE symbol_id = 1");
    assert!(
        cascade.contains("COVERING INDEX idx_lexical_postings_symbol"),
        "{label}: {cascade}"
    );
    let relations = plan(store, "DELETE FROM symbol_relations WHERE symbol_id = 1");
    assert!(
        relations.contains("COVERING INDEX idx_symbol_relations_symbol_id"),
        "{label}: {relations}"
    );
}

#[test]
fn request_path_plans_are_index_driven_with_and_without_statistics() {
    let tmp = tempfile::tempdir().unwrap();
    let mut store = SqliteStore::open(&tmp.path().join("index.db")).unwrap();
    // Enough rows that ANALYZE has real statistics to change its mind on.
    for f in 0..50 {
        let file = format!("m{f}.py");
        let syms: Vec<Symbol> = (0..20)
            .map(|i| sym(&file, &format!("fn_{f}_{i}")))
            .collect();
        let src: String = syms
            .iter()
            .map(|s| format!("{}\n  pass\n", s.signature))
            .collect();
        let postings = oxide::lexical::compute_file_postings(&syms, &src);
        store
            .replace_file(&file, f as u64, &syms, &[], &postings)
            .unwrap();
    }
    assert!(store.get_meta("root").unwrap().is_none());
    assert_plans(&store, "no statistics");
    store.analyze().unwrap();
    assert_plans(&store, "after ANALYZE");
}
