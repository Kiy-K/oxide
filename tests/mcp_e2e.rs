use serde_json::{json, Value};
use std::io::{BufRead, BufReader, Write};
use std::path::Path;
use std::process::{Child, ChildStdin, ChildStdout, Command};

struct McpProcess {
    child: Child,
    input: ChildStdin,
    output: BufReader<ChildStdout>,
    /// The `initialize` response captured during the mandatory MCP lifecycle
    /// handshake every `start*` constructor performs before returning, so
    /// callers don't have to re-send it (rmcp, unlike the old hand-rolled
    /// server, enforces that `initialize` precedes any other request).
    init_result: Value,
}

impl McpProcess {
    fn start(root: &Path) -> Self {
        Self::spawn(root, None)
    }

    fn start_with_embedder_url(root: &Path, url: &str) -> Self {
        Self::spawn(root, Some(url))
    }

    fn spawn(root: &Path, embedder_url: Option<&str>) -> Self {
        let mut command = Command::new(env!("CARGO_BIN_EXE_oxide"));
        command
            .arg("mcp")
            .current_dir(root)
            .stdin(std::process::Stdio::piped())
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::piped());
        if let Some(url) = embedder_url {
            command.env("OXIDE_EMBED_URL", url);
        }
        let mut child = command.spawn().unwrap();
        let mut process = Self {
            input: child.stdin.take().unwrap(),
            output: BufReader::new(child.stdout.take().unwrap()),
            child,
            init_result: Value::Null,
        };
        process.init_result = process.request(json!({
            "jsonrpc": "2.0",
            "id": "handshake",
            "method": "initialize",
            "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "test", "version": "0"}}
        }));
        writeln!(
            process.input,
            "{}",
            json!({"jsonrpc": "2.0", "method": "notifications/initialized"})
        )
        .unwrap();
        process.input.flush().unwrap();
        process
    }

    fn request(&mut self, request: Value) -> Value {
        writeln!(self.input, "{request}").unwrap();
        self.input.flush().unwrap();
        let mut line = String::new();
        self.output.read_line(&mut line).unwrap();
        assert!(!line.is_empty(), "MCP server exited without a response");
        serde_json::from_str(&line).unwrap()
    }
}

impl Drop for McpProcess {
    fn drop(&mut self) {
        let _ = self.input.flush();
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

fn write(root: &Path, relative: &str, source: &str) {
    let path = root.join(relative);
    std::fs::create_dir_all(path.parent().unwrap()).unwrap();
    std::fs::write(path, source).unwrap();
}

fn index(root: &Path) {
    let output = Command::new(env!("CARGO_BIN_EXE_oxide"))
        .args(["index", "."])
        .current_dir(root)
        .output()
        .unwrap();
    assert!(
        output.status.success(),
        "index failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );
}

fn call(name: &str, arguments: Value) -> Value {
    json!({
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments}
    })
}

#[test]
fn initialize_and_list_expose_only_compact_agent_tools() {
    let root = tempfile::tempdir().unwrap();
    let mut server = McpProcess::start(root.path());

    assert_eq!(
        server.init_result["result"]["protocolVersion"],
        "2024-11-05"
    );
    assert!(
        server.init_result["result"]["instructions"]
            .as_str()
            .unwrap()
            .len()
            < 500
    );

    let listed = server.request(json!({
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/list",
        "params": {}
    }));
    let tools = listed["result"]["tools"].as_array().unwrap();
    let names: Vec<_> = tools
        .iter()
        .map(|tool| tool["name"].as_str().unwrap())
        .collect();
    assert_eq!(names, ["context", "search"]);
    assert!(tools
        .iter()
        .all(|tool| tool["inputSchema"]["additionalProperties"] == false));
    assert_eq!(tools[0]["inputSchema"]["required"], json!(["task"]));
    assert_eq!(tools[1]["inputSchema"]["required"], json!(["query"]));
}

#[test]
fn context_and_search_return_service_evidence_over_real_protocol() {
    let root = tempfile::tempdir().unwrap();
    write(
        root.path(),
        "src/auth.py",
        "def refresh_token(token):\n    return validate_refresh_token(token)\n\ndef validate_refresh_token(token):\n    return token\n",
    );
    index(root.path());
    let mut server = McpProcess::start(root.path());

    let context = server.request(call(
        "context",
        json!({"task": "fix refresh token validation", "path": ".", "token_budget": 128}),
    ));
    assert_eq!(context["id"], 2);
    assert_eq!(context["result"]["isError"], false);
    let context_payload: Value =
        serde_json::from_str(context["result"]["content"][0]["text"].as_str().unwrap()).unwrap();
    assert_eq!(context_payload["task"], "fix refresh token validation");
    assert!(context_payload["used_tokens"].as_u64().unwrap() <= 128);

    let search = server.request(call(
        "search",
        json!({"query": "refresh token", "path": ".", "limit": 3}),
    ));
    assert_eq!(search["result"]["isError"], false);
    let hits: Value =
        serde_json::from_str(search["result"]["content"][0]["text"].as_str().unwrap()).unwrap();
    assert!(hits.as_array().unwrap().len() <= 3);
    assert!(hits[0]["file"].is_string());
    assert!(hits[0]["snippet"].is_string());
}

#[test]
fn malformed_parameters_and_service_failures_preserve_structured_semantics() {
    let root = tempfile::tempdir().unwrap();
    let mut server = McpProcess::start(root.path());

    let malformed = server.request(call("context", json!({"task": 42})));
    assert_eq!(malformed["error"]["code"], -32602);

    let missing = server.request(call(
        "search",
        json!({"query": "anything", "path": root.path()}),
    ));
    assert_eq!(
        missing["result"]["structuredContent"]["error"]["action"],
        "index"
    );
    assert_eq!(missing["result"]["isError"], true);
    let payload: Value =
        serde_json::from_str(missing["result"]["content"][0]["text"].as_str().unwrap()).unwrap();
    assert_eq!(payload["error"]["code"], "index_missing");
    assert_eq!(payload["error"]["action"], "index");
    assert!(payload["error"]["message"].is_string());
}

#[test]
fn unavailable_embedder_preserves_fallback_action() {
    let root = tempfile::tempdir().unwrap();
    write(root.path(), "src/thing.py", "def thing():\n    return 1\n");
    index(root.path());
    let mut server = McpProcess::start_with_embedder_url(root.path(), "http://127.0.0.1:1");

    let response = server.request(call("context", json!({"task": "find thing", "path": "."})));
    let payload: Value =
        serde_json::from_str(response["result"]["content"][0]["text"].as_str().unwrap()).unwrap();
    assert_eq!(payload["error"]["code"], "embedder_unavailable");
    assert_eq!(payload["error"]["action"], "fall_back");
}

#[test]
fn repeated_reads_are_deterministic_and_empty_search_is_valid() {
    let root = tempfile::tempdir().unwrap();
    write(root.path(), "src/thing.py", "def thing():\n    return 1\n");
    index(root.path());
    let mut server = McpProcess::start(root.path());

    let first = server.request(call(
        "search",
        json!({"query": "thing", "path": ".", "limit": 10}),
    ));
    let second = server.request(call(
        "search",
        json!({"query": "thing", "path": ".", "limit": 10}),
    ));
    assert_eq!(
        first["result"]["content"][0]["text"],
        second["result"]["content"][0]["text"]
    );

    let empty = server.request(call(
        "search",
        json!({"query": "any query", "path": ".", "limit": 0}),
    ));
    let hits: Value =
        serde_json::from_str(empty["result"]["content"][0]["text"].as_str().unwrap()).unwrap();
    assert_eq!(hits, json!([]));
}

#[test]
fn repository_and_incompatible_index_errors_keep_service_actions() {
    let missing_root = tempfile::tempdir().unwrap();
    let mut missing_server = McpProcess::start(missing_root.path());
    let missing = missing_server.request(call(
        "context",
        json!({"task": "find code", "path": missing_root.path().join("missing")}),
    ));
    let missing_payload: Value =
        serde_json::from_str(missing["result"]["content"][0]["text"].as_str().unwrap()).unwrap();
    assert_eq!(missing_payload["error"]["code"], "repository_not_found");
    assert_eq!(missing_payload["error"]["action"], "stop");

    let incompatible_root = tempfile::tempdir().unwrap();
    write(
        incompatible_root.path(),
        "src/thing.py",
        "def thing():\n    return 1\n",
    );
    index(incompatible_root.path());
    let db = incompatible_root.path().join(".oxide/index.db");
    let connection = rusqlite::Connection::open(db).unwrap();
    connection
        .execute(
            "UPDATE meta SET value = '999' WHERE key = 'schema_version'",
            [],
        )
        .unwrap();
    let mut incompatible_server = McpProcess::start(incompatible_root.path());
    let incompatible =
        incompatible_server.request(call("search", json!({"query": "thing", "path": "."})));
    let incompatible_payload: Value = serde_json::from_str(
        incompatible["result"]["content"][0]["text"]
            .as_str()
            .unwrap(),
    )
    .unwrap();
    assert_eq!(incompatible_payload["error"]["code"], "index_incompatible");
    assert_eq!(incompatible_payload["error"]["action"], "repair");
}

#[test]
fn concurrent_mcp_reads_return_valid_results() {
    let root = tempfile::tempdir().unwrap();
    write(root.path(), "src/thing.py", "def thing():\n    return 1\n");
    index(root.path());
    let root = root.path().to_path_buf();

    std::thread::scope(|scope| {
        for id in 0..4 {
            let root = root.clone();
            scope.spawn(move || {
                let mut server = McpProcess::start(&root);
                let response = server.request(json!({
                    "jsonrpc": "2.0",
                    "id": id,
                    "method": "tools/call",
                    "params": {"name": "search", "arguments": {"query": "thing", "path": ".", "limit": 1}}
                }));
                assert_eq!(response["result"]["isError"], false);
                let payload: Value = serde_json::from_str(
                    response["result"]["content"][0]["text"].as_str().unwrap(),
                )
                .unwrap();
                assert_eq!(payload.as_array().unwrap().len(), 1);
            });
        }
    });
}
