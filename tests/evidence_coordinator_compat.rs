//! Compatibility gates for the evidence-coordinator refactor — see
//! docs/superpowers/specs/2026-09-15-evidence-coordinator-refactor-design.md's
//! "Compatibility gates" section, and the hardening-pass follow-up (item
//! #4) that reshaped this file: `default`, `git_disabled`, `lsp_disabled`
//! used to run the identical CLI invocation (no `--git`/`--lsp`, since the
//! CLI's plain `bool` flags can't express "explicit false" at all — a flag
//! is either present or absent) against three separately-named fixtures
//! that were, in fact, byte-identical files — three fixtures pinning one
//! condition, not three distinct ones.
//!
//! `default_query_is_byte_identical`/`git_enabled_is_byte_identical` stay
//! CLI-based: `default` is the only test that pins the actual CLI surface,
//! and `--git` is a genuinely distinct condition (the flag present).
//! `git_disabled`/`lsp_disabled` now go through the MCP `query` tool with
//! an EXPLICIT `"git": false`/`"lsp": false` argument — representable only
//! at that layer (`mcp.rs::optional_bool` distinguishes an explicit `false`
//! from an absent key; both end up `false`, but the parsing path differs) —
//! compared against `default.json`, since explicit-false must produce
//! identical output to omission.
//!
//! Comparison is structural (parsed JSON), not a plain string diff, for two
//! reasons: it's insensitive to incidental formatting, and it lets
//! `normalize` strip the whole volatile `git` subtree out before comparing.
//! `fixtures/py_repo` is a subdirectory of this actively-developed repo,
//! not an isolated git repo with its own frozen history — `--git`'s
//! `range: ""` (worktree vs HEAD) means `recent_commits` reflects *this
//! worktree's* real, ever-growing commit log, and `changed_files`/
//! `co_change` reflect whatever this worktree's working tree happens to
//! have uncommitted at test-run time (confirmed empirically: this test
//! once failed on `git.changed_files` against a mid-task uncommitted diff,
//! even though `items`/`omitted` — the actual coordinator output this gate
//! exists to protect — were byte-identical). Only `git.range` (a fixed
//! `"HEAD"` string) stays in the comparison. The meaningful proof this gate
//! gives lives entirely in `items`/`omitted`, always fully compared.
//!
//! `lsp_enabled_is_byte_identical` uses a small, fully deterministic fake
//! LSP server (`fixtures/fake_lsp_deterministic/server.py`, spawned via
//! `$OXIDE_LSP_SERVER` pointed at its own absolute path — its shebang line
//! makes it directly executable, and it ignores the `server` argument
//! `LspClient::spawn` always appends) instead of real `ty`. A real `ty`
//! session's `textDocument/references`/call-hierarchy responses are not
//! byte-stable across separate process invocations — discovered
//! empirically while fixing an unrelated regression: five consecutive
//! `oxide query --lsp` runs against the same unmodified fixture, same
//! binary, produced five different `lsp-reference`/score combinations for
//! two symbols, while only the *set* of item ids stayed identical. That's
//! real, pre-existing `ty` behavior, orthogonal to this refactor, and it
//! made a genuinely byte-identical `lsp_enabled` gate impossible against a
//! real server. The fake server's fixed, byte-stable response set removes
//! that source of nondeterminism, so this condition can go back to a full
//! structural comparison instead of the weaker item-id-set check an
//! earlier version of this file used. Real `ty` correctness stays covered
//! by the `lsp-integration` CI job's real-server tests.

use std::io::{BufRead, Write};
use std::path::Path;
use std::process::Command;

fn run(args: &[&str]) -> serde_json::Value {
    let out = Command::new(env!("CARGO_BIN_EXE_oxide"))
        .args(args)
        .env("OXIDE_EMBED_NATIVE", "hashed")
        .output()
        .expect("oxide must run");
    assert!(
        out.status.success(),
        "{}",
        String::from_utf8_lossy(&out.stderr)
    );
    serde_json::from_slice(&out.stdout).expect("oxide --json output must parse as JSON")
}

/// Strips the volatile parts of the `git` subtree (see module doc) so the
/// comparison isn't sensitive to this worktree's commit count or its
/// working tree's dirty/clean state at test-run time. `git.range` stays —
/// it's a fixed string, not diff-dependent.
fn normalize(mut value: serde_json::Value) -> serde_json::Value {
    if let Some(git) = value.get_mut("git") {
        if let Some(obj) = git.as_object_mut() {
            obj.remove("recent_commits");
            obj.remove("changed_files");
            obj.remove("co_change");
        }
    }
    value
}

fn expect_fixture(fixture_name: &str, condition_name: &str, actual: serde_json::Value) {
    let expected_raw =
        std::fs::read_to_string(format!("fixtures/evidence_compat/{fixture_name}.json"))
            .unwrap_or_else(|_| panic!("missing fixtures/evidence_compat/{fixture_name}.json"));
    let expected: serde_json::Value =
        serde_json::from_str(&expected_raw).expect("fixture must be valid JSON");
    assert_eq!(
        normalize(actual),
        normalize(expected),
        "condition `{condition_name}` must stay structurally identical to `{fixture_name}.json` \
         (git.recent_commits/changed_files/co_change excluded — see module doc)"
    );
}

#[test]
fn default_query_is_byte_identical() {
    let actual = run(&[
        "query",
        "where is retry logic",
        "--json",
        "--path",
        "fixtures/py_repo",
    ]);
    expect_fixture("default", "default", actual);
}

#[test]
fn git_enabled_is_byte_identical() {
    let actual = run(&[
        "query",
        "where is retry logic",
        "--git",
        "--json",
        "--path",
        "fixtures/py_repo",
    ]);
    expect_fixture("git_enabled", "git_enabled", actual);
}

#[test]
fn lsp_enabled_is_byte_identical() {
    let script = concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/fixtures/fake_lsp_deterministic/server.py"
    );
    let out = Command::new(env!("CARGO_BIN_EXE_oxide"))
        .args([
            "query",
            "where is retry logic",
            "--lsp",
            "--json",
            "--path",
            "fixtures/py_repo",
        ])
        .env("OXIDE_EMBED_NATIVE", "hashed")
        .env("OXIDE_LSP_SERVER", script)
        .output()
        .expect("oxide must run");
    assert!(
        out.status.success(),
        "{}",
        String::from_utf8_lossy(&out.stderr)
    );
    let actual: serde_json::Value =
        serde_json::from_slice(&out.stdout).expect("oxide --json output must parse as JSON");
    expect_fixture("lsp_enabled", "lsp_enabled", actual);
}

// ---- explicit-false via the MCP argument shape -----------------------
//
// The CLI's `git`/`lsp` fields are plain `bool`s (see `cli.rs`): a flag is
// either present (true) or absent (false) — there is no way to write
// `--git=false`. MCP's JSON arguments CAN represent that explicitly
// (`{"git": false}` vs `{}`), and `mcp.rs::optional_bool` parses both
// through a genuinely different code path (`Option<bool>` then
// `.unwrap_or(false)`) even though they produce the same final `bool`.
// These two tests are what actually exercises that path; the CLI-based
// tests above cannot.

struct McpProcess {
    child: std::process::Child,
    input: std::process::ChildStdin,
    output: std::io::BufReader<std::process::ChildStdout>,
}

impl McpProcess {
    fn start(root: &Path) -> Self {
        let mut child = Command::new(env!("CARGO_BIN_EXE_oxide"))
            .arg("mcp")
            .current_dir(root)
            .env("OXIDE_EMBED_NATIVE", "hashed")
            .stdin(std::process::Stdio::piped())
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::piped())
            .spawn()
            .expect("oxide mcp must spawn");
        let mut process = Self {
            input: child.stdin.take().unwrap(),
            output: std::io::BufReader::new(child.stdout.take().unwrap()),
            child,
        };
        process.request(serde_json::json!({
            "jsonrpc": "2.0", "id": "handshake", "method": "initialize",
            "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "test", "version": "0"}}
        }));
        writeln!(
            process.input,
            "{}",
            serde_json::json!({"jsonrpc": "2.0", "method": "notifications/initialized"})
        )
        .unwrap();
        process.input.flush().unwrap();
        process
    }

    fn request(&mut self, request: serde_json::Value) -> serde_json::Value {
        writeln!(self.input, "{request}").unwrap();
        self.input.flush().unwrap();
        let mut line = String::new();
        self.output.read_line(&mut line).unwrap();
        assert!(!line.is_empty(), "MCP server exited without a response");
        serde_json::from_str(&line).unwrap()
    }

    fn query(&mut self, arguments: serde_json::Value) -> serde_json::Value {
        let response = self.request(serde_json::json!({
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "query", "arguments": arguments}
        }));
        let text = response["result"]["content"][0]["text"]
            .as_str()
            .unwrap_or_else(|| panic!("no text content in response: {response}"));
        serde_json::from_str(text).expect("tool result text must be ContextResult JSON")
    }
}

impl Drop for McpProcess {
    fn drop(&mut self) {
        let _ = self.input.flush();
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

fn py_repo_root() -> std::path::PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("fixtures/py_repo")
}

#[test]
fn git_explicit_false_via_mcp_is_byte_identical_to_default() {
    let mut server = McpProcess::start(&py_repo_root());
    let actual = server.query(serde_json::json!({"task": "where is retry logic", "git": false}));
    expect_fixture("default", "git_explicit_false", actual);
}

#[test]
fn lsp_explicit_false_via_mcp_is_byte_identical_to_default() {
    let mut server = McpProcess::start(&py_repo_root());
    let actual = server.query(serde_json::json!({"task": "where is retry logic", "lsp": false}));
    expect_fixture("default", "lsp_explicit_false", actual);
}
