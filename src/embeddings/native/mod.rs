use super::provider::{
    EmbeddingProvider, EmbeddingSpaceFingerprint, EMBEDDING_FINGERPRINT_SCHEMA_VERSION,
};
mod profiles;
use super::sessions::{
    auto_embed_sessions, available_memory_mb, embed_sessions_from_env, session_mb, EmbedSessions,
    MAX_EMBED_SESSIONS, POOL_GROWTH_AFTER_DOCUMENTS,
};
use profiles::native_model_spec;

/// EmbeddingGemma's documented query-task prompts (README of
/// `onnx-community/embeddinggemma-300m-ONNX`; Google's own model card).
/// `Bare` is not an authoritative variant — it exists so the Phase 3.3 item-2
/// experiment can measure how much the prompt actually matters on this
/// corpus, per the exit gate's "do not assume the generic prompt is optimal"
/// instruction.
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
    /// False for dynamically quantized ONNX models, whose int8 activation
    /// range is computed over the whole batch tensor: the same text embeds
    /// differently alone and inside a batch. `embed_batch` then embeds one
    /// text per ONNX call, so every call shape yields the single-text vector
    /// the index and the query path already use.
    batch_invariant: bool,
    /// Session pool (`$OXIDE_EMBED_SESSIONS`, see [`EmbedSessions`]): extra
    /// ONNX sessions beyond `model`, created lazily and only when every
    /// existing one is busy, so a one-shot search or a small update never
    /// loads more than one. Empty for a single-session embedder, whose path
    /// is exactly the pre-pool one.
    extra_sessions: Vec<std::sync::OnceLock<Option<std::sync::Mutex<fastembed::TextEmbedding>>>>,
    /// How to build an extra session: same model and cache as `model`, the
    /// pool's per-session intra-op thread count.
    session_opts: Option<fastembed::TextInitOptions>,
    /// Documents embedded by this instance so far. The pool only grows once
    /// this reaches [`POOL_GROWTH_AFTER_DOCUMENTS`], so an incremental update
    /// of a few dozen symbols, or a burst of concurrent queries, never pays
    /// for loading sessions it would not amortize.
    documents_embedded: std::sync::atomic::AtomicUsize,
}

/// Environment variable selecting how many ONNX sessions a native embedder
/// may use. Documented in README "Embedding speed and memory".
impl NativeEmbedder {
    /// `profile` selects a built-in model (see [`native_model_spec`] for the
    /// supported set). `query_prompt` only affects Gemma profiles, which
    /// have more than one documented query convention (see item 2 of the
    /// Phase 3.3 follow-up); every other profile uses its own single
    /// upstream-documented query prefix and ignores this parameter.
    pub fn new(profile: &str, query_prompt: GemmaQueryPrompt) -> anyhow::Result<Self> {
        Self::with_sessions(profile, query_prompt, embed_sessions_from_env()?)
    }

    /// [`Self::new`] with an explicit session setting instead of
    /// `$OXIDE_EMBED_SESSIONS`, so tests and benchmarks do not race on the
    /// environment.
    pub fn with_sessions(
        profile: &str,
        query_prompt: GemmaQueryPrompt,
        setting: EmbedSessions,
    ) -> anyhow::Result<Self> {
        let cores = std::thread::available_parallelism().map_or(1, |n| n.get());
        let sessions = match setting {
            EmbedSessions::Auto => {
                auto_embed_sessions(cores, session_mb(profile), available_memory_mb())
            }
            // Never more sessions than cores: each needs a thread of its own.
            EmbedSessions::Fixed(n) => n.clamp(1, MAX_EMBED_SESSIONS).min(cores.max(1)),
        };
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
        let batch_invariant = fastembed::TextEmbedding::get_quantization_mode(&spec.model)
            != fastembed::QuantizationMode::Dynamic;
        // A pool of N sessions splits the cores between them (intra-op
        // threads = cores / N), so pooled workers never oversubscribe the
        // CPU; one session keeps fastembed's default of every core, exactly
        // as before pooling. Vectors are bit-identical to one session
        // (`session_pool_matches_single_session_bit_for_bit`: int8 and fp32
        // Arctic XS), so identity and fingerprint do not change. Slot 0 also
        // runs on cores / N threads, which made a lone warm query faster for
        // the default model (3.4 → 2.2 ms). Lazily created sessions stay
        // resident for the life of a long-lived process such as `oxide mcp`.
        // `from_local_files` always uses one session.
        let mut opts =
            fastembed::TextInitOptions::new(spec.model.clone()).with_cache_dir(cache_dir.clone());
        if sessions > 1 {
            opts = opts.with_intra_threads((cores / sessions).max(1));
        }
        let model = fastembed::TextEmbedding::try_new(opts.clone()).map_err(|e| {
            anyhow::anyhow!(
                "could not load the native embedding model `{profile}` (cache: {}): {e}. \
                 The first run downloads it (~23 MB for the default) from Hugging Face: \
                 check network access, or run with OXIDE_EMBED_NATIVE=hashed to index offline",
                cache_dir.display()
            )
        })?;
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
            batch_invariant,
            extra_sessions: (1..sessions).map(|_| std::sync::OnceLock::new()).collect(),
            session_opts: (sessions > 1).then_some(opts),
            documents_embedded: std::sync::atomic::AtomicUsize::new(0),
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
            // fp32 EmbeddingGemma is the only model this constructor loads;
            // a quantized local profile must derive this from its
            // quantization mode the way `new` does.
            batch_invariant: true,
            extra_sessions: Vec::new(),
            session_opts: None,
            documents_embedded: std::sync::atomic::AtomicUsize::new(0),
        })
    }
}

impl NativeEmbedder {
    /// A free ONNX session. Without a pool this is a plain blocking lock on
    /// the one session, as before pooling. With one: the first idle session,
    /// else — once this instance has done bulk document work — a lazily
    /// created extra session, else wait on the first.
    fn session(&self) -> Option<std::sync::MutexGuard<'_, fastembed::TextEmbedding>> {
        if self.extra_sessions.is_empty() {
            return self.model.lock().ok();
        }
        if let Ok(g) = self.model.try_lock() {
            return Some(g);
        }
        for slot in &self.extra_sessions {
            if let Some(Some(m)) = slot.get() {
                if let Ok(g) = m.try_lock() {
                    return Some(g);
                }
            }
        }
        let bulk = self
            .documents_embedded
            .load(std::sync::atomic::Ordering::Relaxed)
            >= POOL_GROWTH_AFTER_DOCUMENTS;
        for slot in self.extra_sessions.iter().filter(|_| bulk) {
            if slot.get().is_none() {
                let m = slot.get_or_init(|| {
                    let opts = self.session_opts.clone()?;
                    fastembed::TextEmbedding::try_new(opts)
                        .map_err(|e| eprintln!("oxide: extra embedding session failed ({e})"))
                        .ok()
                        .map(std::sync::Mutex::new)
                });
                if let Some(Ok(g)) = m.as_ref().map(|m| m.try_lock()) {
                    return Some(g);
                }
            }
        }
        self.model.lock().ok()
    }

    /// Sessions currently loaded (1 + extra sessions created so far).
    pub fn live_sessions(&self) -> usize {
        1 + self
            .extra_sessions
            .iter()
            .filter(|s| matches!(s.get(), Some(Some(_))))
            .count()
    }
}

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
        let Some(mut model) = self.session() else {
            return vec![Vec::new(); texts.len()];
        };
        let mut run = |batch: &[String]| {
            model.embed(batch, None).unwrap_or_else(|e| {
                eprintln!("oxide: native embedder failed ({e}); vectors will be empty");
                vec![Vec::new(); batch.len()]
            })
        };
        if self.batch_invariant {
            run(texts)
        } else {
            texts
                .iter()
                .flat_map(|t| run(std::slice::from_ref(t)))
                .collect()
        }
    }

    fn embed_query(&self, text: &str) -> Vec<f32> {
        if self.query_prefix.is_empty() {
            self.embed(text)
        } else {
            self.embed(&format!("{}{text}", self.query_prefix))
        }
    }

    fn embed_document(&self, text: &str) -> Vec<f32> {
        self.documents_embedded
            .fetch_add(1, std::sync::atomic::Ordering::Relaxed);
        if self.document_prefix.is_empty() {
            self.embed(text)
        } else {
            self.embed(&format!("{}{text}", self.document_prefix))
        }
    }

    fn embed_documents(&self, texts: &[String]) -> Vec<Vec<f32>> {
        self.documents_embedded
            .fetch_add(texts.len(), std::sync::atomic::Ordering::Relaxed);
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
pub(super) fn native_query_prompt_from_env() -> anyhow::Result<GemmaQueryPrompt> {
    match std::env::var("OXIDE_EMBED_NATIVE_QUERY_PROMPT") {
        Ok(s) if !s.is_empty() => GemmaQueryPrompt::from_env_str(&s),
        _ => Ok(GemmaQueryPrompt::Bare),
    }
}

/// Matches the name `NativeEmbedder::new` computes, without constructing a
/// model — used for the staleness check so a query-prompt-only change is
/// also treated as an embedding-space change requiring reindex.
pub(super) fn native_provider_name(profile: &str, query_prompt: GemmaQueryPrompt) -> String {
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

#[cfg(test)]
mod tests {
    use super::*;
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

    /// The session pool must be a pure throughput change: four
    /// threads embedding concurrently through a 4-session pool produce the
    /// exact vectors the single shipped session produces, for a dynamic-int8
    /// profile (one text per call) and through `embed_documents`.
    ///
    /// Ignored by default: needs the Arctic XS Q model cached locally. Run
    /// with `cargo test --features native-embed -- --ignored
    /// session_pool_matches_single_session_bit_for_bit`.
    #[cfg(feature = "native-embed")]
    #[test]
    #[ignore]
    fn session_pool_matches_single_session_bit_for_bit() {
        // int8 (the shipped default) and an fp32 profile, whose float
        // accumulation could in principle depend on the intra-op split.
        for profile in ["arctic-embed-xs-q", "arctic-embed-xs"] {
            pool_matches_single(profile);
        }
    }

    #[cfg(feature = "native-embed")]
    fn pool_matches_single(profile: &str) {
        let single =
            NativeEmbedder::with_sessions(profile, GemmaQueryPrompt::Bare, EmbedSessions::Fixed(1))
                .unwrap();
        let pooled =
            NativeEmbedder::with_sessions(profile, GemmaQueryPrompt::Bare, EmbedSessions::Fixed(4))
                .unwrap();
        // Queries alone never grow the pool.
        for i in 0..8 {
            pooled.embed_query(&format!("retry {i}"));
        }
        assert_eq!(
            pooled.live_sessions(),
            1,
            "{profile}: queries grew the pool"
        );
        // Enough document work to cross POOL_GROWTH_AFTER_DOCUMENTS, spread
        // over four threads so extra sessions are actually created and used.
        let texts: Vec<String> = (0..POOL_GROWTH_AFTER_DOCUMENTS + 64)
            .map(|i| format!("src/m{i}.py function f{i} def f{i}(x): retry_{i} Client get"))
            .collect();
        let expected: Vec<Vec<f32>> = texts.iter().map(|t| single.embed_document(t)).collect();
        let got: Vec<Vec<f32>> = std::thread::scope(|sc| {
            let hs: Vec<_> = texts
                .chunks(texts.len().div_ceil(4))
                .map(|c| {
                    sc.spawn(|| {
                        c.iter()
                            .map(|t| pooled.embed_document(t))
                            .collect::<Vec<_>>()
                    })
                })
                .collect();
            hs.into_iter().flat_map(|h| h.join().unwrap()).collect()
        });
        assert_eq!(got, expected, "{profile}: pooled vectors differ");
        assert!(pooled.live_sessions() > 1, "{profile}: pool never grew");
        assert_eq!(pooled.embed_documents(&texts[..5]), expected[..5].to_vec());
        assert_eq!(pooled.embed_query("retry"), single.embed_query("retry"));
        assert_eq!(pooled.fingerprint(), single.fingerprint());
        assert_eq!(pooled.name(), single.name());
    }

    /// A dynamically quantized ONNX model (fastembed's `*Q` Arctic/MiniLM
    /// profiles, including the shipped default) derives its int8 activation
    /// range from the whole batch tensor, so the same text embedded inside a
    /// batch and alone came out different (min cosine 0.9975 for
    /// `arctic-embed-xs-q`). `update_embeddings` embeds one text per call
    /// for large updates but batches small ones through `embed_documents`,
    /// so a <8-symbol incremental edit wrote vectors a clean rebuild never
    /// would. Batch output must equal per-text output, bit for bit.
    ///
    /// Ignored by default: needs the Arctic XS Q model cached locally
    /// (network on first run). Run explicitly with `cargo test --features
    /// native-embed -- --ignored dynamic_quant_batch_matches_single_text`.
    #[cfg(feature = "native-embed")]
    #[test]
    #[ignore]
    fn dynamic_quant_batch_matches_single_text() {
        let e = NativeEmbedder::new("arctic-embed-xs-q", GemmaQueryPrompt::Bare).unwrap();
        let texts: Vec<String> = [
            "src/a.py function retry_request def retry_request(conn): httpx",
            "tests/test_client.py function test_get def test_get(server): pytest Client",
            "crates/core/flags/defs.rs method Hidden.update fn update(&self, v: FlagValue)",
        ]
        .iter()
        .map(|s| s.to_string())
        .collect();
        let batched = e.embed_documents(&texts);
        for (t, b) in texts.iter().zip(&batched) {
            assert_eq!(
                &e.embed_document(t),
                b,
                "batch changed the vector for {t:?}"
            );
        }
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
