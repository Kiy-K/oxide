//! Request and result DTOs of the service boundary. Their serialized shape
//! is the CLI/MCP `--json` contract (`docs/review/api-surface.md` SURF-002).

use crate::blast_radius::BlastItem;
use crate::context::{Omitted, Role};
use crate::index::IndexReport;
use crate::retrieval::{RetrievalMode, SearchMode};
use crate::symbols::{Language, SymbolKind};
use serde::Serialize;

#[derive(Debug, Clone, Copy)]
pub struct SearchRequest {
    pub limit: usize,
    pub mode: SearchMode,
    pub expand: bool,
    pub retrieval_mode: RetrievalMode,
    /// Attach each top hit's bounded impact neighborhood
    /// (`blast_radius.rs`). Opt-in and strictly additive: ranking, scores
    /// and hit order are computed before this and never consult it, and
    /// with it off the serialized result is byte-identical to before the
    /// feature existed (`blast_radius` skips serialization when empty).
    pub blast_radius: bool,
}

#[derive(Debug, Serialize)]
pub struct IndexResult {
    pub scanned_files: usize,
    pub changed_files: usize,
    pub reused_files: usize,
    pub removed_files: usize,
    pub new_symbols: usize,
    pub changed_symbols: usize,
    pub deleted_symbols: usize,
    pub embedded_symbols: usize,
    pub reused_embeddings: usize,
    pub embed_failures: usize,
    pub errored_files: usize,
    #[serde(default)]
    pub relations_refreshed_symbols: usize,
    #[serde(skip)]
    pub duration_ms: u128,
    /// Presentation only, like `duration_ms` — never on the wire. Whether
    /// the store held no symbols before this run: the counters alone can't
    /// tell a first build from an edit that happened to touch every file.
    #[serde(skip)]
    pub fresh_index: bool,
    /// Presentation only. Symbols in the store after the run, so a full
    /// build can report the corpus rather than "0 changed".
    #[serde(skip)]
    pub total_symbols: usize,
    /// Presentation only, like the two fields above. Whether the provider
    /// that ran this indexing pass is a remote one (`EmbeddingProvider::
    /// is_remote`) — `cmd_index`'s large-repo hint only fires when this is
    /// `false`, since recommending remote embeddings to someone already
    /// using them would be nonsensical.
    #[serde(skip)]
    pub embedder_is_remote: bool,
}

impl From<IndexReport> for IndexResult {
    fn from(r: IndexReport) -> Self {
        Self {
            scanned_files: r.scanned_files,
            changed_files: r.reparsed_files,
            reused_files: r.unchanged_files,
            removed_files: r.removed_files,
            new_symbols: r.new_symbols,
            changed_symbols: r.changed_symbols,
            deleted_symbols: r.deleted_symbols,
            embedded_symbols: r.embedded_symbols,
            reused_embeddings: r.reused_embeddings,
            embed_failures: r.embed_failures,
            errored_files: r.errored_files,
            relations_refreshed_symbols: r.relations_refreshed_symbols,
            duration_ms: r.duration_ms,
            fresh_index: false,
            total_symbols: 0,
            embedder_is_remote: false,
        }
    }
}

#[derive(Debug, Serialize)]
pub struct StatusResult {
    pub root: String,
    pub index_exists: bool,
    /// Unchanged meaning from before the auto-indexing watcher: files
    /// current AND embedder current AND embedding/symbol counts match.
    /// `base_fresh`/`pending_embeddings` below are the new, finer-grained
    /// fields — added alongside, not a replacement, since existing readers
    /// of `is_current` (tests, the MCP surface) depend on this exact value.
    pub is_current: bool,
    pub embedder_current: bool,
    /// True when every tracked file's on-disk content matches what's
    /// indexed — independent of embedding freshness. A caller that only
    /// needs lexical/structural signals (not semantic search) can treat the
    /// index as usable whenever this is true, even while
    /// `pending_embeddings` is nonzero (auto-indexing watcher: "track base
    /// and semantic freshness independently").
    pub base_fresh: bool,
    /// Symbols whose embedding is missing, stale, or from a different
    /// embedding space than the configured provider — 0 means semantic
    /// search reflects current content. Computed without contacting the
    /// embedder (see `index::content_stale_embedding_count`), so `oxide
    /// status` stays network-free.
    pub pending_embeddings: usize,
    pub files: usize,
    pub symbols: usize,
    pub embeddings: usize,
    pub embedder: Option<String>,
    pub supported_languages: Vec<Language>,
    pub schema_version: u32,
}

#[derive(Debug, Serialize, Clone, PartialEq)]
pub struct Evidence {
    pub id: String,
    pub file: String,
    pub qualified_name: String,
    pub name: String,
    pub kind: SymbolKind,
    pub language: Language,
    pub start_line: u32,
    pub end_line: u32,
    pub score: f32,
    pub reasons: Vec<String>,
    pub snippet: String,
    /// Bounded impact neighborhood, present only when the caller asked for
    /// it *and* this hit was one of the anchored seeds. Absent from JSON
    /// when empty, so every existing consumer sees the shape it always saw.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub blast_radius: Vec<BlastItem>,
}

#[derive(Debug, Serialize)]
pub struct ContextEvidence {
    #[serde(flatten)]
    pub evidence: Evidence,
    pub role: Role,
    pub est_tokens: usize,
}

#[derive(Debug, Serialize)]
pub struct ContextResult {
    pub task: String,
    pub budget_tokens: usize,
    pub used_tokens: usize,
    pub items: Vec<ContextEvidence>,
    pub omitted: Vec<Omitted>,
    #[serde(skip)]
    pub embedder: String,
    /// Non-symbol git provenance; `None` unless `--git` was requested. See
    /// `context.rs::ContextPack::git`.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub git: Option<crate::gitctx::GitEvidence>,
    /// Evidence sources that degraded during this call — see
    /// `context.rs::ContextPack::diagnostics`. Found missing here by
    /// Codex review: `ContextPack` carried this field all along, but
    /// `context()` dropped it when building `ContextResult`, silently
    /// undoing the whole point of surfacing degraded-source visibility in
    /// CLI/MCP JSON output instead of failing silently.
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub diagnostics: Vec<String>,
}
