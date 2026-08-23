//! Embedding provider abstraction plus the default offline embedder.
//!
//! v0.1 ships a deterministic hashed bag-of-tokens embedder: no network, no
//! model download, reproducible across runs. Swap in a real model provider by
//! implementing [`EmbeddingProvider`]; nothing else in the pipeline changes.

use crate::symbols::Symbol;
use std::collections::HashMap;

pub trait EmbeddingProvider: Sync {
    fn name(&self) -> &'static str;
    fn dim(&self) -> usize;
    fn embed(&self, text: &str) -> Vec<f32>;
}

/// Shared source-text tokenizer used by lexical search and embeddings:
/// splits camelCase/snake_case/kebab-case identifiers and path segments,
/// lowercases, drops stopwords and single characters.
pub fn tokenize(text: &str) -> Vec<String> {
    let mut out = Vec::new();
    for raw in text.split(|c: char| !(c.is_alphanumeric() || c == '_')) {
        if raw.is_empty() {
            continue;
        }
        for part in split_identifier(raw) {
            let t = part.to_lowercase();
            if t.len() < 2 || STOPWORDS.contains(&t.as_str()) {
                continue;
            }
            out.push(t);
        }
    }
    out
}

const STOPWORDS: &[&str] = &[
    "the", "and", "for", "with", "this", "that", "from", "into", "self", "none", "null",
    "undefined", "true", "false", "fn", "func", "def", "let", "var", "const", "return", "import",
];

fn split_identifier(raw: &str) -> Vec<String> {
    let mut parts = Vec::new();
    let mut cur = String::new();
    let chars: Vec<char> = raw.chars().collect();
    for (i, c) in chars.iter().enumerate() {
        if *c == '_' || *c == '-' || *c == '.' {
            if !cur.is_empty() {
                parts.push(std::mem::take(&mut cur));
            }
            continue;
        }
        if c.is_uppercase() && !cur.is_empty() && !chars[i - 1].is_uppercase() {
            parts.push(std::mem::take(&mut cur));
        }
        cur.push(*c);
    }
    if !cur.is_empty() {
        parts.push(cur);
    }
    parts
}

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
    fn name(&self) -> &'static str {
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
            vec[b] = (1.0 + tf.ln()) as f32;
        }
        let norm = vec.iter().map(|v| (*v as f64) * (*v as f64)).sum::<f64>().sqrt();
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
        assert!(dot < 0.5, "unrelated texts should not collide strongly: {dot}");
        assert_eq!(a1.len(), e.dim());
    }
}
