//! `OXIDE_DEBUG_DUMP_KEPT` writes the pre-allocation candidate pool (`kept`)
//! to a file as JSON — useful for any evaluation/debugging that needs the real
//! candidates feeding role/floor/budget allocation, since neither a huge token
//! budget nor `items ∪ omitted` in the final pack is faithful to it (both still
//! lose members to the budget-independent per-file/role diversity caps). Used
//! by the (rejected) local-reranker experiment, docs/reranker-eval/README.md,
//! but has no dependency on reranking — it stays as a general candidate-pool
//! diagnostic. Pins that the dump fires whenever the env var is set, and stays
//! silent otherwise.
//!
//! **Why this lives in `tests/` and not in `src/context.rs`'s unit tests**,
//! where it started: `OXIDE_DEBUG_DUMP_KEPT` is process-global, but the dump
//! target it names is a single file. Any *other* test calling `build_context`
//! in the same process while this test has the var set clobbers that file with
//! its own pool (`std::fs::write`, not append), so the assertion below would
//! see a foreign pool and fail — `src/context.rs` has 13 other tests calling
//! `build_context` in the same binary, and the failure was intermittent
//! (~50% once unrelated tests shifted lib-test scheduling). Cargo gives each
//! `tests/*.rs` file its own process, so as the only test in this binary it
//! owns the variable and the race is gone by construction rather than by
//! timing luck. Keep it alone in this file — adding another test here that
//! calls `build_context` would reintroduce the race.

use oxide::context::{build_context, ContextOptions};
use oxide::embeddings::{EmbeddingProvider, HashedEmbedder};
use oxide::index::{IndexBackend, SqliteStore};
use oxide::symbols::{content_hash, Language, Symbol, SymbolKind};

fn sym(file: &str, qname: &str, kind: SymbolKind, sig: &str) -> Symbol {
    let name = qname.rsplit('.').next().unwrap().to_string();
    Symbol {
        qualified_name: qname.into(),
        name,
        kind,
        language: Language::Python,
        file: file.into(),
        start_line: 1,
        end_line: sig.lines().count() as u32,
        content_hash: content_hash(sig),
        signature: sig.into(),
        imports: vec![],
        exported: true,
        parent: None,
        references: vec![],
        calls: Vec::new(),
        bases: Vec::new(),
    }
}

fn seed(file: &str, syms: &[Symbol]) -> SqliteStore {
    let mut store = SqliteStore::open(std::path::Path::new(":memory:")).unwrap();
    store.replace_file(file, 1, syms, &[]).unwrap();
    let emb = HashedEmbedder::default();
    for s in syms {
        store
            .put_embedding(s.id(), &emb.embed(&oxide::index::embed_text(s)))
            .unwrap();
    }
    store
}

#[test]
fn debug_dump_kept_writes_exactly_when_env_var_set() {
    let store = seed(
        "src/a.py",
        &[sym(
            "src/a.py",
            "widget",
            SymbolKind::Function,
            "def widget(): pass",
        )],
    );
    let tmp = tempfile::tempdir().unwrap();
    let dump = tempfile::NamedTempFile::new().unwrap();
    std::fs::remove_file(dump.path()).ok(); // exists() must reflect build_context, not setup

    let pack_without = build_context(
        tmp.path(),
        &store,
        &HashedEmbedder::default(),
        "widget",
        &ContextOptions::default(),
    )
    .unwrap();
    assert!(!pack_without.items.is_empty());
    assert!(!dump.path().exists(), "no dump without the env var");

    // SAFETY (test-only): this binary runs one test, so nothing else in this
    // process reads or writes this var concurrently.
    unsafe { std::env::set_var("OXIDE_DEBUG_DUMP_KEPT", dump.path()) };
    build_context(
        tmp.path(),
        &store,
        &HashedEmbedder::default(),
        "widget",
        &ContextOptions::default(),
    )
    .unwrap();
    unsafe { std::env::remove_var("OXIDE_DEBUG_DUMP_KEPT") };

    let dumped: Vec<serde_json::Value> =
        serde_json::from_str(&std::fs::read_to_string(dump.path()).unwrap()).unwrap();
    assert_eq!(dumped.len(), 1);
    assert_eq!(dumped[0]["symbol"]["qualified_name"], "widget");
}
