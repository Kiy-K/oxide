//! Issue #33: the symbol document-text recipe is part of embedding-space
//! identity.
//!
//! A symbol's `content_hash` covers its `symbol_embed_text`, but only files
//! that are reparsed get a new hash. After a recipe change, unchanged files
//! would keep their old vectors: the stored hashes still match, so the embed
//! stage reuses them. Only a fingerprint mismatch migrates the whole space.
//! These tests drive `update_index` (and the service read path) through that
//! mismatch.
//!
//! Recipe A and recipe B are test-only fingerprint values. Production
//! `symbol_embed_text` is unchanged, so the text and every `content_hash` stay
//! the same across the switch. Any re-embedding here therefore comes from the
//! fingerprint, never from a hash mismatch.

use oxide::embeddings::{
    symbol_embed_text, EmbeddingProvider, EmbeddingSpaceFingerprint, HashedEmbedder,
    SYMBOL_TEXT_RECIPE,
};
use oxide::index::{
    pending_embedding_count, update_base, update_embeddings, update_index, IndexBackend,
    IndexOptions, IndexReport, SqliteStore, EMBEDDING_MIGRATION_KEY,
};
use oxide::retrieval::{RetrievalMode, SearchMode};
use oxide::service::{RepositoryService, SearchRequest};
use std::path::{Path, PathBuf};

/// Pin the offline hashed embedder, and clear any endpoint override, as
/// `tests/provider_migration_recovery.rs` does and for the same reasons:
/// `RepositoryService` resolves its provider from the environment, and
/// `update_index` reads `OXIDE_EMBED_URL` to pick its embed path. The writes
/// run once inside a `Once` that every test calls first, so no test reads
/// the environment while it changes.
fn pin_offline_embedder() {
    static PIN: std::sync::Once = std::sync::Once::new();
    PIN.call_once(|| {
        // SAFETY: the only writes to these variables in this process,
        // serialized by `Once` ahead of every read.
        unsafe {
            std::env::set_var("OXIDE_EMBED_NATIVE", "hashed");
            std::env::remove_var("OXIDE_EMBED_URL");
        }
    });
}

fn write(path: &Path, content: &str) {
    std::fs::create_dir_all(path.parent().unwrap()).unwrap();
    std::fs::write(path, content).unwrap();
}

fn index_path(root: &Path) -> PathBuf {
    root.join(".oxide").join("index.db")
}

/// Three files, so "one file changed" leaves two untouched.
fn seed(root: &Path) {
    write(
        &root.join("auth.py"),
        "def refresh_token(user):\n    return user.token\n\ndef revoke_token(user):\n    user.token = None\n",
    );
    write(
        &root.join("cache.py"),
        "class Cache:\n    def get(self, key):\n        return key\n",
    );
    write(
        &root.join("util.py"),
        "def slugify(text):\n    return text.lower()\n",
    );
}

/// Reports a caller-chosen text recipe and nothing else different: same name,
/// dimension and every other fingerprint field for every instance. Each
/// vector's first component is `tag`, so a stored row shows which provider
/// wrote it.
struct RecipeEmbedder {
    recipe: &'static str,
    tag: f32,
}

impl EmbeddingProvider for RecipeEmbedder {
    fn name(&self) -> &str {
        "recipe-test"
    }
    fn dim(&self) -> usize {
        4
    }
    fn embed(&self, _text: &str) -> Vec<f32> {
        vec![self.tag, 1.0, 1.0, 1.0]
    }
    fn fingerprint(&self) -> EmbeddingSpaceFingerprint {
        EmbeddingSpaceFingerprint {
            document_text_recipe: self.recipe.to_string(),
            ..HashedEmbedder::new(4).fingerprint()
        }
    }
}

const RECIPE_A: &str = "symbol-text:test-a";
const RECIPE_B: &str = "symbol-text:test-b";

/// Every symbol has a vector, and every vector carries `tag`: no row from
/// another space survives.
fn assert_whole_space_is(store: &SqliteStore, tag: f32) {
    let symbols = store.all_symbols().unwrap();
    let embeddings = store.all_embeddings().unwrap();
    assert!(!symbols.is_empty());
    assert_eq!(embeddings.len(), symbols.len());
    for s in &symbols {
        let (_, vec) = embeddings
            .get(&s.id())
            .unwrap_or_else(|| panic!("{} has no embedding", s.qualified_name));
        assert_eq!(
            vec[0], tag,
            "{}:{} kept a vector from another embedding space",
            s.file, s.qualified_name
        );
    }
}

fn stored_fingerprint(store: &SqliteStore) -> EmbeddingSpaceFingerprint {
    let raw = store.get_meta("embedding_fingerprint").unwrap().unwrap();
    serde_json::from_str(&raw).unwrap()
}

#[test]
fn recipe_change_migrates_the_whole_embedding_space() {
    pin_offline_embedder();
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path().canonicalize().unwrap();
    seed(&root);
    let mut store = SqliteStore::open(&index_path(&root)).unwrap();

    let a = RecipeEmbedder {
        recipe: RECIPE_A,
        tag: 1.0,
    };
    let b = RecipeEmbedder {
        recipe: RECIPE_B,
        tag: 2.0,
    };
    let built = update_index(&root, &mut store, &a).unwrap();
    let symbols = built.embedded_symbols;
    assert!(symbols > 0);
    assert_whole_space_is(&store, 1.0);

    // Only the recipe differs; the text and content hashes are unchanged.
    assert_eq!(pending_embedding_count(&store, &b).unwrap(), symbols);
    let migrated = update_index(&root, &mut store, &b).unwrap();
    assert_eq!(migrated.reused_embeddings, 0);
    assert_eq!(migrated.embedded_symbols, symbols);
    assert_eq!(migrated.reparsed_files, 0, "no file was reparsed");
    assert_whole_space_is(&store, 2.0);
    assert_eq!(stored_fingerprint(&store).document_text_recipe, RECIPE_B);
    assert_eq!(
        store.get_meta(EMBEDDING_MIGRATION_KEY).unwrap().as_deref(),
        Some(""),
        "the migration must be published as complete"
    );
    assert_eq!(pending_embedding_count(&store, &b).unwrap(), 0);
}

#[test]
fn same_recipe_reopen_is_a_no_op() {
    pin_offline_embedder();
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path().canonicalize().unwrap();
    seed(&root);
    let mut store = SqliteStore::open(&index_path(&root)).unwrap();

    let a = RecipeEmbedder {
        recipe: RECIPE_A,
        tag: 1.0,
    };
    let symbols = update_index(&root, &mut store, &a)
        .unwrap()
        .embedded_symbols;
    let before = stored_fingerprint(&store);

    // A fresh instance of the same space: a new process opening the index.
    let a_again = RecipeEmbedder {
        recipe: RECIPE_A,
        tag: 3.0,
    };
    assert_eq!(pending_embedding_count(&store, &a_again).unwrap(), 0);
    let report = update_index(&root, &mut store, &a_again).unwrap();
    assert_eq!(report.embedded_symbols, 0);
    assert_eq!(report.reused_embeddings, symbols);
    assert_eq!(report.reparsed_files, 0);
    assert_whole_space_is(&store, 1.0);
    assert_eq!(stored_fingerprint(&store), before);
}

/// The bug #33 describes. Under a recipe change, a file edit refreshes only
/// that file's content hashes; the other files' stored hashes still match
/// their stored vectors. Without a fingerprint mismatch they would keep
/// recipe-A vectors next to the edited file's recipe-B ones.
#[test]
fn recipe_change_with_one_edited_file_leaves_no_unchanged_file_on_the_old_recipe() {
    pin_offline_embedder();
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path().canonicalize().unwrap();
    seed(&root);
    let mut store = SqliteStore::open(&index_path(&root)).unwrap();

    let a = RecipeEmbedder {
        recipe: RECIPE_A,
        tag: 1.0,
    };
    update_index(&root, &mut store, &a).unwrap();
    assert_whole_space_is(&store, 1.0);

    write(
        &root.join("util.py"),
        "def slugify(text):\n    return text.lower().strip()\n",
    );
    let b = RecipeEmbedder {
        recipe: RECIPE_B,
        tag: 2.0,
    };
    let report = update_index(&root, &mut store, &b).unwrap();
    assert_eq!(report.reparsed_files, 1, "only util.py was reparsed");
    assert_eq!(report.reused_embeddings, 0);
    let symbols = store.all_symbols().unwrap();
    assert_eq!(report.embedded_symbols, symbols.len());
    let untouched: Vec<_> = symbols.iter().filter(|s| s.file != "util.py").collect();
    assert!(!untouched.is_empty());
    assert_whole_space_is(&store, 2.0);
}

/// Same name, dimension and (bar the recipe) fingerprint as
/// `HashedEmbedder::default()`, with recognisably different vectors, so a
/// row surviving from it is visible.
struct LegacyHashed;

impl EmbeddingProvider for LegacyHashed {
    fn name(&self) -> &str {
        "hashed-bow-256"
    }
    fn dim(&self) -> usize {
        256
    }
    fn embed(&self, _text: &str) -> Vec<f32> {
        vec![0.5; 256]
    }
}

fn hybrid_request() -> SearchRequest {
    SearchRequest {
        limit: 5,
        mode: SearchMode::Hybrid,
        expand: false,
        retrieval_mode: RetrievalMode::default(),
        blast_radius: false,
    }
}

/// From a legacy index state (vectors from `LegacyHashed`, stored identity
/// not the current one), the current build must refuse the old space,
/// re-embed every symbol through `update_index`, publish today's full
/// fingerprint, and settle into an ordinary no-op index.
fn assert_legacy_index_migrates_to_the_current_space(root: &Path) {
    let current = HashedEmbedder::default();
    let service = RepositoryService::discover(Some(root.to_str().unwrap())).unwrap();

    let store = SqliteStore::open(&index_path(root)).unwrap();
    let symbols = store.all_symbols().unwrap().len();
    assert!(symbols > 0);
    assert_eq!(store.all_embeddings().unwrap().len(), symbols);
    assert_eq!(pending_embedding_count(&store, &current).unwrap(), symbols);
    drop(store);

    // Status, network-free, must not call the legacy space current, and the
    // read side refuses it instead of scoring against it.
    let status = service.status().unwrap();
    assert!(!status.embedder_current, "{status:?}");
    assert!(!status.is_current, "{status:?}");
    let err = service
        .search("refresh token", hybrid_request())
        .unwrap_err();
    assert_eq!(err.code(), "provider_mismatch", "{err:?}");

    let mut store = SqliteStore::open(&index_path(root)).unwrap();
    let report = update_index(root, &mut store, &current).unwrap();
    assert_eq!(report.reused_embeddings, 0);
    assert_eq!(report.embedded_symbols, symbols);
    assert_eq!(report.reparsed_files, 0);
    let embeddings = store.all_embeddings().unwrap();
    assert_eq!(embeddings.len(), symbols);
    for s in store.all_symbols().unwrap() {
        let (_, vec) = &embeddings[&s.id()];
        assert_eq!(
            *vec,
            current.embed_document(&symbol_embed_text(&s)),
            "{} kept a legacy vector",
            s.qualified_name
        );
    }
    let published = stored_fingerprint(&store);
    assert_eq!(published, current.fingerprint());
    assert_eq!(published.document_text_recipe, SYMBOL_TEXT_RECIPE);
    assert_eq!(
        store.get_meta(EMBEDDING_MIGRATION_KEY).unwrap().as_deref(),
        Some("")
    );
    drop(store);

    let status = service.status().unwrap();
    assert!(status.embedder_current, "{status:?}");
    assert!(status.is_current, "{status:?}");
    service.search("refresh token", hybrid_request()).unwrap();

    // And from here on it is an ordinary current index.
    let mut store = SqliteStore::open(&index_path(root)).unwrap();
    let again = update_index(root, &mut store, &current).unwrap();
    assert_eq!(again.embedded_symbols, 0);
    assert_eq!(again.reused_embeddings, symbols);
}

/// An index published before `document_text_recipe` existed: its stored
/// fingerprint has schema 1 and no recipe field. Built through the real
/// pipeline, then given exactly that legacy fingerprint.
#[test]
fn legacy_fingerprint_without_a_recipe_is_readable_and_forces_a_full_reembed() {
    pin_offline_embedder();
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path().canonicalize().unwrap();
    seed(&root);

    let mut store = SqliteStore::open(&index_path(&root)).unwrap();
    update_index(&root, &mut store, &LegacyHashed).unwrap();
    let mut legacy = serde_json::to_value(HashedEmbedder::default().fingerprint()).unwrap();
    let obj = legacy.as_object_mut().unwrap();
    obj.remove("document_text_recipe").unwrap();
    obj.insert("schema_version".into(), 1.into());
    store
        .set_meta("embedding_fingerprint", &legacy.to_string())
        .unwrap();

    // Readable, not "unreadable": it parses, as schema 1 with no recipe.
    let stored = stored_fingerprint(&store);
    assert_eq!(stored.schema_version, 1);
    assert_eq!(stored.document_text_recipe, "");
    assert_ne!(stored, HashedEmbedder::default().fingerprint());
    drop(store);

    assert_legacy_index_migrates_to_the_current_space(&root);
}

/// An index whose vectors predate stored fingerprints entirely: `embedder`
/// and `dim` still name the current provider exactly, but cannot vouch for
/// the text recipe, so the vectors are of unknown space.
#[test]
fn index_with_vectors_and_no_stored_fingerprint_forces_a_full_reembed() {
    pin_offline_embedder();
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path().canonicalize().unwrap();
    seed(&root);

    let mut store = SqliteStore::open(&index_path(&root)).unwrap();
    update_index(&root, &mut store, &LegacyHashed).unwrap();
    drop(store);
    let conn = rusqlite::Connection::open(index_path(&root)).unwrap();
    conn.execute("DELETE FROM meta WHERE key = 'embedding_fingerprint'", [])
        .unwrap();
    drop(conn);

    let store = SqliteStore::open(&index_path(&root)).unwrap();
    assert_eq!(store.get_meta("embedding_fingerprint").unwrap(), None);
    assert_eq!(
        store.get_meta("embedder").unwrap().as_deref(),
        Some(HashedEmbedder::default().name())
    );
    assert_eq!(store.get_meta("dim").unwrap().as_deref(), Some("256"));
    drop(store);

    assert_legacy_index_migrates_to_the_current_space(&root);
}

/// A new, base-only index has no fingerprint and no vectors: there is
/// nothing to migrate, and the first embedding run simply embeds and
/// publishes the current fingerprint.
#[test]
fn empty_index_without_a_fingerprint_initializes_normally() {
    pin_offline_embedder();
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path().canonicalize().unwrap();
    seed(&root);
    let current = HashedEmbedder::default();

    let mut store = SqliteStore::open(&index_path(&root)).unwrap();
    update_base(&root, &mut store, &IndexOptions::default()).unwrap();
    assert_eq!(store.get_meta("embedding_fingerprint").unwrap(), None);
    assert!(store.all_embeddings().unwrap().is_empty());
    let symbols = store.all_symbols().unwrap().len();
    assert_eq!(pending_embedding_count(&store, &current).unwrap(), symbols);

    let mut report = IndexReport::default();
    update_embeddings(
        &root,
        &mut store,
        &current,
        &IndexOptions::default(),
        &mut report,
    )
    .unwrap();
    assert_eq!(report.embedded_symbols, symbols);
    assert_eq!(report.reused_embeddings, 0);
    assert_eq!(stored_fingerprint(&store), current.fingerprint());
    assert_eq!(pending_embedding_count(&store, &current).unwrap(), 0);
    assert_eq!(
        store.get_meta(EMBEDDING_MIGRATION_KEY).unwrap().as_deref(),
        Some("")
    );

    let again = update_index(&root, &mut store, &current).unwrap();
    assert_eq!(again.embedded_symbols, 0);
    assert_eq!(again.reused_embeddings, symbols);
}

/// A first embedding run killed after committing vectors but before
/// publishing its identity leaves vectors with no stored fingerprint. The
/// run marked its space in flight first, so the next run can prove those
/// rows are its own and resumes rather than treating them as unversioned.
#[test]
fn interrupted_first_embedding_run_resumes_instead_of_reembedding() {
    pin_offline_embedder();
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path().canonicalize().unwrap();
    seed(&root);
    let current = HashedEmbedder::default();

    let mut store = SqliteStore::open(&index_path(&root)).unwrap();
    update_base(&root, &mut store, &IndexOptions::default()).unwrap();
    let symbols = store.all_symbols().unwrap().len();
    drop(store);

    // Abort the closing publish, exactly where a killed process would stop.
    let conn = rusqlite::Connection::open(index_path(&root)).unwrap();
    conn.execute_batch(
        "CREATE TRIGGER block_publish BEFORE INSERT ON meta
             WHEN NEW.key = 'embedding_fingerprint'
             BEGIN SELECT RAISE(ABORT, 'simulated interruption'); END;",
    )
    .unwrap();
    let mut store = SqliteStore::open(&index_path(&root)).unwrap();
    let mut report = IndexReport::default();
    update_embeddings(
        &root,
        &mut store,
        &current,
        &IndexOptions::default(),
        &mut report,
    )
    .expect_err("the trigger must abort the closing identity write");
    drop(store);
    conn.execute_batch("DROP TRIGGER block_publish;").unwrap();
    drop(conn);

    let mut store = SqliteStore::open(&index_path(&root)).unwrap();
    assert_eq!(store.get_meta("embedding_fingerprint").unwrap(), None);
    assert_eq!(store.all_embeddings().unwrap().len(), symbols);
    let marker = store.get_meta(EMBEDDING_MIGRATION_KEY).unwrap().unwrap();
    assert_eq!(
        serde_json::from_str::<EmbeddingSpaceFingerprint>(&marker).unwrap(),
        current.fingerprint()
    );
    assert_eq!(pending_embedding_count(&store, &current).unwrap(), 0);

    let mut report = IndexReport::default();
    update_embeddings(
        &root,
        &mut store,
        &current,
        &IndexOptions::default(),
        &mut report,
    )
    .unwrap();
    assert_eq!(report.embedded_symbols, 0);
    assert_eq!(report.reused_embeddings, symbols);
    assert_eq!(stored_fingerprint(&store), current.fingerprint());
    assert_eq!(
        store.get_meta(EMBEDDING_MIGRATION_KEY).unwrap().as_deref(),
        Some("")
    );

    // A different provider still cannot adopt those rows.
    let other = RecipeEmbedder {
        recipe: RECIPE_B,
        tag: 2.0,
    };
    assert_eq!(pending_embedding_count(&store, &other).unwrap(), symbols);
}
