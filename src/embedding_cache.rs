//! Public path for the shared embedding cache. The implementation lives in
//! the crate-private `embeddings::cache`, which the crate's own code uses;
//! this module only re-exports it (kept by #34 S6).
pub use crate::embeddings::cache::{verify_commit, SharedEmbeddingCache};
