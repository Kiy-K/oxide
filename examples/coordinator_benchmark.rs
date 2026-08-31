//! Retrieval-coordinator refactor evidence: concurrent lexical+semantic
//! latency, and `RetrievalMode`'s effect on `build_context`'s pack contents
//! (relevance, tokens, provider contribution) on
//! fixtures/structural_benchmark.json's callers tasks — the only intent
//! `context.rs`'s bounded ast-grep expansion wires in (see the exit report
//! for why implementors wasn't). `cargo run --example coordinator_benchmark --release`

use oxide::context::{build_context, ContextOptions};
use oxide::embeddings::{EmbeddingProvider, HashedEmbedder};
use oxide::index::{update_index, IndexBackend, SqliteStore};
use oxide::retrieval::{RetrievalEngine, RetrievalMode, SearchMode, SearchOptions};
use serde::Deserialize;
use std::collections::{HashMap, HashSet};
use std::fs;
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

#[derive(Deserialize)]
struct Config {
    repos: HashMap<String, String>,
    queries: Vec<Task>,
}

#[derive(Deserialize)]
struct Task {
    id: String,
    repo: String,
    text: String,
    structural_intent: String,
    relevant: Vec<String>,
}

/// Wraps a real embedder but adds a fixed delay to `embed_query`, standing in
/// for a slow HTTP embedder (`HttpEmbedder` in production) without needing
/// a live server. Isolates one variable: does the request pay
/// `lexical_time + delay` (serial, the old code) or `max(lexical_time,
/// delay)` (concurrent, the new code)?
struct SlowEmbedder {
    inner: HashedEmbedder,
    delay: Duration,
}
impl EmbeddingProvider for SlowEmbedder {
    fn name(&self) -> &str {
        self.inner.name()
    }
    fn dim(&self) -> usize {
        self.inner.dim()
    }
    fn embed(&self, text: &str) -> Vec<f32> {
        self.inner.embed(text)
    }
    fn embed_query(&self, text: &str) -> Vec<f32> {
        std::thread::sleep(self.delay);
        self.inner.embed_query(text)
    }
}

fn latency_evidence(store: &dyn IndexBackend) {
    println!("== concurrency: does a slow embedder serialize with lexical scoring? ==");
    let delay = Duration::from_millis(150);
    let slow = SlowEmbedder {
        inner: HashedEmbedder::default(),
        delay,
    };
    let engine = RetrievalEngine::new(store, &slow);
    let opts = SearchOptions {
        limit: 10,
        mode: SearchMode::Hybrid,
        expand: false,
        retrieval_mode: RetrievalMode::default(),
    };
    let t0 = Instant::now();
    engine.search("retry policy", &opts).unwrap();
    let elapsed = t0.elapsed();
    println!(
        "  embed_query delay={}ms, observed search latency={}ms (serial would be >= {}ms + lexical)",
        delay.as_millis(),
        elapsed.as_millis(),
        delay.as_millis()
    );
    assert!(
        elapsed < delay + Duration::from_millis(delay.as_millis() as u64 / 2),
        "search took {elapsed:?}, expected close to the {delay:?} floor if lexical and semantic ran concurrently"
    );
    println!("  PASS: latency tracks max(lexical, semantic), not their sum.\n");
}

fn mode_evidence(config: &Config) {
    println!("== RetrievalMode effect on build_context (relevance / tokens / provenance) ==");
    println!(
        "{:<28} {:>8} {:>10} {:>8} {:>10} {:>10}",
        "task", "mode", "recall", "tokens", "latency_ms", "ast_grep"
    );
    for (name, path) in &config.repos {
        let dir = PathBuf::from(path);
        let mut store = SqliteStore::open(Path::new(":memory:")).unwrap();
        let embedder = HashedEmbedder::default();
        update_index(&dir, &mut store, &embedder).unwrap();

        for task in config
            .queries
            .iter()
            .filter(|t| &t.repo == name && t.structural_intent == "callers")
        {
            let gold: HashSet<&str> = task.relevant.iter().map(|s| s.as_str()).collect();
            for mode in [
                RetrievalMode::Fast,
                RetrievalMode::Balanced,
                RetrievalMode::Quality,
            ] {
                let t0 = Instant::now();
                let pack = build_context(
                    &dir,
                    &store,
                    &embedder,
                    &task.text,
                    &ContextOptions {
                        retrieval_mode: mode,
                        ..Default::default()
                    },
                )
                .unwrap();
                let ms = t0.elapsed().as_secs_f64() * 1000.0;
                let ids: HashSet<String> = pack
                    .items
                    .iter()
                    .map(|i| format!("{}#{}", i.symbol.file, i.symbol.qualified_name))
                    .collect();
                let hit = ids.iter().filter(|id| gold.contains(id.as_str())).count();
                let recall = hit as f32 / gold.len().max(1) as f32;
                let ast_grep_items = pack
                    .items
                    .iter()
                    .filter(|i| i.reasons.iter().any(|r| r.starts_with("ast-grep-caller")))
                    .count();
                println!(
                    "{:<28} {:>8} {:>10.3} {:>8} {:>10.2} {:>10}",
                    task.id,
                    format!("{mode:?}"),
                    recall,
                    pack.used_tokens,
                    ms,
                    ast_grep_items
                );
            }
        }
    }
}

fn provider_contribution(config: &Config) {
    println!("\n== provider contribution (Balanced mode, evidence-tag counts across all tasks) ==");
    let mut counts: HashMap<&str, usize> = HashMap::new();
    for (name, path) in &config.repos {
        let dir = PathBuf::from(path);
        let mut store = SqliteStore::open(Path::new(":memory:")).unwrap();
        let embedder = HashedEmbedder::default();
        update_index(&dir, &mut store, &embedder).unwrap();
        for task in config.queries.iter().filter(|t| &t.repo == name) {
            let pack = build_context(
                &dir,
                &store,
                &embedder,
                &task.text,
                &ContextOptions {
                    retrieval_mode: RetrievalMode::Balanced,
                    ..Default::default()
                },
            )
            .unwrap();
            for item in &pack.items {
                for r in &item.reasons {
                    let tag = r.split(['=', '←']).next().unwrap_or(r);
                    *counts
                        .entry(match tag {
                            "lexical" => "lexical",
                            "semantic" => "semantic",
                            "ast-grep-caller" => "ast-grep-caller",
                            "parent" | "child" | "sibling" => "relationgraph-structure",
                            "uses" => "relationgraph-uses(heuristic)",
                            "imported-definition" => "relationgraph-import",
                            "test" => "relationgraph-test",
                            other => Box::leak(other.to_string().into_boxed_str()),
                        })
                        .or_insert(0) += 1;
                }
            }
        }
    }
    let mut rows: Vec<_> = counts.into_iter().collect();
    rows.sort_by_key(|a| std::cmp::Reverse(a.1));
    for (tag, n) in rows {
        println!("  {tag:<32} {n}");
    }
}

fn main() {
    let config: Config =
        serde_json::from_str(&fs::read_to_string("fixtures/structural_benchmark.json").unwrap())
            .unwrap();

    let mut store = SqliteStore::open(Path::new(":memory:")).unwrap();
    let embedder = HashedEmbedder::default();
    update_index(Path::new("fixtures/py_repo"), &mut store, &embedder).unwrap();
    latency_evidence(&store);

    mode_evidence(&config);
    provider_contribution(&config);
}
