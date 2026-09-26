use crate::term::ColorChoice;

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
  setup       Configure a remote embedding provider (optional; local is the default)

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
        /// Also pull in the bounded impact neighborhood of the top matches:
        /// direct callers, implementors and related tests. Stays inside the
        /// same token budget.
        #[arg(long)]
        blast_radius: bool,
        /// Also pull in the current diff's changed symbols, their
        /// callers/tests, and bounded co-change history as evidence — lower
        /// priority than direct/structural matches. No-op outside a git repo.
        #[arg(long)]
        git: bool,
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
        /// How to match: lexical|semantic|hybrid|literal. `literal` is a
        /// deterministic byte-substring scan over repository text (not just
        /// indexed source files) and needs no index; every other flag below
        /// except --limit and --json is ignored in that mode.
        #[arg(short, long, default_value = "hybrid")]
        mode: String,
        /// Relevance/latency tradeoff: fast|balanced|quality. Falls back to
        /// $OXIDE_RETRIEVAL_MODE, then balanced.
        #[arg(long, alias = "retrieval-mode")]
        profile: Option<String>,
        /// Disable structural expansion.
        #[arg(long, default_value_t = false, hide_short_help = true)]
        no_expand: bool,
        /// Also report the bounded impact neighborhood of the top matches:
        /// direct callers, implementors and related tests. Never changes
        /// which results are returned or how they rank.
        #[arg(long)]
        blast_radius: bool,
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
    /// Configure a remote embedding provider (Voyage, Jina, or a generic
    /// OpenAI-compatible endpoint), or show what's configured.
    ///
    /// Local embedding needs none of this and stays the default — this is
    /// only for opting into a remote provider for higher-quality embeddings
    /// or very large codebases. Source-code excerpts and queries leave this
    /// machine once a remote provider is enabled.
    Setup {
        /// Provider to configure: voyage|jina|openai-compatible. Prompts if omitted.
        #[arg(long)]
        provider: Option<String>,
        /// Read the API key from this environment variable instead of
        /// prompting — for scripted/CI setup.
        #[arg(long, value_name = "VAR")]
        api_key_env: Option<String>,
        /// Endpoint URL. Required for --provider openai-compatible; prompts if omitted there.
        #[arg(long)]
        base_url: Option<String>,
        /// Model name. Prompts if omitted (a sensible default is offered).
        #[arg(long)]
        model: Option<String>,
        /// Skip the privacy-warning confirmation.
        #[arg(short, long)]
        yes: bool,
        /// Print the current configuration (redacted) and exit.
        #[arg(long)]
        show: bool,
    },
}
