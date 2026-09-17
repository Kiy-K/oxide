//! Collects optional structural, Git, and blast-radius evidence in a fixed
//! order. Git is the only blocking source left, so a runtime and worker task
//! add overhead without overlapping useful work.

use crate::blast_radius;
use crate::config::{
    BLAST_RADIUS_CONTEXT_ITEMS, BLAST_RADIUS_MAX_SEEDS, BLAST_RADIUS_SCORE_FRACTION,
    GIT_CHANGED_CONTEXT_ITEMS, GIT_CHANGED_SCORE_FRACTION, GIT_COCHANGE_SCORE_FRACTION,
    GIT_COCHANGE_SYMBOLS_PER_FILE, GIT_NEIGHBOR_HITS_PER_CHANGED, GIT_NEIGHBOR_SCORE_FRACTION,
};
use crate::evidence::contract::{DegradeReason, Degraded, EvidenceCandidate, EvidenceSource};
use crate::evidence::scope::scope_files_from_seeds;
use crate::gitctx;
use crate::relations::RelationGraph;
use crate::retrieval::SearchHit;
use crate::symbols::{Symbol, SymbolKind};
use std::collections::HashMap;
use std::path::Path;
use std::time::Instant;

pub struct CollectInput<'a> {
    pub root: &'a Path,
    pub symbols: &'a [Symbol],
    pub graph: &'a RelationGraph<'a>,
    pub seeds: &'a [SearchHit],
    pub structural_max_seeds: usize,
    pub structural_max_files: usize,
    pub blast_radius: bool,
    pub git: bool,
}

pub struct CollectOutput {
    pub candidates: Vec<EvidenceCandidate>,
    pub degraded: Vec<Degraded>,
    pub git_evidence: Option<crate::gitctx::GitEvidence>,
}

pub struct EvidenceCoordinator;

impl EvidenceCoordinator {
    pub fn collect(input: CollectInput<'_>) -> CollectOutput {
        let CollectInput {
            root,
            symbols,
            graph,
            seeds,
            structural_max_seeds,
            structural_max_files,
            blast_radius,
            git,
        } = input;

        let mut degraded = Vec::new();
        let structural_out =
            structural_evidence(graph, seeds, structural_max_seeds, structural_max_files);
        let blast_out = if blast_radius {
            blast_radius_evidence(graph, seeds)
        } else {
            Vec::new()
        };
        let git_result = if git {
            let start = Instant::now();
            match gitctx::build_git_context(root, symbols, "") {
                Ok(context) => Some(context),
                Err(error) => {
                    degraded.push(Degraded {
                        source: EvidenceSource::Git,
                        reason: DegradeReason::ProtocolError {
                            detail: error.to_string(),
                        },
                        elapsed: start.elapsed(),
                    });
                    None
                }
            }
        } else {
            None
        };

        // ---- fixed-order fold: Structural → Git → BlastRadius ----
        let mut candidates = Vec::new();
        candidates.extend(structural_out);

        let mut git_evidence = None;
        if let Some(ctx) = git_result {
            let top_seed_score = seeds.first().map(|h| h.score).unwrap_or(0.0);
            let git_scope_files = scope_files_from_seeds(seeds, usize::MAX);
            candidates.extend(git_graph_enrichment(
                graph,
                &ctx,
                &git_scope_files,
                symbols,
                top_seed_score,
            ));
            git_evidence = Some(ctx.evidence);
        }
        candidates.extend(blast_out);

        CollectOutput {
            candidates,
            degraded,
            git_evidence,
        }
    }
}

fn structural_evidence(
    graph: &RelationGraph<'_>,
    seeds: &[SearchHit],
    max_seeds: usize,
    max_files: usize,
) -> Vec<EvidenceCandidate> {
    const STRUCTURAL_CALLER_HITS_PER_SEED: usize = 2;
    let scope_files = scope_files_from_seeds(seeds, max_files);
    let mut out = Vec::new();
    for seed in seeds.iter().take(max_seeds) {
        let callers = graph.callers_of(&seed.symbol.name);
        let scoped = callers
            .into_iter()
            .filter(|c| scope_files.contains(&c.file))
            .filter(|c| c.id() != seed.symbol.id())
            .take(STRUCTURAL_CALLER_HITS_PER_SEED);
        for caller in scoped {
            out.push(EvidenceCandidate {
                symbol: caller.clone(),
                score: seed.score * 0.4,
                reasons: vec![format!("ast-grep-caller←{}", seed.symbol.qualified_name)],
            });
        }
    }
    out
}

fn blast_radius_evidence(graph: &RelationGraph<'_>, seeds: &[SearchHit]) -> Vec<EvidenceCandidate> {
    let anchors: Vec<&Symbol> = seeds
        .iter()
        .take(BLAST_RADIUS_MAX_SEEDS)
        .map(|h| &h.symbol)
        .collect();
    let by_qname: HashMap<&str, f32> = seeds
        .iter()
        .map(|h| (h.symbol.qualified_name.as_str(), h.score))
        .collect();
    blast_radius::compute(graph, &anchors, true)
        .into_iter()
        .take(BLAST_RADIUS_CONTEXT_ITEMS)
        .map(|(item, sym)| {
            let seed_score = by_qname.get(item.via.as_str()).copied().unwrap_or(0.0);
            EvidenceCandidate {
                symbol: sym.clone(),
                score: seed_score * BLAST_RADIUS_SCORE_FRACTION,
                reasons: vec![format!("blast-radius:{}←{}", item.relation, item.via)],
            }
        })
        .collect()
}

fn git_graph_enrichment(
    graph: &RelationGraph<'_>,
    ctx: &gitctx::GitContext,
    git_scope_files: &[String],
    symbols: &[Symbol],
    top_seed_score: f32,
) -> Vec<EvidenceCandidate> {
    let mut out = Vec::new();
    for cs in ctx.changed_symbols.iter().take(GIT_CHANGED_CONTEXT_ITEMS) {
        out.push(EvidenceCandidate {
            symbol: cs.symbol.clone(),
            score: top_seed_score * GIT_CHANGED_SCORE_FRACTION,
            reasons: vec![format!(
                "git-changed(+{})←{}",
                cs.added_lines, cs.symbol.file
            )],
        });
        let mut hits = 0usize;
        for (rel, n) in graph.neighbors(&cs.symbol) {
            if hits >= GIT_NEIGHBOR_HITS_PER_CHANGED {
                break;
            }
            if rel != "test" && rel != "uses" {
                continue;
            }
            hits += 1;
            out.push(EvidenceCandidate {
                symbol: n.clone(),
                score: top_seed_score * GIT_NEIGHBOR_SCORE_FRACTION,
                reasons: vec![format!(
                    "git-caller-of-changed←{}",
                    cs.symbol.qualified_name
                )],
            });
        }
        for caller in graph
            .callers_of(&cs.symbol.name)
            .into_iter()
            .filter(|c| git_scope_files.iter().any(|f| f == &c.file))
            .filter(|c| c.id() != cs.symbol.id())
            .take(GIT_NEIGHBOR_HITS_PER_CHANGED - hits.min(GIT_NEIGHBOR_HITS_PER_CHANGED))
        {
            out.push(EvidenceCandidate {
                symbol: caller.clone(),
                score: top_seed_score * GIT_NEIGHBOR_SCORE_FRACTION,
                reasons: vec![format!(
                    "git-caller-of-changed←{}",
                    cs.symbol.qualified_name
                )],
            });
        }
    }
    for entry in &ctx.evidence.co_change {
        for s in symbols
            .iter()
            .filter(|s| s.file == entry.co_changed_with && s.kind != SymbolKind::Module)
            .take(GIT_COCHANGE_SYMBOLS_PER_FILE)
        {
            out.push(EvidenceCandidate {
                symbol: s.clone(),
                score: top_seed_score * GIT_COCHANGE_SCORE_FRACTION * entry.strength.min(1.0),
                reasons: vec![format!(
                    "git-cochange({} commits)←{}",
                    entry.count, entry.file
                )],
            });
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::retrieval::SearchHit;
    use crate::symbols::{Language, SymbolKind};

    fn sym(file: &str, qname: &str, kind: SymbolKind, calls: Vec<&str>) -> Symbol {
        let name = qname.rsplit('.').next().unwrap().to_string();
        Symbol {
            qualified_name: qname.into(),
            name,
            kind,
            language: Language::Python,
            file: file.into(),
            start_line: 1,
            end_line: 2,
            content_hash: 0,
            signature: String::new(),
            imports: vec![],
            exported: true,
            parent: None,
            references: vec![],
            calls: calls.into_iter().map(String::from).collect(),
            bases: Vec::new(),
        }
    }

    fn hit(symbol: Symbol, score: f32) -> SearchHit {
        SearchHit {
            symbol,
            score,
            reasons: vec![],
            snippet: String::new(),
        }
    }

    #[test]
    fn structural_evidence_matches_pre_refactor_reason_tag_and_score_fraction() {
        let seed_sym = sym("a.py", "foo", SymbolKind::Function, vec![]);
        let caller_sym = sym("a.py", "caller", SymbolKind::Function, vec!["foo"]);
        let symbols = vec![seed_sym.clone(), caller_sym.clone()];
        let graph = RelationGraph::build(&symbols);
        let seeds = vec![hit(seed_sym.clone(), 2.0)];

        let out = structural_evidence(&graph, &seeds, 3, 3);
        assert_eq!(out.len(), 1, "{out:?}");
        assert_eq!(out[0].symbol.qualified_name, "caller");
        assert_eq!(out[0].score, 2.0 * 0.4);
        assert_eq!(out[0].reasons, vec!["ast-grep-caller←foo".to_string()]);
    }

    #[test]
    fn blast_radius_evidence_matches_pre_refactor_score_fraction() {
        let seed_sym = sym("a.py", "foo", SymbolKind::Function, vec![]);
        let caller_sym = sym("a.py", "caller", SymbolKind::Function, vec!["foo"]);
        let symbols = vec![seed_sym.clone(), caller_sym.clone()];
        let graph = RelationGraph::build(&symbols);
        let seeds = vec![hit(seed_sym.clone(), 2.0)];

        let out = blast_radius_evidence(&graph, &seeds);
        assert!(!out.is_empty(), "expected at least one blast-radius item");
        for c in &out {
            assert_eq!(c.score, 2.0 * BLAST_RADIUS_SCORE_FRACTION);
        }
    }
}
