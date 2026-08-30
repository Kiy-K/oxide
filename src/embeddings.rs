//! Embedding provider abstraction plus the default offline embedder.
//!
//! v0.1 ships a deterministic hashed bag-of-tokens embedder: no network, no
//! model download, reproducible across runs. Swap in a real model provider by
//! implementing [`EmbeddingProvider`]; nothing else in the pipeline changes.

use crate::symbols::Symbol;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;

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

/// Shared source-text tokenizer used by lexical search and embeddings:
/// splits camelCase/snake_case/kebab-case identifiers and path segments,
/// lowercases, drops stopwords and single characters.
pub fn tokenize(text: &str) -> Vec<String> {
    let mut out = Vec::new();
    tokenize_into(text, &mut |t| out.push(t.to_string()));
    out
}

/// Allocation-light tokenizer core: emits each token to `emit` without building
/// intermediate vectors. Tokens are borrowed slices of `text` whenever no case
/// folding is needed (the common case for code identifiers).
pub fn tokenize_into(text: &str, emit: &mut dyn FnMut(&str)) {
    for raw in text.split(|c: char| !(c.is_alphanumeric() || c == '_')) {
        if raw.is_empty() {
            continue;
        }
        split_identifier_into(raw, &mut |part: &str, needs_lower: bool| {
            // Fast path: already-lowercase tokens pass through borrowed.
            if needs_lower {
                let t = part.to_lowercase();
                if t.len() >= 2 && !STOPWORDS.contains(&t.as_str()) {
                    emit(&t);
                }
            } else if part.len() >= 2 && !STOPWORDS.contains(&part) {
                emit(part);
            }
        });
    }
}

const STOPWORDS: &[&str] = &[
    "the",
    "and",
    "for",
    "with",
    "this",
    "that",
    "from",
    "into",
    "self",
    "none",
    "null",
    "undefined",
    "true",
    "false",
    "fn",
    "func",
    "def",
    "let",
    "var",
    "const",
    "return",
    "import",
];

/// Splits snake_case / kebab-case / camelCase identifiers, emitting subtokens.
/// `needs_lower` tells the caller whether the slice contains uppercase chars.
fn split_identifier_into(raw: &str, emit: &mut dyn FnMut(&str, bool)) {
    let bytes = raw.as_bytes();
    let mut seg_start = 0usize;
    let mut seg_upper = false;
    for i in 0..bytes.len() {
        let b = bytes[i];
        if b == b'_' || b == b'-' || b == b'.' {
            if i > seg_start {
                emit(&raw[seg_start..i], seg_upper);
            }
            seg_start = i + 1;
            seg_upper = false;
            continue;
        }
        // camelCase boundary: lowercase→Upper starts a new token.
        if b.is_ascii_uppercase() && i > seg_start && !bytes[i - 1].is_ascii_uppercase() {
            emit(&raw[seg_start..i], seg_upper);
            seg_start = i;
            seg_upper = false;
        }
        seg_upper |= b.is_ascii_uppercase();
    }
    if raw.len() > seg_start {
        emit(&raw[seg_start..], seg_upper);
    }
}

#[allow(dead_code)]
/// Hashed bag-of-tokens with sublinear tf weighting, L2-normalized.
pub struct HashedEmbedder {
    dim: usize,
}

impl HashedEmbedder {
    pub fn new(dim: usize) -> Self {
        Self { dim }
    }
}

impl Default for HashedEmbedder {
    fn default() -> Self {
        Self::new(256)
    }
}

impl EmbeddingProvider for HashedEmbedder {
    fn name(&self) -> &str {
        "hashed-bow-256"
    }

    fn dim(&self) -> usize {
        self.dim
    }

    fn embed(&self, text: &str) -> Vec<f32> {
        let mut counts: HashMap<usize, f32> = HashMap::new();
        for tok in tokenize(text) {
            // Token-weight buckets so names carry more than body words when the
            // caller repeats them; plain bag-of-tokens otherwise.
            let bucket = crate::symbols::fnv1a64_iter([&tok]) as usize % self.dim;
            *counts.entry(bucket).or_insert(0.0) += 1.0;
        }
        let mut vec = vec![0f32; self.dim];
        for (b, tf) in counts {
            vec[b] = 1.0 + tf.ln();
        }
        let norm = vec
            .iter()
            .map(|v| (*v as f64) * (*v as f64))
            .sum::<f64>()
            .sqrt();
        if norm > 0.0 {
            for v in &mut vec {
                *v /= norm as f32;
            }
        }
        vec
    }
}

/// Convenience: embedding text for a symbol (kept next to the provider).
pub fn symbol_embed_text(s: &Symbol) -> String {
    format!(
        "{} {} {} {} {} {}",
        s.file,
        s.kind,
        s.qualified_name,
        s.signature,
        s.imports.join(" "),
        s.references.join(" ")
    )
}

/// Qwen3's instruction-prefixed query protocol (model-card guidance: improves
/// NL→PL retrieval 1-5%). Pure and independently testable — this is the
/// literal text `HttpEmbedder::embed_query` sends; pin expectations against
/// this function's output, not against the call sites that use it.
///
/// Relocated from `context::instructed_query` (Phase 3.3 embedding-boundary
/// refactor): query formatting is a property of the model behind the
/// provider, not of the caller building a query.
pub(crate) fn qwen3_query_text(task: &str) -> String {
    format!(
        "Instruct: Given a coding task, retrieve repository symbols that are \
         relevant to understand or change to complete it\nQuery: {task}"
    )
}

/// Embedder backed by any OpenAI-compatible `/v1/embeddings` HTTP endpoint
/// (llama.cpp's server by default). OXIDE ships no model code: it POSTs JSON.
///
/// Query/document asymmetry (Qwen3 protocol): `embed_query` applies the
/// instruction prefix internally; documents are embedded verbatim. This
/// provider has only ever served Qwen3 in this codebase (see
/// `docs/canonical-baseline.md`) — a genuinely model-agnostic HTTP provider
/// would need per-model prompt configuration, out of scope here.
pub struct HttpEmbedder {
    endpoint: String,
    model: String,
    dim: usize,
    /// Distinguishes instances so index meta invalidates across endpoints.
    name: String,
    healthy: std::sync::atomic::AtomicBool,
}

impl HttpEmbedder {
    /// Probe the endpoint with a tiny input to learn the vector dimension.
    pub fn new(endpoint: &str, model: &str) -> anyhow::Result<Self> {
        let mut e = Self {
            endpoint: endpoint.trim_end_matches('/').to_string(),
            model: model.to_string(),
            dim: 0,
            name: format!("http:{model}@{endpoint}"),
            healthy: std::sync::atomic::AtomicBool::new(true),
        };
        let probe = e.embed_batch_raw(vec!["dimension probe".to_string()])?;
        e.dim = probe
            .first()
            .map(|v| v.len())
            .ok_or_else(|| anyhow::anyhow!("embedding endpoint returned no vectors: {endpoint}"))?;
        anyhow::ensure!(
            e.dim > 0,
            "embedding endpoint returned empty vectors: {endpoint}"
        );
        Ok(e)
    }

    fn embed_batch_raw(&self, inputs: Vec<String>) -> anyhow::Result<Vec<Vec<f32>>> {
        use std::sync::atomic::Ordering;
        let n = inputs.len();
        let body = serde_json::json!({
            "model": self.model,
            "input": inputs,
        });
        let fail = |msg: String| -> anyhow::Result<Vec<Vec<f32>>> {
            if self.healthy.swap(false, Ordering::Relaxed) {
                eprintln!("oxide: embedding endpoint failed ({msg}); vectors will be empty until it recovers");
            }
            Ok(vec![Vec::new(); n])
        };
        let response = match ureq::post(&self.endpoint)
            .timeout(std::time::Duration::from_secs(120))
            .send_json(body)
        {
            Ok(r) => r,
            Err(e) => return fail(e.to_string()),
        };
        let resp: serde_json::Value = match response.into_json() {
            Ok(v) => v,
            Err(e) => return fail(e.to_string()),
        };
        self.healthy.store(true, Ordering::Relaxed);
        let Some(items) = resp["data"].as_array() else {
            anyhow::bail!("malformed embeddings response from {}", self.endpoint);
        };
        let mut out = Vec::with_capacity(n);
        for item in items {
            out.push(
                item["embedding"]
                    .as_array()
                    .map(|a| {
                        a.iter()
                            .filter_map(|x| x.as_f64().map(|f| f as f32))
                            .collect()
                    })
                    .unwrap_or_default(),
            );
        }
        Ok(out)
    }
}

impl EmbeddingProvider for HttpEmbedder {
    fn name(&self) -> &str {
        &self.name
    }

    fn dim(&self) -> usize {
        self.dim
    }

    fn embed(&self, text: &str) -> Vec<f32> {
        // Single input per call keeps ordering trivially correct.
        self.embed_batch_raw(vec![text.to_string()])
            .ok()
            .and_then(|mut v| (!v.is_empty()).then(|| v.remove(0)))
            .unwrap_or_default()
    }

    /// Server round-trips dominate indexing latency; one request per BATCH
    /// items, preserving input order.
    fn embed_batch(&self, texts: &[String]) -> Vec<Vec<f32>> {
        const BATCH: usize = 64;
        let mut out = Vec::with_capacity(texts.len());
        for chunk in texts.chunks(BATCH) {
            let mut part = self.embed_batch_raw(chunk.to_vec()).unwrap_or_default();
            // Pad a malformed partial response so order/count stay aligned.
            while part.len() < chunk.len() {
                part.push(Vec::new());
            }
            out.extend(part.into_iter().take(chunk.len()));
        }
        out
    }
    fn is_available(&self) -> bool {
        use std::sync::atomic::Ordering;
        self.healthy.load(Ordering::Relaxed)
    }

    fn embed_query(&self, text: &str) -> Vec<f32> {
        self.embed(&qwen3_query_text(text))
    }
}

/// EmbeddingGemma's documented query-task prompts (README of
/// `onnx-community/embeddinggemma-300m-ONNX`; Google's own model card).
/// `Bare` is not an authoritative variant — it exists so the Phase 3.3 item-2
/// experiment can measure how much the prompt actually matters on this
/// corpus, per the exit gate's "do not assume the generic prompt is optimal"
/// instruction.
#[cfg(feature = "native-embed")]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum GemmaQueryPrompt {
    /// No prompt — the ablation baseline, not authoritative usage.
    Bare,
    /// `"task: search result | query: "` — the model's generic-retrieval prompt.
    SearchResult,
    /// `"task: code retrieval | query: "` — one of Gemma's documented task
    /// strings, plausibly the better fit for OXIDE's code-symbol corpus.
    CodeRetrieval,
}

#[cfg(feature = "native-embed")]
impl GemmaQueryPrompt {
    fn apply(self, text: &str) -> String {
        match self {
            GemmaQueryPrompt::Bare => text.to_string(),
            GemmaQueryPrompt::SearchResult => format!("task: search result | query: {text}"),
            GemmaQueryPrompt::CodeRetrieval => format!("task: code retrieval | query: {text}"),
        }
    }

    fn from_env_str(s: &str) -> anyhow::Result<Self> {
        match s {
            "bare" => Ok(Self::Bare),
            "search-result" => Ok(Self::SearchResult),
            "code-retrieval" => Ok(Self::CodeRetrieval),
            other => anyhow::bail!(
                "unknown OXIDE_EMBED_NATIVE_QUERY_PROMPT {other:?}; expected bare|search-result|code-retrieval"
            ),
        }
    }

    /// Fingerprint label — kept separate from `apply`'s literal text so a
    /// prompt wording tweak can't accidentally change the profile label (or
    /// vice versa) without someone noticing both call sites.
    fn profile_label(self) -> &'static str {
        match self {
            GemmaQueryPrompt::Bare => "bare",
            GemmaQueryPrompt::SearchResult => "gemma-search-result",
            GemmaQueryPrompt::CodeRetrieval => "gemma-code-retrieval",
        }
    }
}

/// PROTOTYPE (Phase 3.3 spike): native in-process embedding via `fastembed`
/// (ONNX Runtime + HF tokenizers, no external server). Gated behind the
/// `native-embed` Cargo feature — off by default, zero effect on the default
/// build or the frozen retrieval benchmark.
///
/// What's verified: fastembed's tokenization + output-key selection +
/// normalization for `embeddinggemma-300m` match a direct onnxruntime-python
/// run of the same ONNX weights to ~1e-6. What's NOT verified: agreement with
/// the authoritative Sentence-Transformers `encode_query`/`encode_document`
/// reference or with the current llama.cpp/Qwen3 production baseline — see
/// the Phase 3.3 item-2 report before calling this model "supported". Also
/// unfixed: no config file (env var only); no model-missing/no-silent-download
/// gating beyond fastembed's own auto-download; no index-compatibility
/// fingerprint beyond the existing name-based check `update_index` already does.
#[cfg(feature = "native-embed")]
pub struct NativeEmbedder {
    model: std::sync::Mutex<fastembed::TextEmbedding>,
    dim: usize,
    name: String,
    query_prompt: GemmaQueryPrompt,
}

#[cfg(feature = "native-embed")]
impl NativeEmbedder {
    /// `profile` selects a built-in model; currently only `embeddinggemma-300m`
    /// (the model validated in the Phase 3.3 spike) is supported. `query_prompt`
    /// selects which of Gemma's documented query prompts to apply (see item 2
    /// of the Phase 3.3 follow-up); documents always use Gemma's authoritative
    /// `"title: none | text: "` representation, which is not a variant.
    pub fn new(profile: &str, query_prompt: GemmaQueryPrompt) -> anyhow::Result<Self> {
        let embedding_model = match profile {
            "embeddinggemma-300m" => fastembed::EmbeddingModel::EmbeddingGemma300M,
            other => anyhow::bail!(
                "unsupported native embedding profile {other:?}; supported: embeddinggemma-300m"
            ),
        };
        let dim = fastembed::TextEmbedding::get_model_info(&embedding_model)?.dim;
        // fastembed defaults to the relative `./.fastembed_cache` unless
        // $HF_HOME/$FASTEMBED_CACHE_DIR is set, which would dump ~1.2GB of
        // weights into whatever directory `oxide` happens to run from.
        // Point it at the standard HF Hub cache location instead (still
        // overridable by $HF_HOME, which fastembed checks first).
        let cache_dir = std::env::var_os("HOME")
            .map(std::path::PathBuf::from)
            .unwrap_or_else(|| std::path::PathBuf::from("."))
            .join(".cache/huggingface/hub");
        let model = fastembed::TextEmbedding::try_new(
            fastembed::TextInitOptions::new(embedding_model).with_cache_dir(cache_dir),
        )?;
        // Name encodes the query-prompt variant so index-compatibility
        // staleness detection (name-based, see `update_index`) invalidates
        // across variants too — otherwise switching variants without
        // reindexing would silently mix incompatible vectors.
        let name = match query_prompt {
            GemmaQueryPrompt::Bare => format!("native:{profile}"),
            GemmaQueryPrompt::SearchResult => format!("native:{profile}:search-result"),
            GemmaQueryPrompt::CodeRetrieval => format!("native:{profile}:code-retrieval"),
        };
        Ok(Self {
            model: std::sync::Mutex::new(model),
            dim,
            name,
            query_prompt,
        })
    }

    /// Load from local files only — no `hf-hub`, no network call, ever.
    /// `try_new_from_user_defined`/`UserDefinedEmbeddingModel` take file
    /// *bytes*, not paths or repo ids, so this path cannot silently reach
    /// the network by construction (Phase 3.3 item 4: verified separately
    /// that `fastembed` compiled with `default-features = false` and only
    /// `ort-download-binaries-rustls-tls` — no `hf-hub-*` feature — doesn't
    /// even expose `TextEmbedding::try_new` at all; that's a compile-time
    /// guarantee, not just a runtime discipline).
    ///
    /// `model_dir` must contain `tokenizer.json`, `config.json`,
    /// `special_tokens_map.json`, `tokenizer_config.json`, `model.onnx`, and
    /// (for the unquantized profile) `model.onnx_data`. This is a
    /// deliberately simple, OXIDE-owned convention — not the setup/download
    /// flow that would populate it, which is out of scope here.
    pub fn from_local_files(
        profile: &str,
        query_prompt: GemmaQueryPrompt,
        model_dir: &std::path::Path,
    ) -> anyhow::Result<Self> {
        anyhow::ensure!(
            profile == "embeddinggemma-300m",
            "unsupported native embedding profile {profile:?}; supported: embeddinggemma-300m"
        );
        let read = |name: &str| -> anyhow::Result<Vec<u8>> {
            let path = model_dir.join(name);
            std::fs::read(&path).map_err(|e| {
                anyhow::anyhow!(
                    "model asset missing at {}: {e} (run setup/acquisition first — this constructor never downloads)",
                    path.display()
                )
            })
        };
        let tokenizer_files = fastembed::TokenizerFiles {
            tokenizer_file: read("tokenizer.json")?,
            config_file: read("config.json")?,
            special_tokens_map_file: read("special_tokens_map.json")?,
            tokenizer_config_file: read("tokenizer_config.json")?,
        };
        let onnx_file = read("model.onnx")?;
        let mut user_model = fastembed::UserDefinedEmbeddingModel::new(onnx_file, tokenizer_files)
            .with_pooling(fastembed::Pooling::Mean);
        // EmbeddingGemma's ONNX graph already emits a pre-pooled
        // `sentence_embedding` output alongside raw `last_hidden_state` (see
        // `NativeEmbedder::fingerprint`'s `pooling: "graph-baked"` comment).
        // Without pinning this, fastembed's generic output precedence picks
        // `last_hidden_state` and mean-pools it — a different, wrong
        // representation for this model (confirmed against the authoritative
        // Sentence-Transformers reference during the Phase 3.3 item-2 spike).
        user_model.output_key = Some(fastembed::OutputKey::ByName("sentence_embedding"));
        // EmbeddingGemma's ONNX export stores weights in a sibling
        // `model.onnx_data` file (ONNX external-data convention); only wire
        // it in when present, since a hypothetical single-file export
        // wouldn't need it and `read` would otherwise hard-fail on a file
        // that was never supposed to exist.
        if let Ok(external) = read("model.onnx_data") {
            user_model =
                user_model.with_external_initializer("model.onnx_data".to_string(), external);
        }
        let dim = fastembed::TextEmbedding::get_model_info(
            &fastembed::EmbeddingModel::EmbeddingGemma300M,
        )?
        .dim;
        let model = fastembed::TextEmbedding::try_new_from_user_defined(
            user_model,
            fastembed::InitOptionsUserDefined::new(),
        )?;
        let name = match query_prompt {
            GemmaQueryPrompt::Bare => format!("native:{profile}"),
            GemmaQueryPrompt::SearchResult => format!("native:{profile}:search-result"),
            GemmaQueryPrompt::CodeRetrieval => format!("native:{profile}:code-retrieval"),
        };
        Ok(Self {
            model: std::sync::Mutex::new(model),
            dim,
            name,
            query_prompt,
        })
    }
}

#[cfg(feature = "native-embed")]
impl EmbeddingProvider for NativeEmbedder {
    fn name(&self) -> &str {
        &self.name
    }

    fn dim(&self) -> usize {
        self.dim
    }

    fn embed(&self, text: &str) -> Vec<f32> {
        self.embed_batch(std::slice::from_ref(&text.to_string()))
            .into_iter()
            .next()
            .unwrap_or_default()
    }

    fn embed_batch(&self, texts: &[String]) -> Vec<Vec<f32>> {
        let Ok(mut model) = self.model.lock() else {
            return vec![Vec::new(); texts.len()];
        };
        model.embed(texts, None).unwrap_or_else(|e| {
            eprintln!("oxide: native embedder failed ({e}); vectors will be empty");
            vec![Vec::new(); texts.len()]
        })
    }

    fn embed_query(&self, text: &str) -> Vec<f32> {
        self.embed(&self.query_prompt.apply(text))
    }

    fn embed_document(&self, text: &str) -> Vec<f32> {
        self.embed(&format!("title: none | text: {text}"))
    }

    fn embed_documents(&self, texts: &[String]) -> Vec<Vec<f32>> {
        let prefixed: Vec<String> = texts
            .iter()
            .map(|t| format!("title: none | text: {t}"))
            .collect();
        self.embed_batch(&prefixed)
    }

    fn fingerprint(&self) -> EmbeddingSpaceFingerprint {
        EmbeddingSpaceFingerprint {
            schema_version: EMBEDDING_FINGERPRINT_SCHEMA_VERSION,
            // Profile key, not `self.name` (which already folds the query
            // variant in for the legacy name-based fallback check) — the
            // variant has its own field below instead of being smuggled
            // into the model identity.
            model: "embeddinggemma-300m".to_string(),
            // onnx-community/embeddinggemma-300m-ONNX has no pinned revision
            // in this prototype (fastembed resolves "main" via hf-hub);
            // genuine revision pinning is follow-up work, not this field
            // lying about having one.
            artifact_revision: String::new(),
            // `EmbeddingModel::EmbeddingGemma300M` is the full-precision
            // variant (`onnx/model.onnx`, ~1.2GB) — distinct from the
            // Q4/quantized `EmbeddingModel` variants fastembed also lists,
            // which this prototype does not expose.
            quantization: "fp32".to_string(),
            representation: "dense".to_string(),
            dimension: self.dim,
            query_profile: self.query_prompt.profile_label().to_string(),
            // Always Gemma's authoritative document representation — not a
            // variant, see `embed_document`/`embed_documents` above.
            document_profile: "gemma-title-none-text".to_string(),
            // fastembed pins this model's `output_key` to the ONNX graph's
            // own pre-pooled `sentence_embedding` output (confirmed by
            // reading `fastembed`'s `models/text_embedding.rs`); OXIDE never
            // runs its own pooling math for this model.
            pooling: "graph-baked".to_string(),
            normalization: "l2".to_string(),
            similarity: "cosine".to_string(),
        }
    }
}

/// Query-prompt variant for the native prototype, from
/// `OXIDE_EMBED_NATIVE_QUERY_PROMPT` (`bare` | `search-result` |
/// `code-retrieval`); defaults to `bare` — the variant this codebase has
/// actually measured so far (see the Phase 3.3 item-2 report) — rather than
/// silently assuming the authoritative prompt is better on this corpus.
#[cfg(feature = "native-embed")]
fn native_query_prompt_from_env() -> anyhow::Result<GemmaQueryPrompt> {
    match std::env::var("OXIDE_EMBED_NATIVE_QUERY_PROMPT") {
        Ok(s) if !s.is_empty() => GemmaQueryPrompt::from_env_str(&s),
        _ => Ok(GemmaQueryPrompt::Bare),
    }
}

/// Matches the name `NativeEmbedder::new` computes, without constructing a
/// model — used for the staleness check so a query-prompt-only change is
/// also treated as an embedding-space change requiring reindex.
#[cfg(feature = "native-embed")]
fn native_provider_name(profile: &str, query_prompt: GemmaQueryPrompt) -> String {
    match query_prompt {
        GemmaQueryPrompt::Bare => format!("native:{profile}"),
        GemmaQueryPrompt::SearchResult => format!("native:{profile}:search-result"),
        GemmaQueryPrompt::CodeRetrieval => format!("native:{profile}:code-retrieval"),
    }
}

/// Return the configured provider identity without probing a network endpoint.
pub fn configured_provider_name(explicit: Option<&str>) -> String {
    let url = explicit
        .map(str::to_string)
        .or_else(|| std::env::var("OXIDE_EMBED_URL").ok());
    match url {
        Some(u) if !u.is_empty() => {
            let model = std::env::var("OXIDE_EMBED_MODEL").unwrap_or_default();
            format!("http:{model}@{u}")
        }
        _ => {
            #[cfg(feature = "native-embed")]
            if let Ok(profile) = std::env::var("OXIDE_EMBED_NATIVE") {
                if !profile.is_empty() {
                    let query_prompt =
                        native_query_prompt_from_env().unwrap_or(GemmaQueryPrompt::Bare);
                    return native_provider_name(&profile, query_prompt);
                }
            }
            "hashed-bow-256".into()
        }
    }
}

/// Provider factory: explicit URL wins, then `OXIDE_EMBED_URL`, then (behind
/// the `native-embed` feature) `OXIDE_EMBED_NATIVE`, else the offline hashed
/// embedder. OXIDE stays fully useful without any server or model download.
pub fn open_embedder(explicit: Option<&str>) -> anyhow::Result<Box<dyn EmbeddingProvider>> {
    let url = explicit
        .map(str::to_string)
        .or_else(|| std::env::var("OXIDE_EMBED_URL").ok());
    match url {
        Some(u) if !u.is_empty() => {
            let model = std::env::var("OXIDE_EMBED_MODEL").unwrap_or_default();
            Ok(Box::new(HttpEmbedder::new(&u, &model)?))
        }
        _ => {
            #[cfg(feature = "native-embed")]
            if let Ok(profile) = std::env::var("OXIDE_EMBED_NATIVE") {
                if !profile.is_empty() {
                    let query_prompt = native_query_prompt_from_env()?;
                    return Ok(Box::new(NativeEmbedder::new(&profile, query_prompt)?));
                }
            }
            Ok(Box::new(HashedEmbedder::default()))
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tokenizer_splits_cases_and_drops_stopwords() {
        assert_eq!(
            tokenize("RetryPolicy.handle_request"),
            vec!["retry", "policy", "handle", "request"]
        );
        assert_eq!(tokenize("the self a"), Vec::<String>::new());
        assert!(tokenize("src/authService.ts refresh_token").contains(&"refresh".to_string()));
    }

    #[test]
    fn embeddings_are_deterministic_normalized_and_discriminating() {
        let e = HashedEmbedder::default();
        let a1 = e.embed("retry failed http requests");
        let a2 = e.embed("retry failed http requests");
        let b = e.embed("parse yaml config file");
        assert_eq!(a1, a2);
        let dot: f32 = a1.iter().zip(&b).map(|(x, y)| x * y).sum();
        let self_dot: f32 = a1.iter().map(|x| x * x).sum();
        assert!((self_dot - 1.0).abs() < 1e-5);
        assert!(
            dot < 0.5,
            "unrelated texts should not collide strongly: {dot}"
        );
        assert_eq!(a1.len(), e.dim());
    }

    /// Regression pin (Phase 3.3 embedding-boundary refactor): the exact
    /// bytes previously produced by the now-removed `context::instructed_query`.
    /// The expected string is written out literally, not derived by calling
    /// the function under test, so a future reword of the prompt fails this
    /// test instead of silently vanishing.
    #[test]
    fn qwen3_query_text_matches_legacy_instructed_query_format() {
        assert_eq!(
            qwen3_query_text("fix backoff"),
            "Instruct: Given a coding task, retrieve repository symbols that are \
             relevant to understand or change to complete it\nQuery: fix backoff"
        );
    }

    #[test]
    fn hashed_embedder_embed_query_and_embed_document_are_unmodified_passthrough() {
        // The offline default has no model-specific prompt semantics: query
        // and document embedding must stay byte-identical to plain `embed`,
        // both before and after the refactor (this also protects
        // `eval.rs`/`benchmark_gate.rs`, which call `HashedEmbedder` directly
        // and must never see prompt text they didn't ask for).
        let e = HashedEmbedder::default();
        assert_eq!(e.embed_query("fix backoff"), e.embed("fix backoff"));
        assert_eq!(e.embed_document("fix backoff"), e.embed("fix backoff"));
    }

    #[test]
    fn default_embed_documents_is_order_preserving_passthrough() {
        let e = HashedEmbedder::default();
        let texts = vec!["a".to_string(), "b".to_string()];
        assert_eq!(e.embed_documents(&texts), e.embed_batch(&texts));
    }

    #[cfg(feature = "native-embed")]
    #[test]
    fn gemma_query_prompt_variants_actually_produce_different_text() {
        // Pure text-transformation check (no model, no network): sanity that
        // the three variants are not accidentally identical before trusting
        // any retrieval-level A/B/C comparison built on top of them.
        let text = "fix backoff";
        let bare = GemmaQueryPrompt::Bare.apply(text);
        let search = GemmaQueryPrompt::SearchResult.apply(text);
        let code = GemmaQueryPrompt::CodeRetrieval.apply(text);
        assert_eq!(bare, "fix backoff");
        assert_eq!(search, "task: search result | query: fix backoff");
        assert_eq!(code, "task: code retrieval | query: fix backoff");
        assert_ne!(bare, search);
        assert_ne!(search, code);
    }

    /// Ignored by default: needs the EmbeddingGemma model cached locally
    /// (network on first run). Run explicitly with
    /// `cargo test --features native-embed -- --ignored gemma_query_prompt_variants_produce_different_vectors`.
    #[cfg(feature = "native-embed")]
    #[test]
    #[ignore]
    fn gemma_query_prompt_variants_produce_different_vectors() {
        let bare = NativeEmbedder::new("embeddinggemma-300m", GemmaQueryPrompt::Bare).unwrap();
        let search =
            NativeEmbedder::new("embeddinggemma-300m", GemmaQueryPrompt::SearchResult).unwrap();
        let code =
            NativeEmbedder::new("embeddinggemma-300m", GemmaQueryPrompt::CodeRetrieval).unwrap();

        let text = "fix backoff";
        let v_bare = bare.embed_query(text);
        let v_search = search.embed_query(text);
        let v_code = code.embed_query(text);

        assert_ne!(v_bare, v_search, "bare vs search-result must differ");
        assert_ne!(
            v_search, v_code,
            "search-result vs code-retrieval must differ"
        );
        assert_ne!(v_bare, v_code, "bare vs code-retrieval must differ");

        // Printed for manual cross-check against the Python onnxruntime
        // reference (reference_check_prompts.py) — run with --nocapture.
        for (name, v) in [
            ("bare", &v_bare),
            ("search-result", &v_search),
            ("code-retrieval", &v_code),
        ] {
            let norm: f32 = v.iter().map(|x| x * x).sum::<f32>().sqrt();
            println!("[{name}] norm={norm:.4}");
            println!("[{name}] first8 = {:?}", &v[..8]);
        }
    }

    /// Dumps full-precision vectors for cross-checking against the
    /// authoritative `sentence-transformers` reference
    /// (authoritative_compare.py) — not a pass/fail assertion, a data
    /// export. Ignored by default for the same reason as the test above.
    #[cfg(feature = "native-embed")]
    #[test]
    #[ignore]
    fn dump_vectors_for_authoritative_comparison() {
        let bare = NativeEmbedder::new("embeddinggemma-300m", GemmaQueryPrompt::Bare).unwrap();
        let search =
            NativeEmbedder::new("embeddinggemma-300m", GemmaQueryPrompt::SearchResult).unwrap();
        let code =
            NativeEmbedder::new("embeddinggemma-300m", GemmaQueryPrompt::CodeRetrieval).unwrap();

        let text = "fix backoff";
        let out = serde_json::json!({
            "bare": bare.embed_query(text),
            "search-result": search.embed_query(text),
            "code-retrieval": code.embed_query(text),
            "document": bare.embed_document(text),
        });
        std::fs::write(
            "/tmp/oxide_rust_vectors.json",
            serde_json::to_string(&out).unwrap(),
        )
        .unwrap();
        println!("wrote /tmp/oxide_rust_vectors.json");
    }

    /// No network involved at all here (unlike the tests above): proves the
    /// runtime contract from the caller's side — missing local assets fail
    /// immediately with an actionable message, they don't hang, panic, or
    /// (per the separate compile-time check that `fastembed` built with
    /// `default-features = false` and no `hf-hub-*` feature doesn't even
    /// expose `TextEmbedding::try_new`) silently reach for the network.
    #[cfg(feature = "native-embed")]
    #[test]
    fn from_local_files_fails_deterministically_when_assets_are_missing() {
        let empty_dir = tempfile::tempdir().unwrap();
        let err = NativeEmbedder::from_local_files(
            "embeddinggemma-300m",
            GemmaQueryPrompt::Bare,
            empty_dir.path(),
        )
        .err()
        .unwrap();
        let msg = err.to_string();
        assert!(
            msg.contains("model asset missing"),
            "error should name what's missing, got: {msg}"
        );
        assert!(
            msg.contains("run setup"),
            "error should say what to do about it, got: {msg}"
        );
    }

    #[cfg(feature = "native-embed")]
    #[test]
    fn from_local_files_rejects_unsupported_profiles_without_touching_disk() {
        let missing_dir = std::path::Path::new("/definitely/does/not/exist");
        let err = NativeEmbedder::from_local_files(
            "some-other-model",
            GemmaQueryPrompt::Bare,
            missing_dir,
        )
        .err()
        .unwrap();
        assert!(err
            .to_string()
            .contains("unsupported native embedding profile"));
    }

    /// Ignored by default: needs the model already cached locally (this test
    /// copies from `$HF_HOME`'s hf-hub layout into OXIDE's own local-files
    /// convention, so it also proves the two directory layouts are
    /// compatible without needing a real `oxide setup` implementation yet).
    /// Run with `HF_HOME=... cargo test --features native-embed -- --ignored
    /// from_local_files_matches_hf_hub_loaded_output`.
    #[cfg(feature = "native-embed")]
    #[test]
    #[ignore]
    fn from_local_files_matches_hf_hub_loaded_output() {
        let hf_home = std::env::var("HF_HOME").expect("set HF_HOME to the warm cache");
        let snapshot_glob =
            format!("{hf_home}/models--onnx-community--embeddinggemma-300m-ONNX/snapshots");
        let snapshot_dir = std::fs::read_dir(&snapshot_glob)
            .unwrap()
            .next()
            .unwrap()
            .unwrap()
            .path();

        let local_dir = tempfile::tempdir().unwrap();
        for (src_rel, dst_name) in [
            ("tokenizer.json", "tokenizer.json"),
            ("config.json", "config.json"),
            ("special_tokens_map.json", "special_tokens_map.json"),
            ("tokenizer_config.json", "tokenizer_config.json"),
            ("onnx/model.onnx", "model.onnx"),
            ("onnx/model.onnx_data", "model.onnx_data"),
        ] {
            std::fs::copy(snapshot_dir.join(src_rel), local_dir.path().join(dst_name)).unwrap();
        }

        let via_hf_hub =
            NativeEmbedder::new("embeddinggemma-300m", GemmaQueryPrompt::Bare).unwrap();
        let via_local_files = NativeEmbedder::from_local_files(
            "embeddinggemma-300m",
            GemmaQueryPrompt::Bare,
            local_dir.path(),
        )
        .unwrap();

        let text = "fix backoff";
        assert_eq!(
            via_hf_hub.embed_query(text),
            via_local_files.embed_query(text),
            "local-files loading must produce identical output to hf-hub loading of the same weights"
        );
    }
}
