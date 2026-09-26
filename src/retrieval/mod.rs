//! Hybrid retrieval: lexical (BM25) + semantic (vector) fused with reciprocal
//! rank fusion, followed by structural expansion. Every hit carries the
//! evidence that selected it.
//!
//! Responsibilities, one module each: `options` holds the request and
//! hit contracts callers build and read; `snapshot` owns the whole-corpus
//! snapshot, its lean-vs-complete loading policy and the completion every
//! partial symbol goes through before it can be a seed or leave as output;
//! `top_k` is the bounded score/id ranking both channels share;
//! `engine` is `RetrievalEngine` and the candidate-first request path
//! (concurrent lexical + query embedding, exact streaming vector scan, RRF
//! fusion, candidate-only hydration, structural expansion); `snippet`
//! reads source line ranges for rendering. This file only re-exports the
//! public surface at its original `crate::retrieval::*` paths.

mod engine;
mod options;
mod snapshot;
mod snippet;
#[cfg(test)]
mod test_support;
mod top_k;

pub use engine::RetrievalEngine;
pub use options::{RetrievalMode, SearchHit, SearchMode, SearchOptions};
pub use snapshot::{complete_symbols, lexical_persisted, SymbolSnapshot};
pub use snippet::read_snippet;
