//! Incremental indexing: scan, parse, freshness, and embedding orchestration.
//!
//! - `base`: which files changed (full scan or the watcher's scoped path
//!   set), deletions, and the relations/lexical backfill for unchanged
//!   files, inside the bulk-write window.
//! - `pipeline`: the per-file path both base entry points share — parse →
//!   references → embedding-input hash → relations + postings → one
//!   `replace_file` transaction per file.
//! - `space`: the embedding-space authority — the one interpretation of
//!   the stored fingerprint and migration marker, for the indexer,
//!   `validate_index`, `status` and research harnesses.
//! - `embed`: the embedding stage — acting on `space`'s verdict (the
//!   provider-migration marker, clearing), (re)embedding, and the closing
//!   `set_meta_all` publish.

// Compatibility re-exports of the storage API at its historical
// `oxide::index::*` paths, used by tests, examples and research harnesses.
// The canonical home is `crate::storage`; the crate's own code imports from
// there (#34 S6).
pub use crate::storage::{
    IndexBackend, IndexRead, IndexStats, IndexWrite, ParsedFile, SqliteStore, SymbolRelations,
    EMBEDDING_MIGRATION_KEY, EXTRACTION_VERSION, LEXICAL_INDEX_KEY, LEXICAL_INDEX_VERSION,
    SCHEMA_VERSION,
};

mod base;
mod embed;
mod pipeline;
mod space;

pub use base::{update_base, update_base_for_files, update_base_reporting};
#[cfg(test)]
pub(crate) use embed::incompatible_stored_space;
pub use embed::{
    content_stale_embedding_count, pending_embedding_count, update_embeddings,
    update_embeddings_reporting,
};
pub use space::{EmbeddingSpace, SpaceRead};

use anyhow::Result;
use serde::Serialize;
use std::path::Path;

/// Outcome of one incremental run; surfaced by the CLI to show work avoided.
#[derive(Debug, Default, Clone, Serialize)]
pub struct IndexReport {
    pub scanned_files: usize,
    pub unchanged_files: usize,
    pub reparsed_files: usize,
    pub removed_files: usize,
    pub new_symbols: usize,
    pub changed_symbols: usize,
    pub deleted_symbols: usize,
    pub embedded_symbols: usize,
    pub reused_embeddings: usize,
    pub duration_ms: u128,
    /// Symbols whose embedding came back empty (endpoint failure): skipped,
    /// not stored, so a later healthy run re-embeds them.
    #[serde(default)]
    pub embed_failures: usize,
    /// Discovered files that could not be read/decoded (non-UTF8, IO error) or
    /// whose language resolution failed unexpectedly during parsing. Not
    /// stored; a later run retries them. Every discovered file must land in
    /// exactly one of unchanged_files + reparsed_files + errored_files.
    #[serde(default)]
    pub errored_files: usize,
    /// Symbols whose structural relations (`symbol_relations`) were
    /// recomputed even though their own file wasn't reparsed this run —
    /// nonzero only under `IndexOptions::force_graph` (`oxide index -g`) or
    /// the one-time legacy-index backfill this same code path also serves.
    #[serde(default)]
    pub relations_refreshed_symbols: usize,
}

/// Explicit rebuild scope for `oxide index`'s `-a`/`-g`/`-e` flags. Each
/// field only widens *which* symbols a stage recomputes — it never changes
/// what "stale" means or skips a stage's own required prerequisite work.
/// `Default` (all `false`) is the plain incremental contract every existing
/// caller of `update_index` already relies on, so adding this type changes
/// no existing behavior.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct IndexOptions {
    /// `-a`/`--all` only: reparse every file regardless of `content_hash`
    /// match, e.g. after upgrading OXIDE for an extractor/grammar fix that
    /// should re-derive symbols even where source text didn't change.
    pub force_reparse: bool,
    /// `-g`/`--graph`: recompute structural relations for every existing
    /// symbol, not just symbols in files reparsed this run.
    pub force_graph: bool,
    /// `-e`/`--embeddings`: recompute every symbol's embedding regardless
    /// of whether its stored embedding's hash already matches.
    pub force_embeddings: bool,
}

impl IndexOptions {
    /// `-a`/`--all`: every layer forced.
    pub fn all() -> Self {
        Self {
            force_reparse: true,
            force_graph: true,
            force_embeddings: true,
        }
    }
}

/// One indexing stage, for progress reporting only. Stages are observed,
/// never steered: a sink cannot change what the pipeline does.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Stage {
    Model,
    Scan,
    Relations,
    Parse,
    Store,
    Embed,
    Finalize,
}

impl Stage {
    pub fn label(self) -> &'static str {
        match self {
            Stage::Model => "Loading semantic model...",
            Stage::Scan => "Scanning files...",
            Stage::Relations => "Resolving references...",
            Stage::Parse => "Parsing source...",
            Stage::Store => "Indexing codebase...",
            Stage::Embed => "Embedding symbols...",
            Stage::Finalize => "Finalizing...",
        }
    }
}

/// Receives progress from the indexing pipeline. `Sync` because the parse
/// and embed stages report from their worker threads. `advance` may be
/// called for every item; a sink that draws should throttle itself.
///
/// `total` on `begin` is `Some(n)` when the item count is known up front
/// (Relations/Parse/Store/Embed all compute it before their loop starts) and
/// `None` for stages with no per-item counter (Model, Scan, Finalize) — a
/// drawing sink uses this to choose a determinate bar vs. an indeterminate
/// spinner without needing to promote itself mid-stage on first `advance`.
pub trait ProgressSink: Sync {
    fn begin(&self, stage: Stage, total: Option<usize>);
    fn advance(&self, stage: Stage, done: usize, total: usize);
    fn end(&self, stage: Stage, summary: &str);
}

/// The sink every non-interactive caller gets: MCP, the watcher, tests.
pub struct NoProgress;

impl ProgressSink for NoProgress {
    fn begin(&self, _: Stage, _: Option<usize>) {}
    fn advance(&self, _: Stage, _: usize, _: usize) {}
    fn end(&self, _: Stage, _: &str) {}
}

fn count_summary(done: usize, total: usize) -> String {
    if total == 0 {
        "nothing to do".to_string()
    } else {
        format!(
            "{}/{}",
            crate::term::thousands(done),
            crate::term::thousands(total)
        )
    }
}

/// Run incremental indexing of the repo at `root` into `store`. Equivalent
/// to `update_index_scoped` with `IndexOptions::default()` — the plain
/// incremental contract every pre-existing caller of this function keeps
/// getting unchanged.
pub fn update_index(
    root: &Path,
    store: &mut dyn IndexWrite,
    embedder: &dyn crate::embeddings::EmbeddingProvider,
) -> Result<IndexReport> {
    update_index_scoped(root, store, embedder, &IndexOptions::default())
}

/// Like [`update_index`], but with explicit forced-rebuild scope. Runs the
/// base stage ([`update_base`]) then the embedding stage
/// ([`update_embeddings`]) back to back and returns one combined report —
/// the same single-call contract `update_index` has always had. Callers
/// that need to report progress *between* the two stages (`oxide index -a`)
/// should call `update_base`/`update_embeddings` directly instead.
pub fn update_index_scoped(
    root: &Path,
    store: &mut dyn IndexWrite,
    embedder: &dyn crate::embeddings::EmbeddingProvider,
    opts: &IndexOptions,
) -> Result<IndexReport> {
    let mut report = update_base(root, store, opts)?;
    update_embeddings(root, store, embedder, opts, &mut report)?;
    Ok(report)
}
