use super::render::render_stats;
use super::{Args, CliError, Cmd};
use crate::index::IndexOptions;
use crate::retrieval::{RetrievalMode, SearchMode};
use crate::service::{ErrorAction, RepositoryService, SearchRequest};
use crate::term::Paint;

mod agents;
mod index;
mod mcp;
mod query;
mod review;
mod search;
mod setup;
mod status;
mod watch;

use agents::cmd_agents;
use index::cmd_index;
use mcp::run_mcp;
use query::{cmd_query, QueryFlags};
use review::cmd_review;
use search::{cmd_search, cmd_search_literal};
use setup::cmd_setup;
use status::cmd_status;
use watch::cmd_watch;

fn cmd_stats(path: Option<&str>) -> Result<(), CliError> {
    let service = RepositoryService::discover(path).map_err(|e| CliError::service(e, false))?;
    let stats = service.stats().map_err(|e| CliError::service(e, false))?;
    render_stats(&stats);
    Ok(())
}

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
pub(super) fn dispatch(args: Args) -> Result<(), CliError> {
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
            json,
            ..
        } if mode == "literal" => cmd_search_literal(path.as_deref(), &query, limit, json),
        Cmd::Search {
            query,
            path,
            limit,
            mode,
            no_expand,
            profile,
            blast_radius,
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
                        format!("unknown mode {other}; use lexical|semantic|hybrid|literal"),
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
                    blast_radius,
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
            blast_radius,
            git,
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
                QueryFlags {
                    budget_tokens,
                    retrieval_mode: resolve_profile(profile.as_deref(), json)?,
                    blast_radius,
                    git,
                },
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
        Cmd::Setup {
            provider,
            api_key_env,
            base_url,
            model,
            yes,
            show,
        } => cmd_setup(
            provider.as_deref(),
            api_key_env.as_deref(),
            base_url.as_deref(),
            model.as_deref(),
            yes,
            show,
            paint,
        ),
    }
}
