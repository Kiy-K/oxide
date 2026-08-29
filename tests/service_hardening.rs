//! Service-boundary hardening: read/write invariants, error taxonomy,
//! version/provider compatibility. Exercises `oxide::service` directly
//! (not the CLI binary) so these stay meaningful for a future MCP adapter
//! that calls `RepositoryService` without going through `cli.rs`.

use oxide::embeddings::HashedEmbedder;
use oxide::index::{update_index, IndexBackend, SqliteStore};
use oxide::retrieval::SearchMode;
use oxide::service::{RepositoryService, SearchRequest};
use std::path::Path;

fn write(path: &Path, content: &str) {
    std::fs::create_dir_all(path.parent().unwrap()).unwrap();
    std::fs::write(path, content).unwrap();
}

#[test]
fn dimension_mismatch_under_same_provider_name_is_a_structured_error() {
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path();
    write(&root.join("src/thing.py"), "def thing():\n    return 1\n");

    // Index with a 128-dim hashed embedder. `HashedEmbedder::name()` returns
    // the fixed string "hashed-bow-256" regardless of `dim`, which is exactly
    // the "same identity, different shape" scenario a real model swap could
    // produce (e.g. a server-side quantization change that alters output
    // width without changing the configured name/URL).
    {
        let mut store = SqliteStore::open(&root.join(".oxide").join("index.db")).unwrap();
        update_index(root, &mut store, &HashedEmbedder::new(128)).unwrap();
    }

    // search()/context() open the default (256-dim) embedder internally.
    let service = RepositoryService::discover(Some(root.to_str().unwrap())).unwrap();
    let err = service
        .search(
            "thing",
            SearchRequest {
                limit: 5,
                mode: SearchMode::VectorOnly,
                expand: false,
            },
        )
        .unwrap_err();
    assert_eq!(err.code(), "provider_mismatch");

    let err = service.context("understand thing", 512).unwrap_err();
    assert_eq!(err.code(), "provider_mismatch");

    // Lexical-only search never needs a semantic provider, so a dimension
    // mismatch must not block it: a dim/name check must not "fall back" to
    // failing operations that never asked for embeddings in the first place.
    let hits = service
        .search(
            "thing",
            SearchRequest {
                limit: 5,
                mode: SearchMode::LexicalOnly,
                expand: false,
            },
        )
        .unwrap();
    assert!(!hits.is_empty());
}

#[test]
fn incompatible_index_version_is_a_structured_error_not_a_guess() {
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path();
    write(&root.join("src/thing.py"), "def thing():\n    return 1\n");
    let db_path = root.join(".oxide").join("index.db");
    {
        let mut store = SqliteStore::open(&db_path).unwrap();
        update_index(root, &mut store, &HashedEmbedder::default()).unwrap();
        // Simulate an index written by an incompatible future binary.
        store.set_meta("schema_version", "999").unwrap();
    }

    let service = RepositoryService::discover(Some(root.to_str().unwrap())).unwrap();
    let err = service
        .search(
            "thing",
            SearchRequest {
                limit: 5,
                mode: SearchMode::LexicalOnly,
                expand: false,
            },
        )
        .unwrap_err();
    assert_eq!(err.code(), "index_incompatible");

    let err = service.context("understand thing", 128).unwrap_err();
    assert_eq!(err.code(), "index_incompatible");
}

#[test]
fn index_without_version_meta_is_incompatible_not_legacy_compatible() {
    // v0.1 has no installed base to preserve compatibility for. An index
    // missing schema_version/extraction_version meta (corrupt write, or a
    // hypothetical pre-tracking binary) must fail explicit and force a
    // reindex rather than being silently trusted.
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path();
    write(&root.join("src/thing.py"), "def thing():\n    return 1\n");
    let db_path = root.join(".oxide").join("index.db");
    {
        let mut store = SqliteStore::open(&db_path).unwrap();
        update_index(root, &mut store, &HashedEmbedder::default()).unwrap();
    }
    {
        let conn = rusqlite::Connection::open(&db_path).unwrap();
        conn.execute(
            "DELETE FROM meta WHERE key IN ('schema_version', 'extraction_version')",
            [],
        )
        .unwrap();
    }

    let service = RepositoryService::discover(Some(root.to_str().unwrap())).unwrap();
    let err = service
        .search(
            "thing",
            SearchRequest {
                limit: 5,
                mode: SearchMode::LexicalOnly,
                expand: false,
            },
        )
        .unwrap_err();
    assert_eq!(err.code(), "index_incompatible");
    assert_eq!(err.action(), oxide::service::ErrorAction::Repair);
}
