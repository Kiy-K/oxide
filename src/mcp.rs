//! Minimal stdio MCP transport for coding-agent context discovery.
//!
//! This module owns JSON-RPC/MCP framing and input validation only. Repository
//! discovery, index validation, retrieval, context allocation, and error
//! classification remain in [`crate::service::RepositoryService`].

use crate::retrieval::SearchMode;
use crate::service::{RepositoryService, SearchRequest, ServiceError};
use serde_json::{json, Map, Value};
use std::io::{self, BufRead, Write};

const PROTOCOL_VERSION: &str = "2024-11-05";
const SERVER_INSTRUCTIONS: &str = "Use context for unfamiliar multi-file work; use search for focused follow-up discovery. Read source before editing. If evidence is incomplete, use normal repository tools. OXIDE output is a non-exhaustive lead, not authoritative; skip it for trivial known-file or literal edits.";
const DEFAULT_CONTEXT_BUDGET: usize = 4096;
const DEFAULT_SEARCH_LIMIT: usize = 10;

/// Serve newline-delimited JSON-RPC requests on stdin and responses on stdout.
pub fn serve() -> anyhow::Result<()> {
    let stdin = io::stdin();
    let mut stdout = io::BufWriter::new(io::stdout().lock());
    for line in stdin.lock().lines() {
        let line = line?;
        if line.trim().is_empty() {
            continue;
        }
        if let Some(response) = handle_message(&line) {
            serde_json::to_writer(&mut stdout, &response)?;
            stdout.write_all(b"\n")?;
            stdout.flush()?;
        }
    }
    Ok(())
}

fn handle_message(line: &str) -> Option<Value> {
    let request: Value = match serde_json::from_str(line) {
        Ok(value) => value,
        Err(error) => return Some(json_rpc_error(Value::Null, -32700, error.to_string())),
    };
    let object = match request.as_object() {
        Some(object) => object,
        None => {
            return Some(json_rpc_error(
                Value::Null,
                -32600,
                "request must be an object",
            ))
        }
    };
    if object.get("jsonrpc").and_then(Value::as_str) != Some("2.0") {
        return Some(json_rpc_error(
            object.get("id").cloned().unwrap_or(Value::Null),
            -32600,
            "jsonrpc must be \"2.0\"",
        ));
    }
    let id = object.get("id").cloned().unwrap_or(Value::Null);
    let notification = !object.contains_key("id");
    let method = match object.get("method").and_then(Value::as_str) {
        Some(method) => method,
        None => {
            return (!notification)
                .then(|| json_rpc_error(id, -32600, "request method is required"))
        }
    };

    match method {
        "initialize" => Some(json_rpc_result(id, initialize_result())),
        "notifications/initialized" | "notifications/cancelled" => None,
        "tools/list" => Some(json_rpc_result(id, json!({"tools": tool_definitions()}))),
        "tools/call" => {
            if notification {
                return None;
            }
            let params = match object.get("params").and_then(Value::as_object) {
                Some(params) => params,
                None => {
                    return Some(json_rpc_error(
                        id,
                        -32602,
                        "tools/call params must be an object",
                    ))
                }
            };
            match call_tool(params) {
                Ok(result) => Some(json_rpc_result(id, result)),
                Err(CallError::Invalid(message)) => Some(json_rpc_error(id, -32602, message)),
                Err(CallError::Service(error)) => {
                    Some(json_rpc_result(id, service_error_result(error)))
                }
                Err(CallError::Internal(message)) => Some(json_rpc_error(id, -32603, message)),
            }
        }
        _ if notification => None,
        _ => Some(json_rpc_error(
            id,
            -32601,
            format!("method not found: {method}"),
        )),
    }
}

fn initialize_result() -> Value {
    json!({
        "protocolVersion": PROTOCOL_VERSION,
        "capabilities": {"tools": {}},
        "serverInfo": {"name": "oxide", "version": env!("CARGO_PKG_VERSION")},
        "instructions": SERVER_INSTRUCTIONS,
    })
}

fn tool_definitions() -> Vec<Value> {
    vec![
        json!({
            "name": "context",
            "description": "Build a bounded working set for an unfamiliar coding task.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "task": {"type": "string"},
                    "path": {"type": "string"},
                    "token_budget": {"type": "integer", "minimum": 0},
                },
                "required": ["task"],
                "additionalProperties": false,
            },
        }),
        json!({
            "name": "search",
            "description": "Find repository code relevant to a focused implementation question.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "path": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 0, "maximum": 100},
                },
                "required": ["query"],
                "additionalProperties": false,
            },
        }),
    ]
}

enum CallError {
    Invalid(String),
    Service(ServiceError),
    Internal(String),
}

fn call_tool(params: &Map<String, Value>) -> Result<Value, CallError> {
    let name = params
        .get("name")
        .and_then(Value::as_str)
        .ok_or_else(|| CallError::Invalid("tools/call name must be a string".into()))?;
    let arguments = match params.get("arguments") {
        None => Map::new(),
        Some(Value::Object(arguments)) => arguments.clone(),
        Some(_) => {
            return Err(CallError::Invalid(
                "tools/call arguments must be an object".into(),
            ))
        }
    };
    match name {
        "context" => context(arguments),
        "search" => search(arguments),
        _ => Err(CallError::Invalid(format!("unknown tool: {name}"))),
    }
}

fn reject_unknown(arguments: &Map<String, Value>, allowed: &[&str]) -> Result<(), CallError> {
    if let Some(key) = arguments
        .keys()
        .find(|key| !allowed.iter().any(|allowed| allowed == key))
    {
        return Err(CallError::Invalid(format!("unknown argument: {key}")));
    }
    Ok(())
}

fn context(arguments: Map<String, Value>) -> Result<Value, CallError> {
    reject_unknown(&arguments, &["task", "path", "token_budget"])?;
    let task = required_string(&arguments, "task")?;
    let path = optional_string(&arguments, "path")?;
    let budget = optional_usize(&arguments, "token_budget")?.unwrap_or(DEFAULT_CONTEXT_BUDGET);
    let service = RepositoryService::discover(path).map_err(CallError::Service)?;
    let result = service.context(task, budget).map_err(CallError::Service)?;
    tool_success(
        serde_json::to_value(result).map_err(|error| CallError::Internal(error.to_string()))?,
    )
}

fn search(arguments: Map<String, Value>) -> Result<Value, CallError> {
    reject_unknown(&arguments, &["query", "path", "limit"])?;
    let query = required_string(&arguments, "query")?;
    let path = optional_string(&arguments, "path")?;
    let limit = optional_usize(&arguments, "limit")?.unwrap_or(DEFAULT_SEARCH_LIMIT);
    let service = RepositoryService::discover(path).map_err(CallError::Service)?;
    let result = service
        .search(
            query,
            SearchRequest {
                limit,
                mode: SearchMode::Hybrid,
                expand: true,
            },
        )
        .map_err(CallError::Service)?;
    tool_success(
        serde_json::to_value(result).map_err(|error| CallError::Internal(error.to_string()))?,
    )
}

fn required_string<'a>(arguments: &'a Map<String, Value>, key: &str) -> Result<&'a str, CallError> {
    match arguments.get(key).and_then(Value::as_str) {
        Some(value) if !value.trim().is_empty() => Ok(value),
        Some(_) => Err(CallError::Invalid(format!("{key} must not be empty"))),
        None => Err(CallError::Invalid(format!("{key} must be a string"))),
    }
}

fn optional_string<'a>(
    arguments: &'a Map<String, Value>,
    key: &str,
) -> Result<Option<&'a str>, CallError> {
    match arguments.get(key) {
        None => Ok(None),
        Some(Value::String(value)) if !value.trim().is_empty() => Ok(Some(value)),
        Some(Value::String(_)) => Err(CallError::Invalid(format!("{key} must not be empty"))),
        Some(_) => Err(CallError::Invalid(format!("{key} must be a string"))),
    }
}

fn optional_usize(arguments: &Map<String, Value>, key: &str) -> Result<Option<usize>, CallError> {
    let Some(value) = arguments.get(key) else {
        return Ok(None);
    };
    let Some(value) = value.as_u64() else {
        return Err(CallError::Invalid(format!(
            "{key} must be a non-negative integer"
        )));
    };
    let value =
        usize::try_from(value).map_err(|_| CallError::Invalid(format!("{key} is too large")))?;
    Ok(Some(value))
}

fn tool_success(payload: Value) -> Result<Value, CallError> {
    let text =
        serde_json::to_string(&payload).map_err(|error| CallError::Internal(error.to_string()))?;
    Ok(json!({"content": [{"type": "text", "text": text}], "isError": false}))
}

fn service_error_result(error: ServiceError) -> Value {
    let payload = json!({
        "error": {
            "code": error.code(),
            "action": error.action().as_str(),
            "message": error.message(),
        }
    });
    let text = serde_json::to_string(&payload)
        .unwrap_or_else(|_| "{\"error\":{\"code\":\"service_error\"}}".into());
    json!({
        "content": [{"type": "text", "text": text}],
        "structuredContent": payload,
        "isError": true,
    })
}

fn json_rpc_result(id: Value, result: Value) -> Value {
    json!({"jsonrpc": "2.0", "id": id, "result": result})
}

fn json_rpc_error(id: Value, code: i64, message: impl Into<String>) -> Value {
    json!({"jsonrpc": "2.0", "id": id, "error": {"code": code, "message": message.into()}})
}
