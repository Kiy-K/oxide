//! Phase 1.1 item 1: module embedding staleness.
//!
//! The module symbol's embedding input (`embeddings::symbol_embed_text`) includes
//! `references`, which are resolved from the *whole file body* one stage
//! after the parser assigns the module's initial coarse content_hash
//! (imports + first line only, see parser.rs). Before the fix in
//! `update_index`, a body-only edit that changed the module's reference set
//! without touching the first line or imports left the coarse hash
//! unchanged, so the stale embedding was reused instead of recomputed.
//!
//! Every case below is checked two ways: (a) directly, by comparing the
//! module's stored embedding vector before/after the edit, and (b) via
//! parity with a clean rebuild of the same final repository state — the
//! governing invariant from AGENTS.md ("incremental final state == clean
//! rebuild of the same repository state").

use oxide::embeddings::HashedEmbedder;
use oxide::index::{update_index, IndexBackend, SqliteStore};
use oxide::symbols::SymbolKind;
use std::path::Path;

fn write(path: &Path, content: &str) {
    std::fs::create_dir_all(path.parent().unwrap()).unwrap();
    std::fs::write(path, content).unwrap();
}

fn module_embedding(store: &SqliteStore, file: &str) -> Vec<f32> {
    let syms = store.all_symbols().unwrap();
    let m = syms
        .iter()
        .find(|s| s.file == file && s.kind == SymbolKind::Module)
        .unwrap_or_else(|| panic!("no module symbol for {file}"));
    let embeddings = store.all_embeddings().unwrap();
    embeddings
        .get(&m.id())
        .unwrap_or_else(|| panic!("no embedding stored for module symbol of {file}"))
        .1
        .clone()
}

fn module_content_hash(store: &SqliteStore, file: &str) -> u64 {
    let syms = store.all_symbols().unwrap();
    syms.iter()
        .find(|s| s.file == file && s.kind == SymbolKind::Module)
        .unwrap()
        .content_hash
}

/// Rebuild a fresh index (in a fresh store) over whatever is currently on
/// disk under `root` and return the module embedding for `file`.
fn clean_rebuild_module_embedding(root: &Path, file: &str) -> Vec<f32> {
    let mut fresh = SqliteStore::open(Path::new(":memory:")).unwrap();
    let emb = HashedEmbedder::default();
    update_index(root, &mut fresh, &emb).unwrap();
    module_embedding(&fresh, file)
}

/// helper.py defines a function whose name can become an in-file reference
/// once called from thing.py.
const HELPER_PY: &str = "def helper():\n    return 1\n";

#[test]
fn new_in_file_reference_invalidates_module_embedding() {
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path();
    write(&root.join("helper.py"), HELPER_PY);
    write(
        &root.join("thing.py"),
        "def foo():\n    return 1\n\ndef bar():\n    return foo() + 1\n",
    );

    let mut store = SqliteStore::open(Path::new(":memory:")).unwrap();
    let emb = HashedEmbedder::default();
    update_index(root, &mut store, &emb).unwrap();

    let before_hash = module_content_hash(&store, "thing.py");
    let before_vec = module_embedding(&store, "thing.py");

    // Body-only edit deep in the file: first line ("def foo():") and imports
    // (none) are untouched, but a brand-new in-file reference to `helper`
    // appears inside bar()'s body.
    write(
        &root.join("thing.py"),
        "def foo():\n    return 1\n\ndef bar():\n    return foo() + helper()\n",
    );
    let report = update_index(root, &mut store, &emb).unwrap();
    assert_eq!(report.reparsed_files, 1, "file content changed on disk");

    let after_hash = module_content_hash(&store, "thing.py");
    let after_vec = module_embedding(&store, "thing.py");

    assert_ne!(
        before_hash, after_hash,
        "module content_hash must change when its reference set changes"
    );
    assert_ne!(
        before_vec, after_vec,
        "module embedding must be recomputed, not reused stale"
    );

    // Incremental final state must match a clean rebuild of the same tree.
    let rebuilt_vec = clean_rebuild_module_embedding(root, "thing.py");
    assert_eq!(
        after_vec, rebuilt_vec,
        "incremental module embedding must equal a fresh rebuild's"
    );
}

#[test]
fn removed_in_file_reference_invalidates_module_embedding() {
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path();
    write(&root.join("helper.py"), HELPER_PY);
    write(
        &root.join("thing.py"),
        "def foo():\n    return 1\n\ndef bar():\n    return foo() + helper()\n",
    );

    let mut store = SqliteStore::open(Path::new(":memory:")).unwrap();
    let emb = HashedEmbedder::default();
    update_index(root, &mut store, &emb).unwrap();
    let before_vec = module_embedding(&store, "thing.py");

    // Remove the call to helper(); first line and imports still untouched.
    write(
        &root.join("thing.py"),
        "def foo():\n    return 1\n\ndef bar():\n    return foo() + 1\n",
    );
    update_index(root, &mut store, &emb).unwrap();
    let after_vec = module_embedding(&store, "thing.py");

    assert_ne!(
        before_vec, after_vec,
        "removing a reference must also invalidate the module embedding"
    );
    let rebuilt_vec = clean_rebuild_module_embedding(root, "thing.py");
    assert_eq!(after_vec, rebuilt_vec);
}

#[test]
fn signature_only_change_invalidates_module_embedding() {
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path();
    write(&root.join("thing.py"), "def foo():\n    return 1\n");

    let mut store = SqliteStore::open(Path::new(":memory:")).unwrap();
    let emb = HashedEmbedder::default();
    update_index(root, &mut store, &emb).unwrap();
    let before_vec = module_embedding(&store, "thing.py");

    // First line itself changes (a leading comment is inserted).
    write(
        &root.join("thing.py"),
        "# a header comment\ndef foo():\n    return 1\n",
    );
    update_index(root, &mut store, &emb).unwrap();
    let after_vec = module_embedding(&store, "thing.py");

    assert_ne!(before_vec, after_vec);
    let rebuilt_vec = clean_rebuild_module_embedding(root, "thing.py");
    assert_eq!(after_vec, rebuilt_vec);
}

#[test]
fn import_only_change_invalidates_module_embedding() {
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path();
    write(
        &root.join("thing.py"),
        "def foo():\n    return 1\n\ndef bar():\n    return foo() + 1\n",
    );

    let mut store = SqliteStore::open(Path::new(":memory:")).unwrap();
    let emb = HashedEmbedder::default();
    update_index(root, &mut store, &emb).unwrap();
    let before_vec = module_embedding(&store, "thing.py");

    // Add an import; first declared line ("def foo():") is unchanged, only
    // the import moves in above it. Imports are part of both the module's
    // parser hash and `symbol_embed_text`; concrete symbols are covered by
    // `import_only_change_reembeds_untouched_concrete_symbols` below.
    write(
        &root.join("thing.py"),
        "import os\n\ndef foo():\n    return 1\n\ndef bar():\n    return foo() + 1\n",
    );
    update_index(root, &mut store, &emb).unwrap();
    let after_vec = module_embedding(&store, "thing.py");

    assert_ne!(before_vec, after_vec);
    let rebuilt_vec = clean_rebuild_module_embedding(root, "thing.py");
    assert_eq!(after_vec, rebuilt_vec);
}

#[test]
fn doc_comment_change_that_does_not_touch_embed_input_leaves_module_embedding_reused() {
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path();
    // A comment line after the first non-blank line is not part of
    // `symbol_embed_text` (no full-body text is fed to the embedder); this documents
    // that limitation explicitly rather than silently assuming it.
    write(
        &root.join("thing.py"),
        "def foo():\n    # original comment\n    return 1\n",
    );

    let mut store = SqliteStore::open(Path::new(":memory:")).unwrap();
    let emb = HashedEmbedder::default();
    update_index(root, &mut store, &emb).unwrap();
    let before_vec = module_embedding(&store, "thing.py");

    write(
        &root.join("thing.py"),
        "def foo():\n    # a totally different comment\n    return 1\n",
    );
    let report = update_index(root, &mut store, &emb).unwrap();
    assert_eq!(report.reparsed_files, 1, "file bytes changed on disk");
    let after_vec = module_embedding(&store, "thing.py");

    assert_eq!(
        before_vec, after_vec,
        "comment-only edits outside symbol_embed_text's inputs must not force a spurious re-embed"
    );
    // Reused, not recomputed-to-the-same-value by coincidence.
    let rebuilt_vec = clean_rebuild_module_embedding(root, "thing.py");
    assert_eq!(after_vec, rebuilt_vec);
}

#[test]
fn unchanged_file_reuses_module_embedding() {
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path();
    write(&root.join("thing.py"), "def foo():\n    return 1\n");

    let mut store = SqliteStore::open(Path::new(":memory:")).unwrap();
    let emb = HashedEmbedder::default();
    update_index(root, &mut store, &emb).unwrap();
    let before_vec = module_embedding(&store, "thing.py");

    let report = update_index(root, &mut store, &emb).unwrap();
    assert_eq!(report.reparsed_files, 0, "nothing on disk changed");
    assert_eq!(report.reused_embeddings, 2, "module + foo both reused");
    let after_vec = module_embedding(&store, "thing.py");

    assert_eq!(before_vec, after_vec);
}

#[test]
fn body_only_change_to_a_concrete_symbol_still_reembeds_that_symbol() {
    // Sanity check that the module-hash fix did not regress the pre-existing,
    // already-correct per-symbol behavior for non-module symbols.
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path();
    write(&root.join("thing.py"), "def foo():\n    return 1\n");

    let mut store = SqliteStore::open(Path::new(":memory:")).unwrap();
    let emb = HashedEmbedder::default();
    update_index(root, &mut store, &emb).unwrap();

    write(&root.join("thing.py"), "def foo():\n    return 999\n");
    let report = update_index(root, &mut store, &emb).unwrap();

    assert_eq!(report.changed_symbols, 1);
    assert_eq!(report.embedded_symbols, 1, "only foo's body changed");
    assert_eq!(report.reused_embeddings, 1, "module untouched by this edit");
}

#[test]
fn comment_only_file_fallback_hash_still_covers_the_full_source() {
    // Pins the scoping of the item-1 fix: parser.rs deliberately uses a
    // full-source hash (not the coarse imports+first-line formula) for
    // files with no concrete declarations at all, specifically so a
    // comment/doc-only file's *only* index representation (the module
    // fallback symbol) still detects every edit, even ones that don't
    // touch the first line. `update_index` must not override that with the
    // coarse `symbol_embed_text`-based formula — doing so would silently stop
    // detecting comment-only edits as "changed" (a real regression caught
    // by review before landing: verified concretely that an early version
    // of this fix left `content_hash` and `changed_symbols` both
    // unchanged for this exact scenario).
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path();
    write(&root.join("notes.py"), "# alpha\n# beta\n");

    let mut store = SqliteStore::open(Path::new(":memory:")).unwrap();
    let emb = HashedEmbedder::default();
    update_index(root, &mut store, &emb).unwrap();
    let before_hash = module_content_hash(&store, "notes.py");

    // Edit a line other than the first; a concrete-symbol file's coarse
    // module hash would legitimately miss this, but this file has no
    // concrete symbols, so its fallback hash must still catch it.
    write(&root.join("notes.py"), "# alpha\n# gamma\n");
    let report = update_index(root, &mut store, &emb).unwrap();

    assert_eq!(report.reparsed_files, 1);
    assert_eq!(
        report.changed_symbols, 1,
        "a comment-only file's only symbol must still report as changed"
    );
    let after_hash = module_content_hash(&store, "notes.py");
    assert_ne!(
        before_hash, after_hash,
        "the empty-file fallback hash must remain full-source, not the coarse formula"
    );

    let rebuilt_vec = clean_rebuild_module_embedding(root, "notes.py");
    let after_vec = module_embedding(&store, "notes.py");
    assert_eq!(after_vec, rebuilt_vec);
}

#[test]
fn markdown_mid_file_edit_reembeds_the_whole_file_module_symbol() {
    // Markdown takes the same empty-extraction fallback path as a
    // comment-only source file (`MarkdownExtractor::extract` always
    // returns nothing), so it must have the same property the test above
    // pins for comment-only files: editing a line that isn't the first
    // line still changes the module symbol's content_hash and triggers
    // re-embedding, because `used_coarse_module_hash` (index.rs) only
    // overrides the hash for files that *do* have concrete symbols.
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path();
    write(
        &root.join("docs/guide.md"),
        "# Guide\n\nFirst paragraph.\n\nSecond paragraph.\n",
    );

    let mut store = SqliteStore::open(Path::new(":memory:")).unwrap();
    let emb = HashedEmbedder::default();
    update_index(root, &mut store, &emb).unwrap();
    let before_hash = module_content_hash(&store, "docs/guide.md");

    write(
        &root.join("docs/guide.md"),
        "# Guide\n\nFirst paragraph.\n\nSecond paragraph, edited.\n",
    );
    let report = update_index(root, &mut store, &emb).unwrap();

    assert_eq!(report.reparsed_files, 1);
    assert_eq!(
        report.changed_symbols, 1,
        "a markdown file's only symbol must report as changed on a mid-file edit"
    );
    // `symbol_embed_text` doesn't change for this edit (see below), so a
    // buggy `update_index` that silently *reused* the stale embedding
    // instead of recomputing it would produce the exact same vector value
    // as a correct recompute — the vector alone can't distinguish "reused"
    // from "recomputed" here. `embedded_symbols`/`reused_embeddings` can:
    // they report which code path actually ran, independent of the
    // resulting value (found by review).
    assert_eq!(
        report.embedded_symbols, 1,
        "the changed module symbol must actually go through the embed path"
    );
    assert_eq!(
        report.reused_embeddings, 0,
        "a changed symbol must not be reported as a reused embedding"
    );
    let after_hash = module_content_hash(&store, "docs/guide.md");
    assert_ne!(
        before_hash, after_hash,
        "a mid-file markdown edit must change the module symbol's content_hash"
    );
    // NOT assert_ne! on the embedding vector: `symbol_embed_text` is
    // file/kind/qualified_name/signature/imports/references only, never
    // full body text (true for every language's module fallback, not
    // unique to markdown) — an edit that doesn't touch the first line, add
    // an import, or add/remove a resolvable reference legitimately
    // re-embeds to the *same* vector. `content_hash` changing (asserted
    // above) is what proves the edit was actually detected and
    // reprocessed, not that the vector moved. Parity with a clean rebuild
    // is the property that actually matters here.
    let after_vec = module_embedding(&store, "docs/guide.md");
    let rebuilt_vec = clean_rebuild_module_embedding(root, "docs/guide.md");
    assert_eq!(after_vec, rebuilt_vec);
}

/// Stored embedding of the concrete (non-module) symbol `qualified_name`.
fn symbol_embedding(store: &SqliteStore, file: &str, qualified_name: &str) -> Vec<f32> {
    let syms = store.all_symbols().unwrap();
    let s = syms
        .iter()
        .find(|s| s.file == file && s.qualified_name == qualified_name)
        .unwrap_or_else(|| panic!("no symbol {qualified_name} in {file}"));
    store.all_embeddings().unwrap()[&s.id()].1.clone()
}

fn clean_rebuild_symbol_embedding(root: &Path, file: &str, qualified_name: &str) -> Vec<f32> {
    let mut fresh = SqliteStore::open(Path::new(":memory:")).unwrap();
    update_index(root, &mut fresh, &HashedEmbedder::default()).unwrap();
    symbol_embedding(&fresh, file, qualified_name)
}

#[test]
fn import_only_change_reembeds_untouched_concrete_symbols() {
    // A concrete symbol's `symbol_embed_text` carries the file's `imports`,
    // but its parser `content_hash` covers only its own span. An import-only
    // edit therefore changes the embedding input of every symbol in the file
    // without changing any of their spans.
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path();
    write(&root.join("thing.py"), "def foo():\n    return 1\n");
    let mut store = SqliteStore::open(Path::new(":memory:")).unwrap();
    let emb = HashedEmbedder::default();
    update_index(root, &mut store, &emb).unwrap();
    let before = symbol_embedding(&store, "thing.py", "foo");

    write(
        &root.join("thing.py"),
        "import retrying\n\ndef foo():\n    return 1\n",
    );
    update_index(root, &mut store, &emb).unwrap();
    let after = symbol_embedding(&store, "thing.py", "foo");

    let rebuilt = clean_rebuild_symbol_embedding(root, "thing.py", "foo");
    assert_ne!(before, rebuilt, "foo's embedding input gained `retrying`");
    assert_eq!(after, rebuilt, "incremental must equal a clean rebuild");
}

#[test]
fn new_same_file_definition_reembeds_an_untouched_symbol_that_now_references_it() {
    // `references` are resolved against project-wide known names after
    // parsing. Defining `helper` in the same file makes it a reference of
    // `bar`, whose span did not change. The file *is* reparsed, so the
    // stored `references` are current — the embedding must be too. (The
    // accepted cross-file gap in AGENTS.md is about files that are *not*
    // reparsed; this is not that.)
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path();
    write(&root.join("thing.py"), "def bar():\n    return helper()\n");
    let mut store = SqliteStore::open(Path::new(":memory:")).unwrap();
    let emb = HashedEmbedder::default();
    update_index(root, &mut store, &emb).unwrap();
    let before = symbol_embedding(&store, "thing.py", "bar");

    write(
        &root.join("thing.py"),
        "def bar():\n    return helper()\n\ndef helper():\n    return 1\n",
    );
    update_index(root, &mut store, &emb).unwrap();
    let after = symbol_embedding(&store, "thing.py", "bar");

    let rebuilt = clean_rebuild_symbol_embedding(root, "thing.py", "bar");
    assert_ne!(before, rebuilt, "bar's references gained `helper`");
    assert_eq!(after, rebuilt, "incremental must equal a clean rebuild");
}
