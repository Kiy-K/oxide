//! The process-wide cache a long-lived host keeps between requests, and
//! the `RepositoryService` methods that read and fill it. Keying, the
//! snapshot/`RelationIndex` pairing and embedder reuse live here; when each
//! is consulted within a request is `repository.rs`'s orchestration.

use super::error::{ErrorCode, ServiceError};
use super::repository::RepositoryService;
use crate::embeddings::{open_embedder, EmbeddingProvider};
use crate::relations::RelationIndex;
use crate::retrieval::SymbolSnapshot;
use crate::storage::{
    IndexBackend, IndexStats, SqliteStore, EMBEDDING_MIGRATION_KEY, LEXICAL_INDEX_KEY,
};
use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::{Arc, Mutex, OnceLock};

/// What a long-running host keeps between requests. Everything here is
/// keyed so a stale entry can never be served: a snapshot is reused only
/// when the index's `(index_id, index_generation)` and every
/// compatibility key it depends on (`schema_version`,
/// `extraction_version`, embedding identity, lexical generation) read
/// back identical *inside the request's own read snapshot*, and the
/// embedder is reused only for the same configured provider name. A miss
/// simply loads and replaces. Memory is one symbol snapshot per repository
/// root the process has served, which for MCP is normally one.
#[derive(Default)]
struct ProcessCache {
    snapshots: Mutex<HashMap<PathBuf, Arc<CachedSnapshot>>>,
    embedder: Mutex<Option<(String, Arc<dyn EmbeddingProvider + Send + Sync>)>>,
}

/// `(cache key, matching entry if any)` — `None` when the cache is off or
/// the index cannot be keyed.
pub(super) type CacheLookup = Option<(Vec<String>, Option<Arc<CachedSnapshot>>)>;

pub(super) struct CachedSnapshot {
    key: Vec<String>,
    pub(super) snapshot: SymbolSnapshot,
    /// The structural index over `snapshot.symbols`, built once here so
    /// every request at this generation reuses it (`RetrievalEngine::
    /// with_snapshot_and_index`); it can never outlive the snapshot it
    /// describes because the two share this entry and its key.
    pub(super) index: RelationIndex,
    /// Row counts at this generation — `validate_index`'s completeness
    /// check needs them, and `COUNT(*)` over the embeddings table walks
    /// every row's page, so it is an O(N) read worth keying too.
    stats: IndexStats,
}

static PROCESS_CACHE: OnceLock<ProcessCache> = OnceLock::new();

fn process_cache() -> &'static ProcessCache {
    PROCESS_CACHE.get_or_init(ProcessCache::default)
}

/// Meta values a cached snapshot must match, in a fixed order. `None`
/// when the index carries no generation counter (never touched by a writer
/// that has one), in which case nothing derived from it may be cached.
fn snapshot_key(store: &SqliteStore) -> Result<Option<Vec<String>>, ServiceError> {
    let Some((id, generation)) = store
        .generation()
        .map_err(|e| ServiceError::from_error(ErrorCode::IndexCorrupt, e))?
    else {
        return Ok(None);
    };
    let mut key = vec![id, generation.to_string()];
    for k in [
        "schema_version",
        "extraction_version",
        "embedding_fingerprint",
        "embedder",
        "dim",
        LEXICAL_INDEX_KEY,
        EMBEDDING_MIGRATION_KEY,
    ] {
        key.push(
            store
                .get_meta(k)
                .map_err(|e| ServiceError::from_error(ErrorCode::IndexCorrupt, e))?
                .unwrap_or_default(),
        );
    }
    Ok(Some(key))
}

impl RepositoryService {
    /// The cache entry for this repository at the store's current
    /// generation, if the cache is on, the index is keyable, and an entry
    /// with exactly this key exists.
    pub(super) fn cache_lookup(&self, store: &SqliteStore) -> Result<CacheLookup, ServiceError> {
        if !self.use_process_cache {
            return Ok(None);
        }
        let Some(key) = snapshot_key(store)? else {
            return Ok(None);
        };
        let hit = process_cache()
            .snapshots
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .get(&self.root)
            .filter(|c| c.key == key)
            .map(Arc::clone);
        Ok(Some((key, hit)))
    }

    /// Row counts for validation: from the cache entry when it matches
    /// (identical generation ⇒ identical counts), else counted now.
    pub(super) fn stats_for(
        &self,
        store: &SqliteStore,
        lookup: &CacheLookup,
    ) -> Result<IndexStats, ServiceError> {
        if let Some((_, Some(hit))) = lookup {
            return Ok(hit.stats.clone());
        }
        store
            .stats()
            .map_err(|e| ServiceError::from_error(ErrorCode::IndexCorrupt, e))
    }

    /// The cached snapshot for this repository at the store's current
    /// generation, loading (and replacing) it on a miss. Called only after
    /// `validate_index` accepted the index, so a miss never loads a corpus
    /// the request is about to reject. `None` when the cache is off or the
    /// index cannot be keyed.
    pub(super) fn cached_snapshot(
        &self,
        store: &SqliteStore,
        lookup: CacheLookup,
        stats: &IndexStats,
    ) -> Result<Option<Arc<CachedSnapshot>>, ServiceError> {
        let Some((key, hit)) = lookup else {
            return Ok(None);
        };
        if let Some(hit) = hit {
            return Ok(Some(hit));
        }
        // Loaded inside the same read transaction that produced `key`, so
        // the snapshot cannot describe a different generation than its key.
        let snapshot = SymbolSnapshot::load(store)
            .map_err(|e| ServiceError::from_error(ErrorCode::IndexCorrupt, e))?;
        let index = RelationIndex::build(&snapshot.symbols);
        let entry = Arc::new(CachedSnapshot {
            key,
            snapshot,
            index,
            stats: stats.clone(),
        });
        process_cache()
            .snapshots
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .insert(self.root.clone(), Arc::clone(&entry));
        Ok(Some(entry))
    }

    /// The configured embedding provider, constructed once per process
    /// when the cache is on — `NativeEmbedder::new` loads an ONNX model,
    /// which is most of a small repository's request latency.
    pub(super) fn embedder(
        &self,
    ) -> Result<Arc<dyn EmbeddingProvider + Send + Sync>, ServiceError> {
        if !self.use_process_cache {
            let provider = open_embedder(None)
                .map_err(|e| ServiceError::from_error(ErrorCode::EmbedderUnavailable, e))?;
            return Ok(Arc::from(provider));
        }
        let name = crate::embeddings::configured_provider_name(None);
        let cache = process_cache();
        if let Some((cached_name, provider)) = cache
            .embedder
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .as_ref()
        {
            if *cached_name == name {
                return Ok(Arc::clone(provider));
            }
        }
        let provider: Arc<dyn EmbeddingProvider + Send + Sync> = Arc::from(
            open_embedder(None)
                .map_err(|e| ServiceError::from_error(ErrorCode::EmbedderUnavailable, e))?,
        );
        *cache.embedder.lock().unwrap_or_else(|e| e.into_inner()) =
            Some((name, Arc::clone(&provider)));
        Ok(provider)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::embeddings::HashedEmbedder;
    use crate::index::update_index;
    use crate::service::test_support::seed_repo;

    /// The process cache must be hit on a repeat call at the same
    /// generation and replaced — never served stale — once a write lands.
    #[test]
    fn process_cache_hits_at_the_same_generation_and_reloads_after_a_write() {
        let tmp = tempfile::tempdir().unwrap();
        let root = tmp.path().canonicalize().unwrap();
        seed_repo(&root);
        let emb = HashedEmbedder::default();
        {
            let mut store = SqliteStore::open(&root.join(".oxide").join("index.db")).unwrap();
            update_index(&root, &mut store, &emb).unwrap();
        }
        let service = RepositoryService {
            root: root.clone(),
            use_process_cache: true,
        };
        let store = service.open_index_for_read().unwrap();
        let stats = store.stats().unwrap();
        let lookup = service.cache_lookup(&store).unwrap();
        assert!(
            matches!(lookup, Some((_, None))),
            "keyable, nothing cached yet"
        );
        let first = service
            .cached_snapshot(&store, lookup, &stats)
            .unwrap()
            .unwrap();
        let lookup = service.cache_lookup(&store).unwrap();
        assert!(
            matches!(lookup, Some((_, Some(_)))),
            "second lookup is a hit"
        );
        assert_eq!(service.stats_for(&store, &lookup).unwrap().symbols, 2);
        let second = service
            .cached_snapshot(&store, lookup, &stats)
            .unwrap()
            .unwrap();
        assert!(Arc::ptr_eq(&first, &second), "same generation ⇒ same entry");
        assert!(first.snapshot.with_relations);
        assert_eq!(first.snapshot.symbols.len(), 2); // module + thing
        drop(store);

        std::fs::write(
            root.join("src/thing.py"),
            "def thing():\n    return 1\n\ndef other():\n    return 2\n",
        )
        .unwrap();
        {
            let mut store = SqliteStore::open(&root.join(".oxide").join("index.db")).unwrap();
            update_index(&root, &mut store, &emb).unwrap();
        }
        let store = service.open_index_for_read().unwrap();
        let stats = store.stats().unwrap();
        let lookup = service.cache_lookup(&store).unwrap();
        assert!(matches!(lookup, Some((_, None))), "new generation ⇒ miss");
        let third = service
            .cached_snapshot(&store, lookup, &stats)
            .unwrap()
            .unwrap();
        assert!(!Arc::ptr_eq(&first, &third), "a write must invalidate");
        assert_eq!(third.snapshot.symbols.len(), 3);
        assert_ne!(first.key, third.key);

        let off = RepositoryService {
            root: root.clone(),
            use_process_cache: false,
        };
        assert!(off.cache_lookup(&store).unwrap().is_none());
    }

    /// A deleted-and-rebuilt `.oxide` can reach the very generation the
    /// cached entry was built at; only `index_id` tells the two databases
    /// apart, so the cache must miss on it rather than serve the old corpus.
    /// An index that carries no generation counter is not keyable at all
    /// and must bypass the cache even with it on.
    #[test]
    fn process_cache_misses_on_a_replaced_database_and_skips_an_unkeyable_one() {
        let tmp = tempfile::tempdir().unwrap();
        let root = tmp.path().canonicalize().unwrap();
        seed_repo(&root);
        let db = root.join(".oxide").join("index.db");
        let emb = HashedEmbedder::default();
        let build = || {
            let mut store = SqliteStore::open(&db).unwrap();
            update_index(&root, &mut store, &emb).unwrap();
        };
        build();
        let service = RepositoryService {
            root: root.clone(),
            use_process_cache: true,
        };
        let store = service.open_index_for_read().unwrap();
        let stats = store.stats().unwrap();
        let (id_before, generation_before) = store.generation().unwrap().unwrap();
        let lookup = service.cache_lookup(&store).unwrap();
        let first = service
            .cached_snapshot(&store, lookup, &stats)
            .unwrap()
            .unwrap();
        drop(store);

        for suffix in ["", "-wal", "-shm"] {
            let _ = std::fs::remove_file(format!("{}{suffix}", db.display()));
        }
        build();
        let store = service.open_index_for_read().unwrap();
        let (id_after, generation_after) = store.generation().unwrap().unwrap();
        assert_eq!(
            generation_before, generation_after,
            "same build sequence ⇒ same generation, so only index_id can miss"
        );
        assert_ne!(id_before, id_after);
        let lookup = service.cache_lookup(&store).unwrap();
        assert!(
            matches!(lookup, Some((_, None))),
            "a replaced database must miss"
        );
        let stats = store.stats().unwrap();
        let second = service
            .cached_snapshot(&store, lookup, &stats)
            .unwrap()
            .unwrap();
        assert!(!Arc::ptr_eq(&first, &second));
        assert_ne!(first.key, second.key);
        drop(store);

        {
            let conn = rusqlite::Connection::open(&db).unwrap();
            conn.execute(
                "DELETE FROM meta WHERE key IN ('index_id', 'index_generation')",
                [],
            )
            .unwrap();
        }
        let store = service.open_index_for_read().unwrap();
        assert!(store.generation().unwrap().is_none());
        assert!(
            service.cache_lookup(&store).unwrap().is_none(),
            "an unkeyable index must bypass the cache"
        );
    }
}
