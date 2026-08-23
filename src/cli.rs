//! CLI: index / search / review / stats / eval.

use crate::embeddings::HashedEmbedder;
use crate::index::{update_index, SqliteStore};
use crate::retrieval::{read_snippet, RetrievalEngine, SearchMode, SearchOptions};
use std::path::{Path, PathBuf};

#[derive(clap::Parser)]
#[command(name = "oxide", about = "Local incremental code index and retrieval")]
pub struct Args {
    #[command(subcommand)]
    pub cmd: Cmd,
}

#[derive(clap::Subcommand)]
pub enum Cmd {
    /// Index (or incrementally update) a repository.
    Index {
        /// Repository path.
        path: Option<String>,
    },
    /// Search the index.
    Search {
        query: String,
        /// Max results.
        #[arg(short, long, default_value_t = 10)]
        limit: usize,
        /// Retrieval mode.
        #[arg(short, long, default_value = "hybrid")]
        mode: String,
        /// Disable structural expansion.
        #[arg(long, default_value_t = false)]
        no_expand: bool,
        /// Emit JSON.
        #[arg(long)]
        json: bool,
    },
    /// Assemble review context for a git diff.
    Review {
        /// Diff range (commit, A..B). Empty means worktree vs HEAD.
        #[arg(long, default_value = "HEAD~1")]
        diff: String,
        #[arg(long)]
        json: bool,
    },
    /// Show index statistics.
    Stats,
    /// Run the committed retrieval benchmark.
    Eval {
        #[arg(long, default_value = "fixtures/benchmark.json")]
        config: String,
        #[arg(long)]
        json: bool,
    },
}

fn find_repo_root(explicit: Option<&str>) -> anyhow::Result<PathBuf> {
    if let Some(p) = explicit {
        return Ok(PathBuf::from(p));
    }
    let mut cur = std::env::current_dir()?;
    loop {
        if cur.join(".git").exists() || cur.join(".oxide").exists() {
            return Ok(cur);
        }
        if !cur.pop() {
            anyhow::bail!("not inside a repository; pass a path");
        }
    }
}

fn open_index(root: &Path) -> anyhow::Result<SqliteStore> {
    SqliteStore::open(&root.join(".oxide").join("index.db"))
}

pub fn run(args: Args) -> anyhow::Result<()> {
    match args.cmd {
        Cmd::Index { path } => cmd_index(path.as_deref()),
        Cmd::Search { query, limit, mode, no_expand, json } => {
            let m = match mode.as_str() {
                "lexical" => SearchMode::LexicalOnly,
                "semantic" | "vector" => SearchMode::VectorOnly,
                "hybrid" => SearchMode::Hybrid,
                other => anyhow::bail!("unknown mode {other}; use lexical|semantic|hybrid"),
            };
            cmd_search(&query, limit, m, !no_expand, json)
        }
        Cmd::Review { diff, json } => cmd_review(&diff, json),
        Cmd::Stats {} => cmd_stats(),
        Cmd::Eval { config, json } => crate::eval::cmd_eval(&config, json),
    }
}

fn cmd_index(path: Option<&str>) -> anyhow::Result<()> {
    let root = find_repo_root(path)?;
    let mut store = open_index(&root)?;
    let embedder = HashedEmbedder::default();
    let report = update_index(&root, &mut store, &embedder)?;
    println!(
        "indexed {}: {} files scanned, {} unchanged, {} reparsed, {} removed",
        root.display(),
        report.scanned_files,
        report.unchanged_files,
        report.reparsed_files,
        report.removed_files
    );
    println!(
        "symbols: +{} new, ~{} changed, -{} deleted; embeddings: {} written, {} reused",
        report.new_symbols,
        report.changed_symbols,
        report.deleted_symbols,
        report.embedded_symbols,
        report.reused_embeddings
    );
    println!("took {}ms", report.duration_ms);
    Ok(())
}

fn load_engine() -> anyhow::Result<(PathBuf, SqliteStore)> {
    let root = find_repo_root(None)?;
    let store = open_index(&root)?;
    Ok((root, store))
}

fn render_hit(root: &Path, h: &crate::retrieval::SearchHit) -> String {
    let snippet = read_snippet(&root.join(&h.symbol.file), h.symbol.start_line, h.symbol.end_line, 24);
    format!(
        "{}:{}-{} [{}] {} {}\n  score {:.4}  why: {}\n{}",
        h.symbol.file,
        h.symbol.start_line,
        h.symbol.end_line,
        h.symbol.kind,
        h.symbol.qualified_name,
        if h.symbol.exported { "(exported)" } else { "" },
        h.score,
        h.reasons.join("; "),
        snippet
            .lines()
            .map(|l| format!("  │ {l}"))
            .collect::<Vec<_>>()
            .join("\n")
    )
}

fn cmd_search(query: &str, limit: usize, mode: SearchMode, expand: bool, json: bool) -> anyhow::Result<()> {
    let (root, store) = load_engine()?;
    let embedder = HashedEmbedder::default();
    let engine = RetrievalEngine::new(&store, &embedder);
    let opts = SearchOptions { limit, mode, expand };
    let hits = engine.search(query, &opts)?;
    if json {
        println!("{}", serde_json::to_string_pretty(&hits)?);
        return Ok(());
    }
    for h in &hits {
        println!("{}", render_hit(&root, h));
        println!();
    }
    if hits.is_empty() {
        eprintln!("no results");
    }
    Ok(())
}

fn cmd_review(diff: &str, json: bool) -> anyhow::Result<()> {
    let (root, store) = load_engine()?;
    let embedder = HashedEmbedder::default();
    let ctx = crate::review::build_review_context(&root, &store, &embedder, diff)?;
    if json {
        println!("{}", serde_json::to_string_pretty(&ctx)?);
        return Ok(());
    }
    println!("review context for {} ({})", root.display(), ctx.range);
    println!("changed files: {}", ctx.changed_files.join(", "));
    for c in &ctx.changed_symbols {
        println!(
            "\n● changed: {} [{}] {}:{}-{} (+{})",
            c.symbol.qualified_name,
            c.symbol.kind,
            c.symbol.file,
            c.symbol.start_line,
            c.symbol.end_line,
            c.added_lines
        );
    }
    for r in &ctx.related {
        println!(
            "\n◇ related: {} [{}] {}:{}-{}\n  why: {}\n{}",
            r.symbol.qualified_name,
            r.symbol.kind,
            r.symbol.file,
            r.symbol.start_line,
            r.symbol.end_line,
            r.reasons.join("; "),
            read_snippet(&root.join(&r.symbol.file), r.symbol.start_line, r.symbol.end_line, 16)
                .lines()
                .map(|l| format!("  │ {l}"))
                .collect::<Vec<_>>()
                .join("\n")
        );
    }
    Ok(())
}

fn cmd_stats() -> anyhow::Result<()> {
    let (_root, store) = load_engine()?;
    let stats = store.stats()?;
    println!("files:      {}", stats.files);
    println!("symbols:    {}", stats.symbols);
    println!("embeddings: {}", stats.embeddings);
    Ok(())
}
