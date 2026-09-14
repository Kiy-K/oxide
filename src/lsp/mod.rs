//! Optional, query-time LSP semantic-enrichment layer.
//!
//! Off by default, zero-cost when unused, never persisted, never required
//! for indexing or normal query/search. Tree-sitter/structural evidence
//! (`relations.rs`, `structural_relations.rs`) is always the baseline; this
//! module only ever *adds* higher-confidence evidence on top when a caller
//! opts in (`ContextOptions::lsp` in `context.rs`).
//!
//! Layout mirrors `src/languages/` (this crate's one other folder module):
//! `transport` is the plain JSON-RPC/stdio wire layer, `client` wraps the
//! LSP methods OXIDE needs and the Symbol↔LSP position/URI translation, and
//! `enrich` is the query-time integration `context.rs` calls — the scope-
//! then-cap discipline and the reason-tag vocabulary live there.
//!
//! See docs/lsp-enrichment-eval/README.md for the capability probe against a
//! real server (`ty`) that this design is grounded in.

pub mod client;
pub mod enrich;
pub mod transport;

pub use client::LspClient;
pub use enrich::{enrich_seeds, LspEvidence};
