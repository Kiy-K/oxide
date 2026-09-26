use super::provider::EmbeddingProvider;
use super::tokenize::tokenize;
use std::collections::HashMap;

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

#[cfg(test)]
mod tests {
    use super::*;
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
}
