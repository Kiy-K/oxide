//! Full-rebuild vs incremental-update parity: for the same final repository
//! state, a clean full rebuild and an existing index walked through a long
//! mutation sequence (add / edit / edit again / rename / move / delete /
//! recreate) must reach the same logical index state.
//!
//! Every field that participates in indexed identity, staleness detection,
//! or the embedding cache key is compared, including embedding *vector
//! values* themselves (not just which symbol ids carry one) — now that the
//! module content_hash bug (Phase 1.1 item 1: a body-only edit could change
//! a module's `references`, and therefore its embedding input, without
//! changing the coarse hash gating re-embedding) is fixed, incremental and
//! full-rebuild vectors are expected to be byte-identical for every symbol
//! under the deterministic offline embedder. Ignored: SQLite row order/
//! layout, which carries no logical meaning.

use oxide::embeddings::HashedEmbedder;
use oxide::index::{update_index, IndexBackend, SqliteStore};
use std::collections::HashMap;
use std::path::Path;

fn write(path: &Path, content: &str) {
    std::fs::create_dir_all(path.parent().unwrap()).unwrap();
    std::fs::write(path, content).unwrap();
}

/// Asserts full logical parity between two independently-built indexes of
/// the same repository state: same files, same symbol identities and
/// content, same references, same embedded vectors, same provider metadata.
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
    assert_eq!(
        a.len(),
        b.len(),
        "symbol count must match: incremental={:?} full={:?}",
        a.iter().map(|s| &s.qualified_name).collect::<Vec<_>>(),
        b.iter().map(|s| &s.qualified_name).collect::<Vec<_>>(),
    );
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
            "references (supported structural evidence) mismatch for {}",
            x.qualified_name
        );
    }

    let embeddings_a: HashMap<u64, (u64, Vec<f32>)> = incremental.all_embeddings().unwrap();
    let embeddings_b: HashMap<u64, (u64, Vec<f32>)> = full.all_embeddings().unwrap();
    assert_eq!(
        embeddings_a
            .keys()
            .collect::<std::collections::HashSet<_>>(),
        embeddings_b
            .keys()
            .collect::<std::collections::HashSet<_>>(),
        "the SET of symbols carrying an embedding must match"
    );
    for (id, (hash_a, vec_a)) in &embeddings_a {
        let (hash_b, vec_b) = &embeddings_b[id];
        assert_eq!(
            hash_a, hash_b,
            "embedding-input hash mismatch for symbol id {id}"
        );
        assert_eq!(
            vec_a, vec_b,
            "embedding vector value mismatch for symbol id {id} (embedding staleness bug)"
        );
    }

    for key in [
        "root",
        "embedder",
        "dim",
        "schema_version",
        "extraction_version",
    ] {
        assert_eq!(
            incremental.get_meta(key).unwrap(),
            full.get_meta(key).unwrap(),
            "meta {key} must match"
        );
    }

    let stats_a = incremental.stats().unwrap();
    let stats_b = full.stats().unwrap();
    assert_eq!(stats_a.files, stats_b.files);
    assert_eq!(stats_a.symbols, stats_b.symbols);
    assert_eq!(stats_a.embeddings, stats_b.embeddings);
}

#[test]
fn incremental_mutation_sequence_matches_clean_full_rebuild() {
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path();
    let emb = HashedEmbedder::default();
    let mut incremental = SqliteStore::open(Path::new(":memory:")).unwrap();

    // --- initial state ---
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
    write(&root.join("src/mover.py"), "def moved():\n    return 7\n");
    write(
        &root.join("src/recreated.py"),
        "def first_life():\n    return 1\n",
    );
    // A name that does not yet appear anywhere in util.py, so referencing it
    // later is a genuinely new in-file reference rather than a token that
    // was already present (e.g. the definition of a sibling function).
    write(
        &root.join("src/refhelper.py"),
        "def refhelper():\n    return 99\n",
    );
    update_index(root, &mut incremental, &emb).unwrap();

    // --- add ---
    write(
        &root.join("src/new_file.py"),
        "def brand_new():\n    return 42\n",
    );
    let r = update_index(root, &mut incremental, &emb).unwrap();
    assert_eq!(r.reparsed_files, 1, "only the new file");

    // --- edit: body-only, adds a genuinely new in-file reference (a name
    // not previously present anywhere in the file); first line and imports
    // are untouched, exercising exactly the module-staleness bug ---
    write(
        &root.join("src/util.py"),
        "def helper():\n    return refhelper()\n\ndef other():\n    return 2\n",
    );
    let r = update_index(root, &mut incremental, &emb).unwrap();
    assert_eq!(r.reparsed_files, 1);

    // --- edit again: a second, independent edit to the same file, keeping
    // the refhelper() reference from the previous edit in place so the
    // final on-disk state still carries it through to the parity check ---
    write(
        &root.join("src/util.py"),
        "def helper():\n    return refhelper()\n\ndef other():\n    return 3\n",
    );
    let r = update_index(root, &mut incremental, &emb).unwrap();
    assert_eq!(r.reparsed_files, 1);

    // --- rename: same content, new path ---
    std::fs::remove_file(root.join("lib/keep.ts")).unwrap();
    write(
        &root.join("lib/kept.ts"),
        "export function keep(): number {\n  return 1;\n}\n",
    );
    let r = update_index(root, &mut incremental, &emb).unwrap();
    assert_eq!(r.removed_files, 1);
    assert_eq!(r.reparsed_files, 1);

    // --- move: same content, different directory ---
    std::fs::remove_file(root.join("src/mover.py")).unwrap();
    write(&root.join("lib/mover.py"), "def moved():\n    return 7\n");
    let r = update_index(root, &mut incremental, &emb).unwrap();
    assert_eq!(r.removed_files, 1);
    assert_eq!(r.reparsed_files, 1);

    // --- delete ---
    std::fs::remove_file(root.join("src/gone.py")).unwrap();
    let r = update_index(root, &mut incremental, &emb).unwrap();
    assert_eq!(r.removed_files, 1);

    // --- recreate: delete then re-add at the same path with different
    // content (must be treated as a fresh file, not a stale cache hit) ---
    std::fs::remove_file(root.join("src/recreated.py")).unwrap();
    update_index(root, &mut incremental, &emb).unwrap();
    write(
        &root.join("src/recreated.py"),
        "def second_life():\n    return 2\n",
    );
    let r = update_index(root, &mut incremental, &emb).unwrap();
    assert_eq!(r.reparsed_files, 1);
    assert_eq!(r.new_symbols, 2, "module + second_life, as if never seen");

    // A completely independent full rebuild of the same final on-disk state.
    let mut full = SqliteStore::open(Path::new(":memory:")).unwrap();
    update_index(root, &mut full, &emb).unwrap();

    assert_parity(&incremental, &full);
}
