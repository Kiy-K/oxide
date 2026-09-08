//! Hybrid retrieval: lexical (BM25) + semantic (vector) fused with reciprocal
//! rank fusion, followed by structural expansion. Every hit carries the
//! evidence that selected it.

use crate::config::{
    EXPANSION_STRONG_SEED_FRACTION, FUSION_CANDIDATE_LIMIT, FUSION_LEXICAL_WEIGHT, FUSION_RRF_K,
    FUSION_SEMANTIC_WEIGHT, TERM_COVERAGE_ALPHA_DEFAULT, TERM_COVERAGE_MAX_BONUS_FRACTION,
};
use crate::embeddings::EmbeddingProvider;
use crate::lexical::LexicalIndex;
use crate::relations::RelationGraph;
use crate::storage::IndexBackend;
use crate::symbols::{Symbol, SymbolKind};
use std::collections::HashMap;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SearchMode {
    LexicalOnly,
    VectorOnly,
    Hybrid,
}

/// Relevance/latency tradeoff for a request. Controls how much *expensive*
/// evidence (bounded ast-grep expansion, in `context.rs`) gets collected on
/// top of the always-on lexical+semantic stage — it does not gate lexical or
/// semantic scoring themselves, which run unconditionally and concurrently.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum RetrievalMode {
    Fast,
    #[default]
    Balanced,
    Quality,
}

impl RetrievalMode {
    pub fn parse(s: &str) -> Option<Self> {
        match s.trim().to_ascii_lowercase().as_str() {
            "fast" => Some(Self::Fast),
            "balanced" => Some(Self::Balanced),
            "quality" => Some(Self::Quality),
            _ => None,
        }
    }

    /// `explicit` (a `--mode`/tool-argument flag) wins; then `$OXIDE_RETRIEVAL_MODE`;
    /// an unconfigured agent always lands on `Balanced` (the `Default` impl).
    /// Mirrors the existing embedder-selection precedence in `cli.rs`.
    pub fn resolve(explicit: Option<&str>) -> Self {
        explicit
            .and_then(Self::parse)
            .or_else(|| {
                std::env::var("OXIDE_RETRIEVAL_MODE")
                    .ok()
                    .and_then(|v| Self::parse(&v))
            })
            .unwrap_or_default()
    }

    /// Bounded ast-grep expansion budget: `(max anchored seeds, max files per
    /// seed)`. `None` means skip the stage entirely (`Fast`) — never a
    /// whole-repo scan regardless of mode.
    pub fn structural_budget(self) -> Option<(usize, usize)> {
        match self {
            Self::Fast => None,
            Self::Balanced => Some((2, 3)),
            Self::Quality => Some((3, 6)),
        }
    }

    /// Whether the (currently no-op) downstream reranker stage runs.
    pub fn rerank(self) -> bool {
        matches!(self, Self::Quality)
    }
}

/// `$OXIDE_TERM_COVERAGE_ALPHA` overrides `TERM_COVERAGE_ALPHA_DEFAULT` for
/// the term-coverage corroboration experiment only (docs/term-coverage-eval/) —
/// mirrors `RetrievalMode::resolve`'s env-override precedence. Any parse
/// failure, including unset, falls back to the frozen `0.0` default, which
/// is a no-op.
fn resolve_term_coverage_alpha() -> f32 {
    std::env::var("OXIDE_TERM_COVERAGE_ALPHA")
        .ok()
        .and_then(|v| v.parse::<f32>().ok())
        .unwrap_or(TERM_COVERAGE_ALPHA_DEFAULT)
}

pub struct SearchOptions {
    pub limit: usize,
    pub mode: SearchMode,
    /// Include structural expansion around strong initial hits.
    pub expand: bool,
    pub retrieval_mode: RetrievalMode,
}

impl Default for SearchOptions {
    fn default() -> Self {
        Self {
            limit: 10,
            mode: SearchMode::Hybrid,
            expand: true,
            retrieval_mode: RetrievalMode::default(),
        }
    }
}

/// Score-descending order with a stable tie-break on symbol id. `HashMap`
/// iteration order is randomized per process, so without this, results tied
/// on score (a common outcome of the discrete RRF/BM25 formulas) would sort
/// differently across otherwise-identical runs — read-only search/context
/// must be deterministic for the same index and query.
fn cmp_score_id(a: &(u64, f32), b: &(u64, f32)) -> std::cmp::Ordering {
    b.1.partial_cmp(&a.1)
        .unwrap_or(std::cmp::Ordering::Equal)
        .then_with(|| a.0.cmp(&b.0))
}

/// Same tie-break as [`cmp_score_id`], applied to assembled hits.
fn cmp_hit(a: &SearchHit, b: &SearchHit) -> std::cmp::Ordering {
    b.score
        .partial_cmp(&a.score)
        .unwrap_or(std::cmp::Ordering::Equal)
        .then_with(|| a.symbol.id().cmp(&b.symbol.id()))
}

#[derive(Debug, Clone, serde::Serialize)]
pub struct SearchHit {
    #[serde(flatten)]
    pub symbol: Symbol,
    pub score: f32,
    pub reasons: Vec<String>,
    pub snippet: String,
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
        let root = store
            .get_meta("root")
            .ok()
            .flatten()
            .map(std::path::PathBuf::from);
        let lexical = LexicalIndex::build(&symbols, root.as_deref());
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

        // Vector cache load happens synchronously (once per engine lifetime,
        // cheap on a cache hit) so the two independent evidence providers
        // below only ever need a read-only borrow, which is what lets them
        // run on separate threads: `RefCell` itself is never `Sync`, but a
        // `Ref`'s target is a plain `HashMap`, and `&HashMap` is `Sync`.
        // Degrades gracefully: a failed load (e.g. a corrupt embeddings
        // table) drops semantic evidence for this query instead of failing
        // the whole search — lexical evidence alone is still useful.
        if opts.mode != SearchMode::LexicalOnly && self.vectors.borrow().is_none() {
            let loaded = self
                .store
                .all_embeddings()
                .map(|rows| {
                    rows.into_iter()
                        .map(|(id, (_, v))| (id, v))
                        .collect::<HashMap<_, _>>()
                })
                .unwrap_or_default();
            *self.vectors.borrow_mut() = Some(loaded);
        }
        let vectors_guard = self.vectors.borrow();
        let embeddings: Option<&HashMap<u64, Vec<f32>>> = vectors_guard.as_ref();

        // ---- lexical + semantic stages, concurrently ----
        // Independent evidence providers: BM25 is pure in-memory CPU work,
        // semantic scoring's `embed_query` may be a blocking HTTP round trip
        // (`HttpEmbedder`). Serializing them (the old code did) pays their
        // latency sum; run them on separate OS threads so a request pays the
        // max instead. Plain `std::thread::scope` (no tokio task) because
        // this must work identically from a fully synchronous caller (the
        // `oxide context`/`oxide search` CLI path runs with no async runtime
        // at all) and from inside MCP's `spawn_blocking` closure alike.
        // Bind the specific Sync fields the closures need — capturing `self`
        // wholesale would drag in `store: &dyn IndexBackend` and
        // `vectors: RefCell<..>`, neither of which is `Sync`, even though
        // the closures below never touch them.
        let lexical = &self.lexical;
        let symbols = &self.symbols;
        let embedder = self.embedder;
        let (lex_result, vec_scores) = std::thread::scope(|scope| {
            let lex_handle = scope.spawn(|| lexical.search(query, 1.5, 0.75));
            let vec_handle = scope.spawn(|| -> HashMap<u64, f32> {
                let Some(embeddings) = embeddings else {
                    return HashMap::new();
                };
                let qv = embedder.embed_query(query);
                let mut out = HashMap::with_capacity(embeddings.len());
                for s in symbols {
                    if let Some(v) = embeddings.get(&s.id()) {
                        if v.len() != qv.len() || v.is_empty() {
                            continue;
                        }
                        let dot: f32 = qv.iter().zip(v.iter()).map(|(a, b)| a * b).sum();
                        out.insert(s.id(), dot);
                    }
                }
                out
            });
            // A panicking provider thread must not take the whole search
            // down with it — treat it the same as "no evidence from this
            // provider" rather than propagating the panic.
            let lex = lex_handle.join().unwrap_or_default();
            let vec = vec_handle.join().unwrap_or_default();
            (lex, vec)
        });
        let (lex_scores, lex_total_idf) = lex_result;

        // ---- fuse ----
        let mut rrf: HashMap<u64, f32> = HashMap::new();
        let mut reasons: HashMap<u64, Vec<String>> = HashMap::new();
        let note = |rrf: &mut HashMap<u64, f32>,
                    reasons: &mut HashMap<u64, Vec<String>>,
                    ranked: Vec<(u64, f32, String)>,
                    weight: f32| {
            for (rank, (id, score, why)) in ranked.into_iter().enumerate() {
                *rrf.entry(id).or_insert(0.0) += weight / (FUSION_RRF_K + rank as f32 + 1.0);
                reasons
                    .entry(id)
                    .or_default()
                    .push(format!("{why}={score:.3}"));
            }
        };

        match opts.mode {
            SearchMode::LexicalOnly => {
                let mut ranked: Vec<_> = lex_scores.iter().map(|(id, s)| (*id, s.0)).collect();
                ranked.sort_by(cmp_score_id);
                note(
                    &mut rrf,
                    &mut reasons,
                    ranked
                        .into_iter()
                        .take(FUSION_CANDIDATE_LIMIT)
                        .map(|(id, s)| (id, s, "lexical".into()))
                        .collect(),
                    1.0,
                );
            }
            SearchMode::VectorOnly => {
                let mut ranked: Vec<_> = vec_scores.clone().into_iter().collect();
                ranked.sort_by(cmp_score_id);
                note(
                    &mut rrf,
                    &mut reasons,
                    ranked
                        .into_iter()
                        .take(FUSION_CANDIDATE_LIMIT)
                        .map(|(id, s)| (id, s, "semantic".into()))
                        .collect(),
                    1.0,
                );
            }
            SearchMode::Hybrid => {
                let mut lr: Vec<_> = lex_scores.iter().map(|(id, s)| (*id, s.0)).collect();
                lr.sort_by(cmp_score_id);
                let lrr: Vec<(u64, f32, String)> = lr
                    .into_iter()
                    .take(FUSION_CANDIDATE_LIMIT)
                    .map(|(id, s)| (id, s, "lexical".into()))
                    .collect();
                note(&mut rrf, &mut reasons, lrr, FUSION_LEXICAL_WEIGHT);

                let mut vr: Vec<_> = vec_scores.iter().map(|(id, s)| (*id, *s)).collect();
                vr.sort_by(cmp_score_id);
                let vrr: Vec<(u64, f32, String)> = vr
                    .into_iter()
                    .take(FUSION_CANDIDATE_LIMIT)
                    .map(|(id, s)| (id, s, "semantic".into()))
                    .collect();
                note(&mut rrf, &mut reasons, vrr, FUSION_SEMANTIC_WEIGHT);
            }
        }

        // ---- optional term-coverage corroboration (experiment) ----
        // See docs/term-coverage-eval/README.md. `alpha` is 0.0 (a no-op,
        // byte-identical to pre-experiment scoring) unless
        // `$OXIDE_TERM_COVERAGE_ALPHA` is set — this never runs in shipped
        // Fast/Balanced/Quality behavior by default, independent of
        // `RetrievalMode` entirely. `VectorOnly` is excluded: the lexical
        // thread runs unconditionally regardless of `opts.mode`, so boosting
        // there would inject lexical evidence into the arm
        // `tests/benchmark_gate.rs` uses as the hybrid-vs-vector-only
        // control.
        //
        // Bounded *additive* bonus relative to this query's top fused
        // score, not the original multiplicative `1 + alpha*coverage`
        // reweight: the 21-task sweep found the multiplicative form let a
        // trailing candidate's own coverage share outright overtake a
        // dominant exact-identifier leader once alpha reached 0.2 (Section
        // 3(a), docs/term-coverage-eval/README.md), because the boost
        // scaled with the *trailing candidate's own* score rather than with
        // the leader's margin. Scaling the bonus to a small fraction of the
        // leader's score instead means a leader whose margin exceeds the
        // largest possible bonus can't be dethroned by coverage alone,
        // while still breaking near-ties toward genuine multi-term
        // corroboration — see `term_coverage_bonus_cannot_overturn_a_leader
        // _whose_margin_exceeds_it` below.
        //
        // Whole-file `Module` symbols are excluded from receiving the bonus
        // entirely: their lexical "document" is the entire file (body-text
        // indexing in `LexicalIndex::build`, frozen per AGENTS.md), so
        // `coverage` is close to 1.0 for them almost by construction,
        // independent of relevance — this is the size-bias mechanism
        // (Section 3(b) in the same doc) behind every artifact "win" found
        // in that sweep. Excluding them here, rather than changing how
        // `coverage`/`lex_scores` are computed, keeps the frozen BM25/
        // lexical-index baseline (`LexicalIndex::build`/`search`) untouched
        // for every other caller and for alpha=0.
        let term_coverage_alpha = resolve_term_coverage_alpha();
        if term_coverage_alpha > 0.0
            && matches!(opts.mode, SearchMode::Hybrid | SearchMode::LexicalOnly)
            && lex_total_idf > 0.0
        {
            let top_score = rrf.values().cloned().fold(0.0f32, f32::max);
            if top_score > 0.0 {
                for (id, score) in rrf.iter_mut() {
                    let Some((_, _, matched_idf)) = lex_scores.get(id) else {
                        continue;
                    };
                    if lookup(id).map(|s| s.kind) == Some(SymbolKind::Module) {
                        continue;
                    }
                    let coverage = (matched_idf / lex_total_idf).clamp(0.0, 1.0);
                    let bonus = term_coverage_alpha
                        * coverage
                        * top_score
                        * TERM_COVERAGE_MAX_BONUS_FRACTION;
                    *score += bonus;
                }
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
        hits.sort_by(cmp_hit);

        // ---- structural expansion ----
        let base_scores = rrf.clone();
        if opts.expand && opts.retrieval_mode != RetrievalMode::Fast && !hits.is_empty() {
            let max_lex = lex_scores.values().map(|s| s.0).fold(0.0f32, f32::max);
            let strong: Vec<&Symbol> = hits
                .iter()
                .filter(|h| h.reasons.iter().any(|r| r.starts_with("lexical")))
                .filter_map(|h| lookup(&h.symbol.id()))
                .filter(|s| {
                    lex_scores
                        .get(&s.id())
                        .map(|(sc, _, _)| *sc >= max_lex * EXPANSION_STRONG_SEED_FRACTION)
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
        direct_hits.sort_by(cmp_hit);
        expanded_hits.sort_by(cmp_hit);
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

#[cfg(test)]
mod tests {
    use super::*;
    use crate::embeddings::HashedEmbedder;
    use crate::lexical::LexicalIndex;
    use crate::relations::resolve_module;
    use crate::storage::{IndexBackend, SqliteStore};
    use crate::symbols::{content_hash, SymbolKind};
    use std::collections::HashSet;

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
            calls: Vec::new(),
            bases: Vec::new(),
        }
    }

    fn seed_store() -> SqliteStore {
        let store = SqliteStore::open(std::path::Path::new(":memory:")).unwrap();
        store
    }

    /// Records which method the semantic stage actually calls, so the
    /// migration from `embed` to `embed_query` in `RetrievalEngine::search`
    /// is falsifiable by a future regression.
    struct QuerySpy {
        inner: HashedEmbedder,
        embed_query_calls: std::sync::Mutex<Vec<String>>,
        embed_calls: std::sync::Mutex<Vec<String>>,
    }

    impl QuerySpy {
        fn new() -> Self {
            Self {
                inner: HashedEmbedder::default(),
                embed_query_calls: std::sync::Mutex::new(Vec::new()),
                embed_calls: std::sync::Mutex::new(Vec::new()),
            }
        }
    }

    impl EmbeddingProvider for QuerySpy {
        fn name(&self) -> &str {
            "query-spy"
        }
        fn dim(&self) -> usize {
            self.inner.dim()
        }
        fn embed(&self, text: &str) -> Vec<f32> {
            self.embed_calls.lock().unwrap().push(text.to_string());
            self.inner.embed(text)
        }
        fn embed_query(&self, text: &str) -> Vec<f32> {
            self.embed_query_calls
                .lock()
                .unwrap()
                .push(text.to_string());
            self.inner.embed(text)
        }
    }

    #[test]
    fn search_calls_embed_query_not_embed_for_the_query_text() {
        let mut store = seed_store();
        let s = sym(
            "src/retry.py",
            "RetryPolicy",
            SymbolKind::Class,
            "class RetryPolicy: retry schedule for failed requests",
            &[],
        );
        store
            .replace_file("src/retry.py", 1, std::slice::from_ref(&s), &[])
            .unwrap();
        let seeding_emb = HashedEmbedder::default();
        store
            .put_embedding(
                s.id(),
                &seeding_emb.embed(&crate::embeddings::symbol_embed_text(&s)),
            )
            .unwrap();

        let spy = QuerySpy::new();
        let engine = RetrievalEngine::new(&store, &spy);
        let opts = SearchOptions {
            limit: 5,
            mode: SearchMode::Hybrid,
            expand: false,
            retrieval_mode: RetrievalMode::default(),
        };
        engine.search("retry failed requests", &opts).unwrap();

        assert_eq!(
            spy.embed_query_calls.lock().unwrap().as_slice(),
            ["retry failed requests"]
        );
        assert!(
            spy.embed_calls.lock().unwrap().is_empty(),
            "search() must route the query through embed_query, not embed directly"
        );
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
        store
            .replace_file("src/retry.py", 1, &syms[..1], &[])
            .unwrap();
        store
            .replace_file("src/auth.py", 1, &syms[1..], &[])
            .unwrap();
        let emb = HashedEmbedder::default();
        let engine = RetrievalEngine::new(&store, &emb);
        let opts = SearchOptions {
            limit: 5,
            mode: SearchMode::LexicalOnly,
            expand: false,
            retrieval_mode: RetrievalMode::default(),
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
            .replace_file("src/http/backoff.py", 1, std::slice::from_ref(&s1), &[])
            .unwrap();
        let emb = HashedEmbedder::default();
        {
            let s = &(&s1);
            store
                .put_embedding(s.id(), &emb.embed(&crate::embeddings::symbol_embed_text(s)))
                .unwrap();
        }
        let engine = RetrievalEngine::new(&store, &emb);
        let opts = SearchOptions {
            limit: 3,
            mode: SearchMode::VectorOnly,
            expand: false,
            retrieval_mode: RetrievalMode::default(),
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
            .replace_file("src/net/client.py", 1, std::slice::from_ref(&client), &[])
            .unwrap();
        store
            .replace_file("src/net/retry.py", 1, std::slice::from_ref(&policy), &[])
            .unwrap();
        store
            .replace_file("tests/test_retry.py", 1, std::slice::from_ref(&test), &[])
            .unwrap();
        let emb = HashedEmbedder::default();
        for s in &[&client, &policy, &test] {
            store
                .put_embedding(s.id(), &emb.embed(&crate::embeddings::symbol_embed_text(s)))
                .unwrap();
        }
        let engine = RetrievalEngine::new(&store, &emb);
        let opts = SearchOptions {
            limit: 8,
            mode: SearchMode::LexicalOnly,
            expand: true,
            retrieval_mode: RetrievalMode::default(),
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

        let fast_hits = engine
            .search(
                "RetryPolicy",
                &SearchOptions {
                    retrieval_mode: RetrievalMode::Fast,
                    ..opts
                },
            )
            .unwrap();
        assert!(fast_hits.iter().all(|h| {
            h.reasons
                .iter()
                .all(|reason| !reason.starts_with("uses←") && !reason.starts_with("test←"))
        }));
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

    #[test]
    fn retrieval_mode_parses_case_insensitively_and_rejects_garbage() {
        assert_eq!(RetrievalMode::parse("Fast"), Some(RetrievalMode::Fast));
        assert_eq!(
            RetrievalMode::parse("QUALITY"),
            Some(RetrievalMode::Quality)
        );
        assert_eq!(RetrievalMode::parse("turbo"), None);
    }

    #[test]
    fn retrieval_mode_resolve_prefers_explicit_then_defaults_to_balanced() {
        assert_eq!(RetrievalMode::resolve(Some("fast")), RetrievalMode::Fast);
        // No explicit value and (in a clean test process) no
        // $OXIDE_RETRIEVAL_MODE set: an unconfigured agent must land on
        // Balanced, never silently on Fast or Quality.
        assert_eq!(RetrievalMode::resolve(None), RetrievalMode::Balanced);
    }

    #[test]
    fn hybrid_search_runs_lexical_and_semantic_concurrently_without_changing_results() {
        // Regression guard for the `std::thread::scope` refactor: running the
        // two stages on separate threads must be observationally identical
        // to the old serial code for the same query/mode — same hits, same
        // scores, same order — repeated to catch any nondeterminism from the
        // concurrency itself (e.g. a race on the lazily-loaded vector cache).
        //
        // Also holds `TERM_COVERAGE_ENV_LOCK` even though this test never
        // touches `OXIDE_TERM_COVERAGE_ALPHA` itself: `cargo test` runs
        // tests in parallel, and without this a concurrently-running
        // term-coverage test setting that env var to a nonzero value mid-run
        // would make this test's own repeated `search()` calls legitimately
        // return different scores — a real, reproduced flake, not a
        // hypothetical one.
        let _guard = TERM_COVERAGE_ENV_LOCK
            .lock()
            .unwrap_or_else(|e| e.into_inner());
        let symbols = vec![
            sym(
                "src/retry.py",
                "RetryPolicy",
                SymbolKind::Class,
                "class RetryPolicy: pass",
                &[],
            ),
            sym(
                "src/retry.py",
                "RetryPolicy.should_retry",
                SymbolKind::Method,
                "def should_retry(self, attempt, error): pass",
                &[],
            ),
        ];
        let mut store = SqliteStore::open(std::path::Path::new(":memory:")).unwrap();
        store
            .replace_file("src/retry.py", 1, &symbols, &[])
            .unwrap();
        let emb = HashedEmbedder::default();
        for s in &symbols {
            store
                .put_embedding(s.id(), &emb.embed(&crate::embeddings::symbol_embed_text(s)))
                .unwrap();
        }
        let engine = RetrievalEngine::new(&store, &emb);
        let opts = SearchOptions {
            limit: 5,
            mode: SearchMode::Hybrid,
            expand: false,
            retrieval_mode: RetrievalMode::default(),
        };
        let first = engine.search("retry policy", &opts).unwrap();
        for _ in 0..5 {
            let hits = engine.search("retry policy", &opts).unwrap();
            let ids: Vec<u64> = hits.iter().map(|h| h.symbol.id()).collect();
            let first_ids: Vec<u64> = first.iter().map(|h| h.symbol.id()).collect();
            assert_eq!(ids, first_ids, "concurrent search must be deterministic");
            for (a, b) in hits.iter().zip(first.iter()) {
                assert_eq!(a.score, b.score, "scores must match across repeated runs");
            }
        }
    }

    /// `LexicalIndex::search`'s new coverage evidence (`.1` distinct terms,
    /// `.2` matched IDF) must not perturb `.0` (BM25) or the `retain`
    /// membership at all — this is the RET-001-adjacent invariant the whole
    /// corroboration feature depends on: the frozen fusion scale is only
    /// ever adjusted downstream by a bonus bounded to a small fraction of
    /// the query's top score, never recomputed here.
    #[test]
    fn lexical_index_term_coverage_is_additive_evidence_not_a_score_change() {
        let symbols = vec![
            sym(
                "src/retry.py",
                "RetryPolicy",
                SymbolKind::Class,
                "class RetryPolicy: retry schedule for failed requests",
                &[],
            ),
            sym(
                "src/auth.py",
                "refresh_token",
                SymbolKind::Function,
                "def refresh_token(): pass",
                &[],
            ),
        ];
        let lex = LexicalIndex::build(&symbols, None);
        let (scores, total_idf) = lex.search("retry policy", 1.5, 0.75);
        let retry_id = symbols[0].id();
        let (bm25, terms, matched_idf) = scores[&retry_id];
        assert!(bm25 > 0.0);
        assert_eq!(terms, 2, "both distinct query terms matched RetryPolicy");
        assert!(matched_idf > 0.0);
        // Both query terms exist in the (tiny) corpus, so the coverage
        // denominator equals what the single matching doc contributed.
        assert!((matched_idf - total_idf).abs() < 1e-4);
        assert!(
            !scores.contains_key(&symbols[1].id()),
            "refresh_token matched neither query term and must stay absent, \
             exactly as the pre-coverage `retain` behavior required"
        );
    }

    /// A repeated query token (the norm in whole-issue-body ContextBench
    /// queries, per the corroboration-experiment report) must count once
    /// toward distinct-term coverage — but BM25's own accumulation is
    /// intentionally untouched and still sums a contribution per token
    /// occurrence, standard BM25 query-term-frequency behavior.
    #[test]
    fn lexical_index_repeated_query_token_counts_once_for_coverage_not_for_bm25() {
        let symbols = vec![sym(
            "src/billing/payment.py",
            "Payment",
            SymbolKind::Class,
            "class Payment:",
            &[],
        )];
        let lex = LexicalIndex::build(&symbols, None);
        let (once, once_total) = lex.search("payment", 1.5, 0.75);
        let (twice, twice_total) = lex.search("payment payment", 1.5, 0.75);
        let id = symbols[0].id();
        assert_eq!(
            once[&id].1, twice[&id].1,
            "distinct-term count unaffected by repetition"
        );
        assert!(
            (once[&id].2 - twice[&id].2).abs() < 1e-6,
            "matched-idf coverage sum unaffected by repetition"
        );
        assert!(
            (once_total - twice_total).abs() < 1e-6,
            "total query idf unaffected by repetition"
        );
        assert!(
            twice[&id].0 > once[&id].0,
            "BM25 score itself still accumulates per occurrence, unchanged from before"
        );
    }

    fn corroboration_fixture() -> (SqliteStore, Vec<Symbol>) {
        let mut store = seed_store();
        let syms = vec![
            // Sole match for "payment" (rare, weight-4 name field) — the
            // "one exact identifier" side of the task's example.
            sym(
                "src/billing/payment.py",
                "Payment",
                SymbolKind::Class,
                "class Payment:",
                &[],
            ),
            // Corroborates on "retry" + "policy" (weight-4 name field) —
            // both terms common across the filler docs below, so each is
            // individually low-idf.
            sym(
                "src/retry/checker.py",
                "RetryPolicyChecker",
                SymbolKind::Class,
                "class RetryPolicyChecker:",
                &[],
            ),
            // Weakly corroborates on "retry" + "policy" only in body text
            // (weight 1) — the "several weak natural-language term
            // matches" side of the task's example.
            sym(
                "src/util/misc.py",
                "handle_event",
                SymbolKind::Function,
                "def handle_event(x): retry the policy check once",
                &[],
            ),
            sym(
                "src/filler/a.py",
                "filler_alpha",
                SymbolKind::Function,
                "def filler_alpha(): retry policy here too",
                &[],
            ),
            sym(
                "src/filler/b.py",
                "filler_beta",
                SymbolKind::Function,
                "def filler_beta(): another retry policy mention",
                &[],
            ),
            sym(
                "src/filler/c.py",
                "filler_gamma",
                SymbolKind::Function,
                "def filler_gamma(): yet another retry policy spot",
                &[],
            ),
        ];
        for s in &syms {
            store
                .replace_file(&s.file, 1, std::slice::from_ref(s), &[])
                .unwrap();
        }
        (store, syms)
    }

    /// `cargo test` runs tests in parallel within one process, and
    /// `$OXIDE_TERM_COVERAGE_ALPHA` is global process state — without this,
    /// the three tests below that set/read it can interleave and corrupt
    /// each other's env var, exactly the pre-existing flake pattern
    /// `context::tests::debug_dump_kept_writes_exactly_when_env_var_set`
    /// has for its own env var (unguarded there; empirically confirmed to
    /// flake under `cargo test --lib` even on an unmodified checkout).
    /// Every test that touches `OXIDE_TERM_COVERAGE_ALPHA` must hold this
    /// for its entire set→search→clear sequence.
    static TERM_COVERAGE_ENV_LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());

    /// A guard against exactly the risk RET-005-style review would flag for
    /// this feature: `$OXIDE_TERM_COVERAGE_ALPHA` unset must be
    /// byte-identical to explicitly setting it to `"0.0"`, both landing on
    /// the frozen `TERM_COVERAGE_ALPHA_DEFAULT` no-op path — Balanced (and
    /// every other caller) is provably unaffected unless the experiment
    /// env var is set to something nonzero.
    #[test]
    fn term_coverage_alpha_unset_matches_explicit_zero_byte_for_byte() {
        let _guard = TERM_COVERAGE_ENV_LOCK
            .lock()
            .unwrap_or_else(|e| e.into_inner());
        let (store, _syms) = corroboration_fixture();
        let emb = HashedEmbedder::default();
        let engine = RetrievalEngine::new(&store, &emb);
        let opts = SearchOptions {
            limit: 10,
            mode: SearchMode::LexicalOnly,
            expand: false,
            retrieval_mode: RetrievalMode::default(),
        };
        unsafe { std::env::remove_var("OXIDE_TERM_COVERAGE_ALPHA") };
        let unset_hits = engine.search("payment retry policy", &opts).unwrap();
        unsafe { std::env::set_var("OXIDE_TERM_COVERAGE_ALPHA", "0.0") };
        let zero_hits = engine.search("payment retry policy", &opts).unwrap();
        unsafe { std::env::remove_var("OXIDE_TERM_COVERAGE_ALPHA") };

        let unset_ids: Vec<u64> = unset_hits.iter().map(|h| h.symbol.id()).collect();
        let zero_ids: Vec<u64> = zero_hits.iter().map(|h| h.symbol.id()).collect();
        assert_eq!(unset_ids, zero_ids);
        for (a, b) in unset_hits.iter().zip(zero_hits.iter()) {
            assert_eq!(a.score, b.score);
        }
    }

    /// The task's own worked example: the exact-identifier candidate
    /// (`Payment`, one rare term) must keep outranking the weak-multi-term
    /// candidates at every tested experiment alpha. In this fixture the
    /// corroborating terms ("retry"/"policy") are deliberately common
    /// (shared by `RetryPolicyChecker` and three filler docs), so their
    /// individual IDF is low — meaning `Payment`'s single rare term alone
    /// already carries most of the query's total IDF mass. IDF-weighted
    /// coverage (`matched_idf / total_idf`) therefore gives `Payment` a
    /// *larger* coverage share than `RetryPolicyChecker`'s two common-term
    /// match, so the boost measurably widens `Payment`'s lead as alpha
    /// grows rather than closing it — the opposite of what a raw
    /// distinct-term-count coverage (ignoring IDF) would have done, and
    /// exactly the safety margin the corroboration experiment report
    /// documents as this design's key property.
    #[test]
    fn term_coverage_boost_widens_exact_identifiers_lead_over_common_term_corroborators() {
        let _guard = TERM_COVERAGE_ENV_LOCK
            .lock()
            .unwrap_or_else(|e| e.into_inner());
        let (store, _syms) = corroboration_fixture();
        let emb = HashedEmbedder::default();
        let engine = RetrievalEngine::new(&store, &emb);
        let opts = SearchOptions {
            limit: 10,
            mode: SearchMode::LexicalOnly,
            expand: false,
            retrieval_mode: RetrievalMode::default(),
        };
        let mut prior_gap = 0.0f32;
        for alpha in ["0.1", "0.2", "0.3", "0.5"] {
            unsafe { std::env::set_var("OXIDE_TERM_COVERAGE_ALPHA", alpha) };
            let hits = engine.search("payment retry policy", &opts).unwrap();
            unsafe { std::env::remove_var("OXIDE_TERM_COVERAGE_ALPHA") };
            assert_eq!(
                hits[0].symbol.qualified_name, "Payment",
                "exact single-term match must still rank first at alpha={alpha}"
            );
            let checker = hits
                .iter()
                .find(|h| h.symbol.qualified_name == "RetryPolicyChecker")
                .expect("RetryPolicyChecker present");
            let gap = hits[0].score - checker.score;
            assert!(
                gap > prior_gap,
                "Payment's lead over the common-term corroborator should widen as alpha grows, \
                 since Payment's own coverage share is larger here \
                 (alpha={alpha}, gap={gap}, prior={prior_gap})"
            );
            prior_gap = gap;
        }
    }

    /// Regression test for the size-bias failure mode found by the 21-task
    /// corroboration sweep (Section 3(b), docs/term-coverage-eval/README.md):
    /// every parsed file gets a whole-file `Module`-kind symbol whose
    /// lexical "document" is the entire file, so it wins `coverage` almost
    /// regardless of relevance. Two symbols engineered to have *identical*
    /// coverage and pre-boost score (same query-term matches, same
    /// weights) must diverge only by kind: the `Module` symbol must be
    /// byte-identical to its own alpha=0.0 score at every alpha, while the
    /// non-module symbol's score must actually move.
    #[test]
    fn term_coverage_boost_never_applies_to_module_symbols() {
        let _guard = TERM_COVERAGE_ENV_LOCK
            .lock()
            .unwrap_or_else(|e| e.into_inner());
        let mut store = seed_store();
        let syms = vec![
            sym(
                "src/big/thing.py",
                "alpha_beta_gamma",
                SymbolKind::Module,
                "alpha beta gamma module symbol",
                &[],
            ),
            sym(
                "src/small/thing.py",
                "alpha_beta_gamma",
                SymbolKind::Function,
                "alpha beta gamma module symbol",
                &[],
            ),
        ];
        for s in &syms {
            store
                .replace_file(&s.file, 1, std::slice::from_ref(s), &[])
                .unwrap();
        }
        let emb = HashedEmbedder::default();
        let engine = RetrievalEngine::new(&store, &emb);
        let opts = SearchOptions {
            limit: 10,
            mode: SearchMode::LexicalOnly,
            expand: false,
            retrieval_mode: RetrievalMode::default(),
        };
        unsafe { std::env::remove_var("OXIDE_TERM_COVERAGE_ALPHA") };
        let baseline = engine.search("alpha beta gamma", &opts).unwrap();
        unsafe { std::env::set_var("OXIDE_TERM_COVERAGE_ALPHA", "0.5") };
        let boosted = engine.search("alpha beta gamma", &opts).unwrap();
        unsafe { std::env::remove_var("OXIDE_TERM_COVERAGE_ALPHA") };

        let module_id = syms[0].id();
        let func_id = syms[1].id();
        let base_module = baseline
            .iter()
            .find(|h| h.symbol.id() == module_id)
            .expect("module symbol present at baseline")
            .score;
        let boosted_module = boosted
            .iter()
            .find(|h| h.symbol.id() == module_id)
            .expect("module symbol present when boosted")
            .score;
        assert_eq!(
            base_module, boosted_module,
            "a Module-kind symbol must never receive the coverage bonus, \
             even with identical coverage to a non-module symbol that does"
        );

        let base_func = baseline
            .iter()
            .find(|h| h.symbol.id() == func_id)
            .expect("function symbol present at baseline")
            .score;
        let boosted_func = boosted
            .iter()
            .find(|h| h.symbol.id() == func_id)
            .expect("function symbol present when boosted")
            .score;
        assert!(
            boosted_func > base_func,
            "sanity check: the non-module symbol with the same coverage must \
             actually receive a bonus, or this test would pass vacuously \
             (base={base_func}, boosted={boosted_func})"
        );
    }

    /// Regression test for the identifier-dominance failure mode found by
    /// the 21-task corroboration sweep (Section 3(a),
    /// docs/term-coverage-eval/README.md — the flask `Blueprint` and
    /// requests `Session.request` regressions): a dominant exact-identifier
    /// leader must never be overtaken by a trailing candidate purely
    /// because that candidate corroborates on more distinct query terms.
    /// `blueprint` here matches only one query term but with a strong
    /// weight-4 name/qualified-name match; `helper` weakly corroborates on
    /// the other four terms (weight-1 references), and enough filler docs
    /// exist that all five query terms have comparable document frequency
    /// — so `helper`'s coverage share is provably *larger* than
    /// `blueprint`'s despite `blueprint` being the correct, dominant match.
    /// This reproduces the risky configuration the old multiplicative
    /// `1 + alpha*coverage` design failed on.
    #[test]
    fn term_coverage_bonus_cannot_overturn_a_leader_whose_margin_exceeds_it() {
        let _guard = TERM_COVERAGE_ENV_LOCK
            .lock()
            .unwrap_or_else(|e| e.into_inner());
        let query = "empty name blueprint valueerror raised";
        let mut store = seed_store();
        let syms = vec![
            // The correct, dominant match: one strong weight-4 hit on the
            // identifier itself, no corroboration on the other four terms.
            sym(
                "src/flask/blueprints.py",
                "Blueprint",
                SymbolKind::Class,
                "class Blueprint:",
                &[],
            ),
            // Weakly corroborates on all four *other* terms via low-weight
            // references — matches nothing of "blueprint" itself. The
            // extra padding references are irrelevant to the query and
            // exist only to inflate this doc's length (BM25's `b` length
            // penalty shrinks its real terms' tf_norm) without touching
            // `coverage` at all — coverage sums matched terms' IDF only,
            // independent of document length or tf_norm.
            sym(
                "src/flask/helpers.py",
                "helper",
                SymbolKind::Function,
                "def helper(): pass",
                &[
                    "empty",
                    "name",
                    "valueerror",
                    "raised",
                    "padding_one",
                    "padding_two",
                    "padding_three",
                    "padding_four",
                    "padding_five",
                    "padding_six",
                    "padding_seven",
                    "padding_eight",
                ],
            ),
            // Filler docs give "empty"/"name"/"valueerror"/"raised" a
            // document frequency of 2 each while "blueprint" stays unique
            // (df=1, matched only by `Blueprint` itself) — `blueprint`'s
            // higher per-term idf is what lets it win raw BM25 (one strong
            // hit beats four weak ones), while `helper`'s four-term
            // coverage share is still provably larger than `Blueprint`'s
            // one-term share, since coverage is a share of *total* query
            // idf, not a per-term magnitude.
            sym(
                "src/filler/e.py",
                "unrelated_helper",
                SymbolKind::Function,
                "def unrelated_helper(): pass",
                &[],
            ),
            sym(
                "src/filler/a.py",
                "filler_a",
                SymbolKind::Function,
                "def filler_a(): pass",
                &["empty"],
            ),
            sym(
                "src/filler/b.py",
                "filler_b",
                SymbolKind::Function,
                "def filler_b(): pass",
                &["name"],
            ),
            sym(
                "src/filler/c.py",
                "filler_c",
                SymbolKind::Function,
                "def filler_c(): pass",
                &["valueerror"],
            ),
            sym(
                "src/filler/d.py",
                "filler_d",
                SymbolKind::Function,
                "def filler_d(): pass",
                &["raised"],
            ),
        ];
        for s in &syms {
            store
                .replace_file(&s.file, 1, std::slice::from_ref(s), &[])
                .unwrap();
        }

        // Confirm the fixture actually reproduces the risky condition
        // (helper's coverage share > Blueprint's) before trusting the
        // ranking assertion below — otherwise this test would pass
        // vacuously regardless of whether the fix works.
        let lex = LexicalIndex::build(&syms, None);
        let (scores, total_idf) = lex.search(query, 1.5, 0.75);
        let blueprint_id = syms[0].id();
        let helper_id = syms[1].id();
        let blueprint_coverage = scores[&blueprint_id].2 / total_idf;
        let helper_coverage = scores[&helper_id].2 / total_idf;
        assert!(
            helper_coverage > blueprint_coverage,
            "fixture must reproduce helper's coverage share exceeding Blueprint's \
             (blueprint={blueprint_coverage}, helper={helper_coverage}) or this \
             test proves nothing"
        );
        assert!(
            scores[&blueprint_id].0 > scores[&helper_id].0,
            "fixture must also reproduce Blueprint winning raw BM25 pre-boost \
             (the actually risky configuration — a real leader with a smaller \
             coverage share), not merely tie or lose on both axes"
        );

        let emb = HashedEmbedder::default();
        let engine = RetrievalEngine::new(&store, &emb);
        let opts = SearchOptions {
            limit: 10,
            mode: SearchMode::LexicalOnly,
            expand: false,
            retrieval_mode: RetrievalMode::default(),
        };
        // The alphas actually under consideration for this experiment's
        // rerun (docs/term-coverage-eval/README.md's "0"/"0.05"/"0.1"
        // grid) — not an unbounded claim that no margin can ever be
        // overcome at arbitrarily large alpha, which the bounded-bonus
        // design does not attempt (a leader whose pre-boost margin is
        // this fixture's minimal adjacent-RRF-rank gap will still yield
        // once alpha grows large enough; that's expected, and the
        // sensitivity is exercised — commented, not asserted — below).
        for alpha in ["0.05", "0.1"] {
            unsafe { std::env::set_var("OXIDE_TERM_COVERAGE_ALPHA", alpha) };
            let hits = engine.search(query, &opts).unwrap();
            unsafe { std::env::remove_var("OXIDE_TERM_COVERAGE_ALPHA") };
            assert_eq!(
                hits[0].symbol.qualified_name, "Blueprint",
                "the dominant exact-identifier match must stay first even \
                 though a trailing candidate has higher term coverage \
                 (alpha={alpha})"
            );
        }
    }

    /// The lexical thread runs unconditionally regardless of `opts.mode`
    /// (see `RetrievalEngine::search`'s own comment on why), so without an
    /// explicit `VectorOnly` gate the corroboration boost would leak
    /// lexical evidence into the one arm `tests/benchmark_gate.rs` uses as
    /// the hybrid-vs-vector-only control. A large alpha here is a stress
    /// test: if the gate were ever accidentally removed, this fixture's
    /// scores would visibly change.
    #[test]
    fn term_coverage_boost_never_applies_in_vector_only_mode() {
        let _guard = TERM_COVERAGE_ENV_LOCK
            .lock()
            .unwrap_or_else(|e| e.into_inner());
        let (mut store, syms) = corroboration_fixture();
        let emb = HashedEmbedder::default();
        for s in &syms {
            store
                .put_embedding(s.id(), &emb.embed(&crate::embeddings::symbol_embed_text(s)))
                .unwrap();
        }
        let engine = RetrievalEngine::new(&store, &emb);
        let opts = SearchOptions {
            limit: 10,
            mode: SearchMode::VectorOnly,
            expand: false,
            retrieval_mode: RetrievalMode::default(),
        };
        unsafe { std::env::remove_var("OXIDE_TERM_COVERAGE_ALPHA") };
        let baseline = engine.search("payment retry policy", &opts).unwrap();
        unsafe { std::env::set_var("OXIDE_TERM_COVERAGE_ALPHA", "0.9") };
        let boosted = engine.search("payment retry policy", &opts).unwrap();
        unsafe { std::env::remove_var("OXIDE_TERM_COVERAGE_ALPHA") };

        let baseline_ids: Vec<u64> = baseline.iter().map(|h| h.symbol.id()).collect();
        let boosted_ids: Vec<u64> = boosted.iter().map(|h| h.symbol.id()).collect();
        assert_eq!(baseline_ids, boosted_ids);
        for (a, b) in baseline.iter().zip(boosted.iter()) {
            assert_eq!(
                a.score, b.score,
                "VectorOnly must be immune to the lexical-only signal"
            );
        }
    }
}
