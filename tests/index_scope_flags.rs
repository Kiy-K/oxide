//! `IndexOptions` (`oxide index -a/-g/-e`) contract: each flag only widens
//! which symbols a stage recomputes, never what counts as stale, and never
//! forces work an unrelated flag didn't ask for.

use oxide::embeddings::HashedEmbedder;
use oxide::index::{update_base, update_embeddings, update_index_scoped, IndexOptions};
use oxide::index::{IndexBackend, SqliteStore};
use std::path::Path;

fn write(path: &Path, content: &str) {
    std::fs::create_dir_all(path.parent().unwrap()).unwrap();
    std::fs::write(path, content).unwrap();
}

const SRC_V1: &str =
    "class Retry:\n    def go(self):\n        return other()\n\ndef other():\n    return 1\n";

fn seeded_repo() -> (tempfile::TempDir, SqliteStore, HashedEmbedder) {
    let tmp = tempfile::tempdir().unwrap();
    write(&tmp.path().join("src/thing.py"), SRC_V1);
    let mut store = SqliteStore::open(Path::new(":memory:")).unwrap();
    let emb = HashedEmbedder::default();
    update_index_scoped(tmp.path(), &mut store, &emb, &IndexOptions::default()).unwrap();
    (tmp, store, emb)
}

#[test]
fn default_options_is_the_unchanged_plain_incremental_contract() {
    let (tmp, mut store, emb) = seeded_repo();
    // No edits, no flags: everything reused, nothing recomputed — the exact
    // contract every pre-existing `update_index` caller depends on.
    let r = update_index_scoped(tmp.path(), &mut store, &emb, &IndexOptions::default()).unwrap();
    assert_eq!(r.reparsed_files, 0);
    assert_eq!(r.unchanged_files, 1);
    assert_eq!(r.embedded_symbols, 0);
    assert!(r.reused_embeddings > 0);
    assert_eq!(r.relations_refreshed_symbols, 0);
}

#[test]
fn force_reparse_reparses_every_file_even_unchanged() {
    let (tmp, mut store, emb) = seeded_repo();
    let opts = IndexOptions {
        force_reparse: true,
        ..IndexOptions::default()
    };
    let r = update_index_scoped(tmp.path(), &mut store, &emb, &opts).unwrap();
    assert_eq!(
        r.unchanged_files, 0,
        "force_reparse must bypass the content-hash shortcut"
    );
    assert_eq!(r.reparsed_files, 1);
    // Symbols are unchanged in content, so content_hash matches and re-embed
    // is NOT forced by force_reparse alone — only -e should force that.
    assert_eq!(r.embedded_symbols, 0);
    assert!(r.reused_embeddings > 0);
}

#[test]
fn force_graph_refreshes_relations_without_reparsing_or_reembedding() {
    let (tmp, mut store, emb) = seeded_repo();
    let opts = IndexOptions {
        force_graph: true,
        ..IndexOptions::default()
    };
    let r = update_index_scoped(tmp.path(), &mut store, &emb, &opts).unwrap();
    assert_eq!(r.reparsed_files, 0, "-g alone must not force a reparse");
    assert_eq!(
        r.embedded_symbols, 0,
        "-g alone must not force re-embedding"
    );
    assert!(r.reused_embeddings > 0);
    assert!(
        r.relations_refreshed_symbols > 0,
        "unchanged files' symbols should still get relations recomputed under -g"
    );
}

#[test]
fn force_embeddings_reembeds_everything_without_reparsing_or_graph_refresh() {
    let (tmp, mut store, emb) = seeded_repo();
    let opts = IndexOptions {
        force_embeddings: true,
        ..IndexOptions::default()
    };
    let r = update_index_scoped(tmp.path(), &mut store, &emb, &opts).unwrap();
    assert_eq!(r.reparsed_files, 0, "-e alone must not force a reparse");
    assert_eq!(
        r.relations_refreshed_symbols, 0,
        "-e alone must not force a graph refresh"
    );
    assert_eq!(
        r.reused_embeddings, 0,
        "-e forces every symbol to be re-embedded"
    );
    assert!(r.embedded_symbols > 0);
}

#[test]
fn force_graph_and_force_embeddings_combine_without_forcing_reparse() {
    let (tmp, mut store, emb) = seeded_repo();
    let opts = IndexOptions {
        force_graph: true,
        force_embeddings: true,
        ..IndexOptions::default()
    };
    let r = update_index_scoped(tmp.path(), &mut store, &emb, &opts).unwrap();
    assert_eq!(
        r.reparsed_files, 0,
        "combining -g -e must not imply -a's forced reparse"
    );
    assert!(r.relations_refreshed_symbols > 0);
    assert!(r.embedded_symbols > 0);
    assert_eq!(r.reused_embeddings, 0);
}

#[test]
fn all_forces_every_layer() {
    let (tmp, mut store, emb) = seeded_repo();
    let r = update_index_scoped(tmp.path(), &mut store, &emb, &IndexOptions::all()).unwrap();
    assert_eq!(r.unchanged_files, 0, "-a forces a full reparse");
    assert_eq!(r.reparsed_files, 1);
    assert_eq!(
        r.reused_embeddings, 0,
        "-a forces every embedding to be recomputed"
    );
    assert!(r.embedded_symbols > 0);
    // force_reparse already makes every file's relations get recomputed via
    // the main per-file loop, so the *separate* force_graph backfill path
    // (which only touches files NOT being reparsed) has nothing left to do
    // — this is a real "no redundant work", not a missing feature.
    assert_eq!(
        r.relations_refreshed_symbols, 0,
        "force_reparse already covers every symbol's relations; -g's own path does no extra work on top"
    );
}

#[test]
fn e_never_embeds_against_stale_symbols_when_a_file_actually_changed() {
    // The task's own stated invariant: -e must never embed against stale
    // symbols. Change a file's content (which the normal incremental parse
    // must still pick up) at the same time -e is requested; the forced
    // embedding pass must see the NEW content, not whatever was indexed
    // before this call.
    let (tmp, mut store, emb) = seeded_repo();
    write(
        &tmp.path().join("src/thing.py"),
        "class Retry:\n    def go(self):\n        return other()\n\ndef other():\n    return 999\n",
    );
    let opts = IndexOptions {
        force_embeddings: true,
        ..IndexOptions::default()
    };
    let r = update_index_scoped(tmp.path(), &mut store, &emb, &opts).unwrap();
    assert_eq!(
        r.reparsed_files, 1,
        "the changed file must still be reparsed normally"
    );
    assert_eq!(
        r.reused_embeddings, 0,
        "-e forces every symbol's embedding, including the changed one"
    );
    assert!(r.embedded_symbols > 0);
}

#[test]
fn update_base_alone_never_touches_embeddings() {
    let (tmp, mut store, _emb) = seeded_repo();
    let before = store.all_embeddings().unwrap();
    let opts = IndexOptions::all();
    let r = update_base(tmp.path(), &mut store, &opts).unwrap();
    assert_eq!(r.embedded_symbols, 0);
    assert_eq!(r.reused_embeddings, 0);
    let after = store.all_embeddings().unwrap();
    assert_eq!(
        before.len(),
        after.len(),
        "update_base must not clear or add embeddings"
    );
}

#[test]
fn update_embeddings_after_update_base_matches_update_index_scoped() {
    // Calling the two stages separately (the shape `oxide index -a`'s
    // staged reporting needs) must produce the same final counts as the
    // combined single-call entry point.
    let (tmp, mut store_a, emb) = seeded_repo();
    let opts = IndexOptions::all();
    let combined = update_index_scoped(tmp.path(), &mut store_a, &emb, &opts).unwrap();

    let tmp2 = tempfile::tempdir().unwrap();
    write(&tmp2.path().join("src/thing.py"), SRC_V1);
    let mut store_b = SqliteStore::open(Path::new(":memory:")).unwrap();
    update_index_scoped(tmp2.path(), &mut store_b, &emb, &IndexOptions::default()).unwrap();
    let mut staged = update_base(tmp2.path(), &mut store_b, &opts).unwrap();
    update_embeddings(tmp2.path(), &mut store_b, &emb, &opts, &mut staged).unwrap();

    assert_eq!(combined.reparsed_files, staged.reparsed_files);
    assert_eq!(combined.embedded_symbols, staged.embedded_symbols);
    assert_eq!(combined.reused_embeddings, staged.reused_embeddings);
}
