//! Integration tests for selective Markdown documentation indexing
//! (roadmap #9's "index developer comments and repository documentation").
//!
//! Unit-level correctness (parser fallback shape, incremental content_hash
//! behavior, structural-relations bypass) lives next to the code it tests
//! (`src/parser.rs`, `tests/embedding_staleness.rs`,
//! `src/structural_relations.rs`). This file covers the higher-level
//! properties: retrieval actually surfaces documentation content, and a
//! secret placed in an excluded location never reaches the index at all.

use oxide::embeddings::{symbol_embed_text, HashedEmbedder};
use oxide::index::update_index;
use oxide::retrieval::{RetrievalEngine, SearchMode, SearchOptions};
use oxide::storage::{IndexBackend, SqliteStore};
use oxide::symbols::SymbolKind;
use std::path::Path;

fn write(root: &Path, rel: &str, contents: &str) {
    let p = root.join(rel);
    std::fs::create_dir_all(p.parent().unwrap()).unwrap();
    std::fs::write(p, contents).unwrap();
}

/// A query matching vocabulary that exists ONLY in a README's prose (never
/// in any code file) must surface that README via hybrid search — proving
/// documentation content actually participates in retrieval, not just in
/// indexing bookkeeping.
#[test]
fn readme_prose_is_reachable_via_hybrid_search() {
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path();
    write(
        root,
        "src/retry.py",
        "class RetryPolicy:\n    def should_retry(self):\n        return True\n",
    );
    write(
        root,
        "README.md",
        "# Widget service\n\n\
         This service implements the circuit breaker pattern for network \
         resilience: after repeated upstream failures it stops sending \
         requests for a cooldown window before probing again.\n",
    );
    // Filler unrelated to the query, sharing no vocabulary with it or with
    // README.md: without these, the corpus has only 4 symbols total, and a
    // default limit of 10 would return every symbol regardless of
    // relevance — a query the engine ignored entirely would still pass.
    // With 9 filler symbols plus a `limit` below the total, README.md can
    // only appear by actually ranking above real competition.
    for i in 0..9 {
        write(
            root,
            &format!("src/filler_{i}.py"),
            &format!("def unrelated_helper_{i}():\n    return {i}\n"),
        );
    }

    let mut store = SqliteStore::open(&root.join(".oxide/index.db")).unwrap();
    let embedder = HashedEmbedder::default();
    update_index(root, &mut store, &embedder).unwrap();

    let engine = RetrievalEngine::new(&store, &embedder);
    let hits = engine
        .search(
            "circuit breaker cooldown window upstream failures",
            &SearchOptions {
                limit: 3,
                mode: SearchMode::Hybrid,
                expand: false,
                retrieval_mode: oxide::retrieval::RetrievalMode::default(),
            },
        )
        .unwrap();

    assert!(
        hits.iter().any(|h| h.symbol.file == "README.md"),
        "README.md must rank in the top {} of {} total symbols for a query \
         matching only its prose, against {} unrelated filler symbols: {:?}",
        3,
        13,
        9,
        hits.iter().map(|h| &h.symbol.file).collect::<Vec<_>>()
    );
}

/// A secret-shaped string in a gitignored `.md` file must never reach the
/// index at all — the file is never scanned in the first place, so it
/// structurally cannot leak into any search/query/review result.
#[test]
fn gitignored_markdown_secret_never_reaches_the_index() {
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path();
    write(root, "src/app.py", "def handler():\n    return True\n");
    write(
        root,
        "internal/SECRETS.md",
        "api_key=sk-live-super-secret-do-not-index-zzzzz\n",
    );
    write(root, ".gitignore", "/internal/\n");
    std::process::Command::new("git")
        .args(["init", "-q"])
        .current_dir(root)
        .status()
        .unwrap();

    let mut store = SqliteStore::open(&root.join(".oxide/index.db")).unwrap();
    let embedder = HashedEmbedder::default();
    update_index(root, &mut store, &embedder).unwrap();

    let symbols = store.all_symbols().unwrap();
    assert!(
        !symbols.iter().any(|s| s.file.contains("SECRETS")),
        "a gitignored markdown file must never become an indexed symbol: {:?}",
        symbols.iter().map(|s| &s.file).collect::<Vec<_>>()
    );

    let engine = RetrievalEngine::new(&store, &embedder);
    let hits = engine
        .search(
            "sk-live-super-secret-do-not-index-zzzzz",
            &SearchOptions {
                mode: SearchMode::Hybrid,
                expand: false,
                ..SearchOptions::default()
            },
        )
        .unwrap();
    // Not `hits.is_empty()`: a tiny corpus's dense semantic vectors give
    // every indexed symbol some nonzero score for any query, regardless of
    // relevance — that's expected HashedEmbedder behavior, not a leak. The
    // actual invariant is that no result *comes from* the excluded file,
    // which the `all_symbols` check above already establishes structurally
    // and this re-confirms end-to-end through the search API itself.
    assert!(
        !hits.iter().any(|h| h.symbol.file.contains("SECRETS")),
        "no search result may come from the gitignored secret file: {hits:?}"
    );
}

/// A tracked, legitimately-indexed document's `references` field is derived
/// from `known_names` — bare names of OTHER already-extracted, non-Module
/// symbols (`index.rs::extract_references`). A file that is never scanned
/// at all (the excluded secret) contributes zero symbols and therefore zero
/// names to `known_names`, so it structurally cannot become a `reference`
/// on anything else. This test proves that directly: a real, tracked
/// markdown doc that legitimately references a real Python symbol must
/// pick up that reference, while a co-existing gitignored secret file's
/// distinctive marker must never appear as a reference on ANY symbol in
/// the index, tracked or not.
#[test]
fn excluded_secret_never_becomes_a_reference_on_a_tracked_symbol() {
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path();
    write(
        root,
        "src/widget.py",
        "def render_widget():\n    return 1\n",
    );
    write(
        root,
        "README.md",
        "# Widget docs\n\nSee `render_widget` for details.\n",
    );
    write(
        root,
        "internal/SECRETS.md",
        "api_key=CONFIDENTIAL_MARKER_never_a_reference\n",
    );
    write(root, ".gitignore", "/internal/\n");
    std::process::Command::new("git")
        .args(["init", "-q"])
        .current_dir(root)
        .status()
        .unwrap();

    let mut store = SqliteStore::open(&root.join(".oxide/index.db")).unwrap();
    let embedder = HashedEmbedder::default();
    update_index(root, &mut store, &embedder).unwrap();

    let symbols = store.all_symbols().unwrap();
    let readme = symbols
        .iter()
        .find(|s| s.file == "README.md" && s.kind == SymbolKind::Module)
        .expect("README.md must be indexed");
    assert!(
        readme.references.contains(&"render_widget".to_string()),
        "the legitimate cross-reference must still work: {:?}",
        readme.references
    );
    assert!(
        !symbols
            .iter()
            .any(|s| s.references.iter().any(|r| r.contains("CONFIDENTIAL"))),
        "no symbol's references may ever contain anything derived from the \
         excluded secret file: {:?}",
        symbols
            .iter()
            .map(|s| (&s.file, &s.references))
            .collect::<Vec<_>>()
    );
}

/// A term that exists ONLY in a markdown file's body (never in its heading,
/// and never anywhere else in the corpus) must be findable via lexical
/// search — proving BM25's body-text indexing (`lexical.rs::body_slice`,
/// unmodified by this feature) actually covers whole-file documentation —
/// but must NOT make that file rank competitively via vector-only search,
/// because `symbol_embed_text` never includes body text (only
/// file/kind/qualified_name/signature/imports/references — true for every
/// language's module symbol, not unique to markdown). This is the concrete
/// evidence behind the documented "semantic embeddings reflect metadata
/// only, not full prose" limitation.
#[test]
fn body_only_term_is_lexically_findable_but_not_semantically_distinguishing() {
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path();
    // A generic heading (goes into `symbol_embed_text`'s signature field)
    // with a distinctive, invented body-only term (never in the heading,
    // never in any filler file) buried in a later paragraph.
    write(
        root,
        "README.md",
        "# Widget service\n\n\
         General overview paragraph with no distinctive vocabulary.\n\n\
         A later paragraph mentions the zorblatt subsystem in passing.\n",
    );
    // Filler sharing no vocabulary with "zorblatt" or the heading.
    for i in 0..10 {
        write(
            root,
            &format!("src/filler_{i}.py"),
            &format!("def unrelated_helper_{i}():\n    return {i}\n"),
        );
    }

    let mut store = SqliteStore::open(&root.join(".oxide/index.db")).unwrap();
    let embedder = HashedEmbedder::default();
    update_index(root, &mut store, &embedder).unwrap();
    let engine = RetrievalEngine::new(&store, &embedder);

    let lexical_hits = engine
        .search(
            "zorblatt",
            &SearchOptions {
                limit: 3,
                mode: SearchMode::LexicalOnly,
                expand: false,
                retrieval_mode: oxide::retrieval::RetrievalMode::default(),
            },
        )
        .unwrap();
    assert_eq!(
        lexical_hits.first().map(|h| h.symbol.file.as_str()),
        Some("README.md"),
        "a body-only term's high IDF (present in exactly one of 11 documents) \
         must rank README.md first under BM25: {:?}",
        lexical_hits
            .iter()
            .map(|h| &h.symbol.file)
            .collect::<Vec<_>>()
    );

    let vector_hits = engine
        .search(
            "zorblatt",
            &SearchOptions {
                limit: 3,
                mode: SearchMode::VectorOnly,
                expand: false,
                retrieval_mode: oxide::retrieval::RetrievalMode::default(),
            },
        )
        .unwrap();
    assert_ne!(
        vector_hits.first().map(|h| h.symbol.file.as_str()),
        Some("README.md"),
        "a term that appears only in body prose must NOT make README.md the \
         top vector-only result — symbol_embed_text never includes body \
         text, so semantic scoring has no signal from it at all: {:?}",
        vector_hits
            .iter()
            .map(|h| &h.symbol.file)
            .collect::<Vec<_>>()
    );
}

/// Privacy/payload boundary for remote embedding providers: whatever
/// `symbol_embed_text` returns for a symbol is the ONLY thing a configured
/// remote provider (Voyage/Jina/OpenAI-compatible) ever sends over the
/// network for it (`embeddings/remote.rs` embeds this string, never raw file
/// content). For a markdown file's module symbol, that string must never
/// contain body paragraph text — only path/kind/qualified_name/heading/
/// imports/references — so indexing documentation does not silently widen
/// what a remote provider receives beyond what code symbols already send.
#[test]
fn markdown_embed_text_never_contains_body_paragraph_text() {
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path();
    write(
        root,
        "docs/internal.md",
        "# Internal notes\n\n\
         This paragraph must never be sent to a remote embedding provider: \
         CONFIDENTIAL_MARKER_zzz.\n",
    );

    let mut store = SqliteStore::open(&root.join(".oxide/index.db")).unwrap();
    let embedder = HashedEmbedder::default();
    update_index(root, &mut store, &embedder).unwrap();

    let symbols = store.all_symbols().unwrap();
    let doc = symbols
        .iter()
        .find(|s| s.file == "docs/internal.md" && s.kind == SymbolKind::Module)
        .expect("docs/internal.md must be indexed");
    let payload = symbol_embed_text(doc);

    assert!(
        !payload.contains("CONFIDENTIAL_MARKER_zzz"),
        "symbol_embed_text (the exact remote-provider payload) must not \
         contain body paragraph text: {payload:?}"
    );
    assert!(
        payload.contains("Internal notes"),
        "the heading IS expected in the payload (it's the signature field): {payload:?}"
    );
}
