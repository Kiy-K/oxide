//! Hybrid retrieval: lexical (BM25) + semantic (vector) fused with reciprocal
//! rank fusion, followed by structural expansion. Every hit carries the
//! evidence that selected it.

use crate::embeddings::{tokenize, EmbeddingProvider};
use crate::index::IndexBackend;
use crate::symbols::{Symbol, SymbolKind};
use std::collections::{HashMap, HashSet};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SearchMode {
    LexicalOnly,
    VectorOnly,
    Hybrid,
}

pub struct SearchOptions {
    pub limit: usize,
    pub mode: SearchMode,
    /// Include structural expansion around strong initial hits.
    pub expand: bool,
}

impl Default for SearchOptions {
    fn default() -> Self {
        Self {
            limit: 10,
            mode: SearchMode::Hybrid,
            expand: true,
        }
    }
}

#[derive(Debug, Clone, serde::Serialize)]
pub struct SearchHit {
    #[serde(flatten)]
    pub symbol: Symbol,
    pub score: f32,
    pub reasons: Vec<String>,
    pub snippet: String,
}

const RRF_K: f32 = 60.0;
const LEX_WEIGHT: f32 = 0.6;
const VEC_WEIGHT: f32 = 0.4;
/// Hits scoring at least this fraction of the best lexical score are "strong"
/// seeds for expansion.
const STRONG_SEED_FRAC: f32 = 0.55;

pub struct LexicalIndex {
    postings: HashMap<String, HashMap<u64, u32>>, // term -> doc -> weighted tf
    doc_len: HashMap<u64, f32>,
    doc_count: usize,
}

impl LexicalIndex {
    pub fn build(symbols: &[Symbol]) -> Self {
        // Capacity heuristic: ~20 weighted postings per symbol keeps the
        // posting maps from rehashing during the build.
        let mut postings: HashMap<String, HashMap<u64, u32>> =
            HashMap::with_capacity(symbols.len() * 12);
        let mut doc_len: HashMap<u64, f32> = HashMap::new();
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
            doc_len.insert(id, total_weight as f32);
        }
        Self {
            postings,
            doc_len,
            doc_count: symbols.len(),
        }
    }

    /// BM25 scores for the query terms.
    fn search(&self, query: &str, k1: f32, b: f32) -> HashMap<u64, (f32, usize)> {
        let avg_len = self.doc_len.values().sum::<f32>() / self.doc_count.max(1) as f32;
        let mut scores: HashMap<u64, (f32, usize)> = HashMap::new();
        for tok in tokenize(query) {
            let Some(docs) = self.postings.get(&tok) else {
                continue;
            };
            let df = docs.len() as f32;
            let n = self.doc_count.max(1) as f32;
            let idf = ((n - df + 0.5) / (df + 0.5)).max(0.0).ln_1p();
            for (&doc, &tf) in docs {
                let dl = self.doc_len.get(&doc).copied().unwrap_or(avg_len);
                let tf_norm = tf as f32 * (k1 + 1.0)
                    / (tf as f32 + k1 * (1.0 - b + b * dl / avg_len.max(1.0)));
                let e = scores.entry(doc).or_insert((0.0, 0));
                e.0 += idf * tf_norm;
                e.1 += 1;
            }
        }
        scores.retain(|_, (_, terms)| *terms > 0);
        scores
    }
}

/// Hybrid retrieval engine. Construction builds the lexical index once from a
/// store snapshot; searches reuse it (batch vector loads, no per-symbol SQL).
/// Vectors are loaded lazily on first semantic query and cached for the
/// engine's lifetime, so multi-query sessions pay the load exactly once.
pub struct RetrievalEngine<'a> {
    store: &'a dyn IndexBackend,
    embedder: &'a dyn EmbeddingProvider,
    /// Snapshot of indexed symbols taken at construction time.
    symbols: Vec<Symbol>,
    /// symbol id -> position in `symbols`.
    by_id: HashMap<u64, usize>,
    lexical: LexicalIndex,
    vectors: std::cell::RefCell<Option<HashMap<u64, Vec<f32>>>>,
}

impl<'a> RetrievalEngine<'a> {
    pub fn new(store: &'a dyn IndexBackend, embedder: &'a dyn EmbeddingProvider) -> Self {
        let symbols = store.all_symbols().unwrap_or_default();
        let lexical = LexicalIndex::build(&symbols);
        let by_id = symbols
            .iter()
            .enumerate()
            .map(|(i, s)| (s.id(), i))
            .collect();
        Self {
            store,
            embedder,
            symbols,
            by_id,
            lexical,
            vectors: std::cell::RefCell::new(None),
        }
    }

    pub fn search(&self, query: &str, opts: &SearchOptions) -> anyhow::Result<Vec<SearchHit>> {
        if self.symbols.is_empty() {
            return Ok(Vec::new());
        }
        let lookup =
            |id: &u64| -> Option<&Symbol> { self.by_id.get(id).map(|&i| &self.symbols[i]) };

        // ---- lexical stage ----
        let lex_scores = self.lexical.search(query, 1.5, 0.75);

        // ---- semantic stage ----
        let vec_scores = if opts.mode != SearchMode::LexicalOnly {
            let qv = self.embedder.embed(query);
            let mut cache = self.vectors.borrow_mut();
            if cache.is_none() {
                // One batched load instead of one query per symbol.
                *cache = Some(
                    self.store
                        .all_embeddings()?
                        .into_iter()
                        .map(|(id, (_, v))| (id, v))
                        .collect(),
                );
            }
            let embeddings = cache.as_ref().expect("just populated");
            let mut out = HashMap::with_capacity(embeddings.len());
            for s in &self.symbols {
                if let Some(v) = embeddings.get(&s.id()) {
                    if v.len() != qv.len() || v.is_empty() {
                        continue;
                    }
                    let dot: f32 = qv.iter().zip(v.iter()).map(|(a, b)| a * b).sum();
                    out.insert(s.id(), dot);
                }
            }
            out
        } else {
            HashMap::new()
        };

        // ---- fuse ----
        let mut rrf: HashMap<u64, f32> = HashMap::new();
        let mut reasons: HashMap<u64, Vec<String>> = HashMap::new();
        let note = |rrf: &mut HashMap<u64, f32>,
                    reasons: &mut HashMap<u64, Vec<String>>,
                    ranked: Vec<(u64, f32, String)>,
                    weight: f32| {
            for (rank, (id, score, why)) in ranked.into_iter().enumerate() {
                *rrf.entry(id).or_insert(0.0) += weight / (RRF_K + rank as f32 + 1.0);
                reasons
                    .entry(id)
                    .or_default()
                    .push(format!("{why}={score:.3}"));
            }
        };

        match opts.mode {
            SearchMode::LexicalOnly => {
                let mut ranked: Vec<_> = lex_scores.iter().map(|(id, s)| (*id, s.0)).collect();
                ranked.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
                note(
                    &mut rrf,
                    &mut reasons,
                    ranked
                        .into_iter()
                        .take(200)
                        .map(|(id, s)| (id, s, "lexical".into()))
                        .collect(),
                    1.0,
                );
            }
            SearchMode::VectorOnly => {
                let mut ranked: Vec<_> = vec_scores.clone().into_iter().collect();
                ranked.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
                note(
                    &mut rrf,
                    &mut reasons,
                    ranked
                        .into_iter()
                        .take(200)
                        .map(|(id, s)| (id, s, "semantic".into()))
                        .collect(),
                    1.0,
                );
            }
            SearchMode::Hybrid => {
                let mut lr: Vec<_> = lex_scores.iter().map(|(id, s)| (*id, s.0)).collect();
                lr.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
                let lrr: Vec<(u64, f32, String)> = lr
                    .into_iter()
                    .take(200)
                    .map(|(id, s)| (id, s, "lexical".into()))
                    .collect();
                note(&mut rrf, &mut reasons, lrr, LEX_WEIGHT);

                let mut vr: Vec<_> = vec_scores.iter().map(|(id, s)| (*id, *s)).collect();
                vr.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
                let vrr: Vec<(u64, f32, String)> = vr
                    .into_iter()
                    .take(200)
                    .map(|(id, s)| (id, s, "semantic".into()))
                    .collect();
                note(&mut rrf, &mut reasons, vrr, VEC_WEIGHT);
            }
        }

        let mut hits: Vec<SearchHit> = rrf
            .iter()
            .filter_map(|(id, score)| {
                let s = lookup(id)?;
                Some(SearchHit {
                    symbol: s.clone(),
                    score: *score,
                    reasons: reasons.get(id).cloned().unwrap_or_default(),
                    snippet: String::new(),
                })
            })
            .collect();
        hits.sort_by(|a, b| {
            b.score
                .partial_cmp(&a.score)
                .unwrap_or(std::cmp::Ordering::Equal)
        });

        // ---- structural expansion ----
        let base_scores = rrf.clone();
        if opts.expand && !hits.is_empty() {
            let max_lex = lex_scores.values().map(|s| s.0).fold(0.0f32, f32::max);
            let strong: Vec<&Symbol> = hits
                .iter()
                .filter(|h| h.reasons.iter().any(|r| r.starts_with("lexical")))
                .filter_map(|h| lookup(&h.symbol.id()))
                .filter(|s| {
                    lex_scores
                        .get(&s.id())
                        .map(|(sc, _)| *sc >= max_lex * STRONG_SEED_FRAC)
                        .unwrap_or(false)
                        && max_lex > 0.0
                })
                .take(3)
                .collect();
            if !strong.is_empty() {
                let graph = RelationGraph::build(&self.symbols);
                let mut expansions: HashMap<u64, (f32, Vec<String>)> = HashMap::new();
                for seed in strong {
                    let boost_base = rrf.get(&seed.id()).copied().unwrap_or(0.001);
                    for (rel, cand) in graph.neighbors(seed) {
                        if cand.id() == seed.id() {
                            continue;
                        }
                        let e = expansions.entry(cand.id()).or_insert((0.0, Vec::new()));
                        e.0 += boost_base * 0.5;
                        let why = format!("{}←{}", rel, seed.qualified_name);
                        if !e.1.contains(&why) {
                            e.1.push(why);
                        }
                    }
                }
                for (id, (boost, whys)) in expansions {
                    *rrf.entry(id).or_insert(0.0) += boost;
                    reasons.entry(id).or_default().extend(whys);
                }
            }
        }

        // Direct hits (lexical/semantic evidence) always outrank expansion-only
        // context, and both lists are ordered by their PRE-expansion score so
        // expansion supplements without reordering real matches.
        let has_direct = |id: u64| -> bool {
            reasons
                .get(&id)
                .map(|rs| {
                    rs.iter()
                        .any(|r| r.starts_with("lexical") || r.starts_with("semantic"))
                })
                .unwrap_or(false)
        };
        let mut direct_hits: Vec<SearchHit> = Vec::new();
        let mut expanded_hits: Vec<SearchHit> = Vec::new();
        for (id, score) in &rrf {
            let Some(s) = lookup(id) else { continue };
            let base = base_scores.get(id).copied().unwrap_or(0.0);
            let hit = SearchHit {
                symbol: s.clone(),
                // Expansion-only context ranks by its expansion score; real
                // matches keep their stable pre-expansion score.
                score: if base > 0.0 { base } else { *score },
                reasons: reasons.get(id).cloned().unwrap_or_default(),
                snippet: String::new(),
            };
            if base > 0.0 || has_direct(*id) {
                direct_hits.push(hit);
            } else {
                expanded_hits.push(hit);
            }
        }
        direct_hits.sort_by(|a, b| {
            b.score
                .partial_cmp(&a.score)
                .unwrap_or(std::cmp::Ordering::Equal)
        });
        expanded_hits.sort_by(|a, b| {
            b.score
                .partial_cmp(&a.score)
                .unwrap_or(std::cmp::Ordering::Equal)
        });
        hits.clear();
        hits.extend(direct_hits);
        hits.extend(expanded_hits);

        hits.truncate(opts.limit);
        Ok(hits)
    }
}

/// Slice `start..end` (1-based inclusive) from a file, capped at `cap` lines.
pub fn read_snippet(path: &std::path::Path, start: u32, end: u32, cap: usize) -> String {
    let Ok(src) = std::fs::read_to_string(path) else {
        return String::new();
    };
    src.lines()
        .skip(start.saturating_sub(1) as usize)
        .take(((end.saturating_sub(start - 1)) as usize).min(cap))
        .collect::<Vec<_>>()
        .join("\n")
}

/// High-confidence structural relations used for expansion.
pub struct RelationGraph<'a> {
    symbols: &'a [Symbol],
    by_qualified: HashMap<&'a str, &'a Symbol>,
    children_of: HashMap<&'a str, Vec<&'a Symbol>>,
    defs_by_name: HashMap<&'a str, Vec<&'a Symbol>>,
    files: HashSet<&'a str>,
}

fn is_test_symbol(s: &Symbol) -> bool {
    let f = s.file.to_lowercase();
    let n = s.name.to_lowercase();
    f.starts_with("test_")
        || f.contains("_test.")
        || f.contains(".test.")
        || f.contains(".spec.")
        || f.contains("/tests/")
        || f.contains("\\tests\\")
        || n.starts_with("test_")
        || n.ends_with("_test")
        || n.ends_with("test") && (matches!(s.kind, SymbolKind::Function | SymbolKind::Method))
}

impl<'a> RelationGraph<'a> {
    pub fn build(symbols: &'a [Symbol]) -> Self {
        let mut by_qualified = HashMap::new();
        let mut children_of: HashMap<&str, Vec<&Symbol>> = HashMap::new();
        let mut defs_by_name: HashMap<&str, Vec<&Symbol>> = HashMap::new();
        let mut files = HashSet::new();
        for s in symbols {
            by_qualified.insert(s.qualified_name.as_str(), s);
            if let Some(p) = &s.parent {
                children_of.entry(p.as_str()).or_default().push(s);
            } else if s.kind != SymbolKind::Module {
                defs_by_name.entry(s.name.as_str()).or_default().push(s);
            }
            files.insert(s.file.as_str());
        }
        Self {
            symbols,
            by_qualified,
            children_of,
            defs_by_name,
            files,
        }
    }

    /// Resolve an import string from a file to concrete symbols, when the
    /// target file exists in the indexed set.
    pub fn resolve_import<'b>(&'b self, from_file: &str, module: &str) -> Vec<&'a Symbol> {
        let Some(target) = resolve_module(module, from_file, &self.files) else {
            return Vec::new();
        };
        self.symbols
            .iter()
            .filter(move |s| s.file == target && s.kind != SymbolKind::Module)
            .collect()
    }

    /// Related tests: test-file symbols referencing the seed's bare name.
    pub fn related_tests(&self, seed: &Symbol) -> Vec<&'a Symbol> {
        self.symbols
            .iter()
            .filter(|s| is_test_symbol(s))
            .filter(|t| t.references.iter().any(|r| r == &seed.name) || t.name.contains(&seed.name))
            .collect()
    }

    pub fn neighbors(&self, seed: &Symbol) -> Vec<(String, &'a Symbol)> {
        let mut out: Vec<(String, &'a Symbol)> = Vec::new();
        if let Some(p) = &seed.parent {
            if let Some(parent_sym) = self.by_qualified.get(p.as_str()) {
                out.push(("parent".into(), *parent_sym));
            }
            for c in self.children_of.get(p.as_str()).into_iter().flatten() {
                out.push(("sibling".into(), *c));
            }
        }
        for c in self
            .children_of
            .get(seed.qualified_name.as_str())
            .into_iter()
            .flatten()
        {
            out.push(("child".into(), *c));
        }
        // References from this symbol to known definitions.
        for r in &seed.references {
            if let Some(defs) = self.defs_by_name.get(r.as_str()) {
                for d in defs {
                    if d.file != seed.file {
                        out.push(("uses".into(), *d));
                    }
                }
            }
        }
        // Definitions imported by this file.
        for m in &seed.imports {
            for d in self.resolve_import(&seed.file, m) {
                out.push(("imported-definition".into(), d));
            }
        }
        // Related tests.
        for t in self.related_tests(seed) {
            out.push(("test".into(), t));
        }
        out.truncate(24);
        out
    }
}

/// Map `./utils/token` (+ language extensions / __init__ / index) to a file
/// present in `files`. Returns None when ambiguous or missing.
pub fn resolve_module(module: &str, from_file: &str, files: &HashSet<&str>) -> Option<String> {
    let norm = module.trim_start_matches("@/");
    let joined = if let Some(rest) = norm.strip_prefix("./").or_else(|| norm.strip_prefix("../")) {
        let ups = norm.matches("../").count();
        let mut parts: std::collections::VecDeque<&str> = from_file.split('/').collect();
        parts.pop_back(); // drop file name
        for _ in 0..ups.min(parts.len()) {
            parts.pop_back();
        }
        let mut p = parts.into_iter().collect::<Vec<_>>().join("/");
        if !p.is_empty() {
            p.push('/');
        }
        format!("{p}{rest}")
    } else if norm.starts_with('.') {
        return None;
    } else {
        // Absolute python-style import: try as path anywhere.
        norm.replace('.', "/")
    };

    let candidates = [
        format!("{joined}.py"),
        format!("{joined}.pyi"),
        format!("{joined}.ts"),
        format!("{joined}.tsx"),
        format!("{joined}/__init__.py"),
        format!("{joined}/index.ts"),
        format!("{joined}/index.tsx"),
    ];
    let matches: Vec<String> = candidates
        .into_iter()
        .filter(|c| files.contains(c.as_str()))
        .collect();
    if matches.len() == 1 {
        Some(matches.into_iter().next().unwrap())
    } else {
        None
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::embeddings::HashedEmbedder;
    use crate::index::{IndexBackend, SqliteStore};
    use crate::symbols::{content_hash, SymbolKind};

    fn sym(file: &str, qname: &str, kind: SymbolKind, sig: &str, refs: &[&str]) -> Symbol {
        let name = qname.rsplit('.').next().unwrap().to_string();
        Symbol {
            qualified_name: qname.into(),
            name,
            kind,
            language: if file.ends_with(".py") {
                crate::symbols::Language::Python
            } else {
                crate::symbols::Language::TypeScript
            },
            file: file.into(),
            start_line: 1,
            end_line: 5,
            content_hash: content_hash(sig),
            signature: sig.into(),
            imports: vec![],
            exported: true,
            parent: None,
            references: refs.iter().map(|s| s.to_string()).collect(),
        }
    }

    fn seed_store() -> SqliteStore {
        let store = SqliteStore::open(std::path::Path::new(":memory:")).unwrap();
        store
    }

    #[test]
    fn exact_identifier_search_works_without_embeddings() {
        let mut store = seed_store();
        let syms = [
            sym(
                "src/retry.py",
                "RetryPolicy",
                SymbolKind::Class,
                "class RetryPolicy:",
                &[],
            ),
            sym(
                "src/auth.py",
                "refresh_token",
                SymbolKind::Function,
                "def refresh_token():",
                &[],
            ),
        ];
        store.replace_file("src/retry.py", 1, &syms[..1]).unwrap();
        store.replace_file("src/auth.py", 1, &syms[1..]).unwrap();
        let emb = HashedEmbedder::default();
        let engine = RetrievalEngine::new(&store, &emb);
        let opts = SearchOptions {
            limit: 5,
            mode: SearchMode::LexicalOnly,
            expand: false,
        };
        let hits = engine.search("RetryPolicy", &opts).unwrap();
        assert_eq!(hits[0].symbol.qualified_name, "RetryPolicy");
        assert!(hits[0].reasons.iter().any(|r| r.starts_with("lexical")));
    }

    #[test]
    fn semantic_search_finds_related_without_name_overlap() {
        let mut store = seed_store();
        let s1 = sym(
            "src/http/backoff.py",
            "BackoffScheduler",
            SymbolKind::Class,
            "class BackoffScheduler: retry schedule for failed requests",
            &[],
        );
        store
            .replace_file("src/http/backoff.py", 1, std::slice::from_ref(&s1))
            .unwrap();
        let emb = HashedEmbedder::default();
        {
            let s = &(&s1);
            store
                .put_embedding(s.id(), &emb.embed(&crate::index::embed_text(s)))
                .unwrap();
        }
        let engine = RetrievalEngine::new(&store, &emb);
        let opts = SearchOptions {
            limit: 3,
            mode: SearchMode::VectorOnly,
            expand: false,
        };
        let hits = engine.search("retrying failed http calls", &opts).unwrap();
        assert!(!hits.is_empty());
        assert!(hits[0].reasons.iter().any(|r| r.starts_with("semantic")));
    }

    #[test]
    fn hybrid_expands_to_referenced_definition_and_test() {
        let mut store = seed_store();
        let client = sym(
            "src/net/client.py",
            "HttpClient.fetch",
            SymbolKind::Method,
            "def fetch(self, url): uses retry_policy",
            &["retry_policy"],
        );
        let policy = sym(
            "src/net/retry.py",
            "RetryPolicy",
            SymbolKind::Class,
            "class RetryPolicy:",
            &[],
        );
        let test = sym(
            "tests/test_retry.py",
            "test_retry_policy_expires",
            SymbolKind::Function,
            "def test_retry_policy_expires():",
            &["RetryPolicy"],
        );
        store
            .replace_file("src/net/client.py", 1, std::slice::from_ref(&client))
            .unwrap();
        store
            .replace_file("src/net/retry.py", 1, std::slice::from_ref(&policy))
            .unwrap();
        store
            .replace_file("tests/test_retry.py", 1, std::slice::from_ref(&test))
            .unwrap();
        let emb = HashedEmbedder::default();
        for s in &[&client, &policy, &test] {
            store
                .put_embedding(s.id(), &emb.embed(&crate::index::embed_text(s)))
                .unwrap();
        }
        let engine = RetrievalEngine::new(&store, &emb);
        let opts = SearchOptions {
            limit: 8,
            mode: SearchMode::LexicalOnly,
            expand: true,
        };
        let hits = engine.search("RetryPolicy", &opts).unwrap();
        let names: Vec<&str> = hits
            .iter()
            .map(|h| h.symbol.qualified_name.as_str())
            .collect();
        assert!(names.contains(&"RetryPolicy"), "{names:?}");
        assert!(
            names.contains(&"test_retry_policy_expires"),
            "expansion should surface related test: {names:?}"
        );
        assert!(
            names.contains(&"HttpClient.fetch"),
            "expansion should surface referrer: {names:?}"
        );
        let test_hit = hits
            .iter()
            .find(|h| h.symbol.name == "test_retry_policy_expires")
            .unwrap();
        assert!(test_hit.reasons.iter().any(|r| r.contains("test←")));
    }

    #[test]
    fn module_resolution_probes_extensions_and_indexes() {
        let files: HashSet<&str> = ["src/utils/token.py", "pkg/api/index.ts"]
            .into_iter()
            .collect();
        assert_eq!(
            resolve_module("./token", "src/utils/auth.py", &files).as_deref(),
            Some("src/utils/token.py")
        );
        assert_eq!(
            resolve_module("pkg/api", "src/main.ts", &files).as_deref(),
            Some("pkg/api/index.ts")
        );
        assert_eq!(resolve_module("./missing", "src/main.ts", &files), None);
    }
}
