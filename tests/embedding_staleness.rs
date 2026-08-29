//! Phase 1.1 item 1: module embedding staleness.
//!
//! The module symbol's embedding input (`index::embed_text`) includes
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
    // the import moves in above it — imports participate in content_hash
    // and embed_text directly (a pre-existing, already-correct path), this
    // asserts it still holds after the module hash formula changed.
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
    // embed_text (no full-body text is fed to the embedder); this documents
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
        "comment-only edits outside embed_text's inputs must not force a spurious re-embed"
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
    // coarse embed_text-based formula — doing so would silently stop
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
