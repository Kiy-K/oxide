//! Orchestrates the four opt-in evidence sources concurrently:
//! - Git and LSP *I/O* run as real Tokio tasks (`tokio::spawn`, not just
//!   `spawn_blocking` directly — see below), started before anything else so
//!   the runtime's worker pool picks them up immediately. Both need only
//!   owned/`Send` data: Git needs `Arc<[Symbol]>` + the repo root; LSP needs
//!   the same `Arc<[Symbol]>` plus full ownership of the `LspClient` (moved
//!   in, moved back out via the task's return value — `LspClient`/
//!   `Transport` are `Send`, only the *borrow* a caller normally holds
//!   isn't `'static`). Each of `git_io`/`lsp_io` does its actual blocking
//!   work via an inner `spawn_blocking`, so this is two hops onto Tokio's
//!   pools (worker → blocking), not one.
//! - structural (AST-precise callers) and blast-radius run **synchronously,
//!   on the very thread that called `block_on`**, immediately after
//!   spawning the two tasks above — never inside a spawned task or a
//!   scoped OS thread. `RelationGraph<'a>` uses `std::cell::OnceCell`
//!   (deliberately, matching this codebase's other thread-confined lazy
//!   caches) for its `callers_of_index`/`implementors_of_index`, which
//!   makes `RelationGraph: !Sync` and therefore `&RelationGraph: !Send` —
//!   it cannot cross into *any* spawned thread, scoped or not, regardless
//!   of whether a real race would occur. This is why the design differs
//!   from the original sketch (a `std::thread::scope` for these two): that
//!   sketch does not compile, since `std::thread::Scope::spawn` requires
//!   `Send` captures. Running them synchronously on the `block_on` thread
//!   is still genuinely concurrent with the two spawned I/O tasks, which
//!   the multi-thread runtime is already executing on other OS threads by
//!   the time this code runs — it just never needs `graph` to leave its
//!   thread to get that concurrency.
//! - Git's `graph.neighbors`/`callers_of` enrichment (raw `GitContext` →
//!   scored candidates) is not part of the async task either, for the same
//!   reason — it runs synchronously afterward, once both spawned tasks have
//!   been awaited.
//!
//! All four sources fold into the output in one fixed order
//! (Structural → Git → LSP → BlastRadius) regardless of completion order —
//! see `tests/evidence_coordinator_determinism.rs`.

use crate::blast_radius;
use crate::config::{
    BLAST_RADIUS_CONTEXT_ITEMS, BLAST_RADIUS_MAX_SEEDS, BLAST_RADIUS_SCORE_FRACTION,
    GIT_CHANGED_CONTEXT_ITEMS, GIT_CHANGED_SCORE_FRACTION, GIT_COCHANGE_SCORE_FRACTION,
    GIT_COCHANGE_SYMBOLS_PER_FILE, GIT_NEIGHBOR_HITS_PER_CHANGED, GIT_NEIGHBOR_SCORE_FRACTION,
    LSP_MAX_SEEDS, LSP_PER_SEED_ITEMS, LSP_SCORE_FRACTION,
};
use crate::evidence::contract::{
    DegradeReason, Degraded, EvidenceCandidate, EvidenceSource, Outcome,
};
use crate::evidence::runtime::runtime;
use crate::evidence::scope::scope_files_from_seeds;
use crate::gitctx;
use crate::lsp::{enrich_seeds, LspClient};
use crate::relations::RelationGraph;
use crate::retrieval::SearchHit;
use crate::symbols::{Language, Symbol, SymbolKind};
use std::collections::HashMap;
use std::path::Path;
use std::sync::Arc;
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
    pub lsp: bool,
    /// `Some` only on the MCP `ProcessCache` path (Task 9); moved in and
    /// handed back in the output so the caller restores it into the cache
    /// slot regardless of whether this call used it.
    pub lsp_client: Option<LspClient>,
}

pub struct CollectOutput {
    pub candidates: Vec<EvidenceCandidate>,
    pub degraded: Vec<Degraded>,
    pub git_evidence: Option<crate::gitctx::GitEvidence>,
    pub lsp_client: Option<LspClient>,
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
            lsp,
            lsp_client,
        } = input;

        let mut degraded = Vec::new();
        let symbols_arc: Arc<[Symbol]> = Arc::from(symbols);

        let py_seeds_owned: Vec<Symbol> = seeds
            .iter()
            .filter(|h| h.symbol.language == Language::Python)
            .take(LSP_MAX_SEEDS)
            .map(|h| h.symbol.clone())
            .collect();
        let py_seed_scores: Vec<f32> = seeds
            .iter()
            .filter(|h| h.symbol.language == Language::Python)
            .take(LSP_MAX_SEEDS)
            .map(|h| h.score)
            .collect();
        let lsp_scope_files = scope_files_from_seeds(seeds, usize::MAX);

        let mut structural_out = Vec::new();
        let mut blast_out = Vec::new();
        let mut git_result: Option<gitctx::GitContext> = None;
        let mut git_degraded = None;
        let mut returned_lsp_client: Option<LspClient> = None;
        let mut lsp_out: Vec<EvidenceCandidate> = Vec::new();
        let mut lsp_degraded = None;

        let rt = runtime();
        rt.block_on(async {
            // Spawn both I/O tasks first so the runtime's worker pool picks
            // them up immediately — tokio::spawn (not just constructing the
            // future) is what actually starts them running in the
            // background; futures are lazy and do nothing until polled.
            // Their futures only capture owned/Arc'd data (never `graph`),
            // so they satisfy tokio::spawn's Send + 'static bound.
            let git_handle =
                git.then(|| tokio::spawn(git_io(root.to_path_buf(), symbols_arc.clone())));
            let lsp_handle = if lsp {
                lsp_client.map(|client| {
                    tokio::spawn(lsp_io(
                        client,
                        root.to_path_buf(),
                        symbols_arc.clone(),
                        py_seeds_owned,
                        py_seed_scores,
                        lsp_scope_files,
                    ))
                })
            } else {
                None
            };

            // Runs synchronously on this thread, concurrently with the two
            // background tasks above — see the module doc for why this
            // can't be a spawned task/thread itself (RelationGraph is
            // !Sync by design).
            structural_out =
                structural_evidence(graph, seeds, structural_max_seeds, structural_max_files);
            if blast_radius {
                blast_out = blast_radius_evidence(graph, seeds);
            }

            if let Some(handle) = git_handle {
                let start = Instant::now();
                match handle.await {
                    Ok(Outcome::Ready(ctx)) => git_result = Some(ctx),
                    Ok(Outcome::Degraded(d)) => git_degraded = Some(d),
                    Err(join_err) => {
                        git_degraded = Some(Degraded {
                            source: EvidenceSource::Git,
                            reason: DegradeReason::Unavailable {
                                detail: join_err.to_string(),
                            },
                            elapsed: start.elapsed(),
                        })
                    }
                }
            }
            if let Some(handle) = lsp_handle {
                let start = Instant::now();
                match handle.await {
                    Ok((client, Outcome::Ready(c))) => {
                        returned_lsp_client = client;
                        lsp_out = c;
                    }
                    Ok((client, Outcome::Degraded(d))) => {
                        returned_lsp_client = client;
                        lsp_degraded = Some(d);
                    }
                    Err(join_err) => {
                        lsp_degraded = Some(Degraded {
                            source: EvidenceSource::Lsp,
                            reason: DegradeReason::Unavailable {
                                detail: join_err.to_string(),
                            },
                            elapsed: start.elapsed(),
                        })
                    }
                }
            }
        });

        // ---- fixed-order fold: Structural → Git → LSP → BlastRadius ----
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
        if let Some(d) = git_degraded {
            degraded.push(d);
        }

        candidates.extend(lsp_out);
        if let Some(d) = lsp_degraded {
            degraded.push(d);
        }

        candidates.extend(blast_out);

        CollectOutput {
            candidates,
            degraded,
            git_evidence,
            lsp_client: returned_lsp_client,
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
fn artificial_delay_ms(source: &str) -> u64 {
    std::env::var("OXIDE_EVIDENCE_ARTIFICIAL_DELAY_MS")
        .ok()
        .and_then(|spec| {
            // Parse to an owned u64 inside this closure rather than
            // returning a tuple borrowed from `spec` — `spec` is local to
            // this closure, so a borrowed return value would dangle.
            spec.split(',').find_map(|pair| {
                let (k, v) = pair.split_once('=')?;
                if k == source {
                    v.parse::<u64>().ok()
                } else {
                    None
                }
            })
        })
        .unwrap_or(0)
}
#[cfg(not(test))]
fn artificial_delay_ms(_source: &str) -> u64 {
    0
}

/// Runs entirely on a `spawn_blocking` task — pure subprocess I/O +
/// `symbols`-slice filtering (`gitctx::build_git_context`), no
/// `RelationGraph`. `range` is always `""` (worktree vs HEAD) — matches
/// today's opt-in git-evidence call; `review --diff`'s explicit-range path
/// (Task 1) is a separate, non-coordinator call.
async fn git_io(root: std::path::PathBuf, symbols: Arc<[Symbol]>) -> Outcome<gitctx::GitContext> {
    let start = Instant::now();
    let handle = tokio::task::spawn_blocking(move || {
        std::thread::sleep(std::time::Duration::from_millis(artificial_delay_ms("git")));
        gitctx::build_git_context(&root, &symbols, "")
    });
    match handle.await {
        Ok(Ok(ctx)) => Outcome::Ready(ctx),
        Ok(Err(e)) => Outcome::Degraded(Degraded {
            source: EvidenceSource::Git,
            reason: DegradeReason::ProtocolError {
                detail: e.to_string(),
            },
            elapsed: start.elapsed(),
        }),
        Err(join_err) => Outcome::Degraded(Degraded {
            source: EvidenceSource::Git,
            reason: DegradeReason::Unavailable {
                detail: join_err.to_string(),
            },
            elapsed: start.elapsed(),
        }),
    }
}

/// Runs on a `spawn_blocking` task. Takes ownership of `client` and hands it
/// back in the return tuple regardless of outcome, so the caller can close
/// it (CLI) or restore it to the cache (MCP) either way. `py_seeds_owned`/
/// `py_seed_scores` are owned copies (same length/order) of the top Python
/// seeds — `enrich_seeds` needs data borrowed from something this task
/// itself owns, not the caller's borrowed `symbols`/`seeds`.
async fn lsp_io(
    mut client: LspClient,
    root: std::path::PathBuf,
    symbols: Arc<[Symbol]>,
    py_seeds_owned: Vec<Symbol>,
    py_seed_scores: Vec<f32>,
    scope_files: Vec<String>,
) -> (Option<LspClient>, Outcome<Vec<EvidenceCandidate>>) {
    let start = Instant::now();
    let handle = tokio::task::spawn_blocking(move || {
        std::thread::sleep(std::time::Duration::from_millis(artificial_delay_ms("lsp")));
        let py_seeds: Vec<&Symbol> = py_seeds_owned.iter().collect();
        let by_qname: HashMap<&str, f32> = py_seeds_owned
            .iter()
            .zip(py_seed_scores.iter())
            .map(|(s, score)| (s.qualified_name.as_str(), *score))
            .collect();
        let top_score = py_seed_scores.first().copied().unwrap_or(0.0);
        let evidence = enrich_seeds(
            &mut client,
            &root,
            &symbols,
            &py_seeds,
            &scope_files,
            LSP_MAX_SEEDS,
            LSP_PER_SEED_ITEMS,
        );
        let candidates: Vec<EvidenceCandidate> = evidence
            .into_iter()
            .map(|ev| {
                let seed_score = ev
                    .via
                    .and_then(|v| by_qname.get(v).copied())
                    .unwrap_or(top_score);
                EvidenceCandidate {
                    symbol: ev.symbol.clone(),
                    score: seed_score * LSP_SCORE_FRACTION,
                    reasons: vec![ev.reason],
                }
            })
            .collect();
        (client, candidates)
    });
    match handle.await {
        Ok((client, candidates)) => (Some(client), Outcome::Ready(candidates)),
        Err(join_err) => (
            None,
            Outcome::Degraded(Degraded {
                source: EvidenceSource::Lsp,
                reason: DegradeReason::Unavailable {
                    detail: join_err.to_string(),
                },
                elapsed: start.elapsed(),
            }),
        ),
    }
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
