//! Stable application-facing operations for the agent CLI.
//!
//! This module keeps repository lifecycle, error classification, and wire DTOs
//! out of the argument parser. Retrieval and context algorithms stay below it.

use crate::context::{build_context, ContextOptions, Omitted, Role};
use crate::embeddings::{open_embedder, EmbeddingProvider, HashedEmbedder};
use crate::index::{update_index, IndexBackend, IndexReport, IndexStats, SqliteStore};
use crate::retrieval::{RetrievalEngine, SearchMode, SearchOptions};
use crate::review::{build_review_context, ReviewContext};
use crate::scanner;
use crate::symbols::{Language, Symbol, SymbolKind};
use serde::Serialize;
use std::cmp::Ordering;
use std::collections::HashMap;
use std::path::{Path, PathBuf};

const MAX_SEARCH_RESULTS: usize = 100;

/// Small, stable application error taxonomy. One variant per distinct
/// failure semantic already present in the service boundary (not one per
/// call site): a caller can `match` on this to decide retry / index / repair
/// / fall back / stop without parsing `message`. `as_str()` is the wire code
/// in JSON error output and is part of the stable contract — do not rename
/// an existing variant's string.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ErrorCode {
    RepositoryNotFound,
    RepositoryUnsupported,
    IndexMissing,
    IndexEmpty,
    IndexStale,
    IndexIncompatible,
    IndexCorrupt,
    ProviderMismatch,
    EmbedderUnavailable,
    IndexFailed,
    SearchFailed,
    ContextFailed,
    ReviewFailed,
    StatusFailed,
}

/// What a caller should do about an [`ErrorCode`], independent of the
/// human-readable message.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ErrorAction {
    /// Run `oxide index PATH`, then retry.
    Index,
    /// The index is unusable as-is: delete `.oxide` and reindex from scratch.
    Repair,
    /// Likely transient (lock contention, network hiccup); retry the same call.
    Retry,
    /// Degrade gracefully (e.g. lexical-only search) instead of failing outright.
    FallBack,
    /// Not fixable by retrying; the input, path, or environment needs to change.
    Stop,
}

impl ErrorCode {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::RepositoryNotFound => "repository_not_found",
            Self::RepositoryUnsupported => "no_source_files",
            Self::IndexMissing => "index_missing",
            Self::IndexEmpty => "index_empty",
            Self::IndexStale => "index_stale",
            Self::IndexIncompatible => "index_incompatible",
            Self::IndexCorrupt => "index_unreadable",
            Self::ProviderMismatch => "provider_mismatch",
            Self::EmbedderUnavailable => "embedder_unavailable",
            Self::IndexFailed => "index_failed",
            Self::SearchFailed => "search_failed",
            Self::ContextFailed => "context_failed",
            Self::ReviewFailed => "review_failed",
            Self::StatusFailed => "status_failed",
        }
    }

    pub fn action(&self) -> ErrorAction {
        use ErrorAction::*;
        match self {
            Self::RepositoryNotFound | Self::RepositoryUnsupported => Stop,
            Self::IndexMissing | Self::IndexEmpty | Self::IndexStale | Self::ProviderMismatch => {
                Index
            }
            Self::IndexIncompatible | Self::IndexCorrupt => Repair,
            Self::EmbedderUnavailable => FallBack,
            Self::IndexFailed
            | Self::SearchFailed
            | Self::ContextFailed
            | Self::ReviewFailed
            | Self::StatusFailed => Retry,
        }
    }
}

impl ErrorAction {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Index => "index",
            Self::Repair => "repair",
            Self::Retry => "retry",
            Self::FallBack => "fall_back",
            Self::Stop => "stop",
        }
    }
}

#[derive(Debug)]
pub struct ServiceError {
    code: ErrorCode,
    message: String,
}

impl ServiceError {
    fn new(code: ErrorCode, message: impl Into<String>) -> Self {
        Self {
            code,
            message: message.into(),
        }
    }

    fn from_error(code: ErrorCode, error: impl std::fmt::Display) -> Self {
        Self::new(code, error.to_string())
    }

    pub fn code(&self) -> &'static str {
        self.code.as_str()
    }

    pub fn action(&self) -> ErrorAction {
        self.code.action()
    }

    pub fn message(&self) -> &str {
        &self.message
    }
}

impl std::fmt::Display for ServiceError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.message)
    }
}

impl std::error::Error for ServiceError {}

#[derive(Debug, Clone, Copy)]
pub struct SearchRequest {
    pub limit: usize,
    pub mode: SearchMode,
    pub expand: bool,
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
    #[serde(skip)]
    pub duration_ms: u128,
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
            duration_ms: r.duration_ms,
        }
    }
}

#[derive(Debug, Serialize)]
pub struct StatusResult {
    pub root: String,
    pub index_exists: bool,
    pub is_current: bool,
    pub embedder_current: bool,
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
}

pub struct RepositoryService {
    root: PathBuf,
}

impl RepositoryService {
    pub fn discover(explicit: Option<&str>) -> Result<Self, ServiceError> {
        let root = if let Some(path) = explicit {
            PathBuf::from(path)
        } else {
            let mut current = std::env::current_dir()
                .map_err(|e| ServiceError::from_error(ErrorCode::RepositoryNotFound, e))?;
            loop {
                if current.join(".git").exists() || current.join(".oxide").exists() {
                    break current;
                }
                if !current.pop() {
                    return Err(ServiceError::new(
                        ErrorCode::RepositoryNotFound,
                        "not inside a repository; pass a repository path",
                    ));
                }
            }
        };
        let root = root.canonicalize().map_err(|e| {
            ServiceError::from_error(
                ErrorCode::RepositoryNotFound,
                format!("cannot find repository {}: {e}", root.display()),
            )
        })?;
        if !root.is_dir() {
            return Err(ServiceError::new(
                ErrorCode::RepositoryNotFound,
                format!("repository path is not a directory: {}", root.display()),
            ));
        }
        Ok(Self { root })
    }

    pub fn root(&self) -> &Path {
        &self.root
    }

    pub fn index(&self, embedder_url: Option<&str>) -> Result<IndexResult, ServiceError> {
        if scanner::scan_repo(&self.root)
            .map_err(|e| ServiceError::from_error(ErrorCode::IndexFailed, e))?
            .is_empty()
        {
            return Err(ServiceError::new(
                ErrorCode::RepositoryUnsupported,
                format!(
                    "no supported source files found under {}",
                    self.root.display()
                ),
            ));
        }
        let embedder = open_embedder(embedder_url)
            .map_err(|e| ServiceError::from_error(ErrorCode::EmbedderUnavailable, e))?;
        let mut store = self.open_index_for_write()?;
        let result: IndexResult = update_index(&self.root, &mut store, embedder.as_ref())
            .map_err(|e| ServiceError::from_error(ErrorCode::IndexFailed, e))?
            .into();
        if result.embed_failures > 0 || !embedder.is_available() {
            return Err(ServiceError::new(
                ErrorCode::EmbedderUnavailable,
                format!(
                    "embedding provider {} failed for {} symbol(s)",
                    embedder.name(),
                    result.embed_failures
                ),
            ));
        }
        Ok(result)
    }

    pub fn status(&self) -> Result<StatusResult, ServiceError> {
        let path = self.index_path();
        if !path.exists() {
            return Ok(StatusResult {
                root: self.root.display().to_string(),
                index_exists: false,
                is_current: false,
                embedder_current: false,
                files: 0,
                symbols: 0,
                embeddings: 0,
                embedder: None,
                supported_languages: supported_languages(),
                schema_version: crate::index::SCHEMA_VERSION,
            });
        }
        let store = self.open_index_for_read()?;
        let stats = store
            .stats()
            .map_err(|e| ServiceError::from_error(ErrorCode::IndexCorrupt, e))?;
        let indexed = store
            .file_hashes()
            .map_err(|e| ServiceError::from_error(ErrorCode::IndexCorrupt, e))?;
        let current = current_file_hashes(&self.root)
            .map_err(|e| ServiceError::from_error(ErrorCode::StatusFailed, e))?;
        let embedder = store
            .get_meta("embedder")
            .map_err(|e| ServiceError::from_error(ErrorCode::IndexCorrupt, e))?;
        let embedder_current =
            embedder.as_deref() == Some(crate::embeddings::configured_provider_name(None).as_str());
        let files_current = !current.is_empty()
            && current.len() == indexed.len()
            && current
                .iter()
                .all(|(file, hash)| indexed.get(file).copied() == Some(*hash));
        Ok(StatusResult {
            root: self.root.display().to_string(),
            index_exists: true,
            is_current: files_current && embedder_current && stats.embeddings == stats.symbols,
            embedder_current,
            files: stats.files,
            symbols: stats.symbols,
            embeddings: stats.embeddings,
            embedder,
            supported_languages: supported_languages(),
            schema_version: crate::index::SCHEMA_VERSION,
        })
    }

    pub fn search(
        &self,
        query: &str,
        request: SearchRequest,
    ) -> Result<Vec<Evidence>, ServiceError> {
        let store = self.open_index_for_read()?;
        let provider: Box<dyn EmbeddingProvider> = if request.mode == SearchMode::LexicalOnly {
            Box::new(HashedEmbedder::default())
        } else {
            open_embedder(None)
                .map_err(|e| ServiceError::from_error(ErrorCode::EmbedderUnavailable, e))?
        };
        self.validate_index(
            &store,
            (request.mode != SearchMode::LexicalOnly).then_some((provider.name(), provider.dim())),
        )?;
        let engine = RetrievalEngine::new(&store, provider.as_ref());
        let hits = engine
            .search(
                query,
                &SearchOptions {
                    limit: request.limit.min(MAX_SEARCH_RESULTS),
                    mode: request.mode,
                    expand: request.expand,
                },
            )
            .map_err(|e| ServiceError::from_error(ErrorCode::SearchFailed, e))?;
        if !provider.is_available() {
            return Err(ServiceError::new(
                ErrorCode::EmbedderUnavailable,
                "embedding provider became unavailable during search",
            ));
        }
        let evidence: Vec<_> = hits
            .into_iter()
            .map(|mut hit| {
                hit.snippet = crate::retrieval::read_snippet(
                    &self.root.join(&hit.symbol.file),
                    hit.symbol.start_line,
                    hit.symbol.end_line,
                    40,
                );
                Evidence::from_symbol(&hit.symbol, hit.score, hit.reasons, hit.snippet)
            })
            .collect();
        Ok(evidence)
    }

    pub fn context(&self, task: &str, budget_tokens: usize) -> Result<ContextResult, ServiceError> {
        let store = self.open_index_for_read()?;
        let provider = open_embedder(None)
            .map_err(|e| ServiceError::from_error(ErrorCode::EmbedderUnavailable, e))?;
        self.validate_index(&store, Some((provider.name(), provider.dim())))?;
        let pack = build_context(
            &self.root,
            &store,
            provider.as_ref(),
            task,
            &ContextOptions {
                budget_tokens,
                ..ContextOptions::default()
            },
        )
        .map_err(|e| ServiceError::from_error(ErrorCode::ContextFailed, e))?;
        if !provider.is_available() {
            return Err(ServiceError::new(
                ErrorCode::EmbedderUnavailable,
                "embedding provider became unavailable during context retrieval",
            ));
        }
        let mut items: Vec<_> = pack
            .items
            .into_iter()
            .map(|item| ContextEvidence {
                evidence: Evidence::from_symbol(
                    &item.symbol,
                    item.score,
                    item.reasons,
                    item.snippet,
                ),
                role: item.role,
                est_tokens: item.est_tokens,
            })
            .collect();
        items.sort_by(compare_context_evidence);
        Ok(ContextResult {
            task: pack.task,
            budget_tokens: pack.budget_tokens,
            used_tokens: pack.used_tokens,
            items,
            omitted: pack.omitted,
            embedder: provider.name().to_string(),
        })
    }
    pub fn stats(&self) -> Result<IndexStats, ServiceError> {
        let store = self.open_index_for_read()?;
        store
            .stats()
            .map_err(|e| ServiceError::from_error(ErrorCode::IndexCorrupt, e))
    }

    pub fn review(&self, diff: &str) -> Result<ReviewContext, ServiceError> {
        let store = self.open_index_for_read()?;
        let provider = open_embedder(None)
            .map_err(|e| ServiceError::from_error(ErrorCode::EmbedderUnavailable, e))?;
        self.validate_index(&store, Some((provider.name(), provider.dim())))?;
        let context = build_review_context(&self.root, &store, provider.as_ref(), diff)
            .map_err(|e| ServiceError::from_error(ErrorCode::ReviewFailed, e))?;
        if !provider.is_available() {
            return Err(ServiceError::new(
                ErrorCode::EmbedderUnavailable,
                "embedding provider became unavailable during review retrieval",
            ));
        }
        Ok(context)
    }

    /// `expected_embedder` is `(provider name, provider dimension)`: both must
    /// match what is stored, not just the name — a server that changes
    /// dimension while keeping the same name/URL would otherwise silently
    /// drop every semantic hit (vectors of mismatched length are skipped by
    /// the retrieval engine) instead of failing explicitly.
    fn validate_index(
        &self,
        store: &SqliteStore,
        expected_embedder: Option<(&str, usize)>,
    ) -> Result<(), ServiceError> {
        let indexed_root = store
            .get_meta("root")
            .map_err(|e| ServiceError::from_error(ErrorCode::IndexCorrupt, e))?;
        if indexed_root.as_deref() != Some(self.root.to_string_lossy().as_ref()) {
            // A missing `root` key means every closing meta write in
            // `update_index` was skipped (they land atomically as one
            // group, see `set_meta_all`) — most often an index whose first
            // build was interrupted before completing, not literally a
            // different repository. The action (Repair: reindex) is the
            // same either way, but the message should not mislead whoever
            // reads it while debugging.
            let reason = if indexed_root.is_none() {
                "index has no recorded root (likely an interrupted or never-completed build)"
            } else {
                "index belongs to a different repository"
            };
            return Err(ServiceError::new(
                ErrorCode::IndexIncompatible,
                format!("{reason}; run `oxide index PATH`"),
            ));
        }
        // v0.1 has no installed base to stay backward-compatible with, so a
        // version mismatch OR a missing meta key both mean this binary must
        // not guess how to read the index: reindex is the unambiguous fix
        // in either case.
        for (key, current) in [
            ("schema_version", crate::index::SCHEMA_VERSION),
            ("extraction_version", crate::index::EXTRACTION_VERSION),
        ] {
            let stored = store
                .get_meta(key)
                .map_err(|e| ServiceError::from_error(ErrorCode::IndexCorrupt, e))?;
            let stored: u32 = match stored {
                Some(s) => s.parse().unwrap_or(0),
                None => {
                    return Err(ServiceError::new(
                        ErrorCode::IndexIncompatible,
                        format!(
                            "index is missing {key} metadata and cannot be trusted; delete .oxide and reindex"
                        ),
                    ))
                }
            };
            if stored != current {
                return Err(ServiceError::new(
                    ErrorCode::IndexIncompatible,
                    format!(
                        "index {key} {stored} is incompatible with this binary (expects {current}); delete .oxide and reindex"
                    ),
                ));
            }
        }
        let stats = store
            .stats()
            .map_err(|e| ServiceError::from_error(ErrorCode::IndexCorrupt, e))?;
        if stats.files == 0 || stats.symbols == 0 {
            return Err(ServiceError::new(
                ErrorCode::IndexEmpty,
                "index contains no searchable symbols; run `oxide index PATH`",
            ));
        }
        if let Some((expected_name, expected_dim)) = expected_embedder {
            let indexed_embedder = store
                .get_meta("embedder")
                .map_err(|e| ServiceError::from_error(ErrorCode::IndexCorrupt, e))?;
            let indexed_dim = store
                .get_meta("dim")
                .map_err(|e| ServiceError::from_error(ErrorCode::IndexCorrupt, e))?;
            let name_matches = indexed_embedder.as_deref() == Some(expected_name);
            let dim_matches = indexed_dim.as_deref() == Some(expected_dim.to_string().as_str());
            if !name_matches || !dim_matches {
                return Err(ServiceError::new(
                    ErrorCode::ProviderMismatch,
                    "index embeddings were built with a different embedding provider or dimension; run `oxide index PATH`",
                ));
            }
            if stats.embeddings != stats.symbols {
                return Err(ServiceError::new(
                    ErrorCode::IndexStale,
                    "index embeddings are incomplete for the current provider; run `oxide index PATH`",
                ));
            }
        }
        Ok(())
    }

    fn index_path(&self) -> PathBuf {
        self.root.join(".oxide").join("index.db")
    }

    fn open_index_for_write(&self) -> Result<SqliteStore, ServiceError> {
        SqliteStore::open(&self.index_path()).map_err(|e| {
            // A cold-start indexing race can exhaust the bounded schema-init
            // retry (see `SqliteStore::open`) and surface as "database is
            // locked". That is transient contention with another writer,
            // not corruption: mapping it to `IndexCorrupt` would tell the
            // caller to delete and rebuild the index over a condition that
            // resolves itself once the other writer finishes.
            if crate::index::is_locked_error(&e) {
                ServiceError::from_error(ErrorCode::IndexFailed, e)
            } else {
                ServiceError::from_error(ErrorCode::IndexCorrupt, e)
            }
        })
    }

    fn open_index_for_read(&self) -> Result<SqliteStore, ServiceError> {
        let path = self.index_path();
        if !path.exists() {
            return Err(ServiceError::new(
                ErrorCode::IndexMissing,
                format!(
                    "index missing at {}; run `oxide index {}`",
                    path.display(),
                    self.root.display()
                ),
            ));
        }
        SqliteStore::open_read_only(&path).map_err(|e| {
            // Symmetric with `open_index_for_write`: extreme lock
            // contention on open is transient, not corruption. This helper
            // is shared by every read operation (status/search/context/
            // stats/review), so the code must not name one of them —
            // `IndexFailed` (Retry) is the same transient-contention code
            // the write path already uses for the identical underlying
            // condition.
            if crate::index::is_locked_error(&e) {
                ServiceError::from_error(ErrorCode::IndexFailed, e)
            } else {
                ServiceError::from_error(ErrorCode::IndexCorrupt, e)
            }
        })
    }
}

impl Evidence {
    fn from_symbol(symbol: &Symbol, score: f32, reasons: Vec<String>, snippet: String) -> Self {
        Self {
            id: format!("{}#{}", symbol.file, symbol.qualified_name),
            file: symbol.file.clone(),
            qualified_name: symbol.qualified_name.clone(),
            name: symbol.name.clone(),
            kind: symbol.kind,
            language: symbol.language,
            start_line: symbol.start_line,
            end_line: symbol.end_line,
            score,
            reasons,
            snippet,
        }
    }
}

fn supported_languages() -> Vec<Language> {
    vec![Language::Python, Language::TypeScript, Language::Tsx]
}

fn current_file_hashes(root: &Path) -> Result<HashMap<String, u64>, std::io::Error> {
    let files = scanner::scan_repo(root).map_err(|e| std::io::Error::other(e.to_string()))?;
    let mut hashes = HashMap::with_capacity(files.len());
    for file in files {
        let relative = file.display().to_string();
        let source = std::fs::read_to_string(root.join(&file))?;
        hashes.insert(relative, crate::symbols::content_hash(&source));
    }
    Ok(hashes)
}

fn compare_evidence(a: &Evidence, b: &Evidence) -> Ordering {
    b.score
        .partial_cmp(&a.score)
        .unwrap_or(Ordering::Equal)
        .then_with(|| a.id.cmp(&b.id))
}

fn role_rank(role: Role) -> u8 {
    match role {
        Role::Primary => 0,
        Role::Dependency => 1,
        Role::Test => 2,
    }
}

fn compare_context_evidence(a: &ContextEvidence, b: &ContextEvidence) -> Ordering {
    role_rank(a.role)
        .cmp(&role_rank(b.role))
        .then_with(|| compare_evidence(&a.evidence, &b.evidence))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn evidence_id_is_repository_relative_symbol_identity() {
        let symbol = Symbol {
            qualified_name: "Auth.refresh".into(),
            name: "refresh".into(),
            kind: SymbolKind::Method,
            language: Language::Python,
            file: "src/auth.py".into(),
            start_line: 2,
            end_line: 4,
            content_hash: 1,
            signature: "def refresh".into(),
            imports: Vec::new(),
            exported: false,
            parent: Some("Auth".into()),
            references: Vec::new(),
        };
        let evidence = Evidence::from_symbol(&symbol, 1.0, Vec::new(), "return token".into());
        assert_eq!(evidence.id, "src/auth.py#Auth.refresh");
        assert_eq!(evidence.file, "src/auth.py");
    }
}
