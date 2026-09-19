//! stdio MCP server for coding-agent context discovery.
//!
//! Protocol framing, lifecycle, and version negotiation are owned by `rmcp`.
//! This module owns only argument/result conversion between MCP tool calls
//! and [`crate::service::RepositoryService`]: it does not own repository
//! discovery, index validation, retrieval, context allocation, or embedding
//! behavior.
//!
//! Argument validation is hand-rolled on the raw [`JsonObject`] rather than
//! going through rmcp's `Parameters<T>` auto-extractor. rmcp's own extractor
//! is convenient but deliberately downgrades shape errors (wrong type,
//! unknown field) into `isError: true` tool results instead of JSON-RPC
//! errors (see `into_tool_argument_error` in rmcp's tool router) so an agent
//! can self-correct without a client that hides protocol errors. OXIDE's
//! contract is the opposite and predates this server: malformed arguments
//! are a JSON-RPC `-32602`, distinct from `RepositoryService` failures
//! (`isError: true` with structured `{code, action, message}`). Hand-rolling
//! keeps that distinction exact instead of depending on rmcp's internal
//! error-message prefix to *not* match.

use crate::literal;
use crate::retrieval::{RetrievalMode, SearchMode};
use crate::service::{RepositoryService, SearchRequest, ServiceError};
use rmcp::model::{
    CallToolResult, ContentBlock, Implementation, JsonObject, ServerCapabilities, ServerInfo,
};
use rmcp::{tool, tool_handler, tool_router, ErrorData as McpError, ServerHandler};
use serde_json::{json, Value};

const SERVER_INSTRUCTIONS: &str = "Use query for unfamiliar multi-file work; use search for focused follow-up discovery; use search with mode: \"literal\" for an exact known string (a path, error message, or identifier) rather than a name or phrase to rank. Read source before editing. If evidence is incomplete, use normal repository tools. OXIDE output is a non-exhaustive lead, not authoritative; skip it for trivial known-file edits.";
const DEFAULT_CONTEXT_BUDGET: usize = 4096;
const DEFAULT_SEARCH_LIMIT: usize = 10;

/// Serve MCP over stdio until the client disconnects (EOF on stdin).
pub async fn serve() -> anyhow::Result<()> {
    let service = rmcp::serve_server(OxideServer::new(), rmcp::transport::stdio()).await?;
    service.waiting().await?;
    Ok(())
}

#[derive(Clone, Default)]
pub struct OxideServer;

impl OxideServer {
    pub fn new() -> Self {
        Self
    }
}

const RETRIEVAL_MODE_DESCRIPTION: &str =
    "Relevance/latency tradeoff. Omit for balanced (the default for an unconfigured agent).";

/// Omitting the argument is the pre-feature behavior exactly: no lookup
/// runs and the serialized result carries no extra field, so a client that
/// predates this sees no change.
const BLAST_RADIUS_DESCRIPTION: &str = "Also report the bounded impact neighborhood of the top matches: direct callers, implementors and related tests. Off by default; never changes which results are returned or how they rank.";

const GIT_DESCRIPTION: &str = "Also pull in the current diff's changed symbols, their callers/tests, and bounded co-change history as evidence, plus a `git` field with changed files and recent commits. Off by default; lower priority than direct/structural evidence, and a no-op outside a git repository.";

/// Found empirically (agent-eval pilot, 2026-09): with no description, a
/// model guesses at `path`'s meaning and gets it wrong in two different
/// ways -- passing `""` (rejected: empty path) and passing a subdirectory
/// like `"cli"` expecting it to scope the search within an already-loaded
/// index (rejected: `index_missing`, since `path` is a repository ROOT to
/// discover an index *at*, not a filter within one). Both failures were
/// 100% of that pilot's malformed-argument calls. The fix is this
/// description, not new validation: `RepositoryService::discover` already
/// walks upward from cwd for `.git`/`.oxide`, so a normal agent invoked
/// inside the target repo never needs this argument at all.
const PATH_DESCRIPTION: &str = "Repository root containing the OXIDE index. Omit this to use the current repository -- that's correct for almost every call. Only pass it to target a different repository than the one you're running in. Never pass a file, an empty string, or a subdirectory that isn't itself an indexed repository root.";

fn query_input_schema() -> JsonObject {
    object(json!({
        "type": "object",
        "properties": {
            "task": {"type": "string"},
            "path": {"type": "string", "description": PATH_DESCRIPTION},
            "budget_tokens": {"type": "integer", "minimum": 0},
            "profile": {"type": "string", "enum": ["fast", "balanced", "quality"], "description": RETRIEVAL_MODE_DESCRIPTION},
            "blast_radius": {"type": "boolean", "description": BLAST_RADIUS_DESCRIPTION},
            "git": {"type": "boolean", "description": GIT_DESCRIPTION},
        },
        "required": ["task"],
        "additionalProperties": false,
    }))
}

const SEARCH_MODE_DESCRIPTION: &str = "Omit for the default ranked hybrid search (returns scored symbol evidence). Set to \"literal\" for a deterministic exact-substring scan across all repository text, not just indexed source files -- needs no index, and returns {hits, truncated} instead of ranked evidence. Use \"literal\" for a known-exact string (a path, error message, config value, identifier); omit it for a name or phrase you want ranked by relevance. Lexical/semantic/hybrid aren't separately selectable here (unlike the CLI's --mode) because they only change internal scoring weights and all return the same evidence shape -- \"literal\" is the one mode that changes capability (no index needed) and output shape, which is what earns it a place in this schema.";

fn search_input_schema() -> JsonObject {
    object(json!({
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "path": {"type": "string", "description": PATH_DESCRIPTION},
            // Greptile review: this ceiling is shared by both modes, so it
            // must be the higher of the two service-side caps
            // (`literal::MAX_RESULTS` = 200) rather than hybrid's own
            // `service::MAX_SEARCH_RESULTS` (100) alone -- otherwise a
            // `mode: "literal"` request for 101-200 results would be
            // rejected here even though the CLI's equivalent
            // `--mode literal --limit` has no such ceiling and the
            // service layer supports it. Hybrid mode silently clamps a
            // higher request down to its own 100 internally, exactly as
            // the CLI already does -- unchanged from before this fix.
            "limit": {"type": "integer", "minimum": 0, "maximum": literal::MAX_RESULTS},
            "profile": {"type": "string", "enum": ["fast", "balanced", "quality"], "description": RETRIEVAL_MODE_DESCRIPTION},
            "blast_radius": {"type": "boolean", "description": BLAST_RADIUS_DESCRIPTION},
            "mode": {"type": "string", "enum": ["literal"], "description": SEARCH_MODE_DESCRIPTION},
        },
        "required": ["query"],
        "additionalProperties": false,
    }))
}

/// Parses the optional `profile` argument (fast|balanced|quality) — the
/// CLI's `--profile`. Absent means `RetrievalMode::resolve(None)` — the
/// process's `$OXIDE_RETRIEVAL_MODE`, or `Balanced` for a fully
/// unconfigured agent. An explicit but unparseable value fails loudly
/// rather than silently falling back.
///
/// Not called `mode`: the CLI's `--mode` is `search`'s lexical|semantic|
/// hybrid|literal switch, and only one of those four values (`literal`) is
/// exposed as `search`'s own `mode` argument here — see
/// `SEARCH_MODE_DESCRIPTION` for why lexical/semantic/hybrid stay
/// unexposed while literal earned a place. `profile` and `mode` are
/// independent arguments on the same tool for that reason: one tunes
/// ranking, the other switches the operation entirely.
fn optional_retrieval_mode(arguments: &JsonObject) -> Result<RetrievalMode, McpError> {
    match optional_string(arguments, "profile")? {
        Some(s) => RetrievalMode::parse(s).ok_or_else(|| {
            McpError::invalid_params(
                format!("profile must be fast|balanced|quality, got {s}"),
                None,
            )
        }),
        None => Ok(RetrievalMode::resolve(None)),
    }
}

fn object(value: Value) -> JsonObject {
    match value {
        Value::Object(object) => object,
        _ => unreachable!("tool schemas are always JSON objects"),
    }
}

#[tool_router]
impl OxideServer {
    #[tool(
        name = "query",
        description = "Find the code relevant to a question or a coding task: a bounded, ranked working set (same as `oxide query`).",
        input_schema = query_input_schema()
    )]
    async fn query(&self, arguments: JsonObject) -> Result<CallToolResult, McpError> {
        reject_unknown(
            &arguments,
            &[
                "task",
                "path",
                "budget_tokens",
                "profile",
                "blast_radius",
                "git",
            ],
        )?;
        let task = required_string(&arguments, "task")?.to_string();
        let path = optional_string(&arguments, "path")?.map(str::to_string);
        let budget = optional_usize(&arguments, "budget_tokens")?.unwrap_or(DEFAULT_CONTEXT_BUDGET);
        let mode = optional_retrieval_mode(&arguments)?;
        let blast_radius = optional_bool(&arguments, "blast_radius")?.unwrap_or(false);
        let git = optional_bool(&arguments, "git")?.unwrap_or(false);
        run_blocking(move || {
            let service = match RepositoryService::discover(path.as_deref()) {
                Ok(service) => service.with_process_cache(),
                Err(error) => return Ok(service_error_result(error)),
            };
            match service.context(&task, budget, mode, blast_radius, git) {
                Ok(result) => tool_success(result),
                Err(error) => Ok(service_error_result(error)),
            }
        })
        .await
    }

    #[tool(
        name = "search",
        description = "Search for code by name, identifier, or phrase (same as `oxide search`).",
        input_schema = search_input_schema()
    )]
    async fn search(&self, arguments: JsonObject) -> Result<CallToolResult, McpError> {
        reject_unknown(
            &arguments,
            &["query", "path", "limit", "profile", "blast_radius", "mode"],
        )?;
        // `mode` gates capability, not just ranking: absent (the default)
        // runs the ranked hybrid path exactly as before this argument
        // existed; `"literal"` is the only other value this schema allows
        // (enforced by the enum, but checked again here since a client
        // that bypasses schema validation must still get a clean error,
        // not a silent fall-through to hybrid). See `SEARCH_MODE_DESCRIPTION`.
        let literal_mode = match optional_string(&arguments, "mode")? {
            None => false,
            Some("literal") => true,
            Some(other) => {
                return Err(McpError::invalid_params(
                    format!("mode must be \"literal\" if present, got {other:?}"),
                    None,
                ))
            }
        };
        let query = required_string(&arguments, "query")?.to_string();
        let path = optional_string(&arguments, "path")?.map(str::to_string);
        let limit = optional_usize(&arguments, "limit")?.unwrap_or(DEFAULT_SEARCH_LIMIT);
        if literal_mode {
            return run_blocking(move || {
                let service = match RepositoryService::discover(path.as_deref()) {
                    Ok(service) => service.with_process_cache(),
                    Err(error) => return Ok(service_error_result(error)),
                };
                match service.search_literal(&query, limit) {
                    Ok(result) => tool_success(result),
                    Err(error) => Ok(service_error_result(error)),
                }
            })
            .await;
        }
        let retrieval_mode = optional_retrieval_mode(&arguments)?;
        let blast_radius = optional_bool(&arguments, "blast_radius")?.unwrap_or(false);
        run_blocking(move || {
            let service = match RepositoryService::discover(path.as_deref()) {
                Ok(service) => service.with_process_cache(),
                Err(error) => return Ok(service_error_result(error)),
            };
            let result = service.search(
                &query,
                SearchRequest {
                    limit,
                    mode: SearchMode::Hybrid,
                    expand: true,
                    retrieval_mode,
                    blast_radius,
                },
            );
            match result {
                Ok(hits) => tool_success(hits),
                Err(error) => Ok(service_error_result(error)),
            }
        })
        .await
    }
}

#[tool_handler]
impl ServerHandler for OxideServer {
    fn get_info(&self) -> ServerInfo {
        ServerInfo::new(ServerCapabilities::builder().enable_tools().build())
            .with_server_info(Implementation::new("oxide", env!("CARGO_PKG_VERSION")))
            .with_instructions(SERVER_INSTRUCTIONS)
    }
}

/// Run blocking `RepositoryService`/SQLite/HTTP work off the async runtime's
/// worker thread. `f` itself never returns `Err`: `ServiceError` is folded
/// into `Ok(CallToolResult::structured_error(..))` inside the closure so it
/// stays an `isError: true` tool result rather than a JSON-RPC error. `Err`
/// here is reserved for genuinely internal failures (the blocking task
/// panicking or being cancelled).
async fn run_blocking<F>(f: F) -> Result<CallToolResult, McpError>
where
    F: FnOnce() -> Result<CallToolResult, McpError> + Send + 'static,
{
    match tokio::task::spawn_blocking(f).await {
        Ok(result) => result,
        Err(join_error) => Err(McpError::internal_error(join_error.to_string(), None)),
    }
}

fn reject_unknown(arguments: &JsonObject, allowed: &[&str]) -> Result<(), McpError> {
    if let Some(key) = arguments
        .keys()
        .find(|key| !allowed.iter().any(|allowed| allowed == key))
    {
        return Err(McpError::invalid_params(
            format!("unknown argument: {key}"),
            None,
        ));
    }
    Ok(())
}

fn required_string<'a>(arguments: &'a JsonObject, key: &str) -> Result<&'a str, McpError> {
    match arguments.get(key).and_then(Value::as_str) {
        Some(value) if !value.trim().is_empty() => Ok(value),
        Some(_) => Err(McpError::invalid_params(
            format!("{key} must not be empty"),
            None,
        )),
        None => Err(McpError::invalid_params(
            format!("{key} must be a string"),
            None,
        )),
    }
}

fn optional_string<'a>(arguments: &'a JsonObject, key: &str) -> Result<Option<&'a str>, McpError> {
    match arguments.get(key) {
        None => Ok(None),
        Some(Value::String(value)) if !value.trim().is_empty() => Ok(Some(value)),
        Some(Value::String(_)) => Err(McpError::invalid_params(
            format!("{key} must not be empty"),
            None,
        )),
        Some(_) => Err(McpError::invalid_params(
            format!("{key} must be a string"),
            None,
        )),
    }
}

fn optional_usize(arguments: &JsonObject, key: &str) -> Result<Option<usize>, McpError> {
    let Some(value) = arguments.get(key) else {
        return Ok(None);
    };
    let Some(value) = value.as_u64() else {
        return Err(McpError::invalid_params(
            format!("{key} must be a non-negative integer"),
            None,
        ));
    };
    let value = usize::try_from(value)
        .map_err(|_| McpError::invalid_params(format!("{key} is too large"), None))?;
    Ok(Some(value))
}

fn optional_bool(arguments: &JsonObject, key: &str) -> Result<Option<bool>, McpError> {
    match arguments.get(key) {
        None => Ok(None),
        Some(value) => value
            .as_bool()
            .map(Some)
            .ok_or_else(|| McpError::invalid_params(format!("{key} must be a boolean"), None)),
    }
}

fn tool_success(payload: impl serde::Serialize) -> Result<CallToolResult, McpError> {
    let text = serde_json::to_string(&payload)
        .map_err(|error| McpError::internal_error(error.to_string(), None))?;
    Ok(CallToolResult::success(vec![ContentBlock::text(text)]))
}

fn service_error_result(error: ServiceError) -> CallToolResult {
    let payload: Value = json!({
        "error": {
            "code": error.code(),
            "action": error.action().as_str(),
            "message": error.message(),
        }
    });
    CallToolResult::structured_error(payload)
}
