//! Public path for the opt-in remote embedding providers. The
//! implementation lives in the crate-private `embeddings::remote`, which the
//! crate's own code uses; this module only re-exports it (kept by #34 S6).
pub use crate::embeddings::remote::{
    build, default_model_hint, normalize_provider, provider_display, provider_name,
    resolve_configured_remote, JinaEmbedder, OpenAiCompatibleEmbedder, RemoteHttpClient,
    ResolvedRemote, VoyageEmbedder,
};
