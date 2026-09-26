//! Compatibility path for opt-in remote embedding providers.
pub use crate::embeddings::remote::{
    build, default_model_hint, normalize_provider, provider_display, provider_name,
    resolve_configured_remote, JinaEmbedder, OpenAiCompatibleEmbedder, RemoteHttpClient,
    ResolvedRemote, VoyageEmbedder,
};
