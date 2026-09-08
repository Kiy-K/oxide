//! BM25 lexical retrieval over indexed OXIDE symbols.
//!
//! Two posting sources, one scorer. [`LexicalIndex`] builds the posting map
//! in memory from a symbol snapshot (the original behavior, still used as
//! the fallback for an index whose persisted lexical tables are absent or
//! not known-complete); the persisted tables built by `index::update_base`
//! serve the same postings straight out of SQLite. Both funnel into
//! [`LexicalQuery`] and [`score`], so there is exactly one copy of the BM25
//! arithmetic and parity between the two paths is structural rather than
//! something a test has to keep rediscovering.

use crate::embeddings::tokenize;
use crate::symbols::Symbol;
use std::collections::{HashMap, HashSet};

/// Field weights. A field's tokens are added to the document's term
/// frequencies `weight` times each, and the document's length is the sum of
/// those weights — so a weight is a repetition count, not a score multiplier.
/// Names dominate; signature and path next; context fields last.
pub const W_QUALIFIED_NAME: u32 = 4;
pub const W_NAME: u32 = 4;
pub const W_SIGNATURE: u32 = 2;
pub const W_PATH: u32 = 2;
pub const W_REFERENCE: u32 = 1;
pub const W_IMPORT: u32 = 1;
pub const W_BODY: u32 = 1;

/// Weighted term frequencies for one symbol, plus its document length.
/// `len` is the sum of every weight contributed, which is what BM25's length
/// normalization divides by.
pub struct DocPostings {
    pub symbol_id: u64,
    pub len: u32,
    pub terms: Vec<(String, u32)>,
}

/// Tokenize one symbol's fields into weighted term frequencies.
///
/// `body` is the symbol's own source slice, already in memory — at index
/// time that is `ParsedFile::src`, which the parser has open anyway, so the
/// persisted path never re-reads a file from disk the way
/// [`LexicalIndex::build`] does.
fn postings_for_symbol(s: &Symbol, body: Option<&str>) -> DocPostings {
    let mut tf: HashMap<String, u32> = HashMap::new();
    let mut len = 0u32;
    {
        let mut add = |field: &str, weight: u32| {
            crate::embeddings::tokenize_into(field, &mut |tok| {
                match tf.get_mut(tok) {
                    Some(n) => *n += weight,
                    None => {
                        tf.insert(tok.to_string(), weight);
                    }
                }
                len += weight;
            });
        };
        add(&s.qualified_name, W_QUALIFIED_NAME);
        add(&s.name, W_NAME);
        add(&s.signature, W_SIGNATURE);
        add(&s.file.replace(['/', '.', ':'], " "), W_PATH);
        for r in &s.references {
            add(r, W_REFERENCE);
        }
        for i in &s.imports {
            add(i, W_IMPORT);
        }
        if let Some(body) = body {
            add(body, W_BODY);
        }
    }
    DocPostings {
        symbol_id: s.id(),
        len,
        terms: tf.into_iter().collect(),
    }
}

/// The slice of `src` a symbol's body spans, matching
/// [`LexicalIndex::build`]'s line arithmetic exactly (1-based inclusive
/// lines, clamped to the file, empty when the start line is past the end).
pub fn body_slice(s: &Symbol, src: &str) -> String {
    let start = (s.start_line as usize).saturating_sub(1);
    let lines: Vec<&str> = src.lines().collect();
    if start >= lines.len() {
        return String::new();
    }
    let end = (s.end_line as usize).min(lines.len());
    lines[start..end].join("\n")
}

/// Index-time postings for one parsed file: one entry per symbol, always —
/// including symbols that produce no terms at all. A symbol with no row
/// would have no document length either, and BM25 would silently fall back
/// to the corpus average for it. Same reason `symbol_relations` writes an
/// entry for every symbol even when it is empty.
pub fn compute_file_postings(symbols: &[Symbol], src: &str) -> Vec<DocPostings> {
    symbols
        .iter()
        .map(|s| {
            let body = body_slice(s, src);
            postings_for_symbol(s, Some(&body))
        })
        .collect()
}

/// One query token's postings, materialized. Owned rather than borrowed so
/// scoring can run on a scoped thread that holds no reference to the store
/// (`SqliteStore` is not `Sync`; see `RetrievalEngine::search`).
pub struct QueryTerm {
    /// Whether this is the token's first occurrence in the query — term
    /// coverage counts a repeated query token once, BM25 counts it every time.
    pub first_occurrence: bool,
    pub df: usize,
    /// `(symbol_id, weighted term frequency, document length)`.
    pub postings: Vec<(u64, u32, f32)>,
}

/// Everything BM25 needs for one query, resolved against a posting source.
pub struct LexicalQuery {
    pub doc_count: usize,
    pub avg_len: f32,
    /// Query tokens in query order, absent-from-corpus tokens dropped.
    pub terms: Vec<QueryTerm>,
}

/// BM25 scores for the query terms, plus term-coverage evidence for the
/// corroboration experiment (`term_coverage_alpha`, docs/term-coverage-eval/):
/// for each doc, the number of *distinct* query terms matched — a repeated
/// query token (common in whole-issue-body queries) counts once, not once
/// per occurrence — and the sum of those distinct terms' IDF weights, which
/// discounts common-but-not-stopword terms the way "meaningful" implies. The
/// second return value is the sum of IDF over every distinct query term that
/// appears anywhere in the corpus (`df > 0`) — the coverage denominator.
///
/// Score accumulation is one contribution per query-token occurrence,
/// standard BM25 query-term-frequency behavior.
pub fn score(q: &LexicalQuery, k1: f32, b: f32) -> (HashMap<u64, (f32, usize, f32)>, f32) {
    let mut scores: HashMap<u64, (f32, usize, f32)> = HashMap::new();
    let mut total_idf = 0.0f32;
    let n = q.doc_count.max(1) as f32;
    let avg_len = q.avg_len;
    for term in &q.terms {
        let df = term.df as f32;
        let idf = ((n - df + 0.5) / (df + 0.5)).max(0.0).ln_1p();
        if term.first_occurrence {
            total_idf += idf;
        }
        for &(doc, tf, dl) in &term.postings {
            let tf_norm =
                tf as f32 * (k1 + 1.0) / (tf as f32 + k1 * (1.0 - b + b * dl / avg_len.max(1.0)));
            let e = scores.entry(doc).or_insert((0.0, 0, 0.0));
            e.0 += idf * tf_norm;
            if term.first_occurrence {
                e.1 += 1;
                e.2 += idf;
            }
        }
    }
    scores.retain(|_, (_, terms, _)| *terms > 0);
    (scores, total_idf)
}

/// Query tokens paired with whether each is its first occurrence, in query
/// order. Shared by both posting sources so "first occurrence" means the
/// same thing either way.
pub fn query_tokens(query: &str) -> Vec<(String, bool)> {
    let mut seen: HashSet<String> = HashSet::new();
    tokenize(query)
        .into_iter()
        .map(|tok| {
            let first = seen.insert(tok.clone());
            (tok, first)
        })
        .collect()
}

impl LexicalQuery {
    pub fn empty() -> Self {
        Self {
            doc_count: 0,
            avg_len: 0.0,
            terms: Vec::new(),
        }
    }
}

/// Resolve a query against the persisted postings.
///
/// `doc_count` comes from the caller's symbol snapshot rather than from
/// `lexical_docs`, so the corpus size BM25 divides by is the same number the
/// in-memory index uses (`symbols.len()`) even in the instant between a
/// symbol write and a reader's snapshot.
pub fn prepare_from_store(
    store: &dyn crate::storage::IndexBackend,
    doc_count: usize,
    query: &str,
) -> anyhow::Result<LexicalQuery> {
    let (_, total_len) = store.lexical_totals()?;
    let avg_len = total_len as f32 / doc_count.max(1) as f32;
    let mut terms = Vec::new();
    for (tok, first_occurrence) in query_tokens(query) {
        let rows = store.lexical_postings(&tok)?;
        if rows.is_empty() {
            continue;
        }
        terms.push(QueryTerm {
            first_occurrence,
            df: rows.len(),
            postings: rows
                .into_iter()
                .map(|(doc, tf, len)| (doc, tf, len as f32))
                .collect(),
        });
    }
    Ok(LexicalQuery {
        doc_count,
        avg_len,
        terms,
    })
}

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
            let body = root.map(|root| {
                let src = body_cache.entry(&s.file).or_insert_with(|| {
                    std::fs::read_to_string(root.join(&s.file)).unwrap_or_default()
                });
                body_slice(s, src)
            });
            let doc = postings_for_symbol(s, body.as_deref());
            for (term, tf) in doc.terms {
                postings.entry(term).or_default().insert(doc.symbol_id, tf);
            }
            doc_len.insert(doc.symbol_id, doc.len as f32);
        }
        Self {
            postings,
            doc_len,
            doc_count: symbols.len(),
        }
    }

    fn avg_len(&self) -> f32 {
        self.doc_len.values().sum::<f32>() / self.doc_count.max(1) as f32
    }

    pub fn prepare(&self, query: &str) -> LexicalQuery {
        let avg_len = self.avg_len();
        let mut terms = Vec::new();
        for (tok, first_occurrence) in query_tokens(query) {
            let Some(docs) = self.postings.get(&tok) else {
                continue;
            };
            terms.push(QueryTerm {
                first_occurrence,
                df: docs.len(),
                postings: docs
                    .iter()
                    .map(|(&doc, &tf)| {
                        (doc, tf, self.doc_len.get(&doc).copied().unwrap_or(avg_len))
                    })
                    .collect(),
            });
        }
        LexicalQuery {
            doc_count: self.doc_count,
            avg_len,
            terms,
        }
    }

    pub fn search(&self, query: &str, k1: f32, b: f32) -> (HashMap<u64, (f32, usize, f32)>, f32) {
        score(&self.prepare(query), k1, b)
    }
}
