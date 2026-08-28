//! Full-rebuild vs incremental-update parity: for the same final repository
//! state, a clean full rebuild and an existing index walked through an
//! edit/add/delete/rename sequence must reach the same logical index state.
//!
//! Deliberately NOT compared: embedding vector *values*. The module symbol's
//! content_hash intentionally ignores body edits (imports + first non-empty
//! line only, see `parser.rs` / AGENTS.md), so a body-only edit that adds a
//! new in-file reference can change what the module *would* embed without
//! changing the hash that gates re-embedding. Incremental correctly reuses
//! the old module vector in that case while a full rebuild computes a fresh
//! one from empty state — a real, documented, and intentional divergence.
//! Presence of an embedding for a given symbol id is compared instead.

use oxide::embeddings::HashedEmbedder;
use oxide::index::{update_index, IndexBackend, SqliteStore};
use std::collections::HashSet;
use std::path::Path;

fn write(path: &Path, content: &str) {
    std::fs::create_dir_all(path.parent().unwrap()).unwrap();
    std::fs::write(path, content).unwrap();
}

fn assert_parity(incremental: &SqliteStore, full: &SqliteStore) {
    assert_eq!(
        incremental.file_hashes().unwrap(),
        full.file_hashes().unwrap(),
        "indexed file set and content hashes must match"
    );

    let mut a = incremental.all_symbols().unwrap();
    let mut b = full.all_symbols().unwrap();
    a.sort_by_key(|s| s.id());
    b.sort_by_key(|s| s.id());
    assert_eq!(a.len(), b.len(), "symbol count must match");
    for (x, y) in a.iter().zip(b.iter()) {
        assert_eq!(x.id(), y.id(), "symbol id mismatch");
        assert_eq!(x.file, y.file);
        assert_eq!(x.qualified_name, y.qualified_name);
        assert_eq!(x.name, y.name);
        assert_eq!(x.kind, y.kind);
        assert_eq!(x.language, y.language);
        assert_eq!(x.start_line, y.start_line);
        assert_eq!(x.end_line, y.end_line);
        assert_eq!(
            x.content_hash, y.content_hash,
            "content_hash mismatch for {}",
            x.qualified_name
        );
        assert_eq!(x.signature, y.signature);
        assert_eq!(x.imports, y.imports);
        assert_eq!(x.exported, y.exported);
        assert_eq!(x.parent, y.parent);
        assert_eq!(
            x.references, y.references,
            "references mismatch for {}",
            x.qualified_name
        );
    }

    let embedded_a: HashSet<u64> = incremental
        .all_embeddings()
        .unwrap()
        .keys()
        .copied()
        .collect();
    let embedded_b: HashSet<u64> = full.all_embeddings().unwrap().keys().copied().collect();
    assert_eq!(
        embedded_a, embedded_b,
        "the SET of symbols carrying an embedding must match (not vector values)"
    );

    for key in ["embedder", "dim", "schema_version", "extraction_version"] {
        assert_eq!(
            incremental.get_meta(key).unwrap(),
            full.get_meta(key).unwrap(),
            "meta {key} must match"
        );
    }
}

#[test]
fn incremental_edit_add_delete_rename_matches_clean_full_rebuild() {
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path();

    write(
        &root.join("src/util.py"),
        "def helper():\n    return 1\n\ndef other():\n    return 2\n",
    );
    write(
        &root.join("src/gone.py"),
        "def to_be_deleted():\n    return 0\n",
    );
    write(
        &root.join("lib/keep.ts"),
        "export function keep(): number {\n  return 1;\n}\n",
    );

    let emb = HashedEmbedder::default();
    let mut incremental = SqliteStore::open(Path::new(":memory:")).unwrap();
    update_index(root, &mut incremental, &emb).unwrap();

    // Body-only edit adding a new in-file reference; the file's first
    // non-empty line is unchanged, so the module symbol's coarse content_hash
    // does not change even though its `references` set does.
    write(
        &root.join("src/util.py"),
        "def helper():\n    return other()\n\ndef other():\n    return 2\n",
    );
    std::fs::remove_file(root.join("src/gone.py")).unwrap();
    write(
        &root.join("src/new_file.py"),
        "def brand_new():\n    return 42\n",
    );
    // Rename: same content under a different path.
    std::fs::remove_file(root.join("lib/keep.ts")).unwrap();
    write(
        &root.join("lib/kept.ts"),
        "export function keep(): number {\n  return 1;\n}\n",
    );

    let report = update_index(root, &mut incremental, &emb).unwrap();
    assert_eq!(
        report.removed_files, 2,
        "gone.py and the pre-rename keep.ts"
    );
    assert_eq!(
        report.reparsed_files, 3,
        "util.py (edited), new_file.py (new), kept.ts (new path)"
    );
    assert_eq!(report.errored_files, 0);

    // A completely independent full rebuild of the same final on-disk state.
    let mut full = SqliteStore::open(Path::new(":memory:")).unwrap();
    update_index(root, &mut full, &emb).unwrap();

    assert_parity(&incremental, &full);
}
