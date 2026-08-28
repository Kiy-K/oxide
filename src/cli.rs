//! CLI: index / status / search / review / stats / context / eval.

use crate::retrieval::read_snippet;
use crate::retrieval::SearchMode;
use crate::service::{Evidence, RepositoryService, SearchRequest, ServiceError, StatusResult};

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
        /// Emit JSON.
        #[arg(long)]
        json: bool,
    },
    /// Report repository/index freshness and serving state.
    Status {
        /// Repository path.
        path: Option<String>,
        /// Emit JSON.
        #[arg(long)]
        json: bool,
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

#[derive(Debug)]
pub struct CliError {
    pub code: String,
    pub message: String,
    pub json: bool,
}

impl CliError {
    fn new(code: impl Into<String>, message: impl Into<String>, json: bool) -> Self {
        Self {
            code: code.into(),
            message: message.into(),
            json,
        }
    }

    fn service(error: ServiceError, json: bool) -> Self {
        Self::new(error.code(), error.message(), json)
    }

    fn generic(error: impl std::fmt::Display, json: bool) -> Self {
        Self::new("command_failed", error.to_string(), json)
    }
}

impl std::fmt::Display for CliError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.message)
    }
}

impl std::error::Error for CliError {}

pub fn run(args: Args) -> Result<(), CliError> {
    match args.cmd {
        Cmd::Index {
            path,
            embedder,
            json,
        } => cmd_index(path.as_deref(), embedder.as_deref(), json),
        Cmd::Status { path, json } => cmd_status(path.as_deref(), json),
        Cmd::Search {
            query,
            limit,
            mode,
            no_expand,
            json,
        } => {
            let mode = match mode.as_str() {
                "lexical" => SearchMode::LexicalOnly,
                "semantic" | "vector" => SearchMode::VectorOnly,
                "hybrid" => SearchMode::Hybrid,
                other => {
                    return Err(CliError::new(
                        "invalid_configuration",
                        format!("unknown mode {other}; use lexical|semantic|hybrid"),
                        json,
                    ))
                }
            };
            cmd_search(
                &query,
                SearchRequest {
                    limit,
                    mode,
                    expand: !no_expand,
                },
                json,
            )
        }
        Cmd::Review { diff, json } => cmd_review(&diff, json),
        Cmd::Stats => cmd_stats(),
        Cmd::Context {
            task,
            budget_tokens,
            json,
        } => cmd_context(&task, budget_tokens, json),
        Cmd::Eval { config, json } => {
            crate::eval::cmd_eval(&config, json).map_err(|e| CliError::generic(e, json))
        }
    }
}

fn cmd_index(path: Option<&str>, embedder_url: Option<&str>, json: bool) -> Result<(), CliError> {
    let service = RepositoryService::discover(path).map_err(|e| CliError::service(e, json))?;
    let result = service
        .index(embedder_url)
        .map_err(|e| CliError::service(e, json))?;
    if json {
        println!(
            "{}",
            serde_json::to_string_pretty(&result).map_err(|e| CliError::generic(e, true))?
        );
    } else {
        println!(
            "indexed {}: {} files scanned, {} unchanged, {} reparsed, {} removed",
            service.root().display(),
            result.scanned_files,
            result.reused_files,
            result.changed_files,
            result.removed_files
        );
        println!(
            "symbols: +{} new, ~{} changed, -{} deleted; embeddings: {} written, {} reused",
            result.new_symbols,
            result.changed_symbols,
            result.deleted_symbols,
            result.embedded_symbols,
            result.reused_embeddings
        );
        println!("took {}ms", result.duration_ms);
    }
    Ok(())
}

fn cmd_status(path: Option<&str>, json: bool) -> Result<(), CliError> {
    let service = RepositoryService::discover(path).map_err(|e| CliError::service(e, json))?;
    let status = service.status().map_err(|e| CliError::service(e, json))?;
    if json {
        println!(
            "{}",
            serde_json::to_string_pretty(&status).map_err(|e| CliError::generic(e, true))?
        );
    } else {
        render_status(&status);
    }
    Ok(())
}

fn render_status(status: &StatusResult) {
    println!("repository: {}", status.root);
    println!(
        "index:      {} ({})",
        if status.index_exists {
            "present"
        } else {
            "missing"
        },
        if status.is_current {
            "current"
        } else {
            "stale"
        }
    );
    println!(
        "files:      {}  symbols: {}  embeddings: {}",
        status.files, status.symbols, status.embeddings
    );
    println!(
        "embedder:   {}",
        status.embedder.as_deref().unwrap_or("not indexed")
    );
    println!(
        "languages:  {}",
        status
            .supported_languages
            .iter()
            .map(crate::symbols::Language::as_str)
            .collect::<Vec<_>>()
            .join(", ")
    );
}

fn cmd_search(query: &str, request: SearchRequest, json: bool) -> Result<(), CliError> {
    let service = RepositoryService::discover(None).map_err(|e| CliError::service(e, json))?;
    let hits = service
        .search(query, request)
        .map_err(|e| CliError::service(e, json))?;
    if json {
        println!(
            "{}",
            serde_json::to_string_pretty(&hits).map_err(|e| CliError::generic(e, true))?
        );
    } else {
        for hit in &hits {
            println!("{}", render_evidence(hit));
            println!();
        }
        if hits.is_empty() {
            eprintln!("no results");
        }
    }
    Ok(())
}

fn render_evidence(hit: &Evidence) -> String {
    format!(
        "{}:{}-{} [{}] {}\n  score {:.4}  why: {}\n{}",
        hit.file,
        hit.start_line,
        hit.end_line,
        hit.kind,
        hit.qualified_name,
        hit.score,
        hit.reasons.join("; "),
        hit.snippet
            .lines()
            .map(|line| format!("  │ {line}"))
            .collect::<Vec<_>>()
            .join("\n")
    )
}

fn cmd_review(diff: &str, json: bool) -> Result<(), CliError> {
    let service = RepositoryService::discover(None).map_err(|e| CliError::service(e, json))?;
    let ctx = service
        .review(diff)
        .map_err(|e| CliError::service(e, json))?;
    if json {
        println!(
            "{}",
            serde_json::to_string_pretty(&ctx).map_err(|e| CliError::generic(e, true))?
        );
    } else {
        println!(
            "review context for {} ({})",
            service.root().display(),
            ctx.range
        );
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
                    &service.root().join(&r.symbol.file),
                    r.symbol.start_line,
                    r.symbol.end_line,
                    16
                )
                .lines()
                .map(|line| format!("  │ {line}"))
                .collect::<Vec<_>>()
                .join("\n")
            );
        }
    }
    Ok(())
}

fn cmd_stats() -> Result<(), CliError> {
    let service = RepositoryService::discover(None).map_err(|e| CliError::service(e, false))?;
    let stats = service.stats().map_err(|e| CliError::service(e, false))?;
    println!("files:      {}", stats.files);
    println!("symbols:    {}", stats.symbols);
    println!("embeddings: {}", stats.embeddings);
    Ok(())
}

fn cmd_context(task: &str, budget_tokens: usize, json: bool) -> Result<(), CliError> {
    let service = RepositoryService::discover(None).map_err(|e| CliError::service(e, json))?;
    let pack = service
        .context(task, budget_tokens)
        .map_err(|e| CliError::service(e, json))?;
    if json {
        println!(
            "{}",
            serde_json::to_string_pretty(&pack).map_err(|e| CliError::generic(e, true))?
        );
    } else {
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
                item.evidence.qualified_name,
                item.evidence.kind,
                item.evidence.file,
                item.evidence.start_line,
                item.evidence.end_line,
                item.est_tokens,
                item.evidence.reasons.join("; ")
            );
        }
        if !pack.omitted.is_empty() {
            println!("\nomitted:");
            for omitted in &pack.omitted {
                println!("  - {}: {}", omitted.id, omitted.why);
            }
        }
        println!(
            "\nused {} of {} token budget",
            pack.used_tokens, pack.budget_tokens
        );
    }
    Ok(())
}
