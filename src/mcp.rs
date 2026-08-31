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

use crate::retrieval::{RetrievalMode, SearchMode};
use crate::service::{RepositoryService, SearchRequest, ServiceError};
use rmcp::model::{
    CallToolResult, ContentBlock, Implementation, JsonObject, ServerCapabilities, ServerInfo,
};
use rmcp::{tool, tool_handler, tool_router, ErrorData as McpError, ServerHandler};
use serde_json::{json, Value};

const SERVER_INSTRUCTIONS: &str = "Use context for unfamiliar multi-file work; use search for focused follow-up discovery. Read source before editing. If evidence is incomplete, use normal repository tools. OXIDE output is a non-exhaustive lead, not authoritative; skip it for trivial known-file or literal edits.";
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

fn context_input_schema() -> JsonObject {
    object(json!({
        "type": "object",
        "properties": {
            "task": {"type": "string"},
            "path": {"type": "string"},
            "token_budget": {"type": "integer", "minimum": 0},
            "mode": {"type": "string", "enum": ["fast", "balanced", "quality"], "description": RETRIEVAL_MODE_DESCRIPTION},
        },
        "required": ["task"],
        "additionalProperties": false,
    }))
}

fn search_input_schema() -> JsonObject {
    object(json!({
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "path": {"type": "string"},
            "limit": {"type": "integer", "minimum": 0, "maximum": 100},
            "mode": {"type": "string", "enum": ["fast", "balanced", "quality"], "description": RETRIEVAL_MODE_DESCRIPTION},
        },
        "required": ["query"],
        "additionalProperties": false,
    }))
}

/// Parses the optional `mode` argument (fast|balanced|quality). Absent means
/// `RetrievalMode::resolve(None)` — the process's `$OXIDE_RETRIEVAL_MODE`, or
/// `Balanced` for a fully unconfigured agent. An explicit but unparseable
/// value fails loudly rather than silently falling back.
fn optional_retrieval_mode(arguments: &JsonObject) -> Result<RetrievalMode, McpError> {
    match optional_string(arguments, "mode")? {
        Some(s) => RetrievalMode::parse(s).ok_or_else(|| {
            McpError::invalid_params(format!("mode must be fast|balanced|quality, got {s}"), None)
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
        name = "context",
        description = "Build a bounded working set for an unfamiliar coding task.",
        input_schema = context_input_schema()
    )]
    async fn context(&self, arguments: JsonObject) -> Result<CallToolResult, McpError> {
        reject_unknown(&arguments, &["task", "path", "token_budget", "mode"])?;
        let task = required_string(&arguments, "task")?.to_string();
        let path = optional_string(&arguments, "path")?.map(str::to_string);
        let budget = optional_usize(&arguments, "token_budget")?.unwrap_or(DEFAULT_CONTEXT_BUDGET);
        let mode = optional_retrieval_mode(&arguments)?;
        run_blocking(move || {
            let service = match RepositoryService::discover(path.as_deref()) {
                Ok(service) => service,
                Err(error) => return Ok(service_error_result(error)),
            };
            match service.context(&task, budget, mode) {
                Ok(result) => tool_success(result),
                Err(error) => Ok(service_error_result(error)),
            }
        })
        .await
    }

    #[tool(
        name = "search",
        description = "Find repository code relevant to a focused implementation question.",
        input_schema = search_input_schema()
    )]
    async fn search(&self, arguments: JsonObject) -> Result<CallToolResult, McpError> {
        reject_unknown(&arguments, &["query", "path", "limit", "mode"])?;
        let query = required_string(&arguments, "query")?.to_string();
        let path = optional_string(&arguments, "path")?.map(str::to_string);
        let limit = optional_usize(&arguments, "limit")?.unwrap_or(DEFAULT_SEARCH_LIMIT);
        let retrieval_mode = optional_retrieval_mode(&arguments)?;
        run_blocking(move || {
            let service = match RepositoryService::discover(path.as_deref()) {
                Ok(service) => service,
                Err(error) => return Ok(service_error_result(error)),
            };
            let result = service.search(
                &query,
                SearchRequest {
                    limit,
                    mode: SearchMode::Hybrid,
                    expand: true,
                    retrieval_mode,
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
