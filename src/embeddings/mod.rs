//! Embedding provider abstraction and the providers OXIDE ships.
//!
//! The default is [`DEFAULT_NATIVE_PROFILE`], a real in-process ONNX model
//! whose weights are downloaded the first time the model is *loaded* — which
//! is any command that constructs a semantic provider, not only `oxide
//! index`. The deterministic hashed bag-of-tokens embedder
//! ([`HashedEmbedder`], selected by `OXIDE_EMBED_NATIVE=hashed`) is the
//! offline opt-out and what the benchmark gate constructs directly: no
//! network, no download, reproducible across runs. Add a provider by
//! implementing [`EmbeddingProvider`]; nothing else in the pipeline changes.

mod hashed;
mod http;
mod provider;
mod text;
mod tokenize;

pub use hashed::HashedEmbedder;
pub use http::{HttpEmbedder, HttpPromptProtocol};
pub use provider::{
    EmbeddingProvider, EmbeddingSpaceFingerprint, EMBEDDING_FINGERPRINT_SCHEMA_VERSION,
};
#[allow(unused_imports)]
pub(crate) use text::qwen3_query_text;
pub use text::symbol_embed_text;
pub use tokenize::{tokenize, tokenize_into};

pub(crate) mod cache;
pub(crate) mod remote;
mod selection;
pub use selection::{
    configured_provider_name, open_embedder, DEFAULT_NATIVE_PROFILE, OFFLINE_PROFILE,
};

#[path = "native/sessions.rs"]
mod sessions;
pub use sessions::{
    auto_embed_sessions, embed_sessions_from_env, parse_embed_sessions, EmbedSessions,
    AUTO_MAX_EMBED_SESSIONS, AUTO_MAX_SESSION_MB, EMBED_SESSIONS_ENV, MAX_EMBED_SESSIONS,
    POOL_GROWTH_AFTER_DOCUMENTS,
};
#[cfg(feature = "native-embed")]
mod native;
#[cfg(feature = "native-embed")]
pub use native::{GemmaQueryPrompt, NativeEmbedder};
