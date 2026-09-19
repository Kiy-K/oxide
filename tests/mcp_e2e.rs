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
        } else {
            // No URL configured: without this the server would fall through to
            // `DEFAULT_NATIVE_PROFILE` and load real ONNX weights. Pin the
            // offline embedder so the test stays hermetic and matches the index
            // `index()` above builds.
            command.env("OXIDE_EMBED_NATIVE", "hashed");
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
    // The shipped default provider is now a real ONNX model
    // (`DEFAULT_NATIVE_PROFILE`), which downloads weights and needs
    // network. Tests pin the offline hashed embedder so the suite
    // stays hermetic, fast and deterministic, and so a subprocess
    // never disagrees with an index another one built.
    let output = Command::new(env!("CARGO_BIN_EXE_oxide"))
        .args(["index", "."])
        .env("OXIDE_EMBED_NATIVE", "hashed")
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
    assert_eq!(names, ["query", "search"]);
    assert!(tools
        .iter()
        .all(|tool| tool["inputSchema"]["additionalProperties"] == false));
    assert_eq!(tools[0]["inputSchema"]["required"], json!(["task"]));
    assert_eq!(tools[1]["inputSchema"]["required"], json!(["query"]));
    // Argument names mirror the CLI flags (`oxide query TASK --budget-tokens
    // --profile`, `oxide search QUERY --limit --profile --mode`); in
    // particular the relevance profile is `profile`, never `mode`, because
    // the CLI's `--mode` also covers lexical|semantic|hybrid, none of which
    // are separately selectable here (see `search`'s `mode` schema
    // description in src/mcp.rs) — `mode` on this tool accepts only
    // "literal".
    let keys = |tool: &Value| -> Vec<String> {
        let mut k: Vec<String> = tool["inputSchema"]["properties"]
            .as_object()
            .unwrap()
            .keys()
            .cloned()
            .collect();
        k.sort();
        k
    };
    assert_eq!(
        keys(&tools[0]),
        [
            "blast_radius",
            "budget_tokens",
            "git",
            "path",
            "profile",
            "task"
        ]
    );
    assert_eq!(
        keys(&tools[1]),
        ["blast_radius", "limit", "mode", "path", "profile", "query"]
    );
    assert_eq!(
        tools[1]["inputSchema"]["properties"]["mode"]["enum"],
        json!(["literal"])
    );
}

#[test]
fn search_literal_mode_needs_no_index_and_matches_cli_output() {
    let root = tempfile::tempdir().unwrap();
    std::fs::create_dir(root.path().join(".git")).unwrap();
    write(
        root.path(),
        "README.md",
        "See flaky_widget_marker for details.\n",
    );
    write(
        root.path(),
        "src/thing.py",
        "def thing():\n    return flaky_widget_marker\n",
    );
    // Deliberately no `index(root.path())` call: literal search must work
    // on a repository that has never been indexed at all.
    let mut server = McpProcess::start(root.path());

    let response = server.request(call(
        "search",
        json!({"query": "flaky_widget_marker", "path": ".", "mode": "literal"}),
    ));
    assert_eq!(response["result"]["isError"], false);
    let payload: Value =
        serde_json::from_str(response["result"]["content"][0]["text"].as_str().unwrap()).unwrap();
    let hits = payload["hits"].as_array().unwrap();
    assert_eq!(hits.len(), 2, "{payload:?}");
    let files: Vec<&str> = hits.iter().map(|h| h["file"].as_str().unwrap()).collect();
    assert!(files.contains(&"README.md"), "{files:?}");
    assert!(files.contains(&"src/thing.py"), "{files:?}");
    assert!(hits[0]["line"].is_u64());
    assert!(hits[0]["column"].is_u64());

    let cli = Command::new(env!("CARGO_BIN_EXE_oxide"))
        .args([
            "search",
            "flaky_widget_marker",
            "--mode",
            "literal",
            "--json",
        ])
        .current_dir(root.path())
        .output()
        .unwrap();
    assert!(cli.status.success(), "{:?}", cli.stderr);
    let cli_payload: Value = serde_json::from_slice(&cli.stdout).unwrap();
    assert_eq!(
        cli_payload, payload,
        "CLI and MCP must return identical hits"
    );
}

#[test]
fn search_literal_mode_rejects_missing_query_as_malformed_params() {
    let root = tempfile::tempdir().unwrap();
    let mut server = McpProcess::start(root.path());
    let response = server.request(call("search", json!({"path": ".", "mode": "literal"})));
    assert_eq!(response["error"]["code"], -32602);
}

#[test]
fn search_rejects_a_lexical_semantic_or_hybrid_mode_value() {
    let root = tempfile::tempdir().unwrap();
    write(root.path(), "src/a.py", "def thing():\n    return 1\n");
    index(root.path());
    let mut server = McpProcess::start(root.path());

    // The CLI's `--mode` accepts lexical|semantic|hybrid|literal, but this
    // tool's `mode` argument only ever accepts "literal" (see
    // `SEARCH_MODE_DESCRIPTION`). A value from the CLI's larger set must
    // fail loudly, not silently fall through to the default hybrid search.
    for bogus in ["lexical", "semantic", "hybrid"] {
        let response = server.request(call(
            "search",
            json!({"query": "thing", "path": ".", "mode": bogus}),
        ));
        assert_eq!(
            response["error"]["code"], -32602,
            "mode: {bogus:?} should be rejected, got {response:?}"
        );
    }
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
        "query",
        json!({"task": "fix refresh token validation", "path": ".", "budget_tokens": 128}),
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

    let malformed = server.request(call("query", json!({"task": 42})));
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

    let response = server.request(call("query", json!({"task": "find thing", "path": "."})));
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
        "query",
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

/// `oxide mcp` caches the loaded symbol snapshot across requests
/// (`RepositoryService::with_process_cache`). That cache is keyed by the
/// index's generation counter, so a reindex performed by *another process*
/// while the server is running must be visible on the very next call —
/// both for a symbol that appeared and for one that disappeared.
#[test]
fn a_live_server_sees_an_out_of_process_reindex_on_the_next_call() {
    let root = tempfile::tempdir().unwrap();
    write(
        root.path(),
        "src/auth.py",
        "def refresh_token(token):\n    return token\n\ndef stale_helper():\n    return 1\n",
    );
    index(root.path());
    let mut server = McpProcess::start(root.path());

    let ids = |response: &Value| -> Vec<String> {
        let hits: Value =
            serde_json::from_str(response["result"]["content"][0]["text"].as_str().unwrap())
                .unwrap();
        hits.as_array()
            .unwrap()
            .iter()
            .map(|h| h["id"].as_str().unwrap().to_string())
            .collect()
    };
    // Two calls so the second is definitely served from the warm cache.
    for _ in 0..2 {
        let hits = ids(&server.request(call(
            "search",
            json!({"query": "stale_helper", "path": ".", "limit": 5}),
        )));
        assert!(
            hits.iter().any(|id| id.ends_with("#stale_helper")),
            "{hits:?}"
        );
    }

    // Reindex from a separate process: one symbol removed, one added.
    write(
        root.path(),
        "src/auth.py",
        "def refresh_token(token):\n    return token\n\ndef fresh_helper():\n    return 2\n",
    );
    index(root.path());

    let hits = ids(&server.request(call(
        "search",
        json!({"query": "fresh_helper", "path": ".", "limit": 5}),
    )));
    assert!(
        hits.iter().any(|id| id.ends_with("#fresh_helper")),
        "{hits:?}"
    );
    assert!(
        !hits.iter().any(|id| id.ends_with("#stale_helper")),
        "{hits:?}"
    );
    let hits = ids(&server.request(call(
        "search",
        json!({"query": "stale_helper", "path": ".", "limit": 5}),
    )));
    assert!(
        !hits.iter().any(|id| id.ends_with("#stale_helper")),
        "{hits:?}"
    );

    // Context runs the structural stage over the cached snapshot; it too
    // must only ever name symbols that currently exist.
    let context = server.request(call(
        "query",
        json!({"task": "fresh helper refresh token", "path": ".", "budget_tokens": 512}),
    ));
    let payload: Value =
        serde_json::from_str(context["result"]["content"][0]["text"].as_str().unwrap()).unwrap();
    let names: Vec<&str> = payload["items"]
        .as_array()
        .unwrap()
        .iter()
        .map(|i| i["id"].as_str().unwrap())
        .collect();
    assert!(
        !names.iter().any(|n| n.ends_with("#stale_helper")),
        "{names:?}"
    );
}

#[test]
fn blast_radius_is_opt_in_over_the_protocol_and_absent_by_default() {
    // The MCP surface's compatibility contract: a client that predates the
    // argument sends nothing, and the payload it gets back carries no trace
    // of the feature — not an empty array, nothing. Passing it explicitly is
    // what turns the extra evidence on.
    let root = tempfile::tempdir().unwrap();
    write(
        root.path(),
        "src/store.py",
        "class TokenStore:\n    def refresh(self, token):\n        return token\n",
    );
    write(
        root.path(),
        "src/handler.py",
        "from .store import TokenStore\n\n\ndef handle(request):\n    return TokenStore().refresh(request)\n",
    );
    index(root.path());
    let mut server = McpProcess::start(root.path());

    let plain = server.request(call("search", json!({"query": "TokenStore", "path": "."})));
    let plain_text = plain["result"]["content"][0]["text"].as_str().unwrap();
    assert!(
        !plain_text.contains("blast_radius"),
        "default search payload carried blast-radius evidence: {plain_text}"
    );

    let asked = server.request(call(
        "search",
        json!({"query": "TokenStore", "path": ".", "blast_radius": true}),
    ));
    assert_eq!(asked["result"]["isError"], false);
    let asked_text = asked["result"]["content"][0]["text"].as_str().unwrap();
    let hits: Value = serde_json::from_str(asked_text).unwrap();
    let members: Vec<&Value> = hits
        .as_array()
        .unwrap()
        .iter()
        .filter_map(|h| h.get("blast_radius"))
        .flat_map(|b| b.as_array().unwrap())
        .collect();
    assert!(
        !members.is_empty(),
        "no blast radius returned: {asked_text}"
    );
    assert!(members
        .iter()
        .all(|m| m["id"].is_string() && m["relation"].is_string()));

    // Same for query, and a non-boolean is a protocol-level argument error
    // rather than a tool result, like every other malformed argument here.
    let ctx = server.request(call(
        "query",
        json!({"task": "refresh a token", "path": ".", "blast_radius": true}),
    ));
    assert_eq!(ctx["result"]["isError"], false);

    let bad = server.request(call(
        "search",
        json!({"query": "TokenStore", "path": ".", "blast_radius": "yes"}),
    ));
    assert_eq!(bad["error"]["code"], -32602, "{bad}");
}
