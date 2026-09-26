use serde::{Deserialize, Serialize};

/// Bump when adding/redefining a field below in a way that makes an old
/// stored fingerprint's meaning ambiguous — an old fingerprint (missing the
/// new field, or from before this schema existed) must never silently
/// compare equal to a new one just because the fields it does have match.
pub const EMBEDDING_FINGERPRINT_SCHEMA_VERSION: u32 = 1;

/// Structured description of a provider's effective vector-space semantics —
/// the real index-compatibility contract (Phase 3.3 item 3), as opposed to
/// the single opaque `embedder` name string that predates it and remains the
/// fallback for providers that don't override [`EmbeddingProvider::fingerprint`].
///
/// Equality here is deliberately all-or-nothing: v0.1 does not attempt to
/// reason about which field differences are "safe" to reuse vectors across
/// (e.g. same semantics under a different execution runtime) — any
/// difference means "reindex", per the Phase 3.3 exit gate's instruction to
/// prefer conservative reindexing over uncertain compatibility.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct EmbeddingSpaceFingerprint {
    pub schema_version: u32,
    /// Model/checkpoint identity, e.g. `"embeddinggemma-300m"`, `"qwen3-0.6b"`.
    pub model: String,
    /// Artifact/revision identity where available (HF repo revision, ONNX
    /// file name, endpoint URL for HTTP providers). Empty when unknown.
    pub artifact_revision: String,
    /// Quantization/artifact variant, e.g. `"fp32"`, `"q4"`. Empty when N/A.
    pub quantization: String,
    /// `"dense"` | `"multi-vector"` (the latter unsupported in v0.1; see the
    /// Phase 3.3 ColBERT investigation).
    pub representation: String,
    pub dimension: usize,
    /// How query text is transformed before embedding (e.g. `"bare"`,
    /// `"qwen3-instruct"`, `"gemma-search-result"`).
    pub query_profile: String,
    /// How document text is transformed before embedding.
    pub document_profile: String,
    /// `"mean"` | `"cls"` | `"graph-baked"` (model's own ONNX graph already
    /// pools, e.g. EmbeddingGemma's `sentence_embedding` output) | `"n/a"`.
    pub pooling: String,
    /// `"l2"` | `"none"`.
    pub normalization: String,
    /// `"cosine"` | `"dot"`.
    pub similarity: String,
}

pub trait EmbeddingProvider: Sync {
    fn name(&self) -> &str;
    fn dim(&self) -> usize;
    fn embed(&self, text: &str) -> Vec<f32>;

    /// Whether the provider has observed a successful request recently.
    fn is_available(&self) -> bool {
        true
    }

    /// Whether this provider sends source-code excerpts off-machine. Local
    /// providers (`HashedEmbedder`, `NativeEmbedder`, a self-hosted
    /// `HttpEmbedder`) keep the default `false`; only the opt-in remote
    /// adapters in `embeddings/remote.rs` override it. `Service::search`/
    /// `context`/`review` consult this to decide whether an unavailable
    /// provider should hard-fail (local — today's behavior, unchanged) or
    /// degrade to the lexical-still-good result with a warning (remote).
    fn is_remote(&self) -> bool {
        false
    }

    /// Embed many texts; providers with batch endpoints should override.
    /// Default preserves order via per-text calls.
    fn embed_batch(&self, texts: &[String]) -> Vec<Vec<f32>> {
        texts.iter().map(|t| self.embed(t)).collect()
    }

    /// Embed a search query. Default forwards to [`Self::embed`] unchanged.
    /// Providers whose model defines a distinct query prompt (asymmetric
    /// embedders, e.g. Qwen3's instruction prefix or Gemma's task prompts)
    /// should override this instead of requiring every caller to know the
    /// prefix — query/document formatting is a property of the model behind
    /// the provider, not of retrieval call sites.
    fn embed_query(&self, text: &str) -> Vec<f32> {
        self.embed(text)
    }

    /// Embed one document/passage. Default forwards to [`Self::embed`].
    fn embed_document(&self, text: &str) -> Vec<f32> {
        self.embed(text)
    }

    /// Embed documents/passages. Default forwards to [`Self::embed_batch`].
    fn embed_documents(&self, texts: &[String]) -> Vec<Vec<f32>> {
        self.embed_batch(texts)
    }

    /// The provider's vector-space compatibility contract (Phase 3.3 item 3).
    /// Default is deliberately thin — `model` = [`Self::name`], `dimension` =
    /// [`Self::dim`], everything else `"unspecified"` — which keeps today's
    /// name-based discrimination exactly as strong as before for providers
    /// that don't override this (`HashedEmbedder`, `HttpEmbedder`): since
    /// their `name()` already fully identifies them (including, for
    /// `HttpEmbedder`, distinguishing endpoints/models), wrapping it in a
    /// mostly-`"unspecified"` fingerprint changes nothing about when two
    /// fingerprints compare equal. Providers with real per-field semantics
    /// to report (native/local models) should override this.
    fn fingerprint(&self) -> EmbeddingSpaceFingerprint {
        EmbeddingSpaceFingerprint {
            schema_version: EMBEDDING_FINGERPRINT_SCHEMA_VERSION,
            model: self.name().to_string(),
            artifact_revision: String::new(),
            quantization: String::new(),
            representation: "dense".to_string(),
            dimension: self.dim(),
            query_profile: "unspecified".to_string(),
            document_profile: "unspecified".to_string(),
            pooling: "unspecified".to_string(),
            normalization: "unspecified".to_string(),
            similarity: "cosine".to_string(),
        }
    }
}
