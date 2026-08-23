//! Task-aware context packs: compact, ordered, budgeted symbol context for
//! coding agents. Optimized for signal per token, not raw recall.
//!
//! Pipeline: hybrid seeds → structural expansion → dedup/subsumption merge →
//! role ordering (primaries, dependencies, tests) → greedy budget fill with a
//! recency tail (U-shaped attention: lead with the target, close with the map).
//! Every inclusion and omission carries its reason.

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

pub const CHARS_PER_TOKEN: f32 = 4.0;

/// ~12 tokens of framing per item (header line, separators).
const ITEM_OVERHEAD_TOKENS: usize = 12;

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
            budget_tokens: 4096,
            max_candidates: 16,
        }
    }
}

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
        for seed in seeds.iter().take(5) {
            for (rel, n) in graph.neighbors(&seed.symbol) {
                if seen_seeds.contains(&n.id()) {
                    continue;
                }
                seen_seeds.insert(n.id());
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

    // ---- budgeted greedy fill ------------------------------------------
    let mut items: Vec<ContextItem> = Vec::new();
    let mut used = 0usize;
    for c in &kept {
        let snippet = crate::retrieval::read_snippet(
            &root.join(&c.symbol.file),
            c.symbol.start_line,
            c.symbol.end_line,
            60,
        );
        let est = estimate_tokens(&snippet) + ITEM_OVERHEAD_TOKENS;
        let cid = format!("{}#{}", c.symbol.file, c.symbol.qualified_name);
        if used + est > opts.budget_tokens {
            dropped.push(Omitted {
                id: cid,
                why: "over token budget".into(),
            });
            continue; // try smaller later items rather than stopping
        }
        used += est;
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
}
