//! Review context assembly: git changes become retrieval seeds; the output is
//! a compact, explainable context pack for a downstream model or human.

use crate::embeddings::EmbeddingProvider;
use crate::gitutil::diff_files;
use crate::index::IndexBackend;
use crate::retrieval::{
    RelationGraph, RetrievalEngine, RetrievalMode, SearchHit, SearchMode, SearchOptions,
};
use crate::symbols::Symbol;
use serde::Serialize;
use std::collections::HashMap;
use std::path::Path;

#[derive(Debug, Serialize)]
pub struct ReviewContext {
    pub range: String,
    pub changed_files: Vec<String>,
    pub changed_symbols: Vec<ChangedSymbol>,
    pub related: Vec<SearchHit>,
}

#[derive(Debug, Serialize)]
pub struct ChangedSymbol {
    #[serde(flatten)]
    pub symbol: Symbol,
    pub added_lines: u32,
    pub reason: String,
}

/// Build review context for `range` (see [`diff_files`]).
///
/// Seeds = symbols overlapping added lines in each changed file. Related
/// context = structural neighbors of seeds (definitions used, callers by
/// reference, tests) plus a semantic top-up from the union of seed signatures.
pub fn build_review_context(
    repo_root: &Path,
    store: &dyn IndexBackend,
    embedder: &dyn EmbeddingProvider,
    range: &str,
) -> anyhow::Result<ReviewContext> {
    let deltas = diff_files(repo_root, range)?;
    let symbols = store.all_symbols()?;
    let graph = RelationGraph::build(&symbols);

    let mut changed_symbols: Vec<ChangedSymbol> = Vec::new();
    let mut seen_seeds: Vec<u64> = Vec::new();
    for d in &deltas {
        for s in symbols
            .iter()
            .filter(|s| s.file == d.file && s.kind != crate::symbols::SymbolKind::Module)
        {
            if let Some(overlap) = d
                .added
                .iter()
                .map(|(a, b)| overlap_len(*a, *b, s.start_line, s.end_line))
                .max()
            {
                if overlap > 0 {
                    seen_seeds.push(s.id());
                    changed_symbols.push(ChangedSymbol {
                        symbol: s.clone(),
                        added_lines: overlap,
                        reason: format!("changed in diff (+{} lines)", overlap),
                    });
                }
            }
        }
    }

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
    let engine = RetrievalEngine::new(store, embedder);
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

    let mut related: Vec<SearchHit> = related_ids
        .into_iter()
        .filter_map(|(id, (score, reasons))| {
            let s = symbols.iter().find(|s| s.id() == id)?;
            Some(SearchHit {
                symbol: s.clone(),
                score,
                reasons,
                snippet: String::new(),
            })
        })
        .collect();
    related.sort_by(|a, b| {
        b.score
            .partial_cmp(&a.score)
            .unwrap_or(std::cmp::Ordering::Equal)
    });
    related.truncate(15);

    Ok(ReviewContext {
        range: if range.is_empty() {
            "HEAD".into()
        } else {
            range.into()
        },
        changed_files: deltas.iter().map(|d| d.file.clone()).collect(),
        changed_symbols,
        related,
    })
}

/// Overlap length of two inclusive line ranges.
fn overlap_len(a1: u32, a2: u32, b1: u32, b2: u32) -> u32 {
    let lo = a1.max(b1);
    let hi = a2.min(b2);
    if hi >= lo {
        hi - lo + 1
    } else {
        0
    }
}
