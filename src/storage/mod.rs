//! SQLite-backed persistent storage for OXIDE symbols, metadata, vectors, and relations.
//!
//! Responsibilities, one module each: `backend` is the storage contract —
//! the [`IndexBackend`] trait and the data it exchanges ([`ParsedFile`],
//! [`SymbolRelations`], [`IndexStats`]), with no SQLite in it; `schema` is
//! the persisted format — the exact schema SQL, the version constants and
//! the `meta` keys for identity, generation and in-flight state; `sqlite`
//! is [`SqliteStore`]: connection lifecycle (writer, WAL, foreign keys,
//! schema init, the read-only snapshot connection), every transaction, and
//! the complete `IndexBackend` implementation; `row` decodes `symbols`
//! rows and owns the corpus statements and loader. This file only
//! re-exports the public surface at its original `crate::storage::*`
//! paths.

mod backend;
mod row;
mod schema;
mod sqlite;

pub use backend::{IndexBackend, IndexStats, ParsedFile, SymbolRelations};
pub use row::{CORPUS_SQL, LEAN_CORPUS_SQL};
pub use schema::{
    EMBEDDING_MIGRATION_KEY, EXTRACTION_VERSION, INDEX_GENERATION_KEY, INDEX_ID_KEY,
    LEXICAL_INDEX_KEY, LEXICAL_INDEX_VERSION, SCHEMA_VERSION,
};
pub use sqlite::{is_locked_error, SqliteStore, BULK_WAL_AUTOCHECKPOINT_PAGES};
