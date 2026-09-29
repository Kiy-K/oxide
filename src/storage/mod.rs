//! SQLite-backed persistent storage for OXIDE symbols, metadata, vectors, and relations.
//!
//! Responsibilities, one module each: `backend` is the storage contract —
//! the [`IndexRead`] capability the request path gets, the [`IndexWrite`]
//! capability only the indexer gets, and the data they exchange
//! ([`ParsedFile`], [`SymbolRelations`], [`IndexStats`]), with no SQLite in
//! it; `schema` is
//! the persisted format — the exact schema SQL, the version constants and
//! the `meta` keys for identity, generation and in-flight state; `sqlite`
//! is [`SqliteStore`]: connection lifecycle (writer, WAL, foreign keys,
//! schema init, the read-only snapshot connection), every transaction, and
//! the complete `IndexRead`/`IndexWrite` implementation; `row` decodes `symbols`
//! rows and owns the corpus statements and loader. This file only
//! re-exports the public surface at its original `crate::storage::*`
//! paths.

mod backend;
mod row;
mod schema;
mod sqlite;

/// Compatibility name for the pre-#34-S2 combined trait: an alias of
/// [`IndexWrite`], which extends [`IndexRead`]. Nothing in the crate uses
/// it; code states the capability it needs. Known limits versus the old
/// trait: with only this name imported, read methods on a concrete store
/// need [`IndexRead`] in scope too, and an `impl IndexBackend` must now
/// also implement [`IndexRead`]. Kept by #34 S6 as a compatibility alias:
/// `oxide::index::IndexBackend` and `oxide::storage::IndexBackend` were
/// public import paths, and removing them would break sources for no
/// behavioral gain.
pub use backend::IndexWrite as IndexBackend;
pub use backend::{IndexRead, IndexStats, IndexWrite, ParsedFile, SymbolRelations};
pub use row::{CORPUS_SQL, LEAN_CORPUS_SQL};
// The identity keys stay crate-internal: outside the crate the stored
// embedding space is read only through `index::EmbeddingSpace`.
pub(crate) use schema::{
    DIM_KEY, EMBEDDER_KEY, EMBEDDING_FINGERPRINT_KEY, EXTRACTION_VERSION_KEY, SCHEMA_VERSION_KEY,
};
pub use schema::{
    EMBEDDING_MIGRATION_KEY, EXTRACTION_VERSION, INDEX_GENERATION_KEY, INDEX_ID_KEY,
    LEXICAL_INDEX_KEY, LEXICAL_INDEX_VERSION, ROOT_KEY, SCHEMA_VERSION,
};
pub use sqlite::{is_locked_error, SqliteStore, BULK_WAL_AUTOCHECKPOINT_PAGES};
