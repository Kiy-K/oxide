//! Equivalence fixture for the embedding-space authority (#34 S1): every
//! stored embedding-space state, through every caller that interprets it.
//! `EXPECTED` was captured from the pre-S1 code, where the write side, the
//! read side and `status` each parsed the metadata themselves; it must keep
//! passing unchanged now that they share one interpretation.

use super::*;
use crate::embeddings::{EmbeddingSpaceFingerprint, SYMBOL_TEXT_RECIPE};
use crate::index::{incompatible_stored_space, pending_embedding_count, update_index};
use crate::service::test_support::seed_repo;

fn current() -> EmbeddingSpaceFingerprint {
    HashedEmbedder::default().fingerprint()
}

fn json(fp: &EmbeddingSpaceFingerprint) -> String {
    serde_json::to_string(fp).unwrap()
}

/// A schema-1 fingerprint as it was persisted before #33: no
/// `document_text_recipe` field at all.
fn legacy_schema_1() -> String {
    let mut value = serde_json::to_value(current()).unwrap();
    let map = value.as_object_mut().unwrap();
    map.remove("document_text_recipe");
    map.insert("schema_version".into(), 1.into());
    value.to_string()
}

fn other_recipe() -> String {
    json(&EmbeddingSpaceFingerprint {
        document_text_recipe: "symbol-text:v0".into(),
        ..current()
    })
}

fn other_provider() -> String {
    json(&EmbeddingSpaceFingerprint {
        model: "other-model".into(),
        ..current()
    })
}

fn fingerprint_states() -> Vec<(&'static str, Option<String>)> {
    vec![
        ("absent", None),
        ("empty", Some(String::new())),
        ("unreadable", Some("{not json".into())),
        ("schema-1", Some(legacy_schema_1())),
        ("other-recipe", Some(other_recipe())),
        ("other-provider", Some(other_provider())),
        ("current", Some(json(&current()))),
    ]
}

fn marker_states() -> Vec<(&'static str, Option<String>)> {
    vec![
        ("absent", None),
        ("empty", Some(String::new())),
        ("unreadable", Some("{not json".into())),
        ("self", Some(json(&current()))),
        ("other", Some(other_provider())),
    ]
}

fn put_meta(conn: &rusqlite::Connection, key: &str, value: Option<&str>) {
    match value {
        Some(v) => conn
            .execute(
                "INSERT OR REPLACE INTO meta(key, value) VALUES (?1, ?2)",
                [key, v],
            )
            .unwrap(),
        None => conn
            .execute("DELETE FROM meta WHERE key = ?1", [key])
            .unwrap(),
    };
}

/// One row: build a healthy index under `HashedEmbedder`, rewrite its stored
/// state, then ask each caller for its verdict. The persisted key names are
/// spelled out on purpose — they are the on-disk format under test.
fn row(fp: (&str, &Option<String>), marker: (&str, &Option<String>), vectors: bool) -> String {
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path().canonicalize().unwrap();
    seed_repo(&root);
    let db_path = root.join(".oxide").join("index.db");
    let embedder = HashedEmbedder::default();
    {
        let mut store = SqliteStore::open(&db_path).unwrap();
        update_index(&root, &mut store, &embedder).unwrap();
    }
    {
        let conn = rusqlite::Connection::open(&db_path).unwrap();
        put_meta(&conn, "embedding_fingerprint", fp.1.as_deref());
        put_meta(&conn, "embedding_migration", marker.1.as_deref());
        // `status` compares the stored provider name against the configured
        // one; pin it so only the fingerprint half decides.
        let configured = crate::embeddings::configured_provider_name(None);
        put_meta(&conn, "embedder", Some(&configured));
        if !vectors {
            conn.execute("DELETE FROM embeddings", []).unwrap();
        }
    }

    let service = RepositoryService {
        root: root.clone(),
        use_process_cache: false,
    };
    let store = service.open_index_for_read().unwrap();
    let symbols = store.stats().unwrap().symbols;
    let write = incompatible_stored_space(&store, &embedder)
        .unwrap()
        .unwrap_or_else(|| "reuse".into());
    let pending = pending_embedding_count(&store, &embedder).unwrap();
    let read = match service.validate_index(&store, Some(&embedder), &store.stats().unwrap()) {
        Ok(()) => "ok".to_string(),
        Err(e) => format!("{}: {}", e.code(), e.message()),
    };
    let status_current = service.status().unwrap().embedder_current;
    drop(store);

    // What the next `oxide index` actually does with that state.
    let mut store = SqliteStore::open(&db_path).unwrap();
    let report = update_index(&root, &mut store, &embedder).unwrap();
    let marker_after = store
        .get_meta("embedding_migration")
        .unwrap()
        .unwrap_or_default();

    format!(
        "fp={} marker={} vectors={vectors}\n  write: {write}\n  pending: {pending}/{symbols}\n  read: {read}\n  status.embedder_current: {status_current}\n  reindex: embedded={} reused={} marker_after={:?}\n",
        fp.0,
        marker.0,
        report.embedded_symbols,
        report.reused_embeddings,
        marker_after,
    )
}

#[test]
fn every_stored_embedding_space_state_keeps_its_pre_s1_verdicts() {
    assert_eq!(
        SYMBOL_TEXT_RECIPE, "symbol-text:v1",
        "EXPECTED names this recipe"
    );
    let mut actual = String::new();
    for fp in &fingerprint_states() {
        for marker in &marker_states() {
            for vectors in [true, false] {
                actual.push_str(&row((fp.0, &fp.1), (marker.0, &marker.1), vectors));
            }
        }
    }
    if actual != EXPECTED {
        panic!("embedding-space verdicts changed:\n{actual}");
    }
}

const EXPECTED: &str = r#"fp=absent marker=absent vectors=true
  write: stored vectors have no embedding fingerprint; re-embedding all symbols
  pending: 2/2
  read: provider_mismatch: index embeddings were built with a different embedding provider or dimension; run `oxide index PATH`
  status.embedder_current: false
  reindex: embedded=2 reused=0 marker_after=""
fp=absent marker=absent vectors=false
  write: reuse
  pending: 2/2
  read: provider_mismatch: index embeddings were built with a different embedding provider or dimension; run `oxide index PATH`
  status.embedder_current: false
  reindex: embedded=2 reused=0 marker_after=""
fp=absent marker=empty vectors=true
  write: stored vectors have no embedding fingerprint; re-embedding all symbols
  pending: 2/2
  read: provider_mismatch: index embeddings were built with a different embedding provider or dimension; run `oxide index PATH`
  status.embedder_current: false
  reindex: embedded=2 reused=0 marker_after=""
fp=absent marker=empty vectors=false
  write: reuse
  pending: 2/2
  read: provider_mismatch: index embeddings were built with a different embedding provider or dimension; run `oxide index PATH`
  status.embedder_current: false
  reindex: embedded=2 reused=0 marker_after=""
fp=absent marker=unreadable vectors=true
  write: an interrupted embedding migration left an unreadable fingerprint; re-embedding all symbols
  pending: 2/2
  read: index_stale: index embeddings were left mid-migration by an interrupted `oxide index`; run `oxide index PATH` to finish it (or `--mode lexical` meanwhile)
  status.embedder_current: false
  reindex: embedded=2 reused=0 marker_after=""
fp=absent marker=unreadable vectors=false
  write: an interrupted embedding migration left an unreadable fingerprint; re-embedding all symbols
  pending: 2/2
  read: index_stale: index embeddings were left mid-migration by an interrupted `oxide index`; run `oxide index PATH` to finish it (or `--mode lexical` meanwhile)
  status.embedder_current: false
  reindex: embedded=2 reused=0 marker_after=""
fp=absent marker=self vectors=true
  write: reuse
  pending: 0/2
  read: index_stale: index embeddings were left mid-migration by an interrupted `oxide index`; run `oxide index PATH` to finish it (or `--mode lexical` meanwhile)
  status.embedder_current: false
  reindex: embedded=0 reused=2 marker_after=""
fp=absent marker=self vectors=false
  write: reuse
  pending: 2/2
  read: index_stale: index embeddings were left mid-migration by an interrupted `oxide index`; run `oxide index PATH` to finish it (or `--mode lexical` meanwhile)
  status.embedder_current: false
  reindex: embedded=2 reused=0 marker_after=""
fp=absent marker=other vectors=true
  write: an interrupted migration to other-model left this index's vectors mid-flight; re-embedding all symbols under hashed-bow-256
  pending: 2/2
  read: index_stale: index embeddings were left mid-migration by an interrupted `oxide index`; run `oxide index PATH` to finish it (or `--mode lexical` meanwhile)
  status.embedder_current: false
  reindex: embedded=2 reused=0 marker_after=""
fp=absent marker=other vectors=false
  write: an interrupted migration to other-model left this index's vectors mid-flight; re-embedding all symbols under hashed-bow-256
  pending: 2/2
  read: index_stale: index embeddings were left mid-migration by an interrupted `oxide index`; run `oxide index PATH` to finish it (or `--mode lexical` meanwhile)
  status.embedder_current: false
  reindex: embedded=2 reused=0 marker_after=""
fp=empty marker=absent vectors=true
  write: stored vectors have no embedding fingerprint; re-embedding all symbols
  pending: 2/2
  read: provider_mismatch: index embeddings were built with a different embedding provider or dimension; run `oxide index PATH`
  status.embedder_current: false
  reindex: embedded=2 reused=0 marker_after=""
fp=empty marker=absent vectors=false
  write: reuse
  pending: 2/2
  read: provider_mismatch: index embeddings were built with a different embedding provider or dimension; run `oxide index PATH`
  status.embedder_current: false
  reindex: embedded=2 reused=0 marker_after=""
fp=empty marker=empty vectors=true
  write: stored vectors have no embedding fingerprint; re-embedding all symbols
  pending: 2/2
  read: provider_mismatch: index embeddings were built with a different embedding provider or dimension; run `oxide index PATH`
  status.embedder_current: false
  reindex: embedded=2 reused=0 marker_after=""
fp=empty marker=empty vectors=false
  write: reuse
  pending: 2/2
  read: provider_mismatch: index embeddings were built with a different embedding provider or dimension; run `oxide index PATH`
  status.embedder_current: false
  reindex: embedded=2 reused=0 marker_after=""
fp=empty marker=unreadable vectors=true
  write: an interrupted embedding migration left an unreadable fingerprint; re-embedding all symbols
  pending: 2/2
  read: index_stale: index embeddings were left mid-migration by an interrupted `oxide index`; run `oxide index PATH` to finish it (or `--mode lexical` meanwhile)
  status.embedder_current: false
  reindex: embedded=2 reused=0 marker_after=""
fp=empty marker=unreadable vectors=false
  write: an interrupted embedding migration left an unreadable fingerprint; re-embedding all symbols
  pending: 2/2
  read: index_stale: index embeddings were left mid-migration by an interrupted `oxide index`; run `oxide index PATH` to finish it (or `--mode lexical` meanwhile)
  status.embedder_current: false
  reindex: embedded=2 reused=0 marker_after=""
fp=empty marker=self vectors=true
  write: reuse
  pending: 0/2
  read: index_stale: index embeddings were left mid-migration by an interrupted `oxide index`; run `oxide index PATH` to finish it (or `--mode lexical` meanwhile)
  status.embedder_current: false
  reindex: embedded=0 reused=2 marker_after=""
fp=empty marker=self vectors=false
  write: reuse
  pending: 2/2
  read: index_stale: index embeddings were left mid-migration by an interrupted `oxide index`; run `oxide index PATH` to finish it (or `--mode lexical` meanwhile)
  status.embedder_current: false
  reindex: embedded=2 reused=0 marker_after=""
fp=empty marker=other vectors=true
  write: an interrupted migration to other-model left this index's vectors mid-flight; re-embedding all symbols under hashed-bow-256
  pending: 2/2
  read: index_stale: index embeddings were left mid-migration by an interrupted `oxide index`; run `oxide index PATH` to finish it (or `--mode lexical` meanwhile)
  status.embedder_current: false
  reindex: embedded=2 reused=0 marker_after=""
fp=empty marker=other vectors=false
  write: an interrupted migration to other-model left this index's vectors mid-flight; re-embedding all symbols under hashed-bow-256
  pending: 2/2
  read: index_stale: index embeddings were left mid-migration by an interrupted `oxide index`; run `oxide index PATH` to finish it (or `--mode lexical` meanwhile)
  status.embedder_current: false
  reindex: embedded=2 reused=0 marker_after=""
fp=unreadable marker=absent vectors=true
  write: stored embedding fingerprint is unreadable; re-embedding all symbols
  pending: 2/2
  read: provider_mismatch: index embeddings were built with a different embedding provider or dimension; run `oxide index PATH`
  status.embedder_current: false
  reindex: embedded=2 reused=0 marker_after=""
fp=unreadable marker=absent vectors=false
  write: stored embedding fingerprint is unreadable; re-embedding all symbols
  pending: 2/2
  read: provider_mismatch: index embeddings were built with a different embedding provider or dimension; run `oxide index PATH`
  status.embedder_current: false
  reindex: embedded=2 reused=0 marker_after=""
fp=unreadable marker=empty vectors=true
  write: stored embedding fingerprint is unreadable; re-embedding all symbols
  pending: 2/2
  read: provider_mismatch: index embeddings were built with a different embedding provider or dimension; run `oxide index PATH`
  status.embedder_current: false
  reindex: embedded=2 reused=0 marker_after=""
fp=unreadable marker=empty vectors=false
  write: stored embedding fingerprint is unreadable; re-embedding all symbols
  pending: 2/2
  read: provider_mismatch: index embeddings were built with a different embedding provider or dimension; run `oxide index PATH`
  status.embedder_current: false
  reindex: embedded=2 reused=0 marker_after=""
fp=unreadable marker=unreadable vectors=true
  write: an interrupted embedding migration left an unreadable fingerprint; re-embedding all symbols
  pending: 2/2
  read: index_stale: index embeddings were left mid-migration by an interrupted `oxide index`; run `oxide index PATH` to finish it (or `--mode lexical` meanwhile)
  status.embedder_current: false
  reindex: embedded=2 reused=0 marker_after=""
fp=unreadable marker=unreadable vectors=false
  write: an interrupted embedding migration left an unreadable fingerprint; re-embedding all symbols
  pending: 2/2
  read: index_stale: index embeddings were left mid-migration by an interrupted `oxide index`; run `oxide index PATH` to finish it (or `--mode lexical` meanwhile)
  status.embedder_current: false
  reindex: embedded=2 reused=0 marker_after=""
fp=unreadable marker=self vectors=true
  write: reuse
  pending: 0/2
  read: index_stale: index embeddings were left mid-migration by an interrupted `oxide index`; run `oxide index PATH` to finish it (or `--mode lexical` meanwhile)
  status.embedder_current: false
  reindex: embedded=0 reused=2 marker_after=""
fp=unreadable marker=self vectors=false
  write: reuse
  pending: 2/2
  read: index_stale: index embeddings were left mid-migration by an interrupted `oxide index`; run `oxide index PATH` to finish it (or `--mode lexical` meanwhile)
  status.embedder_current: false
  reindex: embedded=2 reused=0 marker_after=""
fp=unreadable marker=other vectors=true
  write: an interrupted migration to other-model left this index's vectors mid-flight; re-embedding all symbols under hashed-bow-256
  pending: 2/2
  read: index_stale: index embeddings were left mid-migration by an interrupted `oxide index`; run `oxide index PATH` to finish it (or `--mode lexical` meanwhile)
  status.embedder_current: false
  reindex: embedded=2 reused=0 marker_after=""
fp=unreadable marker=other vectors=false
  write: an interrupted migration to other-model left this index's vectors mid-flight; re-embedding all symbols under hashed-bow-256
  pending: 2/2
  read: index_stale: index embeddings were left mid-migration by an interrupted `oxide index`; run `oxide index PATH` to finish it (or `--mode lexical` meanwhile)
  status.embedder_current: false
  reindex: embedded=2 reused=0 marker_after=""
fp=schema-1 marker=absent vectors=true
  write: embedding text recipe changed (unrecorded -> symbol-text:v1); re-embedding all symbols
  pending: 2/2
  read: provider_mismatch: index embeddings were built with a different embedding provider or dimension; run `oxide index PATH`
  status.embedder_current: false
  reindex: embedded=2 reused=0 marker_after=""
fp=schema-1 marker=absent vectors=false
  write: embedding text recipe changed (unrecorded -> symbol-text:v1); re-embedding all symbols
  pending: 2/2
  read: provider_mismatch: index embeddings were built with a different embedding provider or dimension; run `oxide index PATH`
  status.embedder_current: false
  reindex: embedded=2 reused=0 marker_after=""
fp=schema-1 marker=empty vectors=true
  write: embedding text recipe changed (unrecorded -> symbol-text:v1); re-embedding all symbols
  pending: 2/2
  read: provider_mismatch: index embeddings were built with a different embedding provider or dimension; run `oxide index PATH`
  status.embedder_current: false
  reindex: embedded=2 reused=0 marker_after=""
fp=schema-1 marker=empty vectors=false
  write: embedding text recipe changed (unrecorded -> symbol-text:v1); re-embedding all symbols
  pending: 2/2
  read: provider_mismatch: index embeddings were built with a different embedding provider or dimension; run `oxide index PATH`
  status.embedder_current: false
  reindex: embedded=2 reused=0 marker_after=""
fp=schema-1 marker=unreadable vectors=true
  write: an interrupted embedding migration left an unreadable fingerprint; re-embedding all symbols
  pending: 2/2
  read: index_stale: index embeddings were left mid-migration by an interrupted `oxide index`; run `oxide index PATH` to finish it (or `--mode lexical` meanwhile)
  status.embedder_current: false
  reindex: embedded=2 reused=0 marker_after=""
fp=schema-1 marker=unreadable vectors=false
  write: an interrupted embedding migration left an unreadable fingerprint; re-embedding all symbols
  pending: 2/2
  read: index_stale: index embeddings were left mid-migration by an interrupted `oxide index`; run `oxide index PATH` to finish it (or `--mode lexical` meanwhile)
  status.embedder_current: false
  reindex: embedded=2 reused=0 marker_after=""
fp=schema-1 marker=self vectors=true
  write: reuse
  pending: 0/2
  read: index_stale: index embeddings were left mid-migration by an interrupted `oxide index`; run `oxide index PATH` to finish it (or `--mode lexical` meanwhile)
  status.embedder_current: false
  reindex: embedded=0 reused=2 marker_after=""
fp=schema-1 marker=self vectors=false
  write: reuse
  pending: 2/2
  read: index_stale: index embeddings were left mid-migration by an interrupted `oxide index`; run `oxide index PATH` to finish it (or `--mode lexical` meanwhile)
  status.embedder_current: false
  reindex: embedded=2 reused=0 marker_after=""
fp=schema-1 marker=other vectors=true
  write: an interrupted migration to other-model left this index's vectors mid-flight; re-embedding all symbols under hashed-bow-256
  pending: 2/2
  read: index_stale: index embeddings were left mid-migration by an interrupted `oxide index`; run `oxide index PATH` to finish it (or `--mode lexical` meanwhile)
  status.embedder_current: false
  reindex: embedded=2 reused=0 marker_after=""
fp=schema-1 marker=other vectors=false
  write: an interrupted migration to other-model left this index's vectors mid-flight; re-embedding all symbols under hashed-bow-256
  pending: 2/2
  read: index_stale: index embeddings were left mid-migration by an interrupted `oxide index`; run `oxide index PATH` to finish it (or `--mode lexical` meanwhile)
  status.embedder_current: false
  reindex: embedded=2 reused=0 marker_after=""
fp=other-recipe marker=absent vectors=true
  write: embedding text recipe changed (symbol-text:v0 -> symbol-text:v1); re-embedding all symbols
  pending: 2/2
  read: provider_mismatch: index embeddings were built with a different embedding provider or dimension; run `oxide index PATH`
  status.embedder_current: false
  reindex: embedded=2 reused=0 marker_after=""
fp=other-recipe marker=absent vectors=false
  write: embedding text recipe changed (symbol-text:v0 -> symbol-text:v1); re-embedding all symbols
  pending: 2/2
  read: provider_mismatch: index embeddings were built with a different embedding provider or dimension; run `oxide index PATH`
  status.embedder_current: false
  reindex: embedded=2 reused=0 marker_after=""
fp=other-recipe marker=empty vectors=true
  write: embedding text recipe changed (symbol-text:v0 -> symbol-text:v1); re-embedding all symbols
  pending: 2/2
  read: provider_mismatch: index embeddings were built with a different embedding provider or dimension; run `oxide index PATH`
  status.embedder_current: false
  reindex: embedded=2 reused=0 marker_after=""
fp=other-recipe marker=empty vectors=false
  write: embedding text recipe changed (symbol-text:v0 -> symbol-text:v1); re-embedding all symbols
  pending: 2/2
  read: provider_mismatch: index embeddings were built with a different embedding provider or dimension; run `oxide index PATH`
  status.embedder_current: false
  reindex: embedded=2 reused=0 marker_after=""
fp=other-recipe marker=unreadable vectors=true
  write: an interrupted embedding migration left an unreadable fingerprint; re-embedding all symbols
  pending: 2/2
  read: index_stale: index embeddings were left mid-migration by an interrupted `oxide index`; run `oxide index PATH` to finish it (or `--mode lexical` meanwhile)
  status.embedder_current: false
  reindex: embedded=2 reused=0 marker_after=""
fp=other-recipe marker=unreadable vectors=false
  write: an interrupted embedding migration left an unreadable fingerprint; re-embedding all symbols
  pending: 2/2
  read: index_stale: index embeddings were left mid-migration by an interrupted `oxide index`; run `oxide index PATH` to finish it (or `--mode lexical` meanwhile)
  status.embedder_current: false
  reindex: embedded=2 reused=0 marker_after=""
fp=other-recipe marker=self vectors=true
  write: reuse
  pending: 0/2
  read: index_stale: index embeddings were left mid-migration by an interrupted `oxide index`; run `oxide index PATH` to finish it (or `--mode lexical` meanwhile)
  status.embedder_current: false
  reindex: embedded=0 reused=2 marker_after=""
fp=other-recipe marker=self vectors=false
  write: reuse
  pending: 2/2
  read: index_stale: index embeddings were left mid-migration by an interrupted `oxide index`; run `oxide index PATH` to finish it (or `--mode lexical` meanwhile)
  status.embedder_current: false
  reindex: embedded=2 reused=0 marker_after=""
fp=other-recipe marker=other vectors=true
  write: an interrupted migration to other-model left this index's vectors mid-flight; re-embedding all symbols under hashed-bow-256
  pending: 2/2
  read: index_stale: index embeddings were left mid-migration by an interrupted `oxide index`; run `oxide index PATH` to finish it (or `--mode lexical` meanwhile)
  status.embedder_current: false
  reindex: embedded=2 reused=0 marker_after=""
fp=other-recipe marker=other vectors=false
  write: an interrupted migration to other-model left this index's vectors mid-flight; re-embedding all symbols under hashed-bow-256
  pending: 2/2
  read: index_stale: index embeddings were left mid-migration by an interrupted `oxide index`; run `oxide index PATH` to finish it (or `--mode lexical` meanwhile)
  status.embedder_current: false
  reindex: embedded=2 reused=0 marker_after=""
fp=other-provider marker=absent vectors=true
  write: embedding space changed (other-model -> hashed-bow-256); re-embedding all symbols
  pending: 2/2
  read: provider_mismatch: index embeddings were built with a different embedding provider or dimension; run `oxide index PATH`
  status.embedder_current: true
  reindex: embedded=2 reused=0 marker_after=""
fp=other-provider marker=absent vectors=false
  write: embedding space changed (other-model -> hashed-bow-256); re-embedding all symbols
  pending: 2/2
  read: provider_mismatch: index embeddings were built with a different embedding provider or dimension; run `oxide index PATH`
  status.embedder_current: true
  reindex: embedded=2 reused=0 marker_after=""
fp=other-provider marker=empty vectors=true
  write: embedding space changed (other-model -> hashed-bow-256); re-embedding all symbols
  pending: 2/2
  read: provider_mismatch: index embeddings were built with a different embedding provider or dimension; run `oxide index PATH`
  status.embedder_current: true
  reindex: embedded=2 reused=0 marker_after=""
fp=other-provider marker=empty vectors=false
  write: embedding space changed (other-model -> hashed-bow-256); re-embedding all symbols
  pending: 2/2
  read: provider_mismatch: index embeddings were built with a different embedding provider or dimension; run `oxide index PATH`
  status.embedder_current: true
  reindex: embedded=2 reused=0 marker_after=""
fp=other-provider marker=unreadable vectors=true
  write: an interrupted embedding migration left an unreadable fingerprint; re-embedding all symbols
  pending: 2/2
  read: index_stale: index embeddings were left mid-migration by an interrupted `oxide index`; run `oxide index PATH` to finish it (or `--mode lexical` meanwhile)
  status.embedder_current: false
  reindex: embedded=2 reused=0 marker_after=""
fp=other-provider marker=unreadable vectors=false
  write: an interrupted embedding migration left an unreadable fingerprint; re-embedding all symbols
  pending: 2/2
  read: index_stale: index embeddings were left mid-migration by an interrupted `oxide index`; run `oxide index PATH` to finish it (or `--mode lexical` meanwhile)
  status.embedder_current: false
  reindex: embedded=2 reused=0 marker_after=""
fp=other-provider marker=self vectors=true
  write: reuse
  pending: 0/2
  read: index_stale: index embeddings were left mid-migration by an interrupted `oxide index`; run `oxide index PATH` to finish it (or `--mode lexical` meanwhile)
  status.embedder_current: false
  reindex: embedded=0 reused=2 marker_after=""
fp=other-provider marker=self vectors=false
  write: reuse
  pending: 2/2
  read: index_stale: index embeddings were left mid-migration by an interrupted `oxide index`; run `oxide index PATH` to finish it (or `--mode lexical` meanwhile)
  status.embedder_current: false
  reindex: embedded=2 reused=0 marker_after=""
fp=other-provider marker=other vectors=true
  write: an interrupted migration to other-model left this index's vectors mid-flight; re-embedding all symbols under hashed-bow-256
  pending: 2/2
  read: index_stale: index embeddings were left mid-migration by an interrupted `oxide index`; run `oxide index PATH` to finish it (or `--mode lexical` meanwhile)
  status.embedder_current: false
  reindex: embedded=2 reused=0 marker_after=""
fp=other-provider marker=other vectors=false
  write: an interrupted migration to other-model left this index's vectors mid-flight; re-embedding all symbols under hashed-bow-256
  pending: 2/2
  read: index_stale: index embeddings were left mid-migration by an interrupted `oxide index`; run `oxide index PATH` to finish it (or `--mode lexical` meanwhile)
  status.embedder_current: false
  reindex: embedded=2 reused=0 marker_after=""
fp=current marker=absent vectors=true
  write: reuse
  pending: 0/2
  read: ok
  status.embedder_current: true
  reindex: embedded=0 reused=2 marker_after=""
fp=current marker=absent vectors=false
  write: reuse
  pending: 2/2
  read: index_stale: index embeddings are incomplete for the current provider; run `oxide index PATH`
  status.embedder_current: true
  reindex: embedded=2 reused=0 marker_after=""
fp=current marker=empty vectors=true
  write: reuse
  pending: 0/2
  read: ok
  status.embedder_current: true
  reindex: embedded=0 reused=2 marker_after=""
fp=current marker=empty vectors=false
  write: reuse
  pending: 2/2
  read: index_stale: index embeddings are incomplete for the current provider; run `oxide index PATH`
  status.embedder_current: true
  reindex: embedded=2 reused=0 marker_after=""
fp=current marker=unreadable vectors=true
  write: an interrupted embedding migration left an unreadable fingerprint; re-embedding all symbols
  pending: 2/2
  read: index_stale: index embeddings were left mid-migration by an interrupted `oxide index`; run `oxide index PATH` to finish it (or `--mode lexical` meanwhile)
  status.embedder_current: false
  reindex: embedded=2 reused=0 marker_after=""
fp=current marker=unreadable vectors=false
  write: an interrupted embedding migration left an unreadable fingerprint; re-embedding all symbols
  pending: 2/2
  read: index_stale: index embeddings were left mid-migration by an interrupted `oxide index`; run `oxide index PATH` to finish it (or `--mode lexical` meanwhile)
  status.embedder_current: false
  reindex: embedded=2 reused=0 marker_after=""
fp=current marker=self vectors=true
  write: reuse
  pending: 0/2
  read: index_stale: index embeddings were left mid-migration by an interrupted `oxide index`; run `oxide index PATH` to finish it (or `--mode lexical` meanwhile)
  status.embedder_current: false
  reindex: embedded=0 reused=2 marker_after=""
fp=current marker=self vectors=false
  write: reuse
  pending: 2/2
  read: index_stale: index embeddings were left mid-migration by an interrupted `oxide index`; run `oxide index PATH` to finish it (or `--mode lexical` meanwhile)
  status.embedder_current: false
  reindex: embedded=2 reused=0 marker_after=""
fp=current marker=other vectors=true
  write: an interrupted migration to other-model left this index's vectors mid-flight; re-embedding all symbols under hashed-bow-256
  pending: 2/2
  read: index_stale: index embeddings were left mid-migration by an interrupted `oxide index`; run `oxide index PATH` to finish it (or `--mode lexical` meanwhile)
  status.embedder_current: false
  reindex: embedded=2 reused=0 marker_after=""
fp=current marker=other vectors=false
  write: an interrupted migration to other-model left this index's vectors mid-flight; re-embedding all symbols under hashed-bow-256
  pending: 2/2
  read: index_stale: index embeddings were left mid-migration by an interrupted `oxide index`; run `oxide index PATH` to finish it (or `--mode lexical` meanwhile)
  status.embedder_current: false
  reindex: embedded=2 reused=0 marker_after=""
"#;
