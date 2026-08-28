//! Task-aware context packs: compact, ordered, budgeted symbol context for
//! coding agents. Optimized for signal per token, not raw recall.
//!
//! Pipeline: hybrid seeds → capped structural expansion → relevance floor →
//! dedup/subsumption merge → role ordering (primaries, dependencies, tests) →
//! budgeted fill with per-item caps and query-centered windows (shrink-to-fit,
//! so small junk never displaces a large primary) and a per-file diversity cap.
//! Every inclusion and omission carries its reason.

use crate::config::{
    CONTEXT_CHARS_PER_TOKEN, CONTEXT_DEFAULT_BUDGET_TOKENS, CONTEXT_EXPANSION_PER_SEED,
    CONTEXT_EXPANSION_TOTAL, CONTEXT_ITEM_OVERHEAD_TOKENS, CONTEXT_MAX_CANDIDATES,
    CONTEXT_MAX_ITEMS_PER_FILE, CONTEXT_MAX_PRIMARIES, CONTEXT_MAX_TESTS,
    CONTEXT_PER_ITEM_TOKEN_CAP, CONTEXT_RELEVANCE_FLOOR_FRACTION,
};
use crate::embeddings::EmbeddingProvider;
use crate::index::IndexBackend;
use crate::retrieval::{RelationGraph, RetrievalEngine, SearchMode, SearchOptions};
use crate::symbols::{Symbol, SymbolKind};
use anyhow::Result;
use serde::Serialize;
use std::collections::{HashMap, HashSet};
use std::path::Path;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum Role {
    /// Direct retrieval hit for the task.
    Primary,
    /// Structural neighbor: definitions used, parent/child, imports.
    Dependency,
    /// Related tests.
    Test,
}

pub const CHARS_PER_TOKEN: f32 = CONTEXT_CHARS_PER_TOKEN;

#[derive(Debug, Serialize)]
pub struct ContextItem {
    #[serde(flatten)]
    pub symbol: Symbol,
    pub role: Role,
    pub score: f32,
    pub reasons: Vec<String>,
    pub snippet: String,
    pub est_tokens: usize,
}

#[derive(Debug, Serialize)]
pub struct Omitted {
    pub id: String,
    pub why: String,
}

#[derive(Debug, Serialize)]
pub struct ContextPack {
    pub task: String,
    pub query_used: String,
    pub embedder: String,
    pub budget_tokens: usize,
    pub used_tokens: usize,
    pub items: Vec<ContextItem>,
    pub omitted: Vec<Omitted>,
}

/// Qwen3-style instruction prefix; improves NL→PL retrieval 1-5% per model card.
pub fn instructed_query(task: &str) -> String {
    format!(
        "Instruct: Given a coding task, retrieve repository symbols that are \
         relevant to understand or change to complete it\nQuery: {task}"
    )
}

pub struct ContextOptions {
    pub budget_tokens: usize,
    /// Candidate pool before packing.
    pub max_candidates: usize,
}

impl Default for ContextOptions {
    fn default() -> Self {
        // Research guidance puts implementation-task context around 1-4k tokens.
        Self {
            budget_tokens: CONTEXT_DEFAULT_BUDGET_TOKENS,
            max_candidates: CONTEXT_MAX_CANDIDATES,
        }
    }
}

#[derive(Debug, Clone)]
struct Candidate {
    symbol: Symbol,
    score: f32,
    reasons: Vec<String>,
    role: Role,
}

/// Build a context pack for `task` from the index at `store`; snippets are cut
/// from `root`.
pub fn build_context(
    root: &Path,
    store: &dyn IndexBackend,
    embedder: &dyn EmbeddingProvider,
    task: &str,
    opts: &ContextOptions,
) -> Result<ContextPack> {
    let engine = RetrievalEngine::new(store, embedder);
    let query = instructed_query(task);
    let seed_opts = SearchOptions {
        limit: opts.max_candidates,
        mode: SearchMode::Hybrid,
        expand: false,
    };
    let seeds = engine.search(&query, &seed_opts)?;

    let mut candidates: HashMap<u64, Candidate> = HashMap::new();
    let mut order_note = |c: Candidate| {
        candidates
            .entry(c.symbol.id())
            .and_modify(|existing| {
                existing.score += c.score;
                for r in &c.reasons {
                    if !existing.reasons.contains(r) {
                        existing.reasons.push(r.clone());
                    }
                }
            })
            .or_insert(c);
    };

    for h in &seeds {
        let role = if is_test_symbol(&h.symbol) {
            Role::Test
        } else {
            Role::Primary
        };
        order_note(Candidate {
            symbol: h.symbol.clone(),
            score: h.score,
            reasons: h.reasons.clone(),
            role,
        });
    }

    // Structural expansion around strong primaries only (same rule as search:
    // expansion supplements, never displaces direct hits).
    if !seeds.is_empty() {
        let symbols = store.all_symbols()?;
        let graph = RelationGraph::build(&symbols);
        let mut seen_seeds: HashSet<u64> = seeds.iter().map(|h| h.symbol.id()).collect();
        let mut expansion_total = 0usize;
        for seed in seeds.iter().take(5) {
            if expansion_total >= CONTEXT_EXPANSION_TOTAL {
                break;
            }
            let mut from_seed = 0usize;
            for (rel, n) in graph.neighbors(&seed.symbol) {
                if expansion_total >= CONTEXT_EXPANSION_TOTAL
                    || from_seed >= CONTEXT_EXPANSION_PER_SEED
                {
                    break;
                }
                if seen_seeds.contains(&n.id()) {
                    continue;
                }
                seen_seeds.insert(n.id());
                from_seed += 1;
                expansion_total += 1;
                let role = match rel.as_str() {
                    "test" => Role::Test,
                    _ => Role::Dependency,
                };
                order_note(Candidate {
                    symbol: n.clone(),
                    score: seed.score * 0.4,
                    reasons: vec![format!("{rel}←{}", seed.symbol.qualified_name)],
                    role,
                });
            }
        }
    }

    // ---- dedup / subsumption -------------------------------------------
    // Highest score wins first so "kept" items always dominate dropped ones.
    let mut ranked: Vec<Candidate> = candidates.into_values().collect();
    ranked.sort_by(|a, b| {
        b.score
            .partial_cmp(&a.score)
            .unwrap_or(std::cmp::Ordering::Equal)
    });

    let files_with_concrete: HashSet<String> = ranked
        .iter()
        .filter(|c| c.symbol.kind != SymbolKind::Module)
        .map(|c| c.symbol.file.clone())
        .collect();
    let mut dropped: Vec<Omitted> = Vec::new();
    let mut kept: Vec<Candidate> = Vec::new();
    for c in ranked {
        let cid = format!("{}#{}", c.symbol.file, c.symbol.qualified_name);
        if c.symbol.kind == SymbolKind::Module && files_with_concrete.contains(&c.symbol.file) {
            dropped.push(Omitted {
                id: cid,
                why: "module subsumed by concrete symbols".into(),
            });
            continue;
        }
        if kept
            .iter()
            .any(|k| k.symbol.file == c.symbol.file && overlap_ratio(&k.symbol, &c.symbol) > 0.8)
        {
            dropped.push(Omitted {
                id: cid,
                why: "subsumed by overlapping symbol".into(),
            });
            continue;
        }
        kept.push(c);
    }

    // ---- ordering: primaries → dependencies → tests, score-desc within role
    fn rank(role: Role) -> u8 {
        match role {
            Role::Primary => 0,
            Role::Dependency => 1,
            Role::Test => 2,
        }
    }
    kept.sort_by(|a, b| {
        rank(a.role).cmp(&rank(b.role)).then(
            b.score
                .partial_cmp(&a.score)
                .unwrap_or(std::cmp::Ordering::Equal),
        )
    });

    // ---- relevance floor ------------------------------------------------
    // Weak tail candidates dilute the pack; drop them unless nothing survives.
    if let Some(top_score) = seeds.first().map(|h| h.score) {
        let (strong, weak) = split_below_floor(kept, top_score * CONTEXT_RELEVANCE_FLOOR_FRACTION);
        if strong.is_empty() {
            kept = weak; // nothing survives the floor: keep everything
        } else {
            for c in &weak {
                dropped.push(Omitted {
                    id: format!("{}#{}", c.symbol.file, c.symbol.qualified_name),
                    why: "below relevance floor".into(),
                });
            }
            kept = strong;
        }
    }

    // ---- budgeted greedy fill ------------------------------------------
    let mut items: Vec<ContextItem> = Vec::new();
    let mut used = 0usize;
    let terms = query_terms(task);
    let top_id = kept.first().map(|c| c.symbol.id());
    let mut per_file: HashMap<&str, usize> = HashMap::new();
    let mut primaries = 0usize;
    let mut tests = 0usize;
    for c in &kept {
        let cid = format!("{}#{}", c.symbol.file, c.symbol.qualified_name);
        if per_file.get(c.symbol.file.as_str()).copied().unwrap_or(0) >= CONTEXT_MAX_ITEMS_PER_FILE
            && top_id != Some(c.symbol.id())
        {
            dropped.push(Omitted {
                id: cid,
                why: "per-file diversity cap".into(),
            });
            continue;
        }
        let over_role_cap = match c.role {
            Role::Primary => {
                if primaries >= CONTEXT_MAX_PRIMARIES {
                    Some("beyond primary cap")
                } else {
                    primaries += 1;
                    None
                }
            }
            Role::Test => {
                if tests >= CONTEXT_MAX_TESTS {
                    Some("beyond test cap")
                } else {
                    tests += 1;
                    None
                }
            }
            Role::Dependency => None,
        };
        if let Some(why) = over_role_cap {
            dropped.push(Omitted {
                id: cid,
                why: why.into(),
            });
            continue;
        }
        // Shrink-to-fit: halve the per-item cap until it fits, so tiny junk
        // never displaces a large primary.
        let mut cap = CONTEXT_PER_ITEM_TOKEN_CAP.min(opts.budget_tokens);
        let (snippet, est) = loop {
            let snip = render_snippet(root, &c.symbol, &terms, cap);
            let e = estimate_tokens(&snip) + CONTEXT_ITEM_OVERHEAD_TOKENS;
            if used + e <= opts.budget_tokens || cap == 0 {
                break (snip, e);
            }
            cap /= 2;
        };
        if used + est > opts.budget_tokens {
            dropped.push(Omitted {
                id: cid,
                why: "over token budget".into(),
            });
            // Release the reserved role slot.
            match c.role {
                Role::Primary => primaries -= 1,
                Role::Test => tests -= 1,
                Role::Dependency => {}
            }
            continue;
        }
        used += est;
        *per_file.entry(c.symbol.file.as_str()).or_insert(0) += 1;
        items.push(ContextItem {
            symbol: c.symbol.clone(),
            role: c.role,
            score: c.score,
            reasons: dedup_reasons(&c.reasons),
            snippet,
            est_tokens: est,
        });
    }
    // Stable output: keep the ranked order we filled in.
    items.sort_by(|a, b| {
        rank(a.role).cmp(&rank(b.role)).then(
            b.score
                .partial_cmp(&a.score)
                .unwrap_or(std::cmp::Ordering::Equal),
        )
    });

    Ok(ContextPack {
        task: task.to_string(),
        query_used: query,
        embedder: embedder.name().to_string(),
        budget_tokens: opts.budget_tokens,
        used_tokens: used,
        omitted: dropped,
        items,
    })
}

fn dedup_reasons(rs: &[String]) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    for r in rs {
        if !out.contains(r) {
            out.push(r.clone());
        }
    }
    out
}

/// Lowercased, deduplicated query terms used to center snippet windows.
fn query_terms(task: &str) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    for w in task.to_lowercase().split(|c: char| !c.is_alphanumeric()) {
        if w.len() >= 3 && !out.iter().any(|t| t == w) {
            out.push(w.to_string());
        }
    }
    out
}

/// Partition candidates into those at/above `floor` and below it.
fn split_below_floor(kept: Vec<Candidate>, floor: f32) -> (Vec<Candidate>, Vec<Candidate>) {
    kept.into_iter().partition(|c| c.score >= floor)
}

/// Snippet for a symbol capped at `max_tokens`: whole body when it fits,
/// otherwise a window centered on the lines matching the most query terms
/// (head of the symbol as fallback when nothing matches).
fn render_snippet(root: &Path, s: &Symbol, terms: &[String], max_tokens: usize) -> String {
    let Ok(src) = std::fs::read_to_string(root.join(&s.file)) else {
        return String::new();
    };
    let lines: Vec<&str> = src.lines().collect();
    let lo = s.start_line.saturating_sub(1) as usize;
    let hi = (s.end_line as usize).min(lines.len());
    if hi <= lo {
        return String::new();
    }
    let body = &lines[lo..hi];
    let budget = (max_tokens as f32 * CHARS_PER_TOKEN) as usize;
    let total: usize = body.iter().map(|l| l.len() + 1).sum();
    if total <= budget {
        return body.join("\n");
    }
    let hits = |line: &str| -> usize {
        let low = line.to_lowercase();
        terms.iter().filter(|t| low.contains(t.as_str())).count()
    };
    let scores: Vec<usize> = body.iter().map(|l| hits(l)).collect();
    let best = scores.iter().max().copied().unwrap_or(0);
    if best == 0 || budget == 0 {
        // No query-term anchor: keep the head of the symbol.
        let mut out = String::new();
        for l in body {
            if out.len() + l.len() + 1 > budget {
                break;
            }
            out.push_str(l);
            out.push('\n');
        }
        return out.trim_end_matches('\n').to_string();
    }
    // Window around the first densest-match line, growing toward the shorter
    // neighbor line until the character budget is spent.
    let mut lo_i = scores.iter().position(|s| *s == best).unwrap_or(0);
    let mut hi_i = lo_i;
    let mut used = body[lo_i].len() + 1;
    loop {
        let can_lo = lo_i > 0;
        let can_hi = hi_i + 1 < body.len();
        if !can_lo && !can_hi {
            break;
        }
        let grow_lo = can_lo && (!can_hi || body[lo_i - 1].len() <= body[hi_i + 1].len());
        let cost = if grow_lo {
            body[lo_i - 1].len() + 1
        } else {
            body[hi_i + 1].len() + 1
        };
        if used + cost > budget {
            break;
        }
        if grow_lo {
            lo_i -= 1;
        } else {
            hi_i += 1;
        }
        used += cost;
    }
    body[lo_i..=hi_i].join("\n")
}

fn is_test_symbol(s: &Symbol) -> bool {
    let f = s.file.to_lowercase();
    let n = s.name.to_lowercase();
    f.starts_with("test_")
        || f.contains("_test.")
        || f.contains(".test.")
        || f.contains(".spec.")
        || f.contains("/tests/")
        || n.starts_with("test_")
}

fn overlap_ratio(a: &Symbol, b: &Symbol) -> f32 {
    let lo = a.start_line.max(b.start_line);
    let hi = a.end_line.min(b.end_line);
    if hi < lo {
        return 0.0;
    }
    let inter = (hi - lo + 1) as f32;
    let smaller = (a.end_line - a.start_line + 1).min(b.end_line - b.start_line + 1) as f32;
    inter / smaller.max(1.0)
}

pub fn estimate_tokens(text: &str) -> usize {
    (text.chars().count() as f32 / CHARS_PER_TOKEN).ceil() as usize
}

impl ContextPack {
    /// Compact trailing map of the pack contents — recency-friendly closing.
    pub fn tail_summary(&self) -> String {
        let list: Vec<String> = self
            .items
            .iter()
            .map(|i| {
                format!(
                    "{}#{} ({})",
                    i.symbol.file, i.symbol.qualified_name, i.est_tokens
                )
            })
            .collect();
        format!("Pack contents ({}): {}", self.items.len(), list.join(", "))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::embeddings::HashedEmbedder;
    use crate::index::{IndexBackend, SqliteStore};
    use crate::symbols::{content_hash, Language};

    fn sym(file: &str, qname: &str, kind: SymbolKind, sig: &str) -> Symbol {
        let name = qname.rsplit('.').next().unwrap().to_string();
        Symbol {
            qualified_name: qname.into(),
            name,
            kind,
            language: Language::Python,
            file: file.into(),
            start_line: 1,
            end_line: sig.lines().count() as u32,
            content_hash: content_hash(sig),
            signature: sig.into(),
            imports: vec![],
            exported: true,
            parent: None,
            references: vec![],
        }
    }

    fn seed(file: &str, syms: &[Symbol]) -> SqliteStore {
        let mut store = SqliteStore::open(std::path::Path::new(":memory:")).unwrap();
        store.replace_file(file, 1, syms).unwrap();
        let emb = HashedEmbedder::default();
        for s in syms {
            store
                .put_embedding(s.id(), &emb.embed(&crate::index::embed_text(s)))
                .unwrap();
        }
        store
    }

    #[test]
    fn instructed_query_uses_qwen3_protocol() {
        let q = instructed_query("fix backoff");
        assert!(q.starts_with("Instruct: "));
        assert!(q.contains("Query: fix backoff"));
    }

    #[test]
    fn budget_is_respected_and_overflows_are_recorded() {
        // One huge relevant symbol + several small ones.
        let big_body = format!("def big():\n    x = 1\n    {}", "pass\n".repeat(400));
        let mut store = seed(
            "src/big.py",
            &[sym("src/big.py", "big", SymbolKind::Function, &big_body)],
        );
        let smalls: Vec<Symbol> = (0..5)
            .map(|i| {
                sym(
                    "src/small.py",
                    &format!("small_{i}"),
                    SymbolKind::Function,
                    "def run(): pass",
                )
            })
            .collect();
        store.replace_file("src/small.py", 1, &smalls).unwrap();
        let emb = HashedEmbedder::default();
        for s in &smalls {
            store
                .put_embedding(s.id(), &emb.embed(&crate::index::embed_text(s)))
                .unwrap();
        }
        let tmp = tempfile::tempdir().unwrap();
        let pack = build_context(
            tmp.path(),
            &store,
            &HashedEmbedder::default(),
            "small",
            &ContextOptions {
                budget_tokens: 40,
                ..Default::default()
            },
        )
        .unwrap();
        assert!(pack.used_tokens <= 40, "{} > 40", pack.used_tokens);
    }

    #[test]
    fn ordering_groups_by_role() {
        let store = seed(
            "src/auth.py",
            &[sym(
                "src/auth.py",
                "refresh_token",
                SymbolKind::Function,
                "def refresh_token(): expired flow",
            )],
        );
        let tmp = tempfile::tempdir().unwrap();
        let emb = HashedEmbedder::default();
        let pack = build_context(
            tmp.path(),
            &store,
            &emb,
            "token refresh after expiry",
            &ContextOptions::default(),
        )
        .unwrap();
        let ranks: Vec<u8> = pack
            .items
            .iter()
            .map(|i| match i.role {
                Role::Primary => 0,
                Role::Dependency => 1,
                Role::Test => 2,
            })
            .collect();
        let mut sorted = ranks.clone();
        sorted.sort_unstable();
        assert_eq!(
            ranks, sorted,
            "roles must be grouped primaries->dependencies->tests"
        );
    }

    #[test]
    fn module_symbols_yield_to_concrete_siblings() {
        let m = sym(
            "src/a.py",
            "src/a.py:__module__",
            SymbolKind::Module,
            "# module a",
        );
        let f = sym(
            "src/a.py",
            "helper",
            SymbolKind::Function,
            "def helper(): token refresh helper",
        );
        let other = sym(
            "src/other.py",
            "src/other.py:__module__",
            SymbolKind::Module,
            "# unrelated cache module",
        );
        let mut store = SqliteStore::open(std::path::Path::new(":memory:")).unwrap();
        store
            .replace_file("src/a.py", 1, &[m.clone(), f.clone()])
            .unwrap();
        store
            .replace_file("src/other.py", 1, std::slice::from_ref(&other))
            .unwrap();
        let emb = HashedEmbedder::default();
        for s in [&m, &f, &other] {
            store
                .put_embedding(s.id(), &emb.embed(&crate::index::embed_text(s)))
                .unwrap();
        }
        let tmp = tempfile::tempdir().unwrap();
        let pack = build_context(
            tmp.path(),
            &store,
            &emb,
            "token refresh",
            &ContextOptions::default(),
        )
        .unwrap();
        let ids: Vec<&str> = pack
            .items
            .iter()
            .map(|i| i.symbol.qualified_name.as_str())
            .collect();
        assert!(ids.contains(&"helper"), "{ids:?}");
        assert!(
            !ids.iter()
                .any(|q| q.ends_with("__module__") && *q != "src/other.py:__module__"),
            "a.py module must be subsumed by helper: {ids:?}"
        );
        assert!(pack.omitted.iter().any(|o| o.why.contains("subsumed")));
    }

    fn write_file(root: &Path, rel: &str, content: &str) {
        let p = root.join(rel);
        std::fs::create_dir_all(p.parent().unwrap()).unwrap();
        std::fs::write(p, content).unwrap();
    }

    /// Move a symbol's span to `start` so same-file fixtures don't fully
    /// overlap (overlapping spans are removed by subsumption).
    fn spaced(mut s: Symbol, start: u32) -> Symbol {
        let len = s.end_line - s.start_line + 1;
        s.start_line = start;
        s.end_line = start + len - 1;
        s
    }

    #[test]
    fn window_centers_on_query_terms_within_cap() {
        let tmp = tempfile::tempdir().unwrap();
        let mut body = String::from("def job():\n");
        for i in 0..200 {
            body.push_str(&format!("    filler_line_{i} = {i}\n"));
        }
        body.push_str("    backoff_retry_deadline = compute_backoff()\n");
        for i in 0..50 {
            body.push_str(&format!("    tail_filler_{i} = {i}\n"));
        }
        write_file(tmp.path(), "src/job.py", &body);
        let s = sym("src/job.py", "job", SymbolKind::Function, &body);
        let terms = query_terms("fix backoff retry deadline");
        let snip = render_snippet(tmp.path(), &s, &terms, 100);
        assert!(
            snip.contains("backoff_retry_deadline"),
            "window must center on the matching span: {snip:?}"
        );
        assert!(estimate_tokens(&snip) <= 100);
        assert!(
            !snip.contains("tail_filler_49"),
            "window must not reach the tail"
        );
    }

    #[test]
    fn big_primary_survives_tight_budget_by_shrinking() {
        // Old failure: the huge gold symbol was skipped ("over token budget")
        // while tiny junk filled the pack. Shrink-to-fit keeps concentrated
        // evidence from the gold symbol instead.
        let tmp = tempfile::tempdir().unwrap();
        let mut gold_body = String::from("def sync_engine():\n");
        for i in 0..400 {
            gold_body.push_str(&format!("    x{i} = incremental_sync_step_{i}\n"));
        }
        gold_body.push_str("    result = incremental_sync_commit()\n");
        write_file(tmp.path(), "src/sync_engine.py", &gold_body);
        let gold = sym(
            "src/sync_engine.py",
            "sync_engine",
            SymbolKind::Function,
            &gold_body,
        );
        let mut store = seed("src/sync_engine.py", &[gold]);
        let smalls: Vec<Symbol> = (0..20)
            .map(|i| {
                sym(
                    "src/junk.py",
                    &format!("junk_{i}"),
                    SymbolKind::Function,
                    "def run(): pass",
                )
            })
            .collect();
        store.replace_file("src/junk.py", 1, &smalls).unwrap();
        let emb = HashedEmbedder::default();
        for s in &smalls {
            store
                .put_embedding(s.id(), &emb.embed(&crate::index::embed_text(s)))
                .unwrap();
        }
        let pack = build_context(
            tmp.path(),
            &store,
            &HashedEmbedder::default(),
            "incremental sync commit",
            &ContextOptions {
                budget_tokens: 600,
                ..Default::default()
            },
        )
        .unwrap();
        assert!(pack.used_tokens <= 600, "{} > 600", pack.used_tokens);
        let gold = pack
            .items
            .iter()
            .find(|i| i.symbol.qualified_name == "sync_engine")
            .expect("gold primary must be packed, not skipped: omitted={:?}");
        assert!(
            gold.snippet.contains("incremental_sync_commit"),
            "shrunk window must keep the matching span"
        );
    }

    #[test]
    fn expansion_is_capped_per_seed_and_total() {
        // One seed referencing ten defs: fan-out must stay bounded.
        // Each def in its own file so the per-file diversity cap cannot mask
        // the expansion fan-out cap.
        let defs: Vec<Symbol> = (0..10)
            .map(|i| {
                sym(
                    &format!("src/util{i}.py"),
                    &format!("util_{i}"),
                    SymbolKind::Function,
                    &format!("def util_{i}(): pass"),
                )
            })
            .collect();
        let mut caller = sym(
            "src/app.py",
            "caller",
            SymbolKind::Function,
            "def caller(): runs utils",
        );
        caller.references = (0..10).map(|i| format!("util_{i}")).collect();
        let mut store = seed("src/app.py", &[caller.clone()]);
        for d in &defs {
            store
                .replace_file(&d.file, 1, std::slice::from_ref(d))
                .unwrap();
        }
        let emb = HashedEmbedder::default();
        for s in defs.iter().chain(std::iter::once(&caller)) {
            store
                .put_embedding(s.id(), &emb.embed(&crate::index::embed_text(s)))
                .unwrap();
        }
        let tmp = tempfile::tempdir().unwrap();
        let pack = build_context(
            tmp.path(),
            &store,
            &HashedEmbedder::default(),
            "caller",
            // Only `caller` can be a seed; the utils must arrive via expansion.
            &ContextOptions {
                max_candidates: 1,
                ..Default::default()
            },
        )
        .unwrap();
        let deps = pack
            .items
            .iter()
            .filter(|i| i.role == Role::Dependency)
            .count();
        assert!(
            deps >= 1,
            "test requires at least one expansion neighbor to be meaningful"
        );
        assert!(
            deps == CONTEXT_EXPANSION_PER_SEED,
            "fan-out must be capped at exactly {CONTEXT_EXPANSION_PER_SEED}, got {deps}"
        );
    }

    #[test]
    fn diversity_cap_limits_items_per_file() {
        let hot: Vec<Symbol> = (0..6)
            .map(|i| {
                spaced(
                    sym(
                        "src/hot.py",
                        &format!("hot_target_{i}"),
                        SymbolKind::Function,
                        &format!("def hot_target_{i}(): hot target logic {i}"),
                    ),
                    100 * i + 1,
                )
            })
            .collect();
        let mut store = seed("src/hot.py", &hot);
        let other = sym(
            "src/other.py",
            "helper_one",
            SymbolKind::Function,
            "def helper_one(): hot target help",
        );
        store
            .replace_file("src/other.py", 1, std::slice::from_ref(&other))
            .unwrap();
        let emb = HashedEmbedder::default();
        for s in hot.iter().chain(std::iter::once(&other)) {
            store
                .put_embedding(s.id(), &emb.embed(&crate::index::embed_text(s)))
                .unwrap();
        }
        let tmp = tempfile::tempdir().unwrap();
        let pack = build_context(
            tmp.path(),
            &store,
            &HashedEmbedder::default(),
            "hot target",
            &ContextOptions::default(),
        )
        .unwrap();
        let hot_count = pack
            .items
            .iter()
            .filter(|i| i.symbol.file == "src/hot.py")
            .count();
        assert!(
            hot_count <= CONTEXT_MAX_ITEMS_PER_FILE + 1,
            "one hog file must not eat the pack: {hot_count} items"
        );
        assert!(
            pack.omitted.iter().any(|o| o.why.contains("diversity")),
            "dropped hogs must carry an explicit reason"
        );
    }

    #[test]
    fn relevance_floor_drops_weak_but_keeps_everything_when_all_weak() {
        let mk = |score: f32| Candidate {
            symbol: sym("src/a.py", "f", SymbolKind::Function, "def f(): pass"),
            score,
            reasons: vec![],
            role: Role::Primary,
        };
        let kept = vec![mk(1.0), mk(0.5), mk(0.1)];
        let (strong, weak) = split_below_floor(kept.clone(), 0.15);
        assert_eq!(strong.len(), 2);
        assert_eq!(weak.len(), 1);
        let all_weak = vec![mk(0.05), mk(0.01)];
        let (strong, weak) = split_below_floor(all_weak, 0.15);
        assert!(strong.is_empty());
        assert_eq!(weak.len(), 2, "all-weak must keep everything");
    }
    #[test]
    fn primary_cap_bounds_semantic_tail() {
        // Embedding scores cluster; ranks beyond the primary cap are noise in
        // eval probes and must not dilute the pack.
        let many: Vec<Symbol> = (0..(CONTEXT_MAX_PRIMARIES + 3))
            .map(|i| {
                spaced(
                    sym(
                        &format!("src/m{i}.py"),
                        &format!("widget_{i}"),
                        SymbolKind::Function,
                        &format!("def widget_{i}(): widget logic {i}"),
                    ),
                    1,
                )
            })
            .collect();
        let mut store = SqliteStore::open(std::path::Path::new(":memory:")).unwrap();
        for s in &many {
            store
                .replace_file(&s.file, 1, std::slice::from_ref(s))
                .unwrap();
        }
        let emb = HashedEmbedder::default();
        for s in &many {
            store
                .put_embedding(s.id(), &emb.embed(&crate::index::embed_text(s)))
                .unwrap();
        }
        let tmp = tempfile::tempdir().unwrap();
        let pack = build_context(
            tmp.path(),
            &store,
            &HashedEmbedder::default(),
            "widget",
            &ContextOptions {
                max_candidates: CONTEXT_MAX_PRIMARIES + 3,
                ..Default::default()
            },
        )
        .unwrap();
        let prim = pack
            .items
            .iter()
            .filter(|i| i.role == Role::Primary)
            .count();
        assert_eq!(prim, CONTEXT_MAX_PRIMARIES, "semantic tail must be capped");
        assert!(
            pack.omitted.iter().any(|o| o.why.contains("primary cap")),
            "capped primaries need an explicit reason"
        );
    }
    #[test]
    fn orphan_module_stays_a_direct_hit_but_subsumed_when_sibling_exists() {
        // An orphan module (no concrete sibling retrieved) can be the only
        // evidence for its file and must be packed; a module whose file has
        // concrete candidates is subsumed.
        let orphan = sym(
            "src/mod_only.py",
            "src/mod_only.py:__module__",
            SymbolKind::Module,
            "# module with token refresh docs",
        );
        let subsumed = sym(
            "src/both.py",
            "src/both.py:__module__",
            SymbolKind::Module,
            "# both module",
        );
        let concrete = sym(
            "src/both.py",
            "helper",
            SymbolKind::Function,
            "def helper(): token refresh helper",
        );
        let mut store = SqliteStore::open(std::path::Path::new(":memory:")).unwrap();
        store
            .replace_file("src/mod_only.py", 1, std::slice::from_ref(&orphan))
            .unwrap();
        store
            .replace_file("src/both.py", 1, &[subsumed.clone(), concrete.clone()])
            .unwrap();
        let emb = HashedEmbedder::default();
        for s in [&orphan, &subsumed, &concrete] {
            store
                .put_embedding(s.id(), &emb.embed(&crate::index::embed_text(s)))
                .unwrap();
        }
        let tmp = tempfile::tempdir().unwrap();
        let pack = build_context(
            tmp.path(),
            &store,
            &emb,
            "token refresh",
            &ContextOptions::default(),
        )
        .unwrap();
        let ids: Vec<&str> = pack
            .items
            .iter()
            .map(|i| i.symbol.qualified_name.as_str())
            .collect();
        assert!(
            ids.contains(&"src/mod_only.py:__module__"),
            "orphan module is its file's only evidence: {ids:?}"
        );
        assert!(
            !ids.contains(&"src/both.py:__module__"),
            "module with concrete sibling must be subsumed: {ids:?}"
        );
    }
}
