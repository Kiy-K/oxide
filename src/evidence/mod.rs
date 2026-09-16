//! Isolated async evidence collection: independent, optional evidence
//! sources (structural, Git, LSP, blast-radius) run concurrently behind one
//! synchronous entry point, `EvidenceCoordinator::collect()`. The rest of
//! OXIDE — CLI, MCP service API, retrieval core, SQLite/indexing path,
//! allocator — stays fully synchronous; see
//! docs/superpowers/specs/2026-09-15-evidence-coordinator-refactor-design.md.

pub mod contract;
pub mod coordinator;
pub mod runtime;
pub mod scope;

pub use contract::{DegradeReason, Degraded, EvidenceCandidate, EvidenceSource, Outcome};
pub use coordinator::EvidenceCoordinator;
