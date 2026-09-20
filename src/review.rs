//! Review context assembly: git changes become retrieval seeds; the output is
//! a compact, explainable context pack for a downstream model or human.

use crate::embeddings::EmbeddingProvider;
use crate::gitctx::{self, ChangedSymbol, CoChangeEntry};
use crate::gitutil::CommitMeta;
use crate::retrieval::{RetrievalEngine, RetrievalMode, SearchHit, SearchMode, SearchOptions};
use crate::storage::IndexBackend;
use serde::Serialize;
use std::collections::HashMap;
use std::path::Path;

#[derive(Debug, Serialize)]
pub struct ReviewContext {
    pub range: String,
    pub changed_files: Vec<String>,
    pub changed_symbols: Vec<ChangedSymbol>,
    pub related: Vec<SearchHit>,
    /// Commits within `range`, newest first — bounded, see `gitctx.rs`.
    pub recent_commits: Vec<CommitMeta>,
    /// Bounded historical co-change evidence for the changed files.
    pub co_change: Vec<CoChangeEntry>,
}

/// Build review context for `range` (see [`crate::gitutil::diff_text`]).
///
/// Seeds = symbols overlapping added lines in each changed file (plus,
/// unconditionally since review is inherently diff-scoped, recent commits in
/// `range` and bounded co-change history for the changed files — see
/// `gitctx::build_git_context`). Related context = structural neighbors of
/// seeds (definitions used, callers by reference, tests) plus a semantic
/// top-up from the union of seed signatures.
pub fn build_review_context(
    repo_root: &Path,
    store: &dyn IndexBackend,
    embedder: &dyn EmbeddingProvider,
    range: &str,
) -> anyhow::Result<ReviewContext> {
    let engine = RetrievalEngine::new(store, embedder);
    // Routed through the engine's lazy, cached corpus snapshot rather than a
    // direct `store.all_symbols()` call — the request-path invariant
    // `context.rs` already follows (AGENTS.md).
    let snapshot = engine.snapshot_with_relations()?;
    let symbols = &snapshot.symbols;
    let graph = engine.relation_graph()?;

    let git_ctx = gitctx::build_git_context(repo_root, symbols, range)?;
    let changed_symbols = git_ctx.changed_symbols;
    let seen_seeds: Vec<u64> = changed_symbols.iter().map(|c| c.symbol.id()).collect();

    // Structural expansion around the seeds.
    let mut related_ids: HashMap<u64, (f32, Vec<String>)> = HashMap::new();
    for s in symbols.iter().filter(|s| seen_seeds.contains(&s.id())) {
        for (rel, n) in graph.neighbors(s) {
            if seen_seeds.contains(&n.id()) {
                continue;
            }
            let e = related_ids.entry(n.id()).or_insert((0.0, Vec::new()));
            e.0 += 1.0;
            let why = format!("{}←{}", rel, s.qualified_name);
            if !e.1.contains(&why) {
                e.1.push(why);
            }
        }
    }

    // Semantic top-up seeded from changed signatures so purely semantic
    // relatives (no name/reference link) can still surface.
    let query = changed_symbols
        .iter()
        .map(|c| format!("{} {}", c.symbol.qualified_name, c.symbol.signature))
        .take(8)
        .collect::<Vec<_>>()
        .join(" ");
    if !query.trim().is_empty() {
        let opts = SearchOptions {
            limit: 12,
            mode: SearchMode::VectorOnly,
            expand: false,
            retrieval_mode: RetrievalMode::default(),
        };
        if let Ok(hits) = engine.search(&query, &opts) {
            for h in hits {
                if !seen_seeds.contains(&h.symbol.id()) {
                    let e = related_ids
                        .entry(h.symbol.id())
                        .or_insert((0.0, Vec::new()));
                    e.0 += h.score;
                    let why = "semantic-neighbor".to_string();
                    if !e.1.contains(&why) {
                        e.1.push(why);
                    }
                    if !h.reasons.is_empty() {
                        e.1.push(h.reasons[0].clone());
                    }
                }
            }
        }
    }

    // Score-descending with the same id tie-break every other ranked
    // surface uses (`retrieval::cmp_score_id`): structural scores are
    // exact small integers (1.0 per relation), so ties are the norm, and
    // `related_ids` is a `HashMap` whose iteration order changes per
    // process — without the tie-break the same diff produced a different
    // `related` order (and, at the `truncate(15)` boundary, a different
    // set) on every run. `snapshot.get` is the by-id index; the linear
    // `symbols.iter().find(..)` it replaces hashed every symbol's id once
    // per related item.
    let mut related: Vec<(u64, f32, Vec<String>)> = related_ids
        .into_iter()
        .map(|(id, (score, reasons))| (id, score, reasons))
        .collect();
    related.sort_by(|a, b| {
        b.1.partial_cmp(&a.1)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then_with(|| a.0.cmp(&b.0))
    });
    let related: Vec<SearchHit> = related
        .into_iter()
        .filter_map(|(id, score, reasons)| {
            Some(SearchHit {
                symbol: snapshot.get(id)?.clone(),
                score,
                reasons,
                snippet: String::new(),
            })
        })
        .take(15)
        .collect();

    Ok(ReviewContext {
        range: git_ctx.evidence.range,
        changed_files: git_ctx.evidence.changed_files,
        changed_symbols,
        related,
        recent_commits: git_ctx.evidence.recent_commits,
        co_change: git_ctx.evidence.co_change,
    })
}
