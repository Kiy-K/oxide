//! Stable application-facing operations for the agent CLI.
//!
//! This module keeps repository lifecycle, error classification, and wire DTOs
//! out of the argument parser. Retrieval and context algorithms stay below it.
//!
//! Responsibilities, one module each: `error` is the stable error taxonomy
//! (wire codes and the action each implies); `types` holds the request and
//! result DTOs whose serialized shape is the CLI/MCP JSON contract; `cache`
//! is the process-wide cache a long-lived host (`oxide mcp`) keeps between
//! requests — the keyed symbol snapshot paired with the `RelationIndex`
//! built over it, its row counts, and the embedder; `repository` is
//! `RepositoryService`, which opens the index, validates it inside the
//! request's own read snapshot and orchestrates each operation; `evidence`
//! converts retrieval output into wire `Evidence` and orders context items.
//! This file only re-exports the public surface at its original
//! `crate::service::*` paths.

mod cache;
mod error;
mod evidence;
mod repository;
#[cfg(test)]
mod test_support;
mod types;

pub use error::{ErrorAction, ErrorCode, ServiceError};
pub use repository::RepositoryService;
pub use types::{
    ContextEvidence, ContextResult, Evidence, IndexResult, SearchRequest, StatusResult,
};
