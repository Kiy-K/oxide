use super::provider::{
    EmbeddingProvider, EmbeddingSpaceFingerprint, EMBEDDING_FINGERPRINT_SCHEMA_VERSION,
};
use super::text::qwen3_query_text;

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

#[cfg(test)]
mod tests {
    use super::*;
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
}
