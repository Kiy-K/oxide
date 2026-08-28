//! Embedding provider abstraction plus the default offline embedder.
//!
//! v0.1 ships a deterministic hashed bag-of-tokens embedder: no network, no
//! model download, reproducible across runs. Swap in a real model provider by
//! implementing [`EmbeddingProvider`]; nothing else in the pipeline changes.

use crate::symbols::Symbol;
use std::collections::HashMap;

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

/// Embedder backed by any OpenAI-compatible `/v1/embeddings` HTTP endpoint
/// (llama.cpp's server by default). OXIDE ships no model code: it POSTs JSON.
///
/// Query/document asymmetry (Qwen3 protocol): callers pass instruction-prefixed
/// query text; documents are embedded verbatim.
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
        _ => "hashed-bow-256".into(),
    }
}

/// Provider factory: explicit URL wins, then `OXIDE_EMBED_URL`, else the
/// offline hashed embedder. OXIDE stays fully useful without any server.
pub fn open_embedder(explicit: Option<&str>) -> anyhow::Result<Box<dyn EmbeddingProvider>> {
    let url = explicit
        .map(str::to_string)
        .or_else(|| std::env::var("OXIDE_EMBED_URL").ok());
    match url {
        Some(u) if !u.is_empty() => {
            let model = std::env::var("OXIDE_EMBED_MODEL").unwrap_or_default();
            Ok(Box::new(HttpEmbedder::new(&u, &model)?))
        }
        _ => Ok(Box::new(HashedEmbedder::default())),
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
}
