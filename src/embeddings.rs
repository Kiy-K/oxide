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

/// Prompt protocol for an [`HttpEmbedder`] instance: how query/document text
/// is formatted before being sent to the endpoint. Query/document asymmetry
/// is a property of the model behind the endpoint, not of the HTTP transport
/// — mirrors `NativeEmbedder`'s per-profile prefixes (Phase: CPU-embedding
/// survey, `docs/cpu-embedding-survey/`). `HttpEmbedder::new` (the only
/// constructor `open_embedder`/production code calls) always uses
/// `Qwen3Instruct` — this enum exists so survey/benchmark code can construct
/// additional protocols via [`HttpEmbedder::new_with_protocol`] without
/// touching the shipped provider-selection path at all.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum HttpPromptProtocol {
    /// Qwen3's instruction-prefixed query protocol (`qwen3_query_text`);
    /// documents embedded verbatim. The sole protocol in production use.
    Qwen3Instruct,
    /// Literal prefixes prepended verbatim to query/document text, e.g.
    /// Nomic v2's `"search_query: "`/`"search_document: "` task-instruction
    /// convention. `label` feeds `name()`/`fingerprint()` — never leave it
    /// empty for a non-Qwen protocol, or two different models sharing a bare
    /// model-string+endpoint pair could collide in `name()` and get silently
    /// treated as index-compatible when they aren't (see EMB-001).
    Prefixed {
        query_prefix: &'static str,
        document_prefix: &'static str,
        label: &'static str,
    },
}

/// Pure text-transformation helper for [`HttpPromptProtocol::Prefixed`] —
/// independently testable without a network call, same pattern as
/// `qwen3_query_text`.
fn prefixed_text(prefix: &str, text: &str) -> String {
    if prefix.is_empty() {
        text.to_string()
    } else {
        format!("{prefix}{text}")
    }
}

/// Matryoshka-style truncate-then-renormalize: keep the first `dim` values
/// (a no-op if the vector is already that short or shorter) and L2-renormalize
/// so downstream cosine/dot similarity remains meaningful. Only valid for a
/// model provably trained for MRL truncation at `dim` — the caller's
/// responsibility to verify against the model card, not this function's.
fn truncate_and_renormalize(v: &[f32], dim: usize) -> Vec<f32> {
    if v.len() <= dim {
        return v.to_vec();
    }
    let mut t: Vec<f32> = v[..dim].to_vec();
    let norm = t
        .iter()
        .map(|x| (*x as f64) * (*x as f64))
        .sum::<f64>()
        .sqrt();
    if norm > 0.0 {
        for x in &mut t {
            *x /= norm as f32;
        }
    }
    t
}

/// Pure name computation for an `HttpEmbedder`, independently testable
/// without a network probe (mirrors `native_provider_name`). Must match
/// exactly what `HttpEmbedder::new_with`'s `name` field is set to.
fn http_embedder_name(
    endpoint: &str,
    model: &str,
    protocol: &HttpPromptProtocol,
    truncate_dim: Option<usize>,
) -> String {
    let base = match protocol {
        HttpPromptProtocol::Qwen3Instruct => format!("http:{model}@{endpoint}"),
        HttpPromptProtocol::Prefixed { label, .. } => format!("http:{model}:{label}@{endpoint}"),
    };
    match truncate_dim {
        Some(d) => format!("{base}:dim{d}"),
        None => base,
    }
}

/// Embedder backed by any OpenAI-compatible `/v1/embeddings` HTTP endpoint
/// (llama.cpp's server by default). OXIDE ships no model code: it POSTs JSON.
///
/// `HttpEmbedder::new` (what `open_embedder`/production code calls) always
/// builds the [`HttpPromptProtocol::Qwen3Instruct`] protocol — the only one
/// this codebase has shipped (see `docs/canonical-baseline.md`).
/// [`HttpEmbedder::new_with_protocol`] additionally supports other protocols
/// and optional Matryoshka dimension truncation, for survey/benchmark code
/// (`docs/cpu-embedding-survey/`) — it is not wired into `open_embedder`.
pub struct HttpEmbedder {
    endpoint: String,
    model: String,
    dim: usize,
    /// Distinguishes instances so index meta invalidates across endpoints.
    name: String,
    healthy: std::sync::atomic::AtomicBool,
    protocol: HttpPromptProtocol,
    truncate_dim: Option<usize>,
}

impl HttpEmbedder {
    /// Probe the endpoint with a tiny input to learn the vector dimension.
    pub fn new(endpoint: &str, model: &str) -> anyhow::Result<Self> {
        Self::new_with_protocol(endpoint, model, HttpPromptProtocol::Qwen3Instruct, None)
    }

    /// Like [`Self::new`], but with an explicit prompt protocol and optional
    /// Matryoshka truncation dimension (see [`truncate_and_renormalize`]).
    /// Not used by `open_embedder` — construct directly for survey/benchmark
    /// use.
    pub fn new_with_protocol(
        endpoint: &str,
        model: &str,
        protocol: HttpPromptProtocol,
        truncate_dim: Option<usize>,
    ) -> anyhow::Result<Self> {
        let endpoint = endpoint.trim_end_matches('/').to_string();
        let name = http_embedder_name(&endpoint, model, &protocol, truncate_dim);
        let mut e = Self {
            endpoint,
            model: model.to_string(),
            dim: 0,
            name,
            healthy: std::sync::atomic::AtomicBool::new(true),
            protocol,
            truncate_dim,
        };
        // Truncation is applied inside `embed_batch_raw`, so probing with it
        // already set reports the *effective* (possibly truncated) dimension.
        let probe = e.embed_batch_raw(vec!["dimension probe".to_string()])?;
        e.dim = probe.first().map(|v| v.len()).ok_or_else(|| {
            anyhow::anyhow!("embedding endpoint returned no vectors: {}", e.endpoint)
        })?;
        anyhow::ensure!(
            e.dim > 0,
            "embedding endpoint returned empty vectors: {}",
            e.endpoint
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
            let v: Vec<f32> = item["embedding"]
                .as_array()
                .map(|a| {
                    a.iter()
                        .filter_map(|x| x.as_f64().map(|f| f as f32))
                        .collect()
                })
                .unwrap_or_default();
            out.push(match self.truncate_dim {
                Some(d) if !v.is_empty() => truncate_and_renormalize(&v, d),
                _ => v,
            });
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
        match &self.protocol {
            HttpPromptProtocol::Qwen3Instruct => self.embed(&qwen3_query_text(text)),
            HttpPromptProtocol::Prefixed { query_prefix, .. } => {
                self.embed(&prefixed_text(query_prefix, text))
            }
        }
    }

    fn embed_document(&self, text: &str) -> Vec<f32> {
        match &self.protocol {
            HttpPromptProtocol::Qwen3Instruct => self.embed(text),
            HttpPromptProtocol::Prefixed {
                document_prefix, ..
            } => self.embed(&prefixed_text(document_prefix, text)),
        }
    }

    fn embed_documents(&self, texts: &[String]) -> Vec<Vec<f32>> {
        match &self.protocol {
            HttpPromptProtocol::Qwen3Instruct => self.embed_batch(texts),
            HttpPromptProtocol::Prefixed {
                document_prefix, ..
            } => {
                if document_prefix.is_empty() {
                    return self.embed_batch(texts);
                }
                let prefixed: Vec<String> = texts
                    .iter()
                    .map(|t| prefixed_text(document_prefix, t))
                    .collect();
                self.embed_batch(&prefixed)
            }
        }
    }

    fn fingerprint(&self) -> EmbeddingSpaceFingerprint {
        let (query_profile, document_profile) = match &self.protocol {
            HttpPromptProtocol::Qwen3Instruct => ("qwen3-instruct".to_string(), "none".to_string()),
            HttpPromptProtocol::Prefixed {
                query_prefix,
                document_prefix,
                label,
            } => (
                format!(
                    "{label}:{}",
                    if query_prefix.is_empty() {
                        "none"
                    } else {
                        "prefix"
                    }
                ),
                format!(
                    "{label}:{}",
                    if document_prefix.is_empty() {
                        "none"
                    } else {
                        "prefix"
                    }
                ),
            ),
        };
        EmbeddingSpaceFingerprint {
            schema_version: EMBEDDING_FINGERPRINT_SCHEMA_VERSION,
            model: self.model.clone(),
            artifact_revision: String::new(),
            quantization: String::new(),
            representation: "dense".to_string(),
            dimension: self.dim,
            query_profile,
            document_profile,
            pooling: "unspecified".to_string(),
            normalization: "unspecified".to_string(),
            similarity: "cosine".to_string(),
        }
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
    /// The literal prefix this variant prepends to the raw query text
    /// (empty for `Bare`).
    fn query_prefix(self) -> &'static str {
        match self {
            GemmaQueryPrompt::Bare => "",
            GemmaQueryPrompt::SearchResult => "task: search result | query: ",
            GemmaQueryPrompt::CodeRetrieval => "task: code retrieval | query: ",
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

/// Native in-process embedding via `fastembed` (ONNX Runtime + HF tokenizers,
/// no external server). Behind the `native-embed` Cargo feature, which is
/// **on by default** — `DEFAULT_NATIVE_PROFILE` is the provider an
/// unconfigured OXIDE uses. `--no-default-features` drops it and restores the
/// hashed embedder. The frozen retrieval benchmark is unaffected either way:
/// `src/eval.rs` constructs `HashedEmbedder` directly.
///
/// Began as a Phase 3.3 spike; `arctic-embed-xs-q` graduated to the shipped
/// default on the 21-task ContextBench evidence in
/// `docs/cpu-embedding-survey/`. The other profiles in `native_model_spec`
/// remain opt-in and carry the spike's original caveats.
///
/// What's verified: fastembed's tokenization + output-key selection +
/// normalization for `embeddinggemma-300m` match a direct onnxruntime-python
/// run of the same ONNX weights to ~1e-6. What's NOT verified: agreement with
/// the authoritative Sentence-Transformers `encode_query`/`encode_document`
/// reference — see the Phase 3.3 item-2 report before calling *that* model
/// "supported". Still unfixed: no config file (env var only), and no
/// model-missing gating beyond fastembed's own auto-download — the download is
/// now the documented default behaviour rather than an unguarded surprise, but
/// nothing verifies the fetched artifact's revision (`artifact_revision` is
/// empty for every native profile, see `fingerprint`).
/// Per-model metadata needed to reproduce each candidate's authoritative
/// upstream query/document semantics. Prefixes are prepended verbatim to the
/// raw text (empty = no prefix). Sourced from each model's own HF model card
/// during the Phase 3.3b Pareto survey — not assumed interchangeable with
/// EmbeddingGemma's convention.
#[cfg(feature = "native-embed")]
struct NativeModelSpec {
    model: fastembed::EmbeddingModel,
    model_id: &'static str,
    quantization: &'static str,
    /// Empty for Gemma profiles — theirs is resolved from `GemmaQueryPrompt`
    /// instead, since Gemma is the only model with more than one documented
    /// query convention.
    query_prefix: &'static str,
    document_prefix: &'static str,
    pooling: &'static str,
}

#[cfg(feature = "native-embed")]
fn native_model_spec(profile: &str) -> anyhow::Result<NativeModelSpec> {
    use fastembed::EmbeddingModel::*;
    Ok(match profile {
        "embeddinggemma-300m" => NativeModelSpec {
            model: EmbeddingGemma300M,
            model_id: "embeddinggemma-300m",
            quantization: "fp32",
            query_prefix: "",
            document_prefix: "title: none | text: ",
            pooling: "graph-baked",
        },
        "embeddinggemma-300m-q4" => NativeModelSpec {
            model: EmbeddingGemma300MQ4,
            model_id: "embeddinggemma-300m",
            quantization: "int4",
            query_prefix: "",
            document_prefix: "title: none | text: ",
            pooling: "graph-baked",
        },
        // BAAI/bge-small-en-v1.5 model card: query-only instruction prefix,
        // no document-side prefix ("no instruction needs to be added to
        // passages").
        "bge-small-en-v1.5" => NativeModelSpec {
            model: BGESmallENV15,
            model_id: "bge-small-en-v1.5",
            quantization: "fp32",
            query_prefix: "Represent this sentence for searching relevant passages: ",
            document_prefix: "",
            pooling: "cls",
        },
        // Quantized (int8) BGESmallENV15 — same model card/prompt convention
        // as fp32, different onnx weights (Qdrant/bge-small-en-v1.5-onnx-Q).
        // Added for the CPU-first tiny-embedder screen (see
        // docs/cpu-embedding-survey/quantized-tiny-screen.md); quantization
        // does not change the documented prompt or pooling convention.
        "bge-small-en-v1.5-q" => NativeModelSpec {
            model: BGESmallENV15Q,
            model_id: "bge-small-en-v1.5",
            quantization: "int8",
            query_prefix: "Represent this sentence for searching relevant passages: ",
            document_prefix: "",
            pooling: "cls",
        },
        // snowflake/snowflake-arctic-embed-{xs,s} model cards: same query
        // prefix convention as BGE, CLS pooling (confirmed against fastembed's
        // own `get_default_pooling_method` table, which matches).
        "arctic-embed-xs" => NativeModelSpec {
            model: SnowflakeArcticEmbedXS,
            model_id: "snowflake-arctic-embed-xs",
            quantization: "fp32",
            query_prefix: "Represent this sentence for searching relevant passages: ",
            document_prefix: "",
            pooling: "cls",
        },
        // Quantized (int8) SnowflakeArcticEmbedXS — same repo, quantized onnx.
        "arctic-embed-xs-q" => NativeModelSpec {
            model: SnowflakeArcticEmbedXSQ,
            model_id: "snowflake-arctic-embed-xs",
            quantization: "int8",
            query_prefix: "Represent this sentence for searching relevant passages: ",
            document_prefix: "",
            pooling: "cls",
        },
        "arctic-embed-s" => NativeModelSpec {
            model: SnowflakeArcticEmbedS,
            model_id: "snowflake-arctic-embed-s",
            quantization: "fp32",
            query_prefix: "Represent this sentence for searching relevant passages: ",
            document_prefix: "",
            pooling: "cls",
        },
        // jinaai/jina-embeddings-v2-base-code model card: plain mean-pooled
        // bi-encoder, no query/document instruction convention (predates
        // Jina v3's task-instruction prompts).
        "jina-code-v2" => NativeModelSpec {
            model: JinaEmbeddingsV2BaseCode,
            model_id: "jina-embeddings-v2-base-code",
            quantization: "fp32",
            query_prefix: "",
            document_prefix: "",
            pooling: "mean",
        },
        // sentence-transformers/all-MiniLM-L6-v2: plain baseline, no prefix.
        "minilm-l6-v2" => NativeModelSpec {
            model: AllMiniLML6V2,
            model_id: "all-MiniLM-L6-v2",
            quantization: "fp32",
            query_prefix: "",
            document_prefix: "",
            pooling: "mean",
        },
        // Quantized (int8) AllMiniLML6V2 — same repo, quantized onnx.
        "minilm-l6-v2-q" => NativeModelSpec {
            model: AllMiniLML6V2Q,
            model_id: "all-MiniLM-L6-v2",
            quantization: "int8",
            query_prefix: "",
            document_prefix: "",
            pooling: "mean",
        },
        other => anyhow::bail!(
            "unsupported native embedding profile {other:?}; supported: \
             embeddinggemma-300m, embeddinggemma-300m-q4, bge-small-en-v1.5, \
             bge-small-en-v1.5-q, arctic-embed-xs, arctic-embed-xs-q, \
             arctic-embed-s, jina-code-v2, minilm-l6-v2, minilm-l6-v2-q"
        ),
    })
}

#[cfg(feature = "native-embed")]
pub struct NativeEmbedder {
    model: std::sync::Mutex<fastembed::TextEmbedding>,
    dim: usize,
    name: String,
    query_prompt: GemmaQueryPrompt,
    /// Whether `query_prompt` actually affects this instance's embedding
    /// behavior. Only Gemma profiles have more than one documented query
    /// convention (see `native_model_spec`); every other profile stores
    /// whatever `query_prompt` its caller happened to pass (typically just
    /// resolved from `$OXIDE_EMBED_NATIVE_QUERY_PROMPT`, which is otherwise
    /// unrelated to this profile) but must not let it leak into reported
    /// identity — `fingerprint()` gates on this instead of on
    /// `query_prompt != Bare` alone.
    is_gemma: bool,
    query_prefix: String,
    document_prefix: String,
    model_id: String,
    quantization: String,
    pooling: String,
}

#[cfg(feature = "native-embed")]
impl NativeEmbedder {
    /// `profile` selects a built-in model (see [`native_model_spec`] for the
    /// supported set). `query_prompt` only affects Gemma profiles, which
    /// have more than one documented query convention (see item 2 of the
    /// Phase 3.3 follow-up); every other profile uses its own single
    /// upstream-documented query prefix and ignores this parameter.
    pub fn new(profile: &str, query_prompt: GemmaQueryPrompt) -> anyhow::Result<Self> {
        let spec = native_model_spec(profile)?;
        let is_gemma = profile.starts_with("embeddinggemma");
        let dim = fastembed::TextEmbedding::get_model_info(&spec.model)?.dim;
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
            fastembed::TextInitOptions::new(spec.model.clone()).with_cache_dir(cache_dir),
        )?;
        // Name encodes the query-prompt variant so index-compatibility
        // staleness detection (name-based, see `update_index`) invalidates
        // across variants too — otherwise switching variants without
        // reindexing would silently mix incompatible vectors.
        let name = if is_gemma {
            match query_prompt {
                GemmaQueryPrompt::Bare => format!("native:{profile}"),
                GemmaQueryPrompt::SearchResult => format!("native:{profile}:search-result"),
                GemmaQueryPrompt::CodeRetrieval => format!("native:{profile}:code-retrieval"),
            }
        } else {
            format!("native:{profile}")
        };
        let query_prefix = if is_gemma {
            query_prompt.query_prefix().to_string()
        } else {
            spec.query_prefix.to_string()
        };
        Ok(Self {
            model: std::sync::Mutex::new(model),
            dim,
            name,
            query_prompt,
            is_gemma,
            query_prefix,
            document_prefix: spec.document_prefix.to_string(),
            model_id: spec.model_id.to_string(),
            quantization: spec.quantization.to_string(),
            pooling: spec.pooling.to_string(),
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
        let query_prefix = query_prompt.query_prefix().to_string();
        Ok(Self {
            model: std::sync::Mutex::new(model),
            dim,
            name,
            query_prompt,
            is_gemma: true,
            query_prefix,
            document_prefix: "title: none | text: ".to_string(),
            model_id: "embeddinggemma-300m".to_string(),
            quantization: "fp32".to_string(),
            pooling: "graph-baked".to_string(),
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
        if self.query_prefix.is_empty() {
            self.embed(text)
        } else {
            self.embed(&format!("{}{text}", self.query_prefix))
        }
    }

    fn embed_document(&self, text: &str) -> Vec<f32> {
        if self.document_prefix.is_empty() {
            self.embed(text)
        } else {
            self.embed(&format!("{}{text}", self.document_prefix))
        }
    }

    fn embed_documents(&self, texts: &[String]) -> Vec<Vec<f32>> {
        if self.document_prefix.is_empty() {
            return self.embed_batch(texts);
        }
        let prefixed: Vec<String> = texts
            .iter()
            .map(|t| format!("{}{t}", self.document_prefix))
            .collect();
        self.embed_batch(&prefixed)
    }

    fn fingerprint(&self) -> EmbeddingSpaceFingerprint {
        let query_profile = if self.is_gemma && self.query_prompt != GemmaQueryPrompt::Bare {
            self.query_prompt.profile_label().to_string()
        } else if self.query_prefix.is_empty() {
            "none".to_string()
        } else {
            format!("prefix:{}", self.query_prefix)
        };
        let document_profile = if self.document_prefix.is_empty() {
            "none".to_string()
        } else {
            format!("prefix:{}", self.document_prefix)
        };
        EmbeddingSpaceFingerprint {
            schema_version: EMBEDDING_FINGERPRINT_SCHEMA_VERSION,
            // Profile key, not `self.name` (which already folds the query
            // variant in for the legacy name-based fallback check) — the
            // variant has its own field below instead of being smuggled
            // into the model identity.
            model: self.model_id.clone(),
            // No pinned revision in this prototype (fastembed resolves
            // "main" via hf-hub); genuine revision pinning is follow-up
            // work, not this field lying about having one.
            artifact_revision: String::new(),
            quantization: self.quantization.clone(),
            representation: "dense".to_string(),
            dimension: self.dim,
            query_profile,
            document_profile,
            pooling: self.pooling.clone(),
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
    // Must match the is_gemma-gated logic in `NativeEmbedder::new`'s `name`
    // computation — this function exists so `configured_provider_name` can
    // report the same identity without constructing a model, and the two
    // must never diverge or the name-based staleness fallback breaks.
    if !profile.starts_with("embeddinggemma") {
        return format!("native:{profile}");
    }
    match query_prompt {
        GemmaQueryPrompt::Bare => format!("native:{profile}"),
        GemmaQueryPrompt::SearchResult => format!("native:{profile}:search-result"),
        GemmaQueryPrompt::CodeRetrieval => format!("native:{profile}:code-retrieval"),
    }
}

/// Return the configured provider identity without probing a network endpoint.
///
/// Shares `resolve_native_profile` with `open_embedder` so the two always pick
/// the same profile — see AGENTS.md for what breaks when they drift. They do
/// diverge on *invalid* configuration, deliberately: an unsupported profile
/// name or an unparseable `$OXIDE_EMBED_NATIVE_QUERY_PROMPT` is an error from
/// `open_embedder`, while this function still reports what was configured, so
/// `oxide status` can say the configured provider differs from the stored one
/// rather than refusing to answer.
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
            // Must resolve through the same `resolve_native_profile` as
            // `open_embedder`, or this name disagrees with the provider that
            // actually embedded: `oxide status` would report
            // `embedder_current: false` against a perfectly current index, and
            // `validate_index` would fire on a space that never changed.
            #[cfg(feature = "native-embed")]
            {
                let configured = std::env::var("OXIDE_EMBED_NATIVE").ok();
                if let Some(profile) = resolve_native_profile(configured.as_deref()) {
                    let query_prompt =
                        native_query_prompt_from_env().unwrap_or(GemmaQueryPrompt::Bare);
                    return native_provider_name(&profile, query_prompt);
                }
            }
            "hashed-bow-256".into()
        }
    }
}

/// The native profile `open_embedder` uses when nothing is configured.
///
/// Chosen over the previous offline-hashed fallback and over the
/// `qwen3-Q8_0` HTTP recommendation on the frozen 21-task ContextBench
/// evidence in `docs/cpu-embedding-survey/` — decisively better vector-only
/// retrieval than Qwen (R@5 0.655 vs 0.536), a gold file in the candidate
/// pool on 21/21 tasks where Qwen manages 19, ~9x faster full-repo indexing
/// (113.6s vs the Jina reference's 1025.5s on the same corpus), and no
/// separate server process to run at all. Qwen remains ahead on budgeted R@5
/// by 0.035 (one to two tasks out of twenty-one); that margin does not pay
/// for a llama.cpp server in the loop.
///
/// Peak RSS is deliberately *not* claimed as a win over Qwen: Arctic's
/// measured 230MB is in-process, while Qwen's ~140-260MB is a separate
/// long-lived server measured with `ps` at varying points. The two are not
/// comparable, and Arctic's 4.3x RSS advantage in the survey is over
/// `jina-code-v2` (996MB), not over Qwen.
pub const DEFAULT_NATIVE_PROFILE: &str = "arctic-embed-xs-q";

/// The `OXIDE_EMBED_NATIVE` value that opts back out to the
/// `HashedEmbedder` — no model, no download. The value OXIDE's own test
/// suite pins, since the default now loads real weights.
///
/// This is *provider selection*, not a network prohibition, and it sits at
/// the bottom of the precedence order in [`open_embedder`]: an explicit
/// `--embedder URL` or a set `$OXIDE_EMBED_URL` still wins over it and still
/// talks to that endpoint. For an actually-offline run, clear the endpoint
/// configuration as well:
///
/// ```sh
/// env -u OXIDE_EMBED_URL OXIDE_EMBED_NATIVE=hashed oxide index .
/// ```
///
/// Building `--no-default-features` drops the `native-embed` feature, so no
/// ONNX model can be loaded at all — but it does not disable the HTTP
/// provider either, for the same reason: the endpoint is still honoured if
/// configured.
pub const OFFLINE_PROFILE: &str = "hashed";

/// Resolves `$OXIDE_EMBED_NATIVE` to the native profile to load, or `None`
/// for the offline hashed embedder.
///
/// Split out from `open_embedder` so the precedence is testable without a
/// model download: unset and empty both mean "use the default", and only the
/// explicit `OFFLINE_PROFILE` opts out.
#[cfg(feature = "native-embed")]
fn resolve_native_profile(configured: Option<&str>) -> Option<String> {
    match configured.map(str::trim) {
        Some(OFFLINE_PROFILE) => None,
        Some(p) if !p.is_empty() => Some(p.to_string()),
        _ => Some(DEFAULT_NATIVE_PROFILE.to_string()),
    }
}

/// Provider factory: explicit URL wins, then `OXIDE_EMBED_URL`, then
/// `OXIDE_EMBED_NATIVE` (or, unset, `DEFAULT_NATIVE_PROFILE`), and finally
/// the offline hashed embedder.
///
/// **The default is no longer offline.** Unconfigured, this loads
/// `DEFAULT_NATIVE_PROFILE` through fastembed, which downloads its ONNX
/// weights (~23MB) into `$HF_HOME` (else `~/.cache/huggingface/hub`) the
/// first time the model is loaded, and needs network to do so.
///
/// "First model load" is not "first index": every command that can answer
/// semantically — `search`, `context`, `review`, `watch` — calls this before
/// touching the index, and `RepositoryService::search` in particular
/// constructs the provider *before* `validate_index` runs. So a machine that
/// already has an index but no cached weights will still download on its
/// first semantic query. `--mode lexical` never reaches here.
///
/// A failed download is not sticky: nothing is written to the index, so
/// re-running the same command after restoring network retries cleanly.
/// Switching providers later is safe but not free — the new fingerprint
/// makes `update_embeddings` clear and recompute every vector, so budget a
/// full re-embed for the next `oxide index`.
///
/// That is a deliberate trade for a default that actually retrieves well;
/// the previous zero-download behaviour is still one env var away
/// (`OXIDE_EMBED_NATIVE=hashed`, see [`OFFLINE_PROFILE`] for the endpoint
/// caveat), and is what you want for air-gapped machines and for reproducing
/// the benchmark gate, which constructs `HashedEmbedder` directly and is
/// unaffected by any of this.
///
/// A missing model is an error, never a silent downgrade to the hashed
/// embedder: the two are different embedding spaces, and quietly swapping
/// them would trip `update_index`'s fingerprint check and wipe every stored
/// vector on the next run.
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
            {
                let configured = std::env::var("OXIDE_EMBED_NATIVE").ok();
                if let Some(profile) = resolve_native_profile(configured.as_deref()) {
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

    /// `open_embedder` and `configured_provider_name` must name the same
    /// provider for the same environment — see the comment in the latter for
    /// what breaks otherwise. Read-only on the environment, so it cannot race
    /// with a concurrently running test.
    #[cfg(feature = "native-embed")]
    #[test]
    fn unconfigured_provider_name_is_the_shipped_native_default() {
        if std::env::var_os("OXIDE_EMBED_URL").is_some()
            || std::env::var_os("OXIDE_EMBED_NATIVE").is_some()
        {
            return; // a configured environment is not what this pins
        }
        assert_eq!(
            configured_provider_name(None),
            format!("native:{DEFAULT_NATIVE_PROFILE}")
        );
    }

    /// Pins the precedence `open_embedder` applies without constructing a
    /// provider — so it stays offline and cannot be flaked by a missing
    /// model. The `open_embedder` wiring above is a thin `match` over this.
    #[cfg(feature = "native-embed")]
    #[test]
    fn native_profile_resolution_defaults_to_arctic_and_opts_out_on_hashed() {
        assert_eq!(
            resolve_native_profile(None).as_deref(),
            Some(DEFAULT_NATIVE_PROFILE),
            "unset must load the shipped default, not the hashed embedder"
        );
        assert_eq!(
            resolve_native_profile(Some("")).as_deref(),
            Some(DEFAULT_NATIVE_PROFILE),
            "an empty value is 'unconfigured', same as unset"
        );
        assert_eq!(
            resolve_native_profile(Some("jina-code-v2")).as_deref(),
            Some("jina-code-v2"),
            "an explicit profile wins over the default"
        );
        assert_eq!(
            resolve_native_profile(Some(OFFLINE_PROFILE)),
            None,
            "only the explicit offline profile opts back out to hashed"
        );
        assert_eq!(
            resolve_native_profile(Some("  hashed  ")),
            None,
            "the offline opt-out survives surrounding whitespace"
        );
    }

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

    #[test]
    fn prefixed_text_prepends_verbatim_and_passes_through_when_empty() {
        assert_eq!(
            prefixed_text("search_query: ", "fix backoff"),
            "search_query: fix backoff"
        );
        assert_eq!(prefixed_text("", "fix backoff"), "fix backoff");
    }

    #[test]
    fn truncate_and_renormalize_shortens_and_restores_unit_norm() {
        // A simple normalized vector, truncated well below its full length.
        let full = vec![0.5f32; 4]; // norm = 1.0
        let truncated = truncate_and_renormalize(&full, 2);
        assert_eq!(truncated.len(), 2);
        let norm: f32 = truncated.iter().map(|x| x * x).sum::<f32>().sqrt();
        assert!(
            (norm - 1.0).abs() < 1e-5,
            "truncated vector should be renormalized to unit length, got norm={norm}"
        );
        // Direction (relative component ratios) must be preserved, not just norm.
        assert!((truncated[0] - truncated[1]).abs() < 1e-6);
    }

    #[test]
    fn truncate_and_renormalize_is_noop_when_already_short_enough() {
        let v = vec![0.6f32, 0.8f32];
        assert_eq!(truncate_and_renormalize(&v, 8), v);
    }

    #[test]
    fn http_embedder_name_discriminates_protocol_and_truncation() {
        // The Qwen3 (production) protocol's name format must stay byte-identical
        // to the pre-refactor `format!("http:{model}@{endpoint}")`.
        let qwen = http_embedder_name(
            "http://127.0.0.1:8191/v1/embeddings",
            "qwen3-Q8_0",
            &HttpPromptProtocol::Qwen3Instruct,
            None,
        );
        assert_eq!(qwen, "http:qwen3-Q8_0@http://127.0.0.1:8191/v1/embeddings");

        let nomic_768 = http_embedder_name(
            "http://127.0.0.1:8192/v1/embeddings",
            "nomic-embed-text-v2-moe-Q8_0",
            &HttpPromptProtocol::Prefixed {
                query_prefix: "search_query: ",
                document_prefix: "search_document: ",
                label: "nomic-v2",
            },
            None,
        );
        let nomic_256 = http_embedder_name(
            "http://127.0.0.1:8192/v1/embeddings",
            "nomic-embed-text-v2-moe-Q8_0",
            &HttpPromptProtocol::Prefixed {
                query_prefix: "search_query: ",
                document_prefix: "search_document: ",
                label: "nomic-v2",
            },
            Some(256),
        );
        // Same endpoint+model as Qwen would collide under the old bare format;
        // the protocol label must keep it distinct.
        assert_ne!(qwen, nomic_768);
        // Truncated and full-dimension variants of the same model must never
        // compare equal either, or `update_index` would reuse 768d vectors
        // for a 256d-configured provider.
        assert_ne!(nomic_768, nomic_256);
        assert!(nomic_256.ends_with(":dim256"));
    }

    #[cfg(feature = "native-embed")]
    #[test]
    fn gemma_query_prompt_variants_actually_produce_different_text() {
        // Pure text-transformation check (no model, no network): sanity that
        // the three variants are not accidentally identical before trusting
        // any retrieval-level A/B/C comparison built on top of them.
        let text = "fix backoff";
        let bare = format!("{}{text}", GemmaQueryPrompt::Bare.query_prefix());
        let search = format!("{}{text}", GemmaQueryPrompt::SearchResult.query_prefix());
        let code = format!("{}{text}", GemmaQueryPrompt::CodeRetrieval.query_prefix());
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

    /// Regression for a real bug caught in review: `$OXIDE_EMBED_NATIVE_QUERY_PROMPT`
    /// only means something for Gemma profiles, but a non-Bare value passed
    /// alongside a non-Gemma profile (e.g. left over in the environment from
    /// a previous Gemma run) must not leak into that profile's reported
    /// identity — otherwise the same BGE vectors get two different
    /// fingerprints depending on an env var BGE doesn't even consult,
    /// spuriously invalidating an otherwise-compatible index on reindex.
    ///
    /// Ignored by default: needs the BGE-small model cached locally (network
    /// on first run). Run explicitly with `cargo test --features
    /// native-embed -- --ignored non_gemma_profile_ignores_gemma_query_prompt_in_identity`.
    #[cfg(feature = "native-embed")]
    #[test]
    #[ignore]
    fn non_gemma_profile_ignores_gemma_query_prompt_in_identity() {
        let bare = NativeEmbedder::new("bge-small-en-v1.5", GemmaQueryPrompt::Bare).unwrap();
        let with_leftover_prompt =
            NativeEmbedder::new("bge-small-en-v1.5", GemmaQueryPrompt::SearchResult).unwrap();

        assert_eq!(bare.name(), with_leftover_prompt.name());
        assert_eq!(
            bare.fingerprint(),
            with_leftover_prompt.fingerprint(),
            "a query_prompt this profile doesn't consult must not appear in its identity"
        );
        assert!(!bare.fingerprint().query_profile.contains("gemma"));

        let text = "fix backoff";
        assert_eq!(
            bare.embed_query(text),
            with_leftover_prompt.embed_query(text),
            "the ignored query_prompt must not affect embedding output either"
        );
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
