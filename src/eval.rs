//! Deterministic retrieval benchmark: committed fixtures + ground truth,
//! comparing vector-only vs hybrid retrieval on identical queries.

use crate::embeddings::HashedEmbedder;
use crate::index::{update_index, SqliteStore};
use crate::retrieval::{RetrievalEngine, SearchMode, SearchOptions};
use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};

#[derive(Debug, Deserialize)]
pub struct BenchConfig {
    pub repos: std::collections::HashMap<String, String>,
    pub queries: Vec<BenchQuery>,
    #[serde(default = "default_k")]
    pub k: usize,
}

fn default_k() -> usize {
    5
}

#[derive(Debug, Deserialize)]
pub struct BenchQuery {
    pub id: String,
    /// Key into `repos`.
    pub repo: String,
    pub text: String,
    /// Relevant symbols as "path#QualifiedName".
    pub relevant: Vec<String>,
    pub task: String,
}

#[derive(Debug, Serialize)]
pub struct QueryResult {
    pub id: String,
    pub task: String,
    pub mode: String,
    pub recall_at_k: f32,
    pub precision_at_k: f32,
    pub returned: usize,
    pub context_bytes: usize,
    pub obvious_false_positives: usize,
    pub hits: Vec<String>,
}

#[derive(Debug, Serialize)]
pub struct Aggregate {
    pub mode: String,
    pub mean_recall_at_k: f32,
    pub mean_precision_at_k: f32,
    pub avg_returned: f32,
    pub avg_context_bytes: f32,
}

#[derive(Debug, Serialize)]
pub struct BenchmarkReport {
    pub k: usize,
    pub per_query: Vec<QueryResult>,
    pub aggregate: Vec<Aggregate>,
}

fn materialize_repo(fixture_rel: &str) -> Result<(PathBuf, tempfile::TempDir)> {
    let manifest = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let src = manifest.join(fixture_rel);
    anyhow::ensure!(src.exists(), "fixture missing: {}", src.display());
    let tmp = tempfile::tempdir()?;
    let dst = tmp.path().join("repo");
    copy_dir(&src, &dst)?;
    Ok((dst, tmp))
}

fn copy_dir(src: &Path, dst: &Path) -> Result<()> {
    std::fs::create_dir_all(dst)?;
    for entry in std::fs::read_dir(src)? {
        let entry = entry?;
        let ty = entry.file_type()?;
        if ty.is_dir() {
            // keep .gitignore (dotfiles) but drop VCS internals
            if entry.file_name() == ".git" {
                continue;
            }
            copy_dir(&entry.path(), &dst.join(entry.file_name()))?;
        } else {
            std::fs::copy(entry.path(), dst.join(entry.file_name()))?;
        }
    }
    Ok(())
}

fn span_bytes(root: &Path, file: &str, start: u32, end: u32) -> usize {
    std::fs::read_to_string(root.join(file))
        .map(|src| {
            src.lines()
                .skip(start.saturating_sub(1) as usize)
                .take(end.saturating_sub(start - 1) as usize + 1)
                .map(|l| l.len() + 1)
                .sum::<usize>()
        })
        .unwrap_or((end - start + 1) as usize * 40)
}

pub fn run_benchmark(config_path: &Path) -> Result<BenchmarkReport> {
    let text = std::fs::read_to_string(config_path)
        .with_context(|| format!("read {}", config_path.display()))?;
    let config: BenchConfig =
        serde_json::from_str(&text).with_context(|| format!("parse {}", config_path.display()))?;
    let k = config.k;

    // Materialize and index each fixture repo once.
    let mut repos: Vec<(String, tempfile::TempDir, SqliteStore)> = Vec::new();
    for name in config.repos.keys() {
        let (dir, tmp) = materialize_repo(&config.repos[name])?;
        let mut store = SqliteStore::open(Path::new(":memory:"))?;
        update_index(&dir, &mut store, &HashedEmbedder::default())?;
        repos.push((name.clone(), tmp, store));
    }

    // One engine per repo (lexicon built once), reused across queries × modes.
    let mut per_query: Vec<QueryResult> = Vec::new();
    for (repo_name, tmp, store) in &repos {
        let embedder = HashedEmbedder::default();
        let engine = RetrievalEngine::new(store, &embedder);
        let root_dir = tmp.path().join("repo");
        for q in config.queries.iter().filter(|q| q.repo == *repo_name) {
            for (mode_name, mode) in [
                ("vector-only", SearchMode::VectorOnly),
                ("hybrid", SearchMode::Hybrid),
            ] {
                let expand = mode == SearchMode::Hybrid;
                let opts = SearchOptions {
                    limit: k,
                    mode,
                    expand,
                };
                let hits = engine.search(&q.text, &opts)?;
                let relevant: std::collections::HashSet<&str> =
                    q.relevant.iter().map(|s| s.as_str()).collect();
                let mut matched = 0usize;
                let mut bytes = 0usize;
                let mut fp_modules = 0usize;
                let mut hit_ids = Vec::new();
                for h in &hits {
                    let id = format!("{}#{}", h.symbol.file, h.symbol.qualified_name);
                    hit_ids.push(id.clone());
                    if relevant.contains(id.as_str()) {
                        matched += 1;
                    }
                    if h.symbol.kind == crate::symbols::SymbolKind::Module
                        && !relevant.contains(id.as_str())
                    {
                        fp_modules += 1;
                    }
                    bytes += span_bytes(
                        &root_dir,
                        &h.symbol.file,
                        h.symbol.start_line,
                        h.symbol.end_line,
                    );
                }
                let denom_recall = relevant.len().max(1) as f32;
                let denom_prec = hits.len().min(k).max(1) as f32;
                per_query.push(QueryResult {
                    id: q.id.clone(),
                    task: q.task.clone(),
                    mode: mode_name.into(),
                    recall_at_k: matched as f32 / denom_recall,
                    precision_at_k: matched as f32 / denom_prec,
                    returned: hits.len(),
                    context_bytes: bytes,
                    obvious_false_positives: fp_modules,
                    hits: hit_ids,
                });
            }
        }
    }

    let mut aggregate: Vec<Aggregate> = Vec::new();
    for mode in ["vector-only", "hybrid"] {
        let rows: Vec<&QueryResult> = per_query.iter().filter(|r| r.mode == mode).collect();
        let n = rows.len().max(1) as f32;
        aggregate.push(Aggregate {
            mode: mode.into(),
            mean_recall_at_k: rows.iter().map(|r| r.recall_at_k).sum::<f32>() / n,
            mean_precision_at_k: rows.iter().map(|r| r.precision_at_k).sum::<f32>() / n,
            avg_returned: rows.iter().map(|r| r.returned as f32).sum::<f32>() / n,
            avg_context_bytes: rows.iter().map(|r| r.context_bytes as f32).sum::<f32>() / n,
        });
    }

    Ok(BenchmarkReport {
        k,
        per_query,
        aggregate,
    })
}

pub fn cmd_eval(config: &str, json: bool) -> Result<()> {
    let report = run_benchmark(Path::new(config))?;
    if json {
        println!("{}", serde_json::to_string_pretty(&report)?);
        return Ok(());
    }
    println!(
        "{:<34} {:<14} {:>8} {:>9} {:>8} {:>10}",
        "query", "task", "mode", "recall@k", "prec@k", "ctx bytes"
    );
    for r in &report.per_query {
        println!(
            "{:<34} {:<14} {:>8} {:>9.3} {:>9.3} {:>10}",
            r.id, r.task, r.mode, r.recall_at_k, r.precision_at_k, r.context_bytes
        );
    }
    println!();
    for a in &report.aggregate {
        println!(
            "{:<12} recall@{} {:.3}  precision@{} {:.3}  avg returned {:.1}  avg ctx {:.0}B",
            a.mode,
            report.k,
            a.mean_recall_at_k,
            report.k,
            a.mean_precision_at_k,
            a.avg_returned,
            a.avg_context_bytes
        );
    }
    Ok(())
}
