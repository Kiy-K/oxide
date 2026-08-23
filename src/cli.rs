//! CLI: index / search / review / stats / eval.

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
        /// Embedding endpoint (OpenAI-compatible /v1/embeddings).
        /// Falls back to $OXIDE_EMBED_URL, then the offline hashed embedder.
        #[arg(long)]
        embedder: Option<String>,
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
    /// Build a compact, ordered, budgeted context pack for a coding task.
    Context {
        /// Natural-language task description (also drives lexical retrieval).
        #[arg(short = 't', long)]
        task: String,
        /// Token budget for the pack (estimate: chars/4).
        #[arg(long, default_value_t = 4096)]
        budget_tokens: usize,
        #[arg(long)]
        json: bool,
    },
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
        Cmd::Index { path, embedder } => cmd_index(path.as_deref(), embedder.as_deref()),
        Cmd::Search {
            query,
            limit,
            mode,
            no_expand,
            json,
        } => {
            let m = match mode.as_str() {
                "lexical" => SearchMode::LexicalOnly,
                "semantic" | "vector" => SearchMode::VectorOnly,
                "hybrid" => SearchMode::Hybrid,
                other => anyhow::bail!("unknown mode {other}; use lexical|semantic|hybrid"),
            };
            cmd_search(&query, limit, m, !no_expand, json)
        }
        Cmd::Review { diff, json } => cmd_review(&diff, json),
        Cmd::Stats => cmd_stats(),
        Cmd::Context {
            task,
            budget_tokens,
            json,
        } => cmd_context(&task, budget_tokens, json),
        Cmd::Eval { config, json } => crate::eval::cmd_eval(&config, json),
    }
}

fn cmd_index(path: Option<&str>, embedder_url: Option<&str>) -> anyhow::Result<()> {
    let root = find_repo_root(path)?;
    let mut store = open_index(&root)?;
    let embedder = crate::embeddings::open_embedder(embedder_url)?;
    let report = update_index(&root, &mut store, embedder.as_ref())?;
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

fn default_embedder() -> anyhow::Result<Box<dyn crate::embeddings::EmbeddingProvider>> {
    crate::embeddings::open_embedder(None)
}

fn render_hit(root: &Path, h: &crate::retrieval::SearchHit) -> String {
    let snippet = read_snippet(
        &root.join(&h.symbol.file),
        h.symbol.start_line,
        h.symbol.end_line,
        24,
    );
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

fn cmd_search(
    query: &str,
    limit: usize,
    mode: SearchMode,
    expand: bool,
    json: bool,
) -> anyhow::Result<()> {
    let (root, store) = load_engine()?;
    let embedder = default_embedder()?;
    let engine = RetrievalEngine::new(&store, embedder.as_ref());
    let opts = SearchOptions {
        limit,
        mode,
        expand,
    };
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
    let embedder = default_embedder()?;
    let ctx = crate::review::build_review_context(&root, &store, embedder.as_ref(), diff)?;
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
            read_snippet(
                &root.join(&r.symbol.file),
                r.symbol.start_line,
                r.symbol.end_line,
                16
            )
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

fn cmd_context(task: &str, budget_tokens: usize, json: bool) -> anyhow::Result<()> {
    use crate::context::{build_context, ContextOptions};
    let (root, store) = load_engine()?;
    let embedder = default_embedder()?;
    let opts = ContextOptions {
        budget_tokens,
        ..ContextOptions::default()
    };
    let pack = build_context(&root, &store, embedder.as_ref(), task, &opts)?;

    if json {
        println!("{}", serde_json::to_string_pretty(&pack)?);
        return Ok(());
    }
    println!(
        "context for: {}\nembedder: {}  |  budget {} tok, used {} tok, {} items\n",
        pack.task,
        pack.embedder,
        pack.budget_tokens,
        pack.used_tokens,
        pack.items.len()
    );
    for (i, item) in pack.items.iter().enumerate() {
        println!(
            "{:>2}. [{:?}] {} [{}] {}:{}-{}  (~{} tok)\n    why: {}",
            i + 1,
            item.role,
            item.symbol.qualified_name,
            item.symbol.kind,
            item.symbol.file,
            item.symbol.start_line,
            item.symbol.end_line,
            item.est_tokens,
            item.reasons.join("; ")
        );
    }
    if !pack.omitted.is_empty() {
        println!("\nomitted:");
        for o in &pack.omitted {
            println!("  - {}: {}", o.id, o.why);
        }
    }
    println!("\n{}", pack.tail_summary());
    Ok(())
}
