//! Remote (opt-in) embedding providers: Voyage AI, Jina AI, and a generic
//! OpenAI-compatible `/v1/embeddings` endpoint. None of these are ever
//! constructed unless a user explicitly opted in — either via `$OXIDE_EMBED_
//! PROVIDER` + its matching API-key env var, or via `oxide setup`, which
//! records `remote_consent_ack` in `user_config::UserConfig` only after the
//! privacy warning is confirmed (`cli/commands/setup.rs::cmd_setup`). `open_embedder`
//! (`embeddings/selection.rs`) is the only caller of [`resolve_configured_remote`].
//!
//! All three share [`RemoteHttpClient`]'s retry/backoff, batching, and
//! response parsing, and all three follow `HttpEmbedder`'s existing failure
//! convention exactly: after retries are exhausted, return an empty vector
//! per failed input rather than erroring the whole batch — `index.rs`'s
//! content-hash-driven resume already retries those symbols on the next
//! `oxide index`/`watch` run, so no new resumability machinery is needed
//! here. Request bodies only ever carry the `texts: &[String]` that
//! `update_embeddings_reporting` already built from `symbol_embed_text` —
//! never whole files.

use crate::embeddings::{
    EmbeddingProvider, EmbeddingSpaceFingerprint, EMBEDDING_FINGERPRINT_SCHEMA_VERSION,
};
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::Duration;

const MAX_RETRIES: u32 = 5;
const BASE_BACKOFF: Duration = Duration::from_millis(500);
const REQUEST_TIMEOUT: Duration = Duration::from_secs(120);

const VOYAGE_ENDPOINT: &str = "https://api.voyageai.com/v1/embeddings";
const VOYAGE_BATCH: usize = 128; // Voyage's documented `input` cap.
const JINA_ENDPOINT: &str = "https://api.jina.ai/v1/embeddings";
const JINA_BATCH: usize = 64; // Jina documents no cap; still bound one request's size.
const GENERIC_BATCH: usize = 64; // No asymmetry/cap contract to rely on generically.

/// Shared HTTP mechanics: Bearer auth, JSON body, exponential backoff with
/// jitter-free doubling on 429/5xx (honoring `Retry-After` when present),
/// capped at [`MAX_RETRIES`].
pub struct RemoteHttpClient {
    endpoint: String,
    api_key: String,
    healthy: AtomicBool,
}

impl RemoteHttpClient {
    pub fn new(endpoint: impl Into<String>, api_key: impl Into<String>) -> Self {
        Self {
            endpoint: endpoint.into(),
            api_key: api_key.into(),
            healthy: AtomicBool::new(true),
        }
    }

    pub fn is_available(&self) -> bool {
        self.healthy.load(Ordering::Relaxed)
    }

    /// `None` means every attempt failed (and `healthy` was flipped) —
    /// callers must turn that into empty vectors, matching `HttpEmbedder`'s
    /// convention, never propagate an error up through `EmbeddingProvider`.
    fn post_json(&self, body: &serde_json::Value) -> Option<serde_json::Value> {
        let mut attempt = 0u32;
        loop {
            let result = ureq::post(&self.endpoint)
                .set("Authorization", &format!("Bearer {}", self.api_key))
                .set("Content-Type", "application/json")
                .timeout(REQUEST_TIMEOUT)
                .send_json(body.clone());
            match result {
                Ok(resp) => {
                    // Only a *parseable* body counts as healthy: a 200 with
                    // a malformed/unexpected body is a real failure the
                    // caller must see, or `is_available()` would keep
                    // reporting healthy while every real embed call
                    // silently comes back empty — no `is_remote()` degrade
                    // signal and no warning either.
                    let parsed = resp.into_json().ok();
                    let was_healthy = self.healthy.swap(parsed.is_some(), Ordering::Relaxed);
                    if parsed.is_none() && was_healthy {
                        eprintln!(
                            "oxide: remote embedding endpoint returned an unparseable response; vectors will be empty until it recovers"
                        );
                    }
                    return parsed;
                }
                Err(ureq::Error::Status(code, resp))
                    if attempt < MAX_RETRIES && (code == 429 || code >= 500) =>
                {
                    let retry_after = resp
                        .header("Retry-After")
                        .and_then(|s| s.parse::<u64>().ok())
                        .map(Duration::from_secs);
                    let backoff = retry_after.unwrap_or(BASE_BACKOFF * 2u32.pow(attempt));
                    std::thread::sleep(backoff);
                    attempt += 1;
                }
                Err(e) => {
                    if self.healthy.swap(false, Ordering::Relaxed) {
                        eprintln!(
                            "oxide: remote embedding endpoint failed ({e}); vectors will be empty until it recovers"
                        );
                    }
                    return None;
                }
            }
        }
    }

    /// Splits `texts` into `batch_size`-sized requests, calling `build_body`
    /// per chunk so each provider only needs to supply its own request
    /// shape; response parsing (`extract_embeddings`) is shared.
    pub fn embed_in_chunks(
        &self,
        texts: &[String],
        batch_size: usize,
        mut build_body: impl FnMut(&[String]) -> serde_json::Value,
    ) -> Vec<Vec<f32>> {
        let mut out = Vec::with_capacity(texts.len());
        for chunk in texts.chunks(batch_size.max(1)) {
            let body = build_body(chunk);
            let vectors = match self.post_json(&body) {
                Some(resp) => extract_embeddings(&resp, chunk.len()),
                None => vec![Vec::new(); chunk.len()],
            };
            out.extend(vectors);
        }
        out
    }
}

/// Voyage, Jina, and OpenAI-compatible responses all share this shape:
/// `{"data": [{"embedding": [...], "index": n}, ...]}`. Placed by `index`
/// rather than array position, so a provider that reorders results (or a
/// partial response) still lands each vector at the input it belongs to.
fn extract_embeddings(resp: &serde_json::Value, expected: usize) -> Vec<Vec<f32>> {
    let mut out = vec![Vec::new(); expected];
    let Some(items) = resp["data"].as_array() else {
        return out;
    };
    for (fallback_idx, item) in items.iter().enumerate() {
        let idx = item["index"].as_u64().map_or(fallback_idx, |n| n as usize);
        let Some(slot) = out.get_mut(idx) else {
            continue;
        };
        *slot = item["embedding"]
            .as_array()
            .map(|a| {
                a.iter()
                    .filter_map(|x| x.as_f64().map(|f| f as f32))
                    .collect()
            })
            .unwrap_or_default();
    }
    out
}

/// The identity string every remote provider's `name()` uses, and the same
/// one `configured_provider_name` builds for a resolved-but-not-yet-
/// constructed remote config — the two must never diverge (same invariant
/// `open_embedder`/`configured_provider_name` already hold for the local
/// providers). Folding `dimensions` in closes a real gap: without it, a
/// long-running `oxide mcp` process's provider cache (keyed on this string,
/// `service.rs::embedder`) would not notice an output-dimension-only config
/// change.
pub fn provider_name(
    provider: &str,
    model: &str,
    base_url: Option<&str>,
    dimensions: Option<usize>,
) -> String {
    let dim_suffix = dimensions.map(|d| format!(":{d}")).unwrap_or_default();
    match provider {
        "openai-compatible" => format!(
            "openai-compatible:{model}@{}{dim_suffix}",
            base_url.unwrap_or_default()
        ),
        other => format!("{other}:{model}{dim_suffix}"),
    }
}

/// A generic OpenAI-compatible `/v1/embeddings` endpoint: OpenAI itself, or
/// any self-hosted/third-party server implementing the same contract. No
/// documented query/document asymmetry, so both forward to the shared batch
/// path — matching `HttpEmbedder`'s own `Qwen3Instruct` (symmetric) variant.
pub struct OpenAiCompatibleEmbedder {
    client: RemoteHttpClient,
    model: String,
    base_url: String,
    dim: usize,
    name: String,
}

impl OpenAiCompatibleEmbedder {
    /// `known_dim`: when `Some` (the normal case — `oxide setup`'s live test
    /// call already recorded it in `UserConfig.vector_dim`), construction
    /// never touches the network, so a currently-unreachable provider still
    /// constructs successfully and degrades gracefully on the first real
    /// call instead of failing here before `is_remote()` is ever reachable.
    /// `None` only for the raw `$OXIDE_EMBED_PROVIDER` env-var opt-in (no
    /// persisted config to read), which keeps today's probe-or-fail
    /// behavior — the same contract `HttpEmbedder::new` already has for
    /// `$OXIDE_EMBED_URL`.
    pub fn new(
        base_url: &str,
        model: &str,
        api_key: &str,
        known_dim: Option<usize>,
    ) -> anyhow::Result<Self> {
        let base_url = base_url.trim_end_matches('/').to_string();
        let client = RemoteHttpClient::new(base_url.clone(), api_key);
        let name = provider_name("openai-compatible", model, Some(&base_url), known_dim);
        let dim = match known_dim {
            Some(d) => d,
            None => {
                let model_owned = model.to_string();
                let probe = client.embed_in_chunks(
                    &["dimension probe".to_string()],
                    1,
                    move |chunk| serde_json::json!({ "model": model_owned, "input": chunk }),
                );
                probe
                    .first()
                    .map(|v| v.len())
                    .filter(|d| *d > 0)
                    .ok_or_else(|| {
                        anyhow::anyhow!(
                        "could not reach {base_url} (or it returned no vectors) for model {model}"
                    )
                    })?
            }
        };
        Ok(Self {
            client,
            model: model.to_string(),
            base_url,
            dim,
            name,
        })
    }
}

impl EmbeddingProvider for OpenAiCompatibleEmbedder {
    fn name(&self) -> &str {
        &self.name
    }
    fn dim(&self) -> usize {
        self.dim
    }
    fn is_available(&self) -> bool {
        self.client.is_available()
    }
    fn is_remote(&self) -> bool {
        true
    }
    fn embed(&self, text: &str) -> Vec<f32> {
        self.embed_batch(std::slice::from_ref(&text.to_string()))
            .into_iter()
            .next()
            .unwrap_or_default()
    }
    fn embed_batch(&self, texts: &[String]) -> Vec<Vec<f32>> {
        let model = self.model.clone();
        self.client.embed_in_chunks(
            texts,
            GENERIC_BATCH,
            move |chunk| serde_json::json!({ "model": model, "input": chunk }),
        )
    }
    fn fingerprint(&self) -> EmbeddingSpaceFingerprint {
        EmbeddingSpaceFingerprint {
            schema_version: EMBEDDING_FINGERPRINT_SCHEMA_VERSION,
            model: self.model.clone(),
            artifact_revision: self.base_url.clone(),
            quantization: String::new(),
            representation: "dense".to_string(),
            dimension: self.dim,
            query_profile: "none".to_string(),
            document_profile: "none".to_string(),
            pooling: "unspecified".to_string(),
            normalization: "unspecified".to_string(),
            similarity: "cosine".to_string(),
        }
    }
}

/// Voyage AI (`docs.voyageai.com/reference/embeddings-api`). `input_type:
/// "query"|"document"` is Voyage's real asymmetric-embedding parameter, and
/// `output_dimension` its Matryoshka truncation field — both feed
/// [`fingerprint`](EmbeddingProvider::fingerprint) so switching either
/// safely reindexes.
pub struct VoyageEmbedder {
    client: RemoteHttpClient,
    model: String,
    output_dimension: Option<usize>,
    dim: usize,
    name: String,
}

impl VoyageEmbedder {
    /// `output_dimension` is Voyage's real Matryoshka-truncation request
    /// parameter — sent on every embed call when `Some`. `known_dim` is a
    /// separate, purely local concern: the dimension already observed for
    /// this exact configuration (persisted by `oxide setup`'s live test
    /// call), used only to skip the construction-time probe. Conflating the
    /// two used to mean a persisted *observed* dimension got resent as a
    /// *requested* truncation on every later call — harmless when Voyage
    /// happens to accept that value, a hard 400 on every embed otherwise.
    pub fn new(
        model: &str,
        api_key: &str,
        output_dimension: Option<usize>,
        known_dim: Option<usize>,
    ) -> anyhow::Result<Self> {
        let client = RemoteHttpClient::new(VOYAGE_ENDPOINT, api_key);
        let name = provider_name("voyage", model, None, known_dim.or(output_dimension));
        // See `OpenAiCompatibleEmbedder::new`'s doc comment: skipping the
        // probe when the dimension is already known (the normal, `oxide
        // setup`-configured path) is what lets construction succeed while
        // Voyage is unreachable, so the outage degrades instead of failing
        // before `is_remote()` is ever checked.
        let dim = match known_dim {
            Some(d) => d,
            None => {
                let probe = Self::embed_raw(
                    &client,
                    model,
                    output_dimension,
                    &["dimension probe".to_string()],
                    "document",
                );
                probe
                    .first()
                    .map(|v| v.len())
                    .filter(|d| *d > 0)
                    .ok_or_else(|| {
                        anyhow::anyhow!(
                        "could not reach Voyage AI (or it returned no vectors) for model {model}"
                    )
                    })?
            }
        };
        Ok(Self {
            client,
            model: model.to_string(),
            output_dimension,
            dim,
            name,
        })
    }

    fn embed_raw(
        client: &RemoteHttpClient,
        model: &str,
        output_dimension: Option<usize>,
        texts: &[String],
        input_type: &'static str,
    ) -> Vec<Vec<f32>> {
        let model = model.to_string();
        client.embed_in_chunks(texts, VOYAGE_BATCH, move |chunk| {
            let mut body = serde_json::json!({
                "model": model,
                "input": chunk,
                "input_type": input_type,
            });
            if let Some(d) = output_dimension {
                body["output_dimension"] = d.into();
            }
            body
        })
    }
}

impl EmbeddingProvider for VoyageEmbedder {
    fn name(&self) -> &str {
        &self.name
    }
    fn dim(&self) -> usize {
        self.dim
    }
    fn is_available(&self) -> bool {
        self.client.is_available()
    }
    fn is_remote(&self) -> bool {
        true
    }
    fn embed(&self, text: &str) -> Vec<f32> {
        self.embed_document(text)
    }
    fn embed_query(&self, text: &str) -> Vec<f32> {
        Self::embed_raw(
            &self.client,
            &self.model,
            self.output_dimension,
            std::slice::from_ref(&text.to_string()),
            "query",
        )
        .into_iter()
        .next()
        .unwrap_or_default()
    }
    fn embed_document(&self, text: &str) -> Vec<f32> {
        self.embed_documents(std::slice::from_ref(&text.to_string()))
            .into_iter()
            .next()
            .unwrap_or_default()
    }
    fn embed_documents(&self, texts: &[String]) -> Vec<Vec<f32>> {
        Self::embed_raw(
            &self.client,
            &self.model,
            self.output_dimension,
            texts,
            "document",
        )
    }
    fn embed_batch(&self, texts: &[String]) -> Vec<Vec<f32>> {
        self.embed_documents(texts)
    }
    fn fingerprint(&self) -> EmbeddingSpaceFingerprint {
        EmbeddingSpaceFingerprint {
            schema_version: EMBEDDING_FINGERPRINT_SCHEMA_VERSION,
            model: self.model.clone(),
            artifact_revision: String::new(),
            quantization: String::new(),
            representation: "dense".to_string(),
            dimension: self.dim,
            query_profile: "voyage:query".to_string(),
            document_profile: "voyage:document".to_string(),
            pooling: "unspecified".to_string(),
            normalization: "unspecified".to_string(),
            similarity: "cosine".to_string(),
        }
    }
}

/// Jina AI (`jina.ai/embeddings`). `task: "retrieval.query"|"retrieval.
/// passage"` is Jina's asymmetric parameter; `dimensions` its Matryoshka
/// truncation — same treatment as Voyage's `input_type`/`output_dimension`.
pub struct JinaEmbedder {
    client: RemoteHttpClient,
    model: String,
    dimensions: Option<usize>,
    dim: usize,
    name: String,
}

impl JinaEmbedder {
    /// See `VoyageEmbedder::new`'s doc comment: `dimensions` is Jina's real
    /// Matryoshka-truncation request parameter, `known_dim` a separate,
    /// purely local probe-skip value — never conflate the two.
    pub fn new(
        model: &str,
        api_key: &str,
        dimensions: Option<usize>,
        known_dim: Option<usize>,
    ) -> anyhow::Result<Self> {
        let client = RemoteHttpClient::new(JINA_ENDPOINT, api_key);
        let name = provider_name("jina", model, None, known_dim.or(dimensions));
        // See `OpenAiCompatibleEmbedder::new`'s doc comment.
        let dim = match known_dim {
            Some(d) => d,
            None => {
                let probe = Self::embed_raw(
                    &client,
                    model,
                    dimensions,
                    &["dimension probe".to_string()],
                    "retrieval.passage",
                );
                probe
                    .first()
                    .map(|v| v.len())
                    .filter(|d| *d > 0)
                    .ok_or_else(|| {
                        anyhow::anyhow!(
                            "could not reach Jina AI (or it returned no vectors) for model {model}"
                        )
                    })?
            }
        };
        Ok(Self {
            client,
            model: model.to_string(),
            dimensions,
            dim,
            name,
        })
    }

    fn embed_raw(
        client: &RemoteHttpClient,
        model: &str,
        dimensions: Option<usize>,
        texts: &[String],
        task: &'static str,
    ) -> Vec<Vec<f32>> {
        let model = model.to_string();
        client.embed_in_chunks(texts, JINA_BATCH, move |chunk| {
            let mut body = serde_json::json!({
                "model": model,
                "input": chunk,
                "task": task,
            });
            if let Some(d) = dimensions {
                body["dimensions"] = d.into();
            }
            body
        })
    }
}

impl EmbeddingProvider for JinaEmbedder {
    fn name(&self) -> &str {
        &self.name
    }
    fn dim(&self) -> usize {
        self.dim
    }
    fn is_available(&self) -> bool {
        self.client.is_available()
    }
    fn is_remote(&self) -> bool {
        true
    }
    fn embed(&self, text: &str) -> Vec<f32> {
        self.embed_document(text)
    }
    fn embed_query(&self, text: &str) -> Vec<f32> {
        Self::embed_raw(
            &self.client,
            &self.model,
            self.dimensions,
            std::slice::from_ref(&text.to_string()),
            "retrieval.query",
        )
        .into_iter()
        .next()
        .unwrap_or_default()
    }
    fn embed_document(&self, text: &str) -> Vec<f32> {
        self.embed_documents(std::slice::from_ref(&text.to_string()))
            .into_iter()
            .next()
            .unwrap_or_default()
    }
    fn embed_documents(&self, texts: &[String]) -> Vec<Vec<f32>> {
        Self::embed_raw(
            &self.client,
            &self.model,
            self.dimensions,
            texts,
            "retrieval.passage",
        )
    }
    fn embed_batch(&self, texts: &[String]) -> Vec<Vec<f32>> {
        self.embed_documents(texts)
    }
    fn fingerprint(&self) -> EmbeddingSpaceFingerprint {
        EmbeddingSpaceFingerprint {
            schema_version: EMBEDDING_FINGERPRINT_SCHEMA_VERSION,
            model: self.model.clone(),
            artifact_revision: String::new(),
            quantization: String::new(),
            representation: "dense".to_string(),
            dimension: self.dim,
            query_profile: "jina:retrieval.query".to_string(),
            document_profile: "jina:retrieval.passage".to_string(),
            pooling: "unspecified".to_string(),
            normalization: "unspecified".to_string(),
            similarity: "cosine".to_string(),
        }
    }
}

/// Constructs the named provider, probing it once for its output dimension.
/// The one factory used by both `oxide setup`'s live test call and
/// `open_embedder`'s resolution branch, so the two can never disagree about
/// what a saved config builds.
pub fn build(
    provider: &str,
    model: &str,
    base_url: Option<&str>,
    dimensions: Option<usize>,
    known_dim: Option<usize>,
    api_key: &str,
) -> anyhow::Result<Box<dyn EmbeddingProvider + Send + Sync>> {
    match provider {
        "voyage" => Ok(Box::new(VoyageEmbedder::new(
            model, api_key, dimensions, known_dim,
        )?)),
        "jina" => Ok(Box::new(JinaEmbedder::new(
            model, api_key, dimensions, known_dim,
        )?)),
        "openai-compatible" => {
            let url = base_url.ok_or_else(|| {
                anyhow::anyhow!("the openai-compatible provider needs a base_url")
            })?;
            Ok(Box::new(OpenAiCompatibleEmbedder::new(
                url, model, api_key, known_dim,
            )?))
        }
        other => anyhow::bail!("unknown remote embedding provider {other:?}"),
    }
}

pub fn normalize_provider(s: &str) -> Option<&'static str> {
    match s.trim().to_ascii_lowercase().as_str() {
        "voyage" | "voyageai" | "voyage-ai" => Some("voyage"),
        "jina" | "jinaai" | "jina-ai" => Some("jina"),
        "openai-compatible" | "openai" | "generic" => Some("openai-compatible"),
        _ => None,
    }
}

pub fn default_model_hint(provider: &str) -> &'static str {
    match provider {
        "voyage" => "voyage-code-3",
        "jina" => "jina-embeddings-v3",
        _ => "text-embedding-3-small",
    }
}

pub fn provider_display(provider: &str) -> &'static str {
    match provider {
        "voyage" => "Voyage AI",
        "jina" => "Jina AI",
        _ => "the configured OpenAI-compatible endpoint",
    }
}

/// A remote provider resolved from the environment or `oxide setup`'s saved
/// config, with its API key already decrypted — everything [`build`] needs.
pub struct ResolvedRemote {
    pub provider: String,
    pub model: String,
    pub base_url: Option<String>,
    /// Requested Matryoshka/output-dimension truncation. Not yet settable
    /// through `oxide setup` (no `--dimensions` flag exists), so always
    /// `None` from resolution today — see `known_dim` for the persisted
    /// probe result that stands in for it at construction time.
    pub dimensions: Option<usize>,
    /// The provider's dimension as already observed and persisted by
    /// `oxide setup`'s live test call (`UserConfig.vector_dim`), used only
    /// to skip the construction-time probe — never sent as a truncation
    /// request. `None` for the raw env-var opt-in, which has nothing
    /// persisted to trust and keeps today's probe-or-fail contract.
    pub known_dim: Option<usize>,
    pub api_key: String,
}

/// `open_embedder`/`configured_provider_name`'s shared remote-resolution
/// step, checked after `$OXIDE_EMBED_URL` and before the local default.
///
/// Two independent opt-ins, each sufficient on its own:
/// - `$OXIDE_EMBED_PROVIDER` + `$OXIDE_{PROVIDER}_API_KEY`: exporting these
///   is itself the deliberate, explicit action — no `oxide setup` run or
///   `remote_consent_ack` is required, so CI/ephemeral use is not gated on
///   ever having gone through the interactive wizard.
/// - `oxide setup`'s saved `config.toml`: only honored when
///   `remote_consent_ack` is `true`, which `cmd_setup` sets only after the
///   privacy warning is confirmed — a hand-edited config naming a provider
///   without that flag is treated as unconfigured, never silently trusted.
pub fn resolve_configured_remote() -> anyhow::Result<Option<ResolvedRemote>> {
    if let Some(provider) = std::env::var("OXIDE_EMBED_PROVIDER")
        .ok()
        .filter(|s| !s.trim().is_empty())
    {
        let provider = normalize_provider(&provider)
            .ok_or_else(|| anyhow::anyhow!("unknown $OXIDE_EMBED_PROVIDER {provider:?}"))?
            .to_string();
        let env_key_name = format!(
            "OXIDE_{}_API_KEY",
            provider.to_ascii_uppercase().replace('-', "_")
        );
        let api_key = std::env::var(&env_key_name).map_err(|_| {
            anyhow::anyhow!(
                "${env_key_name} is not set (required when $OXIDE_EMBED_PROVIDER={provider})"
            )
        })?;
        let model = std::env::var("OXIDE_EMBED_MODEL")
            .unwrap_or_else(|_| default_model_hint(&provider).to_string());
        // Trimmed here, once, so this identity always matches what
        // `OpenAiCompatibleEmbedder::new` (which trims independently, since
        // `oxide setup`'s own live test call reaches it without going
        // through this function at all) actually constructs and
        // fingerprints — never a raw, possibly-trailing-slash value from
        // the environment.
        let base_url = std::env::var("OXIDE_EMBED_BASE_URL")
            .ok()
            .map(|u| u.trim_end_matches('/').to_string());
        return Ok(Some(ResolvedRemote {
            provider,
            model,
            base_url,
            dimensions: None,
            known_dim: None,
            api_key,
        }));
    }

    // A config-dir resolution failure (e.g. no `$HOME`/`$USERPROFILE`, on a
    // minimal container or cron-style environment) means there is no
    // `config.toml` to have configured a remote provider in — genuinely
    // unconfigured, not a real error. Propagating it here used to make
    // every `oxide index`/`search`/`review` fail outright on such a box,
    // even for a user who never touched remote embeddings at all — the
    // exact "local remains zero-config" contract this feature must not
    // disturb. The env-var branch above is unaffected: it never touches the
    // config dir.
    let Ok(config_dir) = crate::user_config::oxide_config_dir() else {
        return Ok(None);
    };
    let cfg = crate::user_config::UserConfig::load(&config_dir)?;
    let Some(raw_provider) = cfg.provider.filter(|_| cfg.remote_consent_ack) else {
        return Ok(None);
    };
    // `cmd_setup` only ever persists an already-normalized name, so this
    // only fires on a hand-edited `config.toml` — but without it, a typo'd
    // spelling there would sail through here (looking configured, showing a
    // plausible identity) and only fail later inside `build`'s exact-match
    // `other => bail!` arm, on a totally different code path than the one
    // that actually caught the analogous env-var typo above.
    let provider = normalize_provider(&raw_provider)
        .ok_or_else(|| anyhow::anyhow!("unknown provider {raw_provider:?} in config.toml"))?
        .to_string();
    let Some(api_key) = crate::credentials::get_key(&config_dir, &provider)? else {
        // Config names a provider but no key was ever saved for it — treat
        // as unconfigured rather than erroring every command.
        return Ok(None);
    };
    let model = cfg
        .model
        .unwrap_or_else(|| default_model_hint(&provider).to_string());
    Ok(Some(ResolvedRemote {
        provider,
        model,
        base_url: cfg.base_url.map(|u| u.trim_end_matches('/').to_string()),
        dimensions: None,
        known_dim: cfg.vector_dim,
        api_key,
    }))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn listener() -> (std::net::TcpListener, String) {
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let addr = listener.local_addr().unwrap();
        (listener, format!("http://{addr}"))
    }

    fn respond(mut stream: std::net::TcpStream, status_line: &str, body: &str) {
        use std::io::{Read, Write};
        let mut buf = [0u8; 4096];
        let _ = stream.read(&mut buf);
        let response = format!(
            "{status_line}\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n{body}",
            body.len()
        );
        let _ = stream.write_all(response.as_bytes());
    }

    // ---- fingerprinting ----

    #[test]
    fn voyage_query_and_document_fingerprints_differ_only_in_profile() {
        let query_fp = VoyageEmbedder {
            client: RemoteHttpClient::new("http://unused", "key"),
            model: "voyage-code-3".to_string(),
            output_dimension: None,
            dim: 4,
            name: "voyage:voyage-code-3".to_string(),
        }
        .fingerprint();
        let mut doc_fp = query_fp.clone();
        doc_fp.query_profile = "voyage:document".to_string();
        doc_fp.document_profile = "voyage:document".to_string();
        assert_ne!(query_fp.query_profile, doc_fp.query_profile);
        assert_eq!(query_fp.model, doc_fp.model);
        assert_eq!(query_fp.dimension, doc_fp.dimension);
    }

    #[test]
    fn different_voyage_models_have_unequal_fingerprints() {
        let a = VoyageEmbedder {
            client: RemoteHttpClient::new("http://unused", "key"),
            model: "voyage-code-3".to_string(),
            output_dimension: None,
            dim: 4,
            name: "voyage:voyage-code-3".to_string(),
        };
        let b = VoyageEmbedder {
            client: RemoteHttpClient::new("http://unused", "key"),
            model: "voyage-3-large".to_string(),
            output_dimension: None,
            dim: 4,
            name: "voyage:voyage-3-large".to_string(),
        };
        assert_ne!(a.fingerprint(), b.fingerprint());
    }

    #[test]
    fn different_jina_dimensions_have_unequal_fingerprints() {
        let a = JinaEmbedder {
            client: RemoteHttpClient::new("http://unused", "key"),
            model: "jina-embeddings-v3".to_string(),
            dimensions: Some(512),
            dim: 512,
            name: "jina:jina-embeddings-v3:512".to_string(),
        };
        let b = JinaEmbedder {
            client: RemoteHttpClient::new("http://unused", "key"),
            model: "jina-embeddings-v3".to_string(),
            dimensions: Some(1024),
            dim: 1024,
            name: "jina:jina-embeddings-v3:1024".to_string(),
        };
        assert_ne!(a.fingerprint(), b.fingerprint());
    }

    #[test]
    fn provider_name_folds_in_dimensions_so_the_process_cache_notices_a_change() {
        let a = provider_name("voyage", "voyage-code-3", None, Some(512));
        let b = provider_name("voyage", "voyage-code-3", None, Some(1024));
        assert_ne!(a, b);
    }

    // ---- retry/backoff/batching against a fake server ----

    #[test]
    fn retries_on_429_then_succeeds() {
        let (listener, url) = listener();
        let handle = std::thread::spawn(move || {
            for (i, stream) in listener.incoming().enumerate() {
                let stream = stream.unwrap();
                if i < 2 {
                    respond(stream, "HTTP/1.1 429 Too Many Requests", "{}");
                } else {
                    respond(
                        stream,
                        "HTTP/1.1 200 OK",
                        r#"{"data":[{"embedding":[1.0,2.0],"index":0}]}"#,
                    );
                    break;
                }
            }
        });
        let client = RemoteHttpClient::new(url, "test-key");
        let out = client.embed_in_chunks(
            &["hello".to_string()],
            8,
            |chunk| serde_json::json!({"model": "m", "input": chunk}),
        );
        handle.join().unwrap();
        assert_eq!(out, vec![vec![1.0, 2.0]]);
    }

    #[test]
    fn gives_up_after_exhausting_retries_and_returns_empty_vectors() {
        let (listener, url) = listener();
        let handle = std::thread::spawn(move || {
            // One request per attempt; every attempt gets a 500.
            for _ in 0..=MAX_RETRIES {
                let stream = listener.incoming().next().unwrap().unwrap();
                respond(stream, "HTTP/1.1 500 Internal Server Error", "{}");
            }
        });
        let client = RemoteHttpClient::new(url, "test-key");
        let out = client.embed_in_chunks(
            &["hello".to_string(), "world".to_string()],
            8,
            |chunk| serde_json::json!({"model": "m", "input": chunk}),
        );
        handle.join().unwrap();
        assert_eq!(out, vec![Vec::<f32>::new(), Vec::<f32>::new()]);
        assert!(!client.is_available());
    }

    /// Reads one full HTTP/1.1 request (headers + exactly `Content-Length`
    /// body bytes) off `stream`, looping until both are in hand — a single
    /// `read()` call is not guaranteed to return the whole request in one
    /// go even on loopback.
    fn read_request_body(stream: &mut std::net::TcpStream) -> serde_json::Value {
        use std::io::Read;
        let mut buf = Vec::new();
        let mut chunk = [0u8; 4096];
        let (header_end, content_length) = loop {
            let n = stream.read(&mut chunk).unwrap_or(0);
            if n == 0 {
                return serde_json::Value::Null;
            }
            buf.extend_from_slice(&chunk[..n]);
            let text = String::from_utf8_lossy(&buf);
            if let Some(idx) = text.find("\r\n\r\n") {
                let headers = &text[..idx];
                let len = headers
                    .lines()
                    .find(|l| l.to_ascii_lowercase().starts_with("content-length:"))
                    .and_then(|l| l.split(':').nth(1))
                    .and_then(|v| v.trim().parse::<usize>().ok())
                    .unwrap_or(0);
                break (idx + 4, len);
            }
        };
        while buf.len() < header_end + content_length {
            let n = stream.read(&mut chunk).unwrap_or(0);
            if n == 0 {
                break;
            }
            buf.extend_from_slice(&chunk[..n]);
        }
        let body_bytes = &buf[header_end..(header_end + content_length).min(buf.len())];
        serde_json::from_slice(body_bytes).unwrap_or_default()
    }

    #[test]
    fn batches_respect_the_configured_chunk_size() {
        let (listener, url) = listener();
        let seen_chunk_sizes = std::sync::Arc::new(std::sync::Mutex::new(Vec::new()));
        let seen = seen_chunk_sizes.clone();
        let handle = std::thread::spawn(move || {
            use std::io::Write;
            for stream in listener.incoming() {
                let mut stream = stream.unwrap();
                let body = read_request_body(&mut stream);
                let count = body["input"].as_array().map(|a| a.len()).unwrap_or(0);
                seen.lock().unwrap().push(count);
                let items: Vec<_> = (0..count)
                    .map(|i| serde_json::json!({"embedding": [1.0], "index": i}))
                    .collect();
                let resp = serde_json::json!({"data": items}).to_string();
                let response = format!(
                    "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n{resp}",
                    resp.len()
                );
                let _ = stream.write_all(response.as_bytes());
                if seen.lock().unwrap().len() == 3 {
                    break;
                }
            }
        });
        let client = RemoteHttpClient::new(url, "test-key");
        let texts: Vec<String> = (0..5).map(|i| format!("text-{i}")).collect();
        let out = client.embed_in_chunks(
            &texts,
            2,
            |chunk| serde_json::json!({"model": "m", "input": chunk}),
        );
        handle.join().unwrap();
        assert_eq!(out.len(), 5);
        assert_eq!(*seen_chunk_sizes.lock().unwrap(), vec![2, 2, 1]);
    }

    // ---- resolution precedence ----

    #[test]
    fn normalize_provider_accepts_common_spellings() {
        assert_eq!(normalize_provider("Voyage"), Some("voyage"));
        assert_eq!(normalize_provider("voyage-ai"), Some("voyage"));
        assert_eq!(normalize_provider("JINA"), Some("jina"));
        assert_eq!(normalize_provider("openai"), Some("openai-compatible"));
        assert_eq!(normalize_provider("bogus"), None);
    }

    // ---- known_dim (probe-skip) must never leak into the request body as a
    // truncation request ----

    /// A `known_dim` persisted purely to skip the construction-time probe
    /// (`oxide setup`'s recorded `vector_dim`) must never be resent to the
    /// provider as a Matryoshka-truncation request: a dimension outside the
    /// provider's accepted truncation set would otherwise turn every
    /// subsequent embed call into a 400, even though nothing ever asked for
    /// truncation.
    ///
    /// Goes through the real `VoyageEmbedder::new` (not a struct literal)
    /// so the constructor's own `known_dim`/`output_dimension` wiring is
    /// what's under test, then swaps in the mock server's address —
    /// `new`'s hardcoded endpoint is the real Voyage AI API, which this
    /// must never touch.
    #[test]
    fn voyage_known_dim_is_not_sent_as_output_dimension() {
        let (listener, url) = listener();
        let handle = std::thread::spawn(move || {
            use std::io::Write;
            let mut stream = listener.incoming().next().unwrap().unwrap();
            let body = read_request_body(&mut stream);
            assert!(
                body.get("output_dimension").is_none(),
                "a probe-skip known_dim must not appear as output_dimension: {body}"
            );
            let resp =
                serde_json::json!({"data": [{"embedding": [1.0, 2.0], "index": 0}]}).to_string();
            let response = format!(
                "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n{resp}",
                resp.len()
            );
            let _ = stream.write_all(response.as_bytes());
        });
        let mut embedder = VoyageEmbedder::new("voyage-code-3", "test-key", None, Some(4)).unwrap();
        embedder.client = RemoteHttpClient::new(url, "test-key");
        let _ = embedder.embed_document("hello");
        handle.join().unwrap();
    }

    #[test]
    fn jina_known_dim_is_not_sent_as_dimensions() {
        let (listener, url) = listener();
        let handle = std::thread::spawn(move || {
            use std::io::Write;
            let mut stream = listener.incoming().next().unwrap().unwrap();
            let body = read_request_body(&mut stream);
            assert!(
                body.get("dimensions").is_none(),
                "a probe-skip known_dim must not appear as dimensions: {body}"
            );
            let resp =
                serde_json::json!({"data": [{"embedding": [1.0, 2.0], "index": 0}]}).to_string();
            let response = format!(
                "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n{resp}",
                resp.len()
            );
            let _ = stream.write_all(response.as_bytes());
        });
        let mut embedder =
            JinaEmbedder::new("jina-embeddings-v3", "test-key", None, Some(4)).unwrap();
        embedder.client = RemoteHttpClient::new(url, "test-key");
        let _ = embedder.embed_document("hello");
        handle.join().unwrap();
    }

    /// Both `::new()` constructors above swap in the mock server's address
    /// *after* construction — their hardcoded endpoint at construction time
    /// is the real Voyage/Jina API, which a still-network-bound probe would
    /// actually hit. This test is the one that would catch that directly:
    /// `known_dim: Some(_)` must short-circuit the probe before any
    /// network call, or this would hang or fail against the live network
    /// instead of returning instantly with `dim() == 4`.
    #[test]
    fn known_dim_skips_the_construction_probe_entirely() {
        let embedder = VoyageEmbedder::new("voyage-code-3", "test-key", None, Some(4)).unwrap();
        assert_eq!(embedder.dim(), 4);
    }
}
