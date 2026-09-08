//! `$OXIDE_CONTEXT_MAX_PRIMARIES` overrides the shipped `CONTEXT_MAX_PRIMARIES`
//! allocator cap for the primary-cap sensitivity experiment
//! (docs/cpu-embedding-survey/primary-cap-sensitivity.md). Pins both halves of
//! the contract: unset is byte-identical to the frozen default, and a parseable
//! value replaces it.
//!
//! **Why this lives in `tests/` and not in `src/context.rs`'s unit tests** —
//! same reason `tests/debug_dump_kept.rs` does, and the same bug it was split
//! out to fix (commit f66a4d6). The variable is process-global, but
//! `src/context.rs` has a dozen other tests calling `build_context` in the same
//! binary, one of which (`primary_cap_bounds_semantic_tail`) asserts the exact
//! shipped cap. Setting the var from a lib unit test would intermittently
//! change what that test sees. Cargo gives each `tests/*.rs` file its own
//! process, so as the only test in this binary it owns the variable. Keep it
//! alone in this file — adding another test here that calls `build_context`
//! would reintroduce the race.

use oxide::context::{build_context, ContextOptions, Role};
use oxide::embeddings::{EmbeddingProvider, HashedEmbedder};
use oxide::index::{IndexBackend, SqliteStore};
use oxide::symbols::{content_hash, Language, Symbol, SymbolKind};

/// The frozen `config::CONTEXT_MAX_PRIMARIES`. Restated here because that
/// constant is `pub(crate)`; if it is ever re-baselined, this must move with it.
const SHIPPED_CAP: usize = 5;
const CANDIDATES: usize = SHIPPED_CAP + 3;

fn sym(file: &str, qname: &str, sig: &str) -> Symbol {
    Symbol {
        qualified_name: qname.into(),
        name: qname.into(),
        kind: SymbolKind::Function,
        language: Language::Python,
        file: file.into(),
        start_line: 1,
        end_line: 1,
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

fn primaries_in_pack(store: &SqliteStore, tmp: &std::path::Path) -> usize {
    build_context(
        tmp,
        store,
        &HashedEmbedder::default(),
        "widget",
        &ContextOptions {
            max_candidates: CANDIDATES,
            ..Default::default()
        },
    )
    .unwrap()
    .items
    .iter()
    .filter(|i| i.role == Role::Primary)
    .count()
}

#[test]
fn env_override_replaces_the_shipped_primary_cap() {
    let many: Vec<Symbol> = (0..CANDIDATES)
        .map(|i| {
            sym(
                &format!("src/m{i}.py"),
                &format!("widget_{i}"),
                &format!("def widget_{i}(): widget logic {i}"),
            )
        })
        .collect();
    let mut store = SqliteStore::open(std::path::Path::new(":memory:")).unwrap();
    let emb = HashedEmbedder::default();
    for s in &many {
        store
            .replace_file(&s.file, 1, std::slice::from_ref(s), &[])
            .unwrap();
        store
            .put_embedding(s.id(), &emb.embed(&oxide::embeddings::symbol_embed_text(s)))
            .unwrap();
    }
    let tmp = tempfile::tempdir().unwrap();

    assert_eq!(
        primaries_in_pack(&store, tmp.path()),
        SHIPPED_CAP,
        "unset must be the frozen default"
    );

    // SAFETY (test-only): this binary runs one test, so nothing else in this
    // process reads or writes this var concurrently.
    unsafe { std::env::set_var("OXIDE_CONTEXT_MAX_PRIMARIES", "7") };
    let raised = primaries_in_pack(&store, tmp.path());
    unsafe { std::env::set_var("OXIDE_CONTEXT_MAX_PRIMARIES", "not-a-number") };
    let unparseable = primaries_in_pack(&store, tmp.path());
    unsafe { std::env::remove_var("OXIDE_CONTEXT_MAX_PRIMARIES") };

    assert_eq!(raised, 7, "a parseable value must replace the cap");
    assert_eq!(
        unparseable, SHIPPED_CAP,
        "an unparseable value must fall back to the frozen default, not panic"
    );
}
