//! Roadmap #9's "index developer comments" audited before writing any new
//! indexing code: `parser.rs::parse_file_with` already adds a whole-file
//! module-fallback symbol (`start_line: 1, end_line: src.lines().count()`)
//! for EVERY file in EVERY language, unconditionally — not only comment-only
//! ones — and `lexical.rs::body_slice` slices that span verbatim into the
//! BM25 index at body weight. A standalone comment between two real
//! declarations therefore already rides into the lexical index as a
//! byproduct of a mechanism that predates this session, with no new code
//! required. This file pins that property with regression tests (nothing
//! here existed to prove it before), a retrieval-quality gate against
//! distractor content, and the "pre-embedding secret filtering" boundary
//! that follows directly from `symbol_embed_text` already being
//! metadata-only: the local lexical index may contain whatever a tracked
//! file contains (by design — OXIDE surfaces real repository content, it
//! is not a secret scanner), but the payload sent to a configured *remote*
//! embedding provider never includes body or comment text, for any symbol
//! in any language.

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

/// A standalone comment between two functions — not a docstring inside
/// either one, not the first line of the file — is findable via lexical
/// search through the whole-file module-fallback symbol, with enough
/// unrelated filler in the corpus that this can't pass by accident.
#[test]
fn standalone_comment_between_symbols_is_lexically_findable() {
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path();
    write(
        root,
        "src/app.py",
        "def helper():\n    return 1\n\n\
         # zorbflarg: this design note lives between two functions and\n\
         # names no identifier defined anywhere in this file.\n\n\
         def other():\n    return 2\n",
    );
    for i in 0..10 {
        write(
            root,
            &format!("src/filler_{i}.py"),
            &format!("def unrelated_{i}():\n    return {i}\n"),
        );
    }

    let mut store = SqliteStore::open(&root.join(".oxide/index.db")).unwrap();
    let embedder = HashedEmbedder::default();
    update_index(root, &mut store, &embedder).unwrap();

    let engine = RetrievalEngine::new(&store, &embedder);
    let hits = engine
        .search(
            "zorbflarg",
            &SearchOptions {
                limit: 3,
                mode: SearchMode::LexicalOnly,
                expand: false,
                retrieval_mode: oxide::retrieval::RetrievalMode::default(),
            },
        )
        .unwrap();

    assert_eq!(
        hits.first().map(|h| h.symbol.file.as_str()),
        Some("src/app.py"),
        "a comment-only term must rank the file containing it first among \
         10 unrelated filler files: {:?}",
        hits.iter().map(|h| &h.symbol.file).collect::<Vec<_>>()
    );
    // `RetrievalEngine::search` itself always leaves `snippet` empty
    // (populated later, by `service.rs`, from the matched span) — the
    // ranking assertion above is the actual proof the comment was found.
    assert_eq!(hits[0].symbol.kind, SymbolKind::Module);
}

/// `symbol_embed_text` — the exact string a configured remote embedding
/// provider receives — is file/kind/qualified_name/signature/imports/
/// references only, for a REAL CODE symbol (not the markdown-specific case
/// already covered by `tests/markdown_indexing.rs`). A comment *after* the
/// file's first non-empty line must never appear in it, even though that
/// same comment IS part of the symbol's local lexical body (a different,
/// never-leaves-the-machine index) and even though it IS part of the
/// module-fallback symbol's lexical body slice (see the test above). This
/// is NOT true for a *leading* comment (the file's very first non-empty
/// line) — see the test below for that narrower, real exception; a fixture
/// starting with a declaration is what proves this test's claim actually
/// holds for the common case, not an oversight (an earlier version of this
/// file only had this test, and its README prose overclaimed the boundary
/// as absolute — found by review).
#[test]
fn code_comment_text_never_reaches_the_remote_embedding_payload() {
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path();
    write(
        root,
        "src/service.py",
        "class PaymentService:\n    \
         # NOTE: staging key only, never production: sk_test_CONFIDENTIAL_zzz\n    \
         def charge(self):\n        return True\n",
    );

    let mut store = SqliteStore::open(&root.join(".oxide/index.db")).unwrap();
    let embedder = HashedEmbedder::default();
    update_index(root, &mut store, &embedder).unwrap();

    let symbols = store.all_symbols().unwrap();
    for s in &symbols {
        let payload = symbol_embed_text(s);
        assert!(
            !payload.contains("CONFIDENTIAL"),
            "symbol {:?}'s remote-provider payload must never contain body \
             comment text: {payload:?}",
            s.qualified_name
        );
    }
    // Sanity: the class was actually indexed with more than zero symbols,
    // so the loop above wasn't vacuously trivial.
    assert!(symbols.iter().any(|s| s.kind == SymbolKind::Class));

    // Contrast: the SAME comment legitimately lives in the local lexical
    // index (module-fallback whole-file body slice) — that's correct,
    // not a leak, since the lexical index never leaves the machine.
    let engine = RetrievalEngine::new(&store, &embedder);
    let hits = engine
        .search(
            "CONFIDENTIAL",
            &SearchOptions {
                mode: SearchMode::LexicalOnly,
                expand: false,
                ..SearchOptions::default()
            },
        )
        .unwrap();
    assert!(
        hits.iter().any(|h| h.symbol.file == "src/service.py"),
        "a tracked file's own comment is legitimately locally searchable \
         (this is not the boundary being tested — symbol_embed_text is): {hits:?}"
    );
}

/// The real, narrow exception to the test above: the module-fallback
/// symbol's `signature` is unconditionally "the file's first non-empty
/// line" (`parser.rs::parse_file_with`), whatever that line is — so a
/// comment that opens the file (a license header, a leading `# TODO`) IS
/// metadata, not body text, and DOES reach `symbol_embed_text`. This is a
/// real, pre-existing property (the same field a leading declaration line
/// would populate identically), not a new bug — but a README claiming
/// "comments never reach a remote provider" without this exception is
/// simply false for this one case, which is why it gets its own test
/// rather than living only in prose (found by review: the previous test's
/// fixture began with a class declaration and could not have caught this).
#[test]
fn a_leading_comment_is_the_one_real_exception_and_does_reach_the_payload() {
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path();
    write(
        root,
        "src/legacy.py",
        "# LEADING_MARKER_zzz: this line opens the file\n\
         def handler():\n    return True\n",
    );

    let mut store = SqliteStore::open(&root.join(".oxide/index.db")).unwrap();
    let embedder = HashedEmbedder::default();
    update_index(root, &mut store, &embedder).unwrap();

    let symbols = store.all_symbols().unwrap();
    let module = symbols
        .iter()
        .find(|s| s.file == "src/legacy.py" && s.kind == SymbolKind::Module)
        .expect("module fallback symbol must exist");
    assert_eq!(
        module.signature, "# LEADING_MARKER_zzz: this line opens the file",
        "the module symbol's signature is unconditionally the file's first \
         non-empty line, comment or not"
    );
    assert!(
        symbol_embed_text(module).contains("LEADING_MARKER_zzz"),
        "a leading comment IS part of the remote-provider payload via \
         `signature` — this is the real exception, not a leak: {:?}",
        symbol_embed_text(module)
    );
}
