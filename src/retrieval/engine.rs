//! [`RetrievalEngine`]: the candidate-first request path. BM25 (persisted
//! postings, or the in-memory fallback) runs on the calling thread while the query is
//! embedded on a scoped thread; the exact vector scan streams through a
//! bounded top-K; RRF fuses the two candidate lists; only fused
//! candidates are hydrated; structural expansion (the one stage that
//! needs the whole corpus) appends after the direct hits.

use super::options::{RetrievalMode, SearchHit, SearchMode, SearchOptions};
use super::snapshot::{complete_symbols, lexical_persisted, SymbolSnapshot};
use super::top_k::{cmp_score_id, top_k_by_score, TopK};
use crate::config::{
    EXPANSION_STRONG_SEED_FRACTION, FUSION_CANDIDATE_LIMIT, FUSION_LEXICAL_WEIGHT, FUSION_RRF_K,
    FUSION_SEMANTIC_WEIGHT, TERM_COVERAGE_ALPHA_DEFAULT, TERM_COVERAGE_MAX_BONUS_FRACTION,
};
use crate::embeddings::EmbeddingProvider;
use crate::lexical::LexicalIndex;
use crate::relations::{RelationGraph, RelationIndex};
use crate::storage::IndexBackend;
use crate::symbols::{Symbol, SymbolKind};
use std::collections::HashMap;

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

/// Hybrid retrieval engine over a store snapshot. Candidate-first: a query
/// is scored against the persisted postings and the embedding rows, the
/// bounded top-K of each side is fused, and only those candidates are
/// hydrated into `Symbol`s. Nothing here loads the whole corpus unless the
/// request asks for structural expansion (see [`SymbolSnapshot`]).
pub struct RetrievalEngine<'a> {
    store: &'a dyn IndexBackend,
    embedder: &'a dyn EmbeddingProvider,
    symbol_count: usize,
    lexical: LexicalSource,
    /// Loaded on first need, or supplied by a caller that already holds one.
    snapshot: std::cell::OnceCell<std::borrow::Cow<'a, SymbolSnapshot>>,
    /// A [`RelationIndex`] built over the caller-supplied snapshot — the
    /// `oxide mcp` process cache keeps one per index generation next to
    /// the snapshot (`service/cache.rs::CachedSnapshot`). Only ever paired with
    /// that exact snapshot: `relation_graph` checks the pointer before
    /// using it and builds a fresh index otherwise.
    index: Option<(&'a SymbolSnapshot, &'a RelationIndex)>,
    /// Only populated when `snapshot` was already loaded *without*
    /// relations by search-side expansion and a later call on the same
    /// engine needs them — no production caller does both, so this is
    /// correctness insurance, not a path that costs anything normally.
    snapshot_with_relations: std::cell::OnceCell<SymbolSnapshot>,
}

/// Where BM25 postings come from for this engine.
///
/// `Persisted` is the normal case and costs nothing to construct — the whole
/// point, since `Memory` re-reads every symbol body off disk and rebuilds the
/// posting map on every process start. `Memory` remains for an index whose
/// persisted lexical generation is absent or stale (built by an older
/// binary, or mid-backfill), so retrieval degrades to "slow but identical"
/// rather than "wrong" or "empty".
enum LexicalSource {
    Persisted,
    Memory(LexicalIndex),
}

impl<'a> RetrievalEngine<'a> {
    pub fn new(store: &'a dyn IndexBackend, embedder: &'a dyn EmbeddingProvider) -> Self {
        Self::build(store, embedder, None, None)
    }

    /// Like [`Self::new`], but reuse an already-loaded snapshot instead of
    /// loading one on demand — the long-running-process path.
    pub fn with_snapshot(
        store: &'a dyn IndexBackend,
        embedder: &'a dyn EmbeddingProvider,
        snapshot: &'a SymbolSnapshot,
    ) -> Self {
        Self::build(store, embedder, Some(snapshot), None)
    }

    /// [`Self::with_snapshot`] plus a [`RelationIndex`] already built over
    /// `snapshot.symbols`, so structural expansion (`context`, `review`,
    /// search's own expansion, blast radius) reuses it instead of
    /// rebuilding the graph's maps on every request.
    pub fn with_snapshot_and_index(
        store: &'a dyn IndexBackend,
        embedder: &'a dyn EmbeddingProvider,
        snapshot: &'a SymbolSnapshot,
        index: &'a RelationIndex,
    ) -> Self {
        Self::build(store, embedder, Some(snapshot), Some(index))
    }

    fn build(
        store: &'a dyn IndexBackend,
        embedder: &'a dyn EmbeddingProvider,
        snapshot: Option<&'a SymbolSnapshot>,
        index: Option<&'a RelationIndex>,
    ) -> Self {
        let cell = std::cell::OnceCell::new();
        if let Some(s) = snapshot {
            let _ = cell.set(std::borrow::Cow::Borrowed(s));
        }
        let engine = Self {
            store,
            embedder,
            symbol_count: 0,
            lexical: LexicalSource::Persisted,
            snapshot: cell,
            snapshot_with_relations: std::cell::OnceCell::new(),
            index: snapshot.zip(index),
        };
        // Trust the persisted postings only on an exact generation match.
        // Anything else — no key, an older format, a run interrupted before
        // it published — means the tables may cover only part of the corpus,
        // which would silently shrink BM25's view of the repo instead of
        // failing. Falling back rebuilds in memory (which needs every
        // symbol, so it forces the snapshot load), and the next `oxide
        // index` repairs the persisted copy.
        let (lexical, symbol_count) = if lexical_persisted(store) {
            (
                LexicalSource::Persisted,
                store.symbol_count().unwrap_or_default(),
            )
        } else {
            let root = store
                .get_meta("root")
                .ok()
                .flatten()
                .map(std::path::PathBuf::from);
            // BM25 indexes `references`/`imports`: on this path every
            // snapshot loader returns complete symbols (`lexical_persisted`),
            // so the one load still serves both the fallback and expansion.
            let symbols = &engine.snapshot().symbols;
            (
                LexicalSource::Memory(LexicalIndex::build(symbols, root.as_deref())),
                symbols.len(),
            )
        };
        Self {
            symbol_count,
            lexical,
            ..engine
        }
    }

    pub fn store(&self) -> &'a dyn IndexBackend {
        self.store
    }

    pub fn embedder(&self) -> &'a dyn EmbeddingProvider {
        self.embedder
    }

    /// The full corpus, loaded on first use (without relations unless a
    /// caller supplied a snapshot that has them). A failed load degrades
    /// to an empty snapshot (no expansion) rather than failing the search,
    /// the same contract the old eager `all_symbols().unwrap_or_default()`
    /// had.
    pub fn snapshot(&self) -> &SymbolSnapshot {
        self.snapshot.get_or_init(|| {
            std::borrow::Cow::Owned(
                SymbolSnapshot::load_without_relations(self.store)
                    .unwrap_or_else(|_| SymbolSnapshot::from_symbols(Vec::new())),
            )
        })
    }

    /// The full corpus with `calls`/`bases` merged in, for
    /// `RelationGraph::callers_of`/`implementors_of`. Unlike
    /// [`Self::snapshot`] this propagates a failed load: `build_context`
    /// always reported an unreadable `symbols`/`symbol_relations` table
    /// rather than quietly returning direct hits with no structural
    /// evidence, and still does.
    pub fn snapshot_with_relations(&self) -> anyhow::Result<&SymbolSnapshot> {
        if self.snapshot.get().is_none() {
            let loaded = SymbolSnapshot::load(self.store)?;
            let _ = self.snapshot.set(std::borrow::Cow::Owned(loaded));
        }
        let loaded = self.snapshot.get().expect("set above");
        if loaded.with_relations {
            return Ok(loaded);
        }
        if self.snapshot_with_relations.get().is_none() {
            let _ = self
                .snapshot_with_relations
                .set(SymbolSnapshot::load(self.store)?);
        }
        Ok(self.snapshot_with_relations.get().expect("set above"))
    }

    /// The structural graph over the full corpus with relations — built
    /// from the cached [`RelationIndex`] when this engine was given one
    /// for the very snapshot it is about to use, otherwise built now.
    pub fn relation_graph(&self) -> anyhow::Result<RelationGraph<'_>> {
        let snapshot = self.snapshot_with_relations()?;
        Ok(self.graph_over(snapshot))
    }

    fn graph_over<'s>(&'s self, snapshot: &'s SymbolSnapshot) -> RelationGraph<'s> {
        match self.index {
            Some((cached, index)) if std::ptr::eq(cached, snapshot) => {
                RelationGraph::with_index(&snapshot.symbols, index)
            }
            _ => RelationGraph::build(&snapshot.symbols),
        }
    }

    /// [`complete_symbols`] against this engine's store.
    pub fn complete<'s>(
        &self,
        symbols: impl IntoIterator<Item = &'s mut Symbol>,
    ) -> anyhow::Result<()> {
        complete_symbols(self.store, symbols)
    }

    /// Candidate ids → symbols. From the snapshot when one is already
    /// loaded, otherwise a bounded `WHERE id IN (...)` read — never a full
    /// table load. Ids with no row (a symbol removed between two reads on
    /// a non-snapshotting connection) are simply absent. Snapshot clones are
    /// partial when the snapshot is lean; `search` completes the few that
    /// become seeds or hits, not all of them.
    fn hydrate(&self, ids: impl IntoIterator<Item = u64>) -> HashMap<u64, Symbol> {
        if let Some(snapshot) = self.snapshot.get() {
            return ids
                .into_iter()
                .filter_map(|id| snapshot.get(id).map(|s| (id, s.clone())))
                .collect();
        }
        let ids: Vec<u64> = ids.into_iter().collect();
        self.store
            .symbols_by_ids(&ids)
            .unwrap_or_default()
            .into_iter()
            .map(|s| (s.id(), s))
            .collect()
    }

    /// Materialize the query's postings from whichever source this engine
    /// has. A store read that fails degrades to no lexical evidence for this
    /// query rather than failing the search, matching how a failed vector
    /// scan is already handled.
    fn prepare_lexical(&self, query: &str) -> crate::lexical::LexicalQuery {
        match &self.lexical {
            LexicalSource::Memory(idx) => idx.prepare(query),
            LexicalSource::Persisted => {
                crate::lexical::prepare_from_store(self.store, self.symbol_count, query)
                    .unwrap_or_else(|_| crate::lexical::LexicalQuery::empty())
            }
        }
    }

    /// Exhaustive dot-product scan, streamed: one embedding row is decoded,
    /// scored against `qv` and dropped before the next is read, and only
    /// the `k` best `(id, score)` pairs are retained. O(N·dim) time, O(k)
    /// memory. Rows whose stored vector is not exactly `qv.len()` long are
    /// skipped, and the dot product is the same sequential `f32` sum the
    /// materialized version computed, so scores are bit-identical.
    fn semantic_top_k(&self, qv: &[f32], k: usize) -> Vec<(u64, f32)> {
        let mut top = TopK::new(k);
        if qv.is_empty() {
            return Vec::new();
        }
        let scanned = self.store.for_each_embedding(&mut |id, dim, bytes| {
            let len = dim.min(bytes.len() / 4);
            if len != qv.len() {
                return;
            }
            let mut dot = 0.0f32;
            for (a, c) in qv.iter().zip(bytes.as_chunks::<4>().0.iter().take(len)) {
                dot += a * f32::from_le_bytes(*c);
            }
            top.push(id, dot);
        });
        // A scan that fails part-way must not rank from whatever it managed
        // to read: drop semantic evidence for this query entirely, exactly
        // as the materialized `all_embeddings().unwrap_or_default()` did.
        match scanned {
            Ok(()) => top.into_sorted(),
            Err(_) => Vec::new(),
        }
    }

    pub fn search(&self, query: &str, opts: &SearchOptions) -> anyhow::Result<Vec<SearchHit>> {
        if self.symbol_count == 0 {
            return Ok(Vec::new());
        }

        // ---- lexical + semantic stages ----
        // The two evidence providers are independent, and semantic
        // scoring's `embed_query` may be a blocking HTTP round trip
        // (`HttpEmbedder`) or a real model forward pass (`NativeEmbedder`),
        // so it runs on its own OS thread while BM25 runs here, on the
        // thread that owns the (non-`Sync`) store. Plain `std::thread::scope`
        // (no tokio task) because this must work identically from a fully
        // synchronous caller (the `oxide context`/`oxide search` CLI path
        // runs with no async runtime at all) and from inside MCP's
        // `spawn_blocking` closure alike. The vector scan itself needs the
        // store too, so it follows on this thread once the query vector is
        // back: a request pays max(lexical, embed_query) + scan, and the
        // scan is a bounded-memory streaming read (`semantic_top_k`).
        //
        // `catch_unwind` preserves the old contract that a panicking
        // evidence provider drops its own evidence instead of taking the
        // whole search down; on a spawned thread `join` gives that for free.
        let embedder = self.embedder;
        let want_semantic = opts.mode != SearchMode::LexicalOnly;
        let want_lexical = opts.mode != SearchMode::VectorOnly;
        let (lex_result, qv) = std::thread::scope(|scope| {
            let vec_handle = scope.spawn(|| -> Vec<f32> {
                if !want_semantic {
                    return Vec::new();
                }
                embedder.embed_query(query)
            });
            let lex = if want_lexical {
                std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                    crate::lexical::score(&self.prepare_lexical(query), 1.5, 0.75)
                }))
                .unwrap_or_default()
            } else {
                Default::default()
            };
            let qv = vec_handle.join().unwrap_or_default();
            (lex, qv)
        });
        let (lex_scores, lex_total_idf) = lex_result;
        let vec_ranked: Vec<(u64, f32)> = if want_semantic {
            self.semantic_top_k(&qv, FUSION_CANDIDATE_LIMIT)
        } else {
            Vec::new()
        };
        let lex_ranked: Vec<(u64, f32)> = top_k_by_score(
            lex_scores.iter().map(|(id, s)| (*id, s.0)).collect(),
            FUSION_CANDIDATE_LIMIT,
        );

        // ---- fuse ----
        let mut rrf: HashMap<u64, f32> = HashMap::new();
        let mut reasons: HashMap<u64, Vec<String>> = HashMap::new();
        let mut note = |ranked: &[(u64, f32)], why: &str, weight: f32| {
            for (rank, (id, score)) in ranked.iter().enumerate() {
                *rrf.entry(*id).or_insert(0.0) += weight / (FUSION_RRF_K + rank as f32 + 1.0);
                reasons
                    .entry(*id)
                    .or_default()
                    .push(format!("{why}={score:.3}"));
            }
        };

        match opts.mode {
            SearchMode::LexicalOnly => note(&lex_ranked, "lexical", 1.0),
            SearchMode::VectorOnly => note(&vec_ranked, "semantic", 1.0),
            SearchMode::Hybrid => {
                note(&lex_ranked, "lexical", FUSION_LEXICAL_WEIGHT);
                note(&vec_ranked, "semantic", FUSION_SEMANTIC_WEIGHT);
            }
        }

        // Only the fused candidates become `Symbol`s.
        let mut symbols: HashMap<u64, Symbol> = self.hydrate(rrf.keys().copied());

        // ---- optional term-coverage corroboration (experiment) ----
        // See docs/term-coverage-eval/README.md. `alpha` is 0.0 (a no-op,
        // byte-identical to pre-experiment scoring) unless
        // `$OXIDE_TERM_COVERAGE_ALPHA` is set — this never runs in shipped
        // Fast/Balanced/Quality behavior by default, independent of
        // `RetrievalMode` entirely. `VectorOnly` is excluded so the arm
        // `tests/benchmark_gate.rs` uses as the hybrid-vs-vector-only
        // control carries no lexical evidence at all.
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
        // for every other caller and for alpha=0. `lex_scores` is the full
        // BM25 map (every posting-matched doc, not just the fused top-K),
        // so a semantic-only candidate with a weak lexical score still
        // receives its coverage share exactly as before.
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
                    if symbols.get(id).map(|s| s.kind) == Some(SymbolKind::Module) {
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

        // Ranking happens on `(id, score)` pairs; a `SearchHit` (which owns
        // a `Symbol` clone) is only assembled for the `limit` entries that
        // are actually returned. Building hits for every fused candidate
        // first — up to 400 symbol clones, twice — was the largest
        // allocation in a search after hydration itself
        // (docs/retrieval-profile/README.md). The tie-break is the key id,
        // which equals `Symbol::id()` for every hydrated candidate: rows
        // are stored under that same FNV1a id and `hydrate` keys by it.
        let mut direct: Vec<(u64, f32)> = rrf
            .iter()
            .filter(|(id, _)| symbols.contains_key(id))
            .map(|(id, score)| (*id, *score))
            .collect();
        direct.sort_by(cmp_score_id);

        // ---- structural expansion ----
        // The only stage that needs the whole corpus (see `SymbolSnapshot`),
        // and it is reached only when the caller asked for expansion, the
        // mode allows it, and there is a strong lexical seed to expand from.
        //
        // Direct hits (lexical/semantic evidence) always outrank
        // expansion-only context and keep their PRE-expansion score, so
        // expansion supplements without reordering real matches: it only
        // appends reasons to direct hits, and contributes new candidates
        // (borrowed from the snapshot, cloned only if returned) ranked by
        // their own expansion score.
        let mut expanded: Vec<(u64, f32)> = Vec::new();
        let mut expansion_symbols: HashMap<u64, &Symbol> = HashMap::new();
        if opts.expand && opts.retrieval_mode != RetrievalMode::Fast && !direct.is_empty() {
            let max_lex = lex_scores.values().map(|s| s.0).fold(0.0f32, f32::max);
            let strong_ids: Vec<u64> = direct
                .iter()
                .filter(|(id, _)| {
                    reasons
                        .get(id)
                        .is_some_and(|rs| rs.iter().any(|r| r.starts_with("lexical")))
                })
                .filter(|(id, _)| {
                    lex_scores
                        .get(id)
                        .map(|(sc, _, _)| *sc >= max_lex * EXPANSION_STRONG_SEED_FRACTION)
                        .unwrap_or(false)
                        && max_lex > 0.0
                })
                .filter(|(id, _)| symbols.contains_key(id))
                .map(|(id, _)| *id)
                .take(3)
                .collect();
            // Seeds from a (lean) snapshot-backed hydrate must be complete
            // before `neighbors()` reads their imports/references.
            self.complete(
                symbols
                    .iter_mut()
                    .filter(|(id, _)| strong_ids.contains(id))
                    .map(|(_, s)| s),
            )?;
            let strong: Vec<&Symbol> = strong_ids.iter().filter_map(|id| symbols.get(id)).collect();
            if !strong.is_empty() {
                let snapshot = self.snapshot();
                let graph = self.graph_over(snapshot);
                let mut expansions: HashMap<u64, (f32, Vec<String>)> = HashMap::new();
                for seed in &strong {
                    let boost_base = rrf.get(&seed.id()).copied().unwrap_or(0.001);
                    for (rel, cand) in graph.neighbors(seed) {
                        let cand_id = cand.id();
                        if cand_id == seed.id() {
                            continue;
                        }
                        let e = expansions.entry(cand_id).or_insert((0.0, Vec::new()));
                        e.0 += boost_base * 0.5;
                        let why = format!("{}←{}", rel, seed.qualified_name);
                        if !e.1.contains(&why) {
                            e.1.push(why);
                        }
                        expansion_symbols.entry(cand_id).or_insert(cand);
                    }
                }
                for (id, (boost, whys)) in expansions {
                    reasons.entry(id).or_default().extend(whys);
                    if !symbols.contains_key(&id) {
                        expanded.push((id, boost));
                    }
                }
                expanded.sort_by(cmp_score_id);
            }
        }

        let mut hits: Vec<SearchHit> = direct
            .into_iter()
            .chain(expanded)
            .take(opts.limit)
            .filter_map(|(id, score)| {
                let symbol = match symbols.remove(&id) {
                    Some(s) => s,
                    None => (*expansion_symbols.get(&id)?).clone(),
                };
                Some(SearchHit {
                    symbol,
                    score,
                    reasons: reasons.remove(&id).unwrap_or_default(),
                    snippet: String::new(),
                })
            })
            .collect();
        // Expansion-only hits are snapshot symbols, and so are direct ones
        // when a supplied snapshot served `hydrate`.
        self.complete(hits.iter_mut().map(|h| &mut h.symbol))?;
        Ok(hits)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::embeddings::HashedEmbedder;
    use crate::relations::resolve_module;
    use crate::retrieval::test_support::{fixture_repo, json, lcg, seed_store, sym};
    use crate::storage::{IndexBackend, SqliteStore};
    use std::collections::HashSet;

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
            .replace_file("src/retry.py", 1, std::slice::from_ref(&s), &[], &[])
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
            .replace_file("src/retry.py", 1, &syms[..1], &[], &[])
            .unwrap();
        store
            .replace_file("src/auth.py", 1, &syms[1..], &[], &[])
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
            .replace_file(
                "src/http/backoff.py",
                1,
                std::slice::from_ref(&s1),
                &[],
                &[],
            )
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
            .replace_file(
                "src/net/client.py",
                1,
                std::slice::from_ref(&client),
                &[],
                &[],
            )
            .unwrap();
        store
            .replace_file(
                "src/net/retry.py",
                1,
                std::slice::from_ref(&policy),
                &[],
                &[],
            )
            .unwrap();
        store
            .replace_file(
                "tests/test_retry.py",
                1,
                std::slice::from_ref(&test),
                &[],
                &[],
            )
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
            .replace_file("src/retry.py", 1, &symbols, &[], &[])
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
                .replace_file(&s.file, 1, std::slice::from_ref(s), &[], &[])
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
                .replace_file(&s.file, 1, std::slice::from_ref(s), &[], &[])
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
                .replace_file(&s.file, 1, std::slice::from_ref(s), &[], &[])
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

    /// The streaming scan must produce bit-identical scores and the same
    /// ranking as the materialized `all_embeddings` + full sort it replaced,
    /// including its rules for skipping wrong-length vectors.
    #[test]
    fn streaming_semantic_scan_matches_materialized_scan_exactly() {
        let mut store = seed_store();
        let emb = HashedEmbedder::default();
        let mut seed = 99u64;
        let mut syms = Vec::new();
        for i in 0..600 {
            syms.push(sym(
                &format!("src/m{}.py", i % 40),
                &format!("f{i}"),
                SymbolKind::Function,
                &format!(
                    "def f{i}(): retry {} backoff {}",
                    lcg(&mut seed) % 50,
                    i % 9
                ),
                &[],
            ));
        }
        for i in 0..40 {
            let file = format!("src/m{i}.py");
            let in_file: Vec<Symbol> = syms.iter().filter(|s| s.file == file).cloned().collect();
            store.replace_file(&file, 1, &in_file, &[], &[]).unwrap();
        }
        for (i, s) in syms.iter().enumerate() {
            let mut v = emb.embed(&crate::embeddings::symbol_embed_text(s));
            // Every fifth row is a wrong-dimension vector that both paths
            // must skip; one is empty.
            if i % 5 == 0 {
                v.truncate(if i % 10 == 0 { 0 } else { v.len() / 2 });
            }
            store.put_embedding(s.id(), &v).unwrap();
        }
        let engine = RetrievalEngine::new(&store, &emb);
        for query in ["retry backoff", "f7", "payment schedule", "backoff 3"] {
            let qv = emb.embed_query(query);
            // Oracle: the pre-streaming implementation, verbatim.
            let all = store.all_embeddings().unwrap();
            let mut oracle: Vec<(u64, f32)> = Vec::new();
            for s in &syms {
                if let Some((_, v)) = all.get(&s.id()) {
                    if v.len() != qv.len() || v.is_empty() {
                        continue;
                    }
                    let dot: f32 = qv.iter().zip(v.iter()).map(|(a, b)| a * b).sum();
                    oracle.push((s.id(), dot));
                }
            }
            oracle.sort_by(cmp_score_id);
            for &k in &[1usize, 25, 200, 500] {
                let got = engine.semantic_top_k(&qv, k);
                let want: Vec<(u64, f32)> = oracle.iter().copied().take(k).collect();
                assert_eq!(got.len(), want.len(), "{query} k={k}");
                for (g, w) in got.iter().zip(want.iter()) {
                    assert_eq!(g.0, w.0, "{query} k={k}: id order");
                    assert_eq!(g.1.to_bits(), w.1.to_bits(), "{query} k={k}: score bits");
                }
            }
        }
    }

    /// Delegating store that counts whole-corpus loads, so the test below
    /// can assert candidate-first retrieval never performs one unless
    /// expansion genuinely needs it.
    struct CountingStore<'a> {
        inner: &'a SqliteStore,
        full_loads: std::cell::Cell<usize>,
        /// Ids requested by each `symbols_by_ids` call, in call order.
        by_ids: std::cell::RefCell<Vec<usize>>,
        /// The frozen pre-lean oracle: `all_symbols_lean` serves complete
        /// symbols, exactly what every corpus load returned before lean
        /// snapshots existed.
        complete_corpus: bool,
        /// Fail the embedding scan after this many rows (simulates an
        /// unreadable row mid-table).
        fail_scan_after: Option<usize>,
        fail_relations: bool,
    }

    impl<'a> CountingStore<'a> {
        fn new(inner: &'a SqliteStore) -> Self {
            Self {
                inner,
                full_loads: std::cell::Cell::new(0),
                by_ids: Default::default(),
                complete_corpus: false,
                fail_scan_after: None,
                fail_relations: false,
            }
        }
    }

    impl IndexBackend for CountingStore<'_> {
        fn get_meta(&self, key: &str) -> anyhow::Result<Option<String>> {
            self.inner.get_meta(key)
        }
        fn set_meta(&mut self, _: &str, _: &str) -> anyhow::Result<()> {
            unreachable!()
        }
        fn set_meta_all(&mut self, _: &str, _: &[(&str, &str)]) -> anyhow::Result<()> {
            unreachable!()
        }
        fn file_hashes(&self) -> anyhow::Result<HashMap<String, u64>> {
            self.inner.file_hashes()
        }
        fn replace_file(
            &mut self,
            _: &str,
            _: u64,
            _: &[Symbol],
            _: &[(u64, Vec<String>, Vec<String>)],
            _: &[crate::lexical::DocPostings],
        ) -> anyhow::Result<()> {
            unreachable!()
        }
        fn remove_files(&mut self, _: &[String]) -> anyhow::Result<()> {
            unreachable!()
        }
        fn put_file_lexical(
            &mut self,
            _: &str,
            _: u64,
            _: &[crate::lexical::DocPostings],
        ) -> anyhow::Result<bool> {
            unreachable!()
        }
        fn lexical_totals(&self) -> anyhow::Result<(usize, i64)> {
            self.inner.lexical_totals()
        }
        fn lexical_postings(&self, term: &str) -> anyhow::Result<Vec<(u64, u32, u32)>> {
            self.inner.lexical_postings(term)
        }
        fn all_symbols(&self) -> anyhow::Result<Vec<Symbol>> {
            self.full_loads.set(self.full_loads.get() + 1);
            self.inner.all_symbols()
        }
        fn all_symbols_lean(&self) -> anyhow::Result<Vec<Symbol>> {
            self.full_loads.set(self.full_loads.get() + 1);
            if self.complete_corpus {
                self.inner.all_symbols()
            } else {
                self.inner.all_symbols_lean()
            }
        }
        fn symbol_count(&self) -> anyhow::Result<usize> {
            self.inner.symbol_count()
        }
        fn symbols_by_ids(&self, ids: &[u64]) -> anyhow::Result<Vec<Symbol>> {
            self.by_ids.borrow_mut().push(ids.len());
            self.inner.symbols_by_ids(ids)
        }
        fn symbol_hash(&self, id: u64) -> anyhow::Result<Option<u64>> {
            self.inner.symbol_hash(id)
        }
        fn put_embedding(&mut self, _: u64, _: &[f32]) -> anyhow::Result<()> {
            unreachable!()
        }
        fn put_embeddings_batch(&mut self, _: &str, _: &[(u64, Vec<f32>)]) -> anyhow::Result<()> {
            unreachable!()
        }
        fn embedding_with_hash(&self, id: u64) -> anyhow::Result<Option<(u64, Vec<f32>)>> {
            self.inner.embedding_with_hash(id)
        }
        fn all_embeddings(&self) -> anyhow::Result<HashMap<u64, (u64, Vec<f32>)>> {
            self.full_loads.set(self.full_loads.get() + 1);
            self.inner.all_embeddings()
        }
        fn for_each_embedding(
            &self,
            visit: &mut dyn FnMut(u64, usize, &[u8]),
        ) -> anyhow::Result<()> {
            let Some(limit) = self.fail_scan_after else {
                return self.inner.for_each_embedding(visit);
            };
            let mut seen = 0usize;
            self.inner.for_each_embedding(&mut |id, dim, bytes| {
                if seen < limit {
                    visit(id, dim, bytes);
                }
                seen += 1;
            })?;
            anyhow::bail!("simulated unreadable embedding row after {limit} rows")
        }
        fn begin_embedding_migration(&mut self, _: &str) -> anyhow::Result<()> {
            unreachable!()
        }
        fn put_symbol_relations_batch(
            &mut self,
            _: &[(u64, Vec<String>, Vec<String>)],
        ) -> anyhow::Result<()> {
            unreachable!()
        }
        fn all_symbol_relations(&self) -> anyhow::Result<crate::storage::SymbolRelations> {
            if self.fail_relations {
                anyhow::bail!("simulated unreadable symbol_relations table");
            }
            self.inner.all_symbol_relations()
        }
    }

    /// A scan that fails part-way must drop *all* semantic evidence for
    /// the query (the search degrades to lexical-only, as it always did on
    /// a failed vector load), never rank from the rows it got through.
    #[test]
    fn a_failed_embedding_scan_yields_no_semantic_evidence_not_partial_evidence() {
        let mut store = seed_store();
        let emb = HashedEmbedder::default();
        let syms: Vec<Symbol> = (0..20)
            .map(|i| {
                sym(
                    "src/m.py",
                    &format!("retry_{i}"),
                    SymbolKind::Function,
                    &format!("def retry_{i}(): backoff"),
                    &[],
                )
            })
            .collect();
        store.replace_file("src/m.py", 1, &syms, &[], &[]).unwrap();
        for s in &syms {
            store
                .put_embedding(s.id(), &emb.embed(&crate::embeddings::symbol_embed_text(s)))
                .unwrap();
        }
        let mut failing = CountingStore::new(&store);
        failing.fail_scan_after = Some(5);
        let engine = RetrievalEngine::new(&failing, &emb);
        let opts = SearchOptions {
            limit: 20,
            mode: SearchMode::Hybrid,
            expand: false,
            retrieval_mode: RetrievalMode::default(),
        };
        let hits = engine.search("retry backoff", &opts).unwrap();
        assert!(!hits.is_empty(), "lexical evidence still serves the query");
        assert!(
            hits.iter()
                .all(|h| h.reasons.iter().all(|r| !r.starts_with("semantic"))),
            "partial scan must contribute nothing: {:?}",
            hits.iter().map(|h| &h.reasons).collect::<Vec<_>>()
        );
        let vec_only = SearchOptions {
            mode: SearchMode::VectorOnly,
            ..opts
        };
        assert!(engine
            .search("retry backoff", &vec_only)
            .unwrap()
            .is_empty());
    }

    /// `build_context` reported an unreadable relations table before the
    /// snapshot refactor; it must still do so rather than serving direct
    /// hits with the structural stage silently skipped.
    #[test]
    fn context_propagates_a_failed_relations_load() {
        let mut store = seed_store();
        let emb = HashedEmbedder::default();
        let s = sym(
            "src/m.py",
            "retry_policy",
            SymbolKind::Function,
            "def retry_policy():",
            &[],
        );
        store
            .replace_file("src/m.py", 1, std::slice::from_ref(&s), &[], &[])
            .unwrap();
        store
            .put_embedding(
                s.id(),
                &emb.embed(&crate::embeddings::symbol_embed_text(&s)),
            )
            .unwrap();
        let mut failing = CountingStore::new(&store);
        failing.fail_relations = true;
        let engine = RetrievalEngine::new(&failing, &emb);
        let result = crate::context::build_context_with(
            std::path::Path::new("/nonexistent"),
            &engine,
            "retry policy",
            &crate::context::ContextOptions::default(),
        );
        let err = result.err().unwrap();
        assert!(err.to_string().contains("symbol_relations"), "{err}");
        // Search-side expansion keeps its degrade-not-fail contract.
        let hits = engine
            .search("retry_policy", &SearchOptions::default())
            .unwrap();
        assert_eq!(hits[0].symbol.qualified_name, "retry_policy");
    }

    /// Candidate-first: on a persisted lexical index, a search without
    /// expansion (or in `Fast` mode) must never call `all_symbols` or
    /// `all_embeddings`; with expansion it loads the corpus at most once,
    /// and only after a strong seed exists. Results are identical to the
    /// eager engine either way.
    #[test]
    fn search_hydrates_only_candidates_unless_expansion_needs_the_corpus() {
        let tmp = tempfile::tempdir().unwrap();
        std::fs::create_dir_all(tmp.path().join("src")).unwrap();
        for i in 0..30 {
            std::fs::write(
                tmp.path().join(format!("src/m{i}.py")),
                format!("def retry_policy_{i}():\n    return {i}\n\ndef helper_{i}():\n    return retry_policy_{i}()\n"),
            )
            .unwrap();
        }
        let mut store = SqliteStore::open(&tmp.path().join(".oxide/index.db")).unwrap();
        let emb = HashedEmbedder::default();
        crate::index::update_index(tmp.path(), &mut store, &emb).unwrap();
        assert_eq!(
            store
                .get_meta(crate::storage::LEXICAL_INDEX_KEY)
                .unwrap()
                .as_deref(),
            Some("1"),
            "test needs the persisted lexical path"
        );
        let counting = CountingStore::new(&store);
        let eager = RetrievalEngine::new(&store, &emb);
        let lazy = RetrievalEngine::new(&counting, &emb);
        assert_eq!(
            counting.full_loads.get(),
            0,
            "construction must not load the corpus"
        );

        let same = |a: &[SearchHit], b: &[SearchHit]| {
            assert_eq!(a.len(), b.len());
            for (x, y) in a.iter().zip(b) {
                assert_eq!(x.symbol.id(), y.symbol.id());
                assert_eq!(x.score.to_bits(), y.score.to_bits());
                assert_eq!(x.reasons, y.reasons);
            }
        };
        for mode in [
            SearchMode::LexicalOnly,
            SearchMode::VectorOnly,
            SearchMode::Hybrid,
        ] {
            let opts = SearchOptions {
                limit: 10,
                mode,
                expand: false,
                retrieval_mode: RetrievalMode::default(),
            };
            same(
                &eager.search("retry policy 7", &opts).unwrap(),
                &lazy.search("retry policy 7", &opts).unwrap(),
            );
            let fast = SearchOptions {
                expand: true,
                retrieval_mode: RetrievalMode::Fast,
                ..opts
            };
            same(
                &eager.search("retry policy 7", &fast).unwrap(),
                &lazy.search("retry policy 7", &fast).unwrap(),
            );
        }
        assert_eq!(
            counting.full_loads.get(),
            0,
            "no expansion ⇒ no corpus load"
        );

        let expand = SearchOptions {
            limit: 10,
            mode: SearchMode::Hybrid,
            expand: true,
            retrieval_mode: RetrievalMode::default(),
        };
        counting.by_ids.borrow_mut().clear();
        let expanded = lazy.search("retry policy 7", &expand).unwrap();
        same(&eager.search("retry policy 7", &expand).unwrap(), &expanded);
        assert_eq!(
            counting.full_loads.get(),
            1,
            "expansion loads the corpus exactly once"
        );
        // The lean corpus costs at most one bounded completion read on top
        // of candidate hydration, never a re-hydration of every candidate:
        // hydrate (before the corpus exists) + completing the returned
        // expansion-only hits.
        let reads = counting.by_ids.borrow().clone();
        assert!(reads.len() <= 2, "{reads:?}");
        assert!(reads[1..].iter().all(|&n| n <= expand.limit), "{reads:?}");
        lazy.search("helper 3", &expand).unwrap();
        assert_eq!(counting.full_loads.get(), 1, "…and the engine keeps it");
    }

    // ---- lean snapshot: mechanism and frozen-oracle parity ----------------

    /// Every request surface that reads the corpus snapshot serializes
    /// byte-identically over the lean snapshot and over the frozen
    /// pre-lean oracle (a store whose corpus loads are complete, i.e.
    /// `main` before lean snapshots): expanded search in both expanding
    /// modes, `context` default / `--git` / `--blast-radius`, `review`,
    /// the `oxide mcp` cached-snapshot shape, and the in-memory BM25
    /// fallback.
    #[test]
    fn lean_snapshot_output_matches_the_complete_corpus_oracle() {
        // Holds `TERM_COVERAGE_ENV_LOCK` for the same reason as the
        // concurrency test above: each comparison runs search/context twice,
        // and a term-coverage test setting `OXIDE_TERM_COVERAGE_ALPHA`
        // between the two runs adds its bonus to one side only (reproduced
        // on a 2-core runner: the lean side's scores carried the bonus).
        let _guard = TERM_COVERAGE_ENV_LOCK
            .lock()
            .unwrap_or_else(|e| e.into_inner());
        let emb = HashedEmbedder::default();
        let cases: [(&str, &str, &[&str]); 2] = [
            (
                "py_repo",
                "oxidepy/retry.py",
                &[
                    "retry with exponential backoff",
                    "refresh auth token",
                    "http client request",
                    "cache",
                ],
            ),
            (
                "ts_repo",
                "src/net/retry.ts",
                &[
                    "retry policy backoff",
                    "versioned store get",
                    "auth service login",
                    "button click",
                ],
            ),
        ];
        for (fixture, changed, queries) in cases {
            let repo = fixture_repo(fixture, changed);
            let root = repo.path();
            let mut store = SqliteStore::open(&root.join(".oxide/index.db")).unwrap();
            crate::index::update_index(root, &mut store, &emb).unwrap();
            let mut seen_expansion = false;
            let mut seen_test = false;
            for fallback in [false, true] {
                if fallback {
                    store
                        .set_meta(crate::storage::LEXICAL_INDEX_KEY, "stale")
                        .unwrap();
                }
                let lean = CountingStore::new(&store);
                let mut oracle = CountingStore::new(&store);
                oracle.complete_corpus = true;
                let lean: &dyn IndexBackend = &lean;
                let oracle: &dyn IndexBackend = &oracle;
                assert_eq!(lexical_persisted(lean), !fallback);

                // The shape each path runs on: lean unless BM25 falls back.
                let lean_snap = SymbolSnapshot::load(lean).unwrap();
                let full_snap = SymbolSnapshot::load(oracle).unwrap();
                assert_eq!(
                    lean_snap.symbols.iter().all(Symbol::is_complete),
                    fallback,
                    "{fixture}: lean iff lexical is persisted"
                );
                assert!(full_snap.symbols.iter().all(Symbol::is_complete));
                let (lean_idx, full_idx) = (
                    RelationIndex::build(&lean_snap.symbols),
                    RelationIndex::build(&full_snap.symbols),
                );

                for q in queries {
                    for retrieval_mode in [RetrievalMode::Balanced, RetrievalMode::Quality] {
                        let opts = SearchOptions {
                            limit: 10,
                            mode: SearchMode::Hybrid,
                            expand: true,
                            retrieval_mode,
                        };
                        let a = RetrievalEngine::new(lean, &emb).search(q, &opts).unwrap();
                        let b = RetrievalEngine::new(oracle, &emb).search(q, &opts).unwrap();
                        assert_eq!(json(&a), json(&b), "{fixture} search {q:?}");
                        seen_expansion |=
                            a.iter().any(|h| h.reasons.iter().any(|r| r.contains('←')));
                        seen_test |= a
                            .iter()
                            .any(|h| h.reasons.iter().any(|r| r.starts_with("test←")));
                        // `oxide mcp`: a cached snapshot + index per generation.
                        let a = RetrievalEngine::with_snapshot_and_index(
                            lean, &emb, &lean_snap, &lean_idx,
                        )
                        .search(q, &opts)
                        .unwrap();
                        let b = RetrievalEngine::with_snapshot_and_index(
                            oracle, &emb, &full_snap, &full_idx,
                        )
                        .search(q, &opts)
                        .unwrap();
                        assert_eq!(json(&a), json(&b), "{fixture} mcp search {q:?}");
                    }
                    for (git_on, blast) in
                        [(false, false), (true, false), (false, true), (true, true)]
                    {
                        let opts = crate::context::ContextOptions {
                            git: git_on,
                            blast_radius: blast,
                            ..Default::default()
                        };
                        let ctx = |e: &RetrievalEngine<'_>| {
                            json(&crate::context::build_context_with(root, e, q, &opts).unwrap())
                        };
                        let a = ctx(&RetrievalEngine::new(lean, &emb));
                        assert_eq!(
                            a,
                            ctx(&RetrievalEngine::new(oracle, &emb)),
                            "{fixture} context {q:?} git={git_on} blast={blast}"
                        );
                        seen_test |= a.contains("\"test←");
                        assert_eq!(
                            ctx(&RetrievalEngine::with_snapshot_and_index(
                                lean, &emb, &lean_snap, &lean_idx
                            )),
                            ctx(&RetrievalEngine::with_snapshot_and_index(
                                oracle, &emb, &full_snap, &full_idx
                            )),
                            "{fixture} mcp context {q:?} git={git_on} blast={blast}"
                        );
                    }
                }
                let a = crate::review::build_review_context(root, lean, &emb, "").unwrap();
                let b = crate::review::build_review_context(root, oracle, &emb, "").unwrap();
                assert!(
                    !a.changed_symbols.is_empty() && !a.related.is_empty(),
                    "{fixture}: review must have seeds"
                );
                assert_eq!(json(&a), json(&b), "{fixture} review");
            }
            assert!(seen_expansion, "{fixture}: queries must expand");
            assert!(seen_test, "{fixture}: queries must reach related_tests");
        }
    }
}
