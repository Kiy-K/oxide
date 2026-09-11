//! CLI: index / query / search / status / watch / install / mcp / review / eval.

use crate::agents::{self, Action, Agent, Paths, Plan};
use crate::index::IndexOptions;
use crate::retrieval::read_snippet;
use crate::retrieval::{RetrievalMode, SearchMode};
use crate::service::{
    ErrorAction, Evidence, IndexResult, RepositoryService, SearchRequest, ServiceError,
    StatusResult,
};
use crate::storage::SqliteStore;
use crate::term::{duration, thousands, ColorChoice, Paint, StderrProgress};
use std::io::Write;

/// Hand-written because clap cannot group subcommands, and the grouping is
/// the point: a first-time reader should see "what do I run first" before
/// "what flags exist". Every public subcommand must appear here —
/// `tests/cli_help.rs` asserts it, so this cannot silently drift.
const TOP_HELP: &str = "\
OXIDE
Give coding agents the relevant code they need.

USAGE
  oxide <command> [options]

GET STARTED
  index       Index this repository
  query       Find code relevant to a question or task
  search      Search for code
  status      Check the index
  watch       Keep the index fresh

AGENTS
  install     Connect OXIDE to coding agents
  uninstall   Remove agent integrations
  mcp         Run the MCP server

WORKFLOWS
  review      Build context for a git diff

DEVELOPER
  eval        Run retrieval benchmarks

QUICK START
  oxide index
  oxide query \"Where is authentication handled?\"

OPTIONS
  --color <auto|always|never>   Color output (default: auto; honors NO_COLOR)

Run `oxide <command> --help` for one command's options, `oxide --version`
for the installed version.
";

/// An explicit, unparseable `--profile` fails loudly (matches how `--mode`
/// is validated); an unset flag falls through to `RetrievalMode::resolve`'s
/// env var / `Balanced`-default precedence.
fn resolve_profile(explicit: Option<&str>, json: bool) -> Result<RetrievalMode, CliError> {
    match explicit {
        Some(s) => RetrievalMode::parse(s).ok_or_else(|| {
            CliError::new(
                "invalid_configuration",
                ErrorAction::Stop,
                format!("unknown profile {s}; use fast|balanced|quality"),
                json,
            )
        }),
        None => Ok(RetrievalMode::resolve(None)),
    }
}

#[derive(clap::Parser)]
#[command(
    name = "oxide",
    about = "Give coding agents the relevant code they need.",
    // From CARGO_PKG_VERSION, so `oxide --version` and the crate version can
    // never disagree — the release workflow checks the packaged binary's
    // output against the tag, and that check is only meaningful if the
    // number comes from Cargo.toml rather than a second hand-kept copy.
    version,
    override_help = TOP_HELP,
    arg_required_else_help = true
)]
pub struct Args {
    #[command(subcommand)]
    pub cmd: Cmd,
    /// When to color output: auto (only on a terminal, and never when
    /// NO_COLOR is set), always, or never. Only human-readable output is
    /// ever colored; `--json` never is.
    #[arg(long, global = true, value_enum, default_value_t = ColorChoice::Auto, value_name = "WHEN")]
    pub color: ColorChoice,
}

#[derive(clap::Subcommand)]
pub enum Cmd {
    /// Index this repository, or update an existing index.
    ///
    /// With no flags this only touches what changed on disk, so it is safe
    /// and cheap to run as often as you like.
    Index {
        /// Repository path. Defaults to the current directory.
        path: Option<String>,
        /// Rebuild the whole index from scratch, even where nothing changed.
        /// Use this if the index looks wrong after an OXIDE upgrade.
        #[arg(short = 'a', long = "rebuild", alias = "all")]
        rebuild: bool,
        /// Embedding endpoint (OpenAI-compatible /v1/embeddings).
        /// Falls back to $OXIDE_EMBED_URL, then to the in-process model
        /// named by $OXIDE_EMBED_NATIVE (default: arctic-embed-xs-q, which
        /// downloads ~23MB of weights the first time it is loaded).
        /// OXIDE_EMBED_NATIVE=hashed selects the offline hashed embedder.
        #[arg(long, hide_short_help = true)]
        embedder: Option<String>,
        /// Rebuild only structural/graph relations, for every existing
        /// symbol rather than just symbols in files that changed.
        #[arg(short = 'g', long = "graph", hide_short_help = true)]
        graph: bool,
        /// Rebuild only embeddings, for every symbol regardless of whether
        /// its stored embedding is already current.
        #[arg(short = 'e', long = "embeddings", hide_short_help = true)]
        embeddings: bool,
        /// Emit JSON.
        #[arg(long)]
        json: bool,
    },
    /// Find the code relevant to a question or a coding task.
    ///
    /// OXIDE returns a bounded working set of real code — it does not
    /// answer the question itself.
    ///
    /// Examples:
    ///   oxide query "Where is authentication handled?"
    ///   oxide query "What code is relevant to fixing refresh-token validation?"
    #[command(alias = "context")]
    Query {
        /// The question or task, in plain language.
        #[arg(value_name = "TASK")]
        question: Option<String>,
        /// Repository path. Defaults to discovering from the current directory.
        #[arg(long)]
        path: Option<String>,
        /// Deprecated spelling of the positional TASK, kept so existing
        /// `oxide context --task ...` callers keep working.
        #[arg(short = 't', long = "task", hide = true, conflicts_with = "question")]
        task_flag: Option<String>,
        /// Token budget for the returned working set (estimate: chars/4).
        #[arg(long, default_value_t = 4096)]
        budget_tokens: usize,
        /// Relevance/latency tradeoff: fast|balanced|quality. Falls back to
        /// $OXIDE_RETRIEVAL_MODE, then balanced.
        #[arg(long, alias = "retrieval-mode")]
        profile: Option<String>,
        /// Emit JSON.
        #[arg(long)]
        json: bool,
    },
    /// Search for code by name, identifier, or phrase.
    ///
    /// Use `oxide query` instead when you have a question or a task rather
    /// than an expression to look up.
    Search {
        /// What to look for: a symbol name, an identifier, or a phrase.
        #[arg(value_name = "QUERY")]
        query: String,
        /// Repository path. Defaults to discovering from the current directory.
        #[arg(long)]
        path: Option<String>,
        /// Max results.
        #[arg(short, long, default_value_t = 10)]
        limit: usize,
        /// How to match: lexical|semantic|hybrid.
        #[arg(short, long, default_value = "hybrid")]
        mode: String,
        /// Relevance/latency tradeoff: fast|balanced|quality. Falls back to
        /// $OXIDE_RETRIEVAL_MODE, then balanced.
        #[arg(long, alias = "retrieval-mode")]
        profile: Option<String>,
        /// Disable structural expansion.
        #[arg(long, default_value_t = false, hide_short_help = true)]
        no_expand: bool,
        /// Emit JSON.
        #[arg(long)]
        json: bool,
    },
    /// Check whether this repository's index is present and current.
    Status {
        /// Repository path. Defaults to the current directory.
        path: Option<String>,
        /// Also show the embedder, pending work, and on-disk index details.
        #[arg(short, long)]
        verbose: bool,
        /// Emit JSON.
        #[arg(long)]
        json: bool,
    },
    /// Keep the index fresh: reconcile, then follow filesystem changes
    /// until stopped. Prefer `oxide index` for one-shot or CI use.
    Watch {
        /// Repository path. Defaults to the current directory.
        path: Option<String>,
        /// Embedding endpoint (OpenAI-compatible /v1/embeddings).
        /// Falls back to $OXIDE_EMBED_URL, then to the in-process model
        /// named by $OXIDE_EMBED_NATIVE.
        #[arg(long, hide_short_help = true)]
        embedder: Option<String>,
    },
    /// Connect OXIDE to the coding agents installed on this machine.
    ///
    /// Detection is never permission: nothing is written until you have
    /// seen and confirmed the exact change.
    Install {
        /// Agent to configure: claude|codex|opencode|antigravity|all.
        /// Repeatable. Without this, OXIDE asks which of the detected
        /// agents to configure.
        #[arg(long, value_name = "NAME")]
        agent: Vec<String>,
        /// Show the exact changes and exit without writing anything.
        #[arg(long)]
        dry_run: bool,
        /// Skip the confirmation prompt.
        #[arg(short, long)]
        yes: bool,
    },
    /// Remove OXIDE's MCP entry from coding agents.
    ///
    /// Only OXIDE's own entry is removed; the rest of each agent's
    /// configuration is left exactly as it was.
    Uninstall {
        /// Agent to clean up: claude|codex|opencode|antigravity|all.
        #[arg(long, value_name = "NAME")]
        agent: Vec<String>,
        /// Show the exact changes and exit without writing anything.
        #[arg(long)]
        dry_run: bool,
        /// Skip the confirmation prompt.
        #[arg(short, long)]
        yes: bool,
    },
    /// Run the stdio MCP server. Coding agents launch this; you normally
    /// do not run it by hand — see `oxide install`.
    Mcp,
    /// Build review context for a git diff.
    Review {
        /// Repository path. Defaults to discovering from the current directory.
        #[arg(long)]
        path: Option<String>,
        /// Diff range (commit, A..B). Empty means worktree vs HEAD.
        #[arg(long, default_value = "HEAD~1")]
        diff: String,
        /// Emit JSON.
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
    /// Deprecated: folded into `oxide status --verbose`.
    #[command(hide = true)]
    Stats {
        /// Repository path. Defaults to discovering from the current directory.
        path: Option<String>,
    },
}

#[derive(Debug)]
pub struct CliError {
    pub code: String,
    pub action: ErrorAction,
    pub message: String,
    pub json: bool,
}

impl CliError {
    fn new(
        code: impl Into<String>,
        action: ErrorAction,
        message: impl Into<String>,
        json: bool,
    ) -> Self {
        Self {
            code: code.into(),
            action,
            message: message.into(),
            json,
        }
    }

    fn service(error: ServiceError, json: bool) -> Self {
        Self::new(error.code(), error.action(), error.message(), json)
    }

    /// Malformed CLI invocation or a wire-serialization failure: neither maps
    /// to an `ErrorCode`, so there is nothing retryable to hint at beyond
    /// fixing the input.
    fn generic(error: impl std::fmt::Display, json: bool) -> Self {
        Self::new("command_failed", ErrorAction::Stop, error.to_string(), json)
    }
}

impl std::fmt::Display for CliError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.message)
    }
}

impl std::error::Error for CliError {}

/// What a human should see for a failure, and what to run next.
///
/// The machine-readable side is untouched: `code`, `action`, and the
/// service `message` still go out verbatim under `--json`. This only
/// rewrites the terminal rendering, where a sentence plus a command beats a
/// diagnostic string with a path and a backticked hint embedded in it.
pub fn render_human_error(error: &CliError, paint: &Paint) -> String {
    let (headline, next) = match error.code.as_str() {
        "index_missing" => (
            "No OXIDE index was found for this repository.",
            Some("oxide index"),
        ),
        "index_empty" => (
            "The OXIDE index has no searchable symbols yet.",
            Some("oxide index"),
        ),
        "index_stale" => (
            "The OXIDE index is out of date for this repository.",
            Some("oxide index"),
        ),
        "provider_mismatch" => (
            "The index was built with a different embedding provider, so its \
             vectors cannot be compared with this one's.",
            Some("oxide index"),
        ),
        "index_incompatible" | "index_unreadable" => (
            "The index cannot be used by this build of OXIDE.",
            Some("rm -rf .oxide && oxide index"),
        ),
        // Discovery walks up for `.git`/`.oxide` and finds neither. An
        // explicit path skips discovery entirely, so the way out is to name
        // the directory once — after which `.oxide` makes it discoverable.
        "repository_not_found" if error.message.starts_with("not inside a repository") => (
            "This directory is not inside a git repository or an existing OXIDE index.",
            Some("oxide index ."),
        ),
        "no_source_files" => (
            "No supported source files were found here. OXIDE indexes Python, \
             TypeScript/TSX, Rust, and Go.",
            None,
        ),
        "embedder_unavailable" => (
            "The embedding provider could not be reached, so semantic search \
             is unavailable.",
            Some("oxide search <query> --mode lexical"),
        ),
        // Everything else already says the useful thing (which path, which
        // flag) in its own message; inventing a headline would lose that.
        _ => (error.message.as_str(), error.action.next_command()),
    };
    match next {
        Some(command) => format!("{headline}\n\nRun:\n  {}", paint.bold(command)),
        None => headline.to_string(),
    }
}

impl ErrorAction {
    /// The command that resolves this class of failure, when there is one.
    fn next_command(&self) -> Option<&'static str> {
        match self {
            Self::Index => Some("oxide index"),
            Self::Repair => Some("rm -rf .oxide && oxide index"),
            Self::Retry | Self::FallBack | Self::Stop => None,
        }
    }
}

pub fn run(args: Args) -> Result<(), CliError> {
    let color = args.color;
    let paint = Paint::for_stdout(color);
    match args.cmd {
        Cmd::Index {
            path,
            embedder,
            rebuild,
            graph,
            embeddings,
            json,
        } => {
            let opts = if rebuild {
                IndexOptions::all()
            } else {
                IndexOptions {
                    force_reparse: false,
                    force_graph: graph,
                    force_embeddings: embeddings,
                }
            };
            cmd_index(path.as_deref(), embedder.as_deref(), &opts, json, color)
        }
        Cmd::Status {
            path,
            verbose,
            json,
        } => cmd_status(path.as_deref(), verbose, json, paint),
        Cmd::Search {
            query,
            path,
            limit,
            mode,
            no_expand,
            profile,
            json,
        } => {
            let mode = match mode.as_str() {
                "lexical" => SearchMode::LexicalOnly,
                "semantic" | "vector" => SearchMode::VectorOnly,
                "hybrid" => SearchMode::Hybrid,
                other => {
                    return Err(CliError::new(
                        "invalid_configuration",
                        ErrorAction::Stop,
                        format!("unknown mode {other}; use lexical|semantic|hybrid"),
                        json,
                    ))
                }
            };
            cmd_search(
                path.as_deref(),
                &query,
                SearchRequest {
                    limit,
                    mode,
                    expand: !no_expand,
                    retrieval_mode: resolve_profile(profile.as_deref(), json)?,
                },
                json,
                paint,
            )
        }
        Cmd::Review { path, diff, json } => cmd_review(path.as_deref(), &diff, json, paint),
        Cmd::Stats { path } => cmd_stats(path.as_deref()),
        Cmd::Query {
            path,
            question,
            task_flag,
            budget_tokens,
            profile,
            json,
        } => {
            let task = question.or(task_flag).ok_or_else(|| {
                CliError::new(
                    "invalid_configuration",
                    ErrorAction::Stop,
                    "missing the question to answer\n\nRun:\n  oxide query \"Where is authentication handled?\"",
                    json,
                )
            })?;
            cmd_query(
                path.as_deref(),
                &task,
                budget_tokens,
                resolve_profile(profile.as_deref(), json)?,
                json,
                paint,
            )
        }
        Cmd::Mcp => run_mcp(),
        Cmd::Eval { config, json } => {
            crate::eval::cmd_eval(&config, json).map_err(|e| CliError::generic(e, json))
        }
        Cmd::Watch { path, embedder } => cmd_watch(path.as_deref(), embedder.as_deref(), paint),
        Cmd::Install {
            agent,
            dry_run,
            yes,
        } => cmd_agents(&agent, dry_run, yes, true, paint),
        Cmd::Uninstall {
            agent,
            dry_run,
            yes,
        } => cmd_agents(&agent, dry_run, yes, false, paint),
    }
}

fn print_index_summary(root: &std::path::Path, result: &IndexResult, p: &Paint) {
    let touched = result.changed_files
        + result.removed_files
        + result.embedded_symbols
        + result.relations_refreshed_symbols;
    let (headline, note) = if touched == 0 {
        (
            "Index current",
            format!("(nothing changed, {})", duration(result.duration_ms)),
        )
    } else {
        ("Indexed", format!("in {}", duration(result.duration_ms)))
    };
    println!(
        "{} {} {} {}",
        p.check(),
        p.bold(headline),
        p.bold(&root.display().to_string()),
        p.dim(&note)
    );
    println!(
        "  {}",
        p.dim(&format!(
            "{} files · {} reparsed · {} unchanged · {} removed",
            thousands(result.scanned_files),
            thousands(result.changed_files),
            thousands(result.reused_files),
            thousands(result.removed_files)
        ))
    );
    println!(
        "  {}",
        p.dim(&format!(
            "{} symbols new · {} changed · {} deleted",
            thousands(result.new_symbols),
            thousands(result.changed_symbols),
            thousands(result.deleted_symbols)
        ))
    );
    let graph_note = if result.relations_refreshed_symbols > 0 {
        format!(
            " · graph refreshed for {} symbols",
            thousands(result.relations_refreshed_symbols)
        )
    } else {
        String::new()
    };
    println!(
        "  {}",
        p.dim(&format!(
            "{} embeddings written · {} reused{graph_note}",
            thousands(result.embedded_symbols),
            thousands(result.reused_embeddings)
        ))
    );
    if result.errored_files > 0 {
        println!(
            "{} {}",
            p.bang(),
            p.warn(&format!(
                "{} file(s) could not be read and were skipped",
                thousands(result.errored_files)
            ))
        );
    }
}

fn cmd_index(
    path: Option<&str>,
    embedder_url: Option<&str>,
    opts: &IndexOptions,
    json: bool,
    color: ColorChoice,
) -> Result<(), CliError> {
    let service = RepositoryService::discover(path).map_err(|e| CliError::service(e, json))?;
    let root = service.root().to_path_buf();
    // Stage progress goes to stderr (live on a terminal, one line per stage
    // otherwise) so stdout is only ever the summary. `--json` gets exactly
    // the result on stdout and nothing else (`tests/cli_e2e.rs` asserts
    // stderr stays empty).
    let progress = (!json).then(|| StderrProgress::new(color));
    let result = match &progress {
        Some(sink) => service.index_staged(embedder_url, opts, sink),
        None => service.index(embedder_url, opts),
    }
    .map_err(|e| CliError::service(e, json))?;
    drop(progress);
    if json {
        println!(
            "{}",
            serde_json::to_string_pretty(&result).map_err(|e| CliError::generic(e, true))?
        );
    } else {
        print_index_summary(&root, &result, &Paint::for_stdout(color));
    }
    Ok(())
}

fn cmd_watch(path: Option<&str>, embedder_url: Option<&str>, p: Paint) -> Result<(), CliError> {
    let json = false; // `oxide watch` is an interactive/long-running command, no --json mode.
    let service = RepositoryService::discover(path).map_err(|e| CliError::service(e, json))?;
    let root = service.root().to_path_buf();

    // `watcher::run` registers the filesystem watch before reconciling, so
    // no edit in this command's startup window can be missed.
    println!(
        "{} Reconciling {} before watching...",
        p.dot(),
        p.bold(&root.display().to_string())
    );

    let lock = crate::watcher::WatchLock::acquire(&root).map_err(|e| CliError::generic(e, json))?;
    let embedder =
        crate::embeddings::open_embedder(embedder_url).map_err(|e| CliError::generic(e, json))?;
    let mut store = SqliteStore::open(&root.join(".oxide").join("index.db"))
        .map_err(|e| CliError::generic(e, json))?;

    println!(
        "{} Watching {} {}",
        p.check(),
        p.bold(&root.display().to_string()),
        p.dim("(ctrl-c to stop)")
    );
    let stop = std::sync::atomic::AtomicBool::new(false);
    crate::watcher::run(
        &root,
        &mut store,
        embedder.as_ref(),
        &lock,
        &stop,
        |report| {
            if report.scanned_files > 0 {
                let marker = if report.errored_files > 0 {
                    p.bang()
                } else {
                    p.check()
                };
                println!(
                    "{marker} {} file(s) changed {}",
                    thousands(report.scanned_files),
                    p.dim(&format!(
                        "· {} reparsed · {} removed · {} errored · {} embedded · {} reused",
                        report.reparsed_files,
                        report.removed_files,
                        report.errored_files,
                        report.embedded_symbols,
                        report.reused_embeddings
                    ))
                );
            }
        },
    )
    .map_err(|e| CliError::generic(e, json))
}

fn cmd_status(path: Option<&str>, verbose: bool, json: bool, p: Paint) -> Result<(), CliError> {
    let service = RepositoryService::discover(path).map_err(|e| CliError::service(e, json))?;
    let status = service.status().map_err(|e| CliError::service(e, json))?;
    if json {
        println!(
            "{}",
            serde_json::to_string_pretty(&status).map_err(|e| CliError::generic(e, true))?
        );
    } else {
        render_status(&status, verbose, &p);
    }
    Ok(())
}

fn language_label(language: crate::symbols::Language) -> &'static str {
    use crate::symbols::Language::*;
    match language {
        Python => "Python",
        TypeScript => "TypeScript",
        Tsx => "TSX",
        Rust => "Rust",
        Go => "Go",
    }
}

fn render_status(status: &StatusResult, verbose: bool, p: &Paint) {
    // Headline: the one fact a user came for, with the marker doubling as
    // the word so the state survives without color.
    let (marker, state) = if !status.index_exists {
        (p.cross(), p.err("Index not found"))
    } else if status.is_current {
        (p.check(), p.ok("Index current"))
    } else {
        (p.bang(), p.warn("Index stale"))
    };
    println!("{marker} {}", p.bold(&state));
    println!("  {}", p.dim(&status.root));
    if status.index_exists {
        println!(
            "  {}",
            p.dim(&format!(
                "{} files · {} symbols",
                thousands(status.files),
                thousands(status.symbols)
            ))
        );
    }

    let (semantic_marker, semantic) = if !status.index_exists {
        (p.dot(), p.dim("Semantic search not indexed"))
    } else if !status.embedder_current {
        (
            p.bang(),
            p.warn("Semantic search built with a different embedding provider"),
        )
    } else if status.pending_embeddings > 0 {
        (
            p.bang(),
            p.warn(&format!(
                "Semantic search {} symbols pending",
                thousands(status.pending_embeddings)
            )),
        )
    } else if !status.base_fresh {
        // Every stored vector is current for what is stored — but files
        // have changed since, so "ready" would overstate it.
        (
            p.bang(),
            p.warn("Semantic search ready for the indexed content only"),
        )
    } else {
        (p.check(), p.ok("Semantic search ready"))
    };
    println!("{semantic_marker} {semantic}");
    // Every language this *build* extracts, not the ones present in this
    // repo (the index does not track that) — labelled for what it reports.
    println!(
        "{} {}",
        p.dot(),
        p.dim(&format!(
            "Supports {}",
            status
                .supported_languages
                .iter()
                .map(|l| language_label(*l))
                .collect::<Vec<_>>()
                .join(" · ")
        ))
    );

    if verbose {
        println!();
        // Pad before styling: a width applied to an escaped string counts
        // the escape bytes and misaligns the column on a terminal.
        let row =
            |label: &str, value: &str| println!("  {}{value}", p.dim(&format!("{label:<12}")));
        row(
            "Embedder",
            status.embedder.as_deref().unwrap_or("not indexed"),
        );
        row(
            "Embeddings",
            &format!(
                "{} stored · {} pending",
                thousands(status.embeddings),
                thousands(status.pending_embeddings)
            ),
        );
        row(
            "Files",
            if status.base_fresh {
                "all indexed content matches disk"
            } else {
                "some indexed content is out of date"
            },
        );
        let index_path = std::path::Path::new(&status.root)
            .join(".oxide")
            .join("index.db");
        let size = std::fs::metadata(&index_path)
            .map(|m| format!("{:.1} MB", m.len() as f64 / 1_048_576.0))
            .unwrap_or_else(|_| "absent".to_string());
        row("Index file", &format!("{} ({size})", index_path.display()));
        row("Schema", &status.schema_version.to_string());
    }

    if !status.index_exists || !status.is_current {
        println!("\nRun:\n  {}", p.bold("oxide index"));
    }
}

fn cmd_search(
    path: Option<&str>,
    query: &str,
    request: SearchRequest,
    json: bool,
    p: Paint,
) -> Result<(), CliError> {
    let service = RepositoryService::discover(path).map_err(|e| CliError::service(e, json))?;
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
            println!("{}", render_evidence(hit, &p));
            println!();
        }
        if hits.is_empty() {
            eprintln!("no results");
        }
    }
    Ok(())
}

/// `a←b` → `a ← b`, `lexical=2.6` → `lexical`: the scores and arrows stay
/// verbatim in `--json`; a person only needs the kind of evidence.
fn describe_reasons(reasons: &[String]) -> String {
    let mut out: Vec<String> = Vec::with_capacity(reasons.len());
    for reason in reasons {
        let human = if let Some((rel, seed)) = reason.split_once('←') {
            let seed = short_symbol_id(seed);
            match rel {
                "test" => format!("tests {seed}"),
                "uses" => format!("used by {seed}"),
                "imported-definition" => format!("imported by {seed}"),
                "caller" | "ast-grep-caller" => format!("calls {seed}"),
                "parent" => format!("parent of {seed}"),
                "child" => format!("child of {seed}"),
                "sibling" => format!("sibling of {seed}"),
                other => format!("{other} ← {seed}"),
            }
        } else {
            reason
                .split_once('=')
                .map_or(reason.as_str(), |(k, _)| k)
                .to_string()
        };
        if !out.contains(&human) {
            out.push(human);
        }
    }
    out.join(", ")
}

fn render_evidence(hit: &Evidence, p: &Paint) -> String {
    let location = format!("{}:{}–{}", hit.file, hit.start_line, hit.end_line);
    format!(
        "{}  {}  {}\n  {}\n{}",
        p.bold(&location),
        p.accent(&hit.qualified_name),
        p.dim(&hit.kind.to_string()),
        p.dim(&format!(
            "{} · score {:.4}",
            describe_reasons(&hit.reasons),
            hit.score
        )),
        hit.snippet
            .lines()
            .map(|line| format!("  {} {line}", p.dim("│")))
            .collect::<Vec<_>>()
            .join("\n")
    )
}

fn cmd_review(path: Option<&str>, diff: &str, json: bool, p: Paint) -> Result<(), CliError> {
    let service = RepositoryService::discover(path).map_err(|e| CliError::service(e, json))?;
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
            "{} {} {}",
            p.bold("Review context for"),
            p.bold(&service.root().display().to_string()),
            p.dim(&format!("({})", ctx.range))
        );
        println!(
            "{}",
            p.dim(&format!("changed files: {}", ctx.changed_files.join(", ")))
        );
        for c in &ctx.changed_symbols {
            println!(
                "\n{} {} {} {}",
                p.warn("● changed"),
                p.accent(&c.symbol.qualified_name),
                p.dim(&c.symbol.kind.to_string()),
                p.dim(&format!(
                    "{}:{}–{} (+{})",
                    c.symbol.file, c.symbol.start_line, c.symbol.end_line, c.added_lines
                ))
            );
        }
        for r in &ctx.related {
            println!(
                "\n{} {} {} {}\n  {}\n{}",
                p.dim("◇ related"),
                p.accent(&r.symbol.qualified_name),
                p.dim(&r.symbol.kind.to_string()),
                p.dim(&format!(
                    "{}:{}–{}",
                    r.symbol.file, r.symbol.start_line, r.symbol.end_line
                )),
                p.dim(&describe_reasons(&r.reasons)),
                read_snippet(
                    &service.root().join(&r.symbol.file),
                    r.symbol.start_line,
                    r.symbol.end_line,
                    16
                )
                .lines()
                .map(|line| format!("  {} {line}", p.dim("│")))
                .collect::<Vec<_>>()
                .join("\n")
            );
        }
    }
    Ok(())
}

/// Build a tokio runtime only for the `mcp` subcommand, so every other CLI
/// command stays fully synchronous and pays no async runtime startup cost.
fn run_mcp() -> Result<(), CliError> {
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .map_err(|e| CliError::generic(e, false))?;
    runtime
        .block_on(crate::mcp::serve())
        .map_err(|e| CliError::generic(e, false))
}

fn cmd_stats(path: Option<&str>) -> Result<(), CliError> {
    let service = RepositoryService::discover(path).map_err(|e| CliError::service(e, false))?;
    let stats = service.stats().map_err(|e| CliError::service(e, false))?;
    println!("files:      {}", stats.files);
    println!("symbols:    {}", stats.symbols);
    println!("embeddings: {}", stats.embeddings);
    Ok(())
}

/// `path#Qualified.Name` -> `Qualified.Name`, and the module pseudo-symbol
/// `path#path:__module__` -> `path (module)`. The full id stays in `--json`;
/// this is only so the human "left out" list reads like symbol names.
fn short_symbol_id(id: &str) -> String {
    let name = id.split_once('#').map(|(_, rest)| rest).unwrap_or(id);
    match name.strip_suffix(":__module__") {
        Some(file) => format!("{file} (module)"),
        None => name.to_string(),
    }
}

fn cmd_query(
    path: Option<&str>,
    task: &str,
    budget_tokens: usize,
    retrieval_mode: RetrievalMode,
    json: bool,
    p: Paint,
) -> Result<(), CliError> {
    let service = RepositoryService::discover(path).map_err(|e| CliError::service(e, json))?;
    let pack = service
        .context(task, budget_tokens, retrieval_mode)
        .map_err(|e| CliError::service(e, json))?;
    if json {
        println!(
            "{}",
            serde_json::to_string_pretty(&pack).map_err(|e| CliError::generic(e, true))?
        );
        return Ok(());
    }
    println!(
        "{} {}\n",
        p.bold("Relevant code for"),
        p.bold(&format!("\"{}\"", pack.task))
    );
    if pack.items.is_empty() {
        println!(
            "Nothing matched. Try different words, or {}.",
            p.bold("oxide search <name>")
        );
        return Ok(());
    }
    // Grouped by file, files in order of their best-ranked item, items in
    // pack order within a file: a file heading is what the eye navigates
    // by, and the ranking still shows through the order of the headings.
    let name_width = pack
        .items
        .iter()
        .map(|item| item.evidence.qualified_name.chars().count())
        .max()
        .unwrap_or(0)
        .min(48);
    let mut files: Vec<&str> = Vec::new();
    for item in &pack.items {
        if !files.contains(&item.evidence.file.as_str()) {
            files.push(&item.evidence.file);
        }
    }
    for (i, file) in files.iter().enumerate() {
        if i > 0 {
            println!();
        }
        println!("{}", p.bold(file));
        for item in pack.items.iter().filter(|it| it.evidence.file == *file) {
            let ev = &item.evidence;
            let role = match item.role {
                crate::context::Role::Primary => String::new(),
                crate::context::Role::Dependency => "  dependency".to_string(),
                crate::context::Role::Test => "  test".to_string(),
            };
            let name = short_symbol_id(&ev.qualified_name);
            let pad = name_width.saturating_sub(name.chars().count());
            println!(
                "  {}{}  {}{}",
                p.accent(&name),
                " ".repeat(pad),
                p.dim(&format!(
                    "{}–{}  {}  ~{} tok",
                    ev.start_line,
                    ev.end_line,
                    ev.kind,
                    thousands(item.est_tokens)
                )),
                p.dim(&role)
            );
            println!(
                "    {}",
                p.dim(&format!("↳ {}", describe_reasons(&ev.reasons)))
            );
        }
    }
    if !pack.omitted.is_empty() {
        println!("\n{}", p.bold("Left out of the budget"));
        let names: Vec<String> = pack
            .omitted
            .iter()
            .map(|o| short_symbol_id(&o.id))
            .collect();
        let width = names.iter().map(|n| n.chars().count()).max().unwrap_or(0);
        for (name, omitted) in names.iter().zip(&pack.omitted) {
            println!(
                "  {name}{}  {}",
                " ".repeat(width - name.chars().count()),
                p.dim(&omitted.why)
            );
        }
    }
    println!(
        "\n{}",
        p.dim(&format!(
            "{} items · {} / {} context tokens · embedder {}",
            pack.items.len(),
            thousands(pack.used_tokens),
            thousands(pack.budget_tokens),
            pack.embedder
        ))
    );
    Ok(())
}

// ------------------------------------------------------- agent integrations

fn cmd_agents(
    requested: &[String],
    dry_run: bool,
    yes: bool,
    install: bool,
    p: Paint,
) -> Result<(), CliError> {
    let json = false; // install/uninstall are interactive; no machine surface.
    let paths = Paths::from_env().map_err(|e| CliError::generic(e, json))?;
    let binary = std::env::current_exe().map_err(|e| {
        CliError::generic(format!("cannot resolve the oxide binary path: {e}"), json)
    })?;

    let selected = select_agents(requested, &paths, install, json, &p)?;
    if selected.is_empty() {
        return Ok(());
    }

    let plans: Vec<Plan> = selected
        .iter()
        .map(|agent| {
            if install {
                agents::plan_install(*agent, &paths, &binary)
            } else {
                agents::plan_uninstall(*agent, &paths)
            }
        })
        .collect();

    print_plans(&plans, &paths, install, &p);

    // A config OXIDE refuses to edit is a failure of the user's request,
    // not a quiet no-op: it must not exit 0 as if the agent were wired up.
    let blocked: Vec<String> = plans
        .iter()
        .filter_map(|p| match &p.action {
            Action::Blocked { reason } => Some(format!("{}: {reason}", p.agent.display())),
            _ => None,
        })
        .collect();

    if !plans.iter().any(|p| p.action.writes()) {
        if blocked.is_empty() {
            println!("\n{} Nothing to do.", p.check());
            return Ok(());
        }
        return Err(agent_failure(blocked, json));
    }
    if dry_run {
        println!("\n{} Dry run: nothing was written.", p.dot());
        return Ok(());
    }
    if !yes && !confirm("Proceed?")? {
        println!("Cancelled. Nothing was written.");
        return Ok(());
    }

    let mut failures = blocked;
    let mut changed = Vec::new();
    for plan in plans.iter().filter(|p| p.action.writes()) {
        match agents::apply(plan) {
            Ok(()) => changed.push(plan),
            Err(e) => failures.push(format!("{}: {e}", plan.agent.display())),
        }
    }

    if !changed.is_empty() {
        println!(
            "\n{} {}",
            p.check(),
            p.bold(if install {
                "Configured"
            } else {
                "Removed from"
            })
        );
        for plan in &changed {
            println!(
                "  {:<18}{}",
                plan.agent.display(),
                p.dim(&paths.shorten(&plan.config_path))
            );
        }
        let restart: Vec<&str> = changed
            .iter()
            .filter(|p| p.agent.needs_restart())
            .map(|p| p.agent.display())
            .collect();
        if !restart.is_empty() {
            println!("\nRestart {} to pick up the change.", join_and(&restart));
        }
        if install {
            println!("\nThen, in this repository:\n  {}", p.bold("oxide index"));
        }
    }

    if failures.is_empty() {
        Ok(())
    } else {
        Err(agent_failure(failures, json))
    }
}

fn agent_failure(failures: Vec<String>, json: bool) -> CliError {
    CliError::new(
        "agent_config_failed",
        ErrorAction::Stop,
        format!(
            "could not update {} agent configuration(s):\n  {}",
            failures.len(),
            failures.join("\n  ")
        ),
        json,
    )
}

fn join_and(items: &[&str]) -> String {
    match items {
        [] => String::new(),
        [one] => one.to_string(),
        [rest @ .., last] => format!("{} and {last}", rest.join(", ")),
    }
}

/// Resolve `--agent` flags, or ask. Detection alone never selects an agent:
/// with no flags and a terminal, OXIDE lists what it found and waits.
fn select_agents(
    requested: &[String],
    paths: &Paths,
    install: bool,
    json: bool,
    p: &Paint,
) -> Result<Vec<Agent>, CliError> {
    let detected: Vec<Agent> = agents::ALL
        .iter()
        .copied()
        .filter(|a| a.detected(paths))
        .collect();

    if !requested.is_empty() {
        if requested
            .iter()
            .any(|r| r.trim().eq_ignore_ascii_case("all"))
        {
            if detected.is_empty() {
                println!("No supported coding agents were detected on this machine.");
                return Ok(Vec::new());
            }
            return Ok(detected);
        }
        let mut out = Vec::new();
        for name in requested {
            let agent = Agent::parse(name).ok_or_else(|| {
                CliError::new(
                    "invalid_configuration",
                    ErrorAction::Stop,
                    format!(
                        "unknown agent {name}; use one of: {}, all",
                        agents::ALL
                            .iter()
                            .map(|a| a.id())
                            .collect::<Vec<_>>()
                            .join(", ")
                    ),
                    json,
                )
            })?;
            if !out.contains(&agent) {
                out.push(agent);
            }
        }
        return Ok(out);
    }

    print_detection(&detected, paths, p);
    if detected.is_empty() {
        println!(
            "\nSupported agents: {}.\nInstall one, or name it explicitly with --agent.",
            agents::ALL
                .iter()
                .map(|a| a.display())
                .collect::<Vec<_>>()
                .join(", ")
        );
        return Ok(Vec::new());
    }
    println!(
        "\nWhich agents should OXIDE {}?",
        if install {
            "integrate with"
        } else {
            "be removed from"
        }
    );
    print!("> ");
    let _ = std::io::stdout().flush();
    let Some(line) = read_answer(json)? else {
        return Err(CliError::new(
            "invalid_configuration",
            ErrorAction::Stop,
            format!(
                "no answer to read from stdin\n\nRun:\n  oxide {} --agent all --yes",
                if install { "install" } else { "uninstall" }
            ),
            json,
        ));
    };
    if line.trim().is_empty() {
        println!("Cancelled. Nothing was written.");
        return Ok(Vec::new());
    }
    parse_selection(&line, &detected)
        .map_err(|e| CliError::new("invalid_configuration", ErrorAction::Stop, e, json))
}

fn print_detection(detected: &[Agent], paths: &Paths, p: &Paint) {
    println!("{}\n", p.bold("Detected coding agents"));
    for (i, agent) in agents::ALL.iter().enumerate() {
        let state = if !detected.contains(agent) {
            p.dim("not detected")
        } else if agent.config_path(paths).exists() {
            format!("{} detected", p.check())
        } else {
            format!("{} detected {}", p.check(), p.dim("(no config file yet)"))
        };
        println!(
            "  {} {:<18}{state}",
            p.dim(&format!("[{}]", i + 1)),
            agent.display()
        );
    }
}

/// Accepts `1,2`, `1 3`, `all`, or agent names — the shapes people actually
/// type at a numbered list.
fn parse_selection(input: &str, offered: &[Agent]) -> Result<Vec<Agent>, String> {
    let mut out = Vec::new();
    for token in input
        .split([',', ' ', '\t', '\n'])
        .map(str::trim)
        .filter(|t| !t.is_empty())
    {
        if token.eq_ignore_ascii_case("all") {
            return Ok(offered.to_vec());
        }
        let agent = match token.parse::<usize>() {
            Ok(n) if n >= 1 && n <= agents::ALL.len() => agents::ALL[n - 1],
            Ok(n) => return Err(format!("{n} is not one of the listed agents")),
            Err(_) => Agent::parse(token).ok_or_else(|| format!("unknown agent {token}"))?,
        };
        if !out.contains(&agent) {
            out.push(agent);
        }
    }
    if out.is_empty() {
        return Err("nothing selected".to_string());
    }
    Ok(out)
}

fn print_plans(plans: &[Plan], paths: &Paths, install: bool, p: &Paint) {
    println!(
        "\n{}\n",
        p.bold(if install {
            "OXIDE will configure:"
        } else {
            "OXIDE will remove its MCP server from:"
        })
    );
    for plan in plans {
        println!("  {}", p.bold(plan.agent.display()));
        println!(
            "    {} {}",
            p.dim("config:"),
            paths.shorten(&plan.config_path)
        );
        if !plan.detected {
            println!(
                "    {}",
                p.warn("note:   this agent was not detected on this machine")
            );
        }
        match &plan.action {
            Action::Add => {
                println!(
                    "    {} MCP server \"{}\"",
                    p.dim("add:   "),
                    agents::SERVER_NAME
                );
                for line in plan.snippet.lines() {
                    println!("            {}", p.dim(line));
                }
            }
            Action::Update { was, now } => {
                println!(
                    "    {} MCP server \"{}\" (anything else in this entry is kept)",
                    p.dim("update:"),
                    agents::SERVER_NAME
                );
                // Either side can be multi-line, so neither is squeezed
                // onto the label's line.
                print_block("was", was, p);
                print_block("now", now, p);
            }
            Action::AlreadyConfigured => {
                println!("    {} already configured — no change", p.check());
            }
            Action::Remove => {
                println!(
                    "    {} MCP server \"{}\"",
                    p.dim("remove:"),
                    agents::SERVER_NAME
                );
            }
            Action::NothingToRemove => {
                println!("    {} no oxide entry present — no change", p.dot());
            }
            Action::Blocked { reason } => {
                println!("    {} {}", p.bang(), p.warn(&format!("skipped: {reason}")));
            }
        }
        println!();
    }
}

/// One line from stdin, or `None` at end of input.
///
/// Deliberately not gated on `stdin().is_terminal()`: a piped answer is a
/// real answer, and refusing to read one would make the confirmation step
/// untestable and unscriptable. What is refused is *silence* — reaching end
/// of input without an answer is an error, never an implied yes and never a
/// no-op that exits 0 as though the work had been done.
fn read_answer(json: bool) -> Result<Option<String>, CliError> {
    let mut line = String::new();
    let read = std::io::stdin()
        .read_line(&mut line)
        .map_err(|e| CliError::generic(e, json))?;
    if read == 0 {
        Ok(None)
    } else {
        Ok(Some(line))
    }
}

fn print_block(label: &str, body: &str, p: &Paint) {
    for (i, line) in body.lines().enumerate() {
        if i == 0 {
            println!("            {}: {}", p.dim(label), p.dim(line));
        } else {
            println!(
                "            {:width$}  {}",
                "",
                p.dim(line),
                width = label.len()
            );
        }
    }
}

fn confirm(question: &str) -> Result<bool, CliError> {
    print!("{question} [y/N] ");
    let _ = std::io::stdout().flush();
    let Some(line) = read_answer(false)? else {
        return Err(CliError::new(
            "invalid_configuration",
            ErrorAction::Stop,
            "no answer on stdin; re-run with --yes to accept the plan above, \
             or --dry-run to only preview it",
            false,
        ));
    };
    Ok(matches!(
        line.trim().to_ascii_lowercase().as_str(),
        "y" | "yes"
    ))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn selection_accepts_numbers_names_and_all() {
        let offered = vec![Agent::ClaudeCode, Agent::Codex];
        assert_eq!(
            parse_selection("1,2", &offered).unwrap(),
            vec![Agent::ClaudeCode, Agent::Codex]
        );
        assert_eq!(
            parse_selection(" codex ", &offered).unwrap(),
            vec![Agent::Codex]
        );
        assert_eq!(parse_selection("all", &offered).unwrap(), offered);
        assert_eq!(
            parse_selection("2 2", &offered).unwrap(),
            vec![Agent::Codex],
            "a repeated pick must not queue two writes to one config"
        );
        assert!(parse_selection("9", &offered).is_err());
        assert!(parse_selection("nope", &offered).is_err());
    }

    #[test]
    fn actionable_errors_say_what_to_run() {
        let err = CliError::new(
            "index_missing",
            ErrorAction::Index,
            "index missing at /x/.oxide/index.db; run `oxide index /x`",
            false,
        );
        let rendered = render_human_error(&err, &Paint::plain());
        assert!(rendered.starts_with("No OXIDE index was found"));
        assert!(rendered.ends_with("Run:\n  oxide index"));
    }

    #[test]
    fn unrecognized_errors_keep_their_own_message() {
        let err = CliError::new(
            "repository_not_found",
            ErrorAction::Stop,
            "no such path: /nope",
            false,
        );
        assert_eq!(
            render_human_error(&err, &Paint::plain()),
            "no such path: /nope"
        );
    }
}
