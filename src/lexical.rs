//! BM25 lexical retrieval over indexed OXIDE symbols.

use crate::embeddings::tokenize;
use crate::symbols::Symbol;
use std::collections::{HashMap, HashSet};

pub struct LexicalIndex {
    postings: HashMap<String, HashMap<u64, u32>>, // term -> doc -> weighted tf
    doc_len: HashMap<u64, f32>,
    doc_count: usize,
}

impl LexicalIndex {
    /// `root` enables body-text indexing: gold-context evaluations showed
    /// bugfix targets hide behind local identifiers that only exist in symbol
    /// bodies (weight 1 vs 4 for names keeps precision).
    pub fn build(symbols: &[Symbol], root: Option<&std::path::Path>) -> Self {
        // Capacity heuristic: ~20 weighted postings per symbol keeps the
        // posting maps from rehashing during the build.
        let mut postings: HashMap<String, HashMap<u64, u32>> =
            HashMap::with_capacity(symbols.len() * 24);
        let mut doc_len: HashMap<u64, f32> = HashMap::new();
        // Body slices come from disk; cache per file so each file is read once.
        let mut body_cache: HashMap<&str, String> = HashMap::new();
        for s in symbols {
            let id = s.id();
            let mut total_weight = 0u32;
            let mut add = |field: &str, weight: u32| {
                crate::embeddings::tokenize_into(field, &mut |tok| {
                    let entry = match postings.get_mut(tok) {
                        Some(docs) => docs,
                        None => {
                            postings.insert(tok.to_string(), HashMap::new());
                            postings.get_mut(tok).unwrap()
                        }
                    };
                    *entry.entry(id).or_insert(0) += weight;
                    total_weight += weight;
                });
            };
            // Qualified names dominate; signature next; context fields last.
            add(&s.qualified_name, 4);
            add(&s.name, 4);
            add(&s.signature, 2);
            add(&s.file.replace(['/', '.', ':'], " "), 2);
            for r in &s.references {
                add(r, 1);
            }
            for i in &s.imports {
                add(i, 1);
            }
            if let Some(root) = root {
                let body = body_cache.entry(&s.file).or_insert_with(|| {
                    std::fs::read_to_string(root.join(&s.file)).unwrap_or_default()
                });
                let start = (s.start_line as usize).saturating_sub(1);
                let lines: Vec<&str> = body.lines().collect();
                if start < lines.len() {
                    let end = (s.end_line as usize).min(lines.len());
                    let slice = lines[start..end].join("\n");
                    add(&slice, 1);
                }
            }
            doc_len.insert(id, total_weight as f32);
        }
        Self {
            postings,
            doc_len,
            doc_count: symbols.len(),
        }
    }

    /// BM25 scores for the query terms, plus term-coverage evidence for the
    /// corroboration experiment (`term_coverage_alpha`, docs/term-coverage-eval/):
    /// for each doc, the number of *distinct* query terms matched — a
    /// repeated query token (common in whole-issue-body queries) counts
    /// once, not once per occurrence — and the sum of those distinct terms'
    /// IDF weights, which discounts common-but-not-stopword terms the way
    /// "meaningful" implies. The second return value is the sum of IDF over
    /// every distinct query term that appears anywhere in the corpus
    /// (`df > 0`) — the coverage denominator.
    ///
    /// BM25's own score accumulation (`e.0`) is untouched — still one
    /// contribution per query-token occurrence, standard BM25 query-term-
    /// frequency behavior. `e.1`'s distinct-term semantics still satisfy
    /// `*terms > 0` identically to the old raw-occurrence count, so
    /// `scores.retain` below keeps its exact original membership: this is
    /// new accompanying evidence, not a scoring-behavior change.
    pub(crate) fn search(
        &self,
        query: &str,
        k1: f32,
        b: f32,
    ) -> (HashMap<u64, (f32, usize, f32)>, f32) {
        let avg_len = self.doc_len.values().sum::<f32>() / self.doc_count.max(1) as f32;
        let mut scores: HashMap<u64, (f32, usize, f32)> = HashMap::new();
        let mut seen_terms: HashSet<String> = HashSet::new();
        let mut total_idf = 0.0f32;
        for tok in tokenize(query) {
            let first_occurrence = seen_terms.insert(tok.clone());
            let Some(docs) = self.postings.get(&tok) else {
                continue;
            };
            let df = docs.len() as f32;
            let n = self.doc_count.max(1) as f32;
            let idf = ((n - df + 0.5) / (df + 0.5)).max(0.0).ln_1p();
            if first_occurrence {
                total_idf += idf;
            }
            for (&doc, &tf) in docs {
                let dl = self.doc_len.get(&doc).copied().unwrap_or(avg_len);
                let tf_norm = tf as f32 * (k1 + 1.0)
                    / (tf as f32 + k1 * (1.0 - b + b * dl / avg_len.max(1.0)));
                let e = scores.entry(doc).or_insert((0.0, 0, 0.0));
                e.0 += idf * tf_norm;
                if first_occurrence {
                    e.1 += 1;
                    e.2 += idf;
                }
            }
        }
        scores.retain(|_, (_, terms, _)| *terms > 0);
        (scores, total_idf)
    }
}
