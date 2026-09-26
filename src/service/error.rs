//! The service's error taxonomy: stable wire codes, the action each one
//! implies for a caller, and the `ServiceError` every operation returns.

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
    pub(super) fn new(code: ErrorCode, message: impl Into<String>) -> Self {
        Self {
            code,
            message: message.into(),
        }
    }

    pub(super) fn from_error(code: ErrorCode, error: impl std::fmt::Display) -> Self {
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
