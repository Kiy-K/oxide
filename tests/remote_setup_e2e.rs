//! End-to-end coverage for `oxide setup` that the original task promised —
//! "tests for … privacy/consent behavior … JSON/MCP cleanliness" — and that
//! the unit tests in `credentials.rs`/`user_config.rs`/`embeddings/remote.rs`
//! don't reach, since they call those modules directly rather than through
//! the real CLI/subprocess boundary `oxide setup` and a configured `oxide
//! index`/`search --json` actually run through.
//!
//! Every case gets a throwaway `$HOME` (`XDG_CONFIG_HOME` unset, so
//! `~/.config/oxide` resolves under it) and a mock `/v1/embeddings` server,
//! run as a real subprocess of the built binary — never in-process — so
//! nothing here can race `tests/remote_provider_degrade.rs`'s own
//! process-global env var mutation.

use std::io::{Read, Write};
use std::net::TcpListener;
use std::path::Path;
use std::process::{Command, Output, Stdio};

fn write_file(root: &Path, relative: &str, source: &str) {
    let path = root.join(relative);
    std::fs::create_dir_all(path.parent().unwrap()).unwrap();
    std::fs::write(path, source).unwrap();
}

fn sample_repo() -> tempfile::TempDir {
    let tmp = tempfile::tempdir().unwrap();
    write_file(
        tmp.path(),
        "src/auth.py",
        "def refresh_token(token):\n    return token is not None\n",
    );
    tmp
}

/// Reads one full HTTP/1.1 request (headers + `Content-Length` body bytes)
/// so a multi-symbol batch's body is never cut short by a single `read()`
/// call not returning everything in one go (not guaranteed even on
/// loopback) — same helper shape as `remote_provider_degrade.rs`.
fn read_request_body(stream: &mut std::net::TcpStream) -> serde_json::Value {
    let mut buf = Vec::new();
    let mut chunk = [0u8; 4096];
    let (header_end, content_length) = loop {
        let n = stream.read(&mut chunk).unwrap_or(0);
        if n == 0 {
            return serde_json::Value::Null;
        }
        buf.extend_from_slice(&chunk[..n]);
        let text = String::from_utf8_lossy(&buf);
        if let Some(idx) = text.find("\r\n\r\n") {
            let len = text[..idx]
                .lines()
                .find(|l| l.to_ascii_lowercase().starts_with("content-length:"))
                .and_then(|l| l.split(':').nth(1))
                .and_then(|v| v.trim().parse::<usize>().ok())
                .unwrap_or(0);
            break (idx + 4, len);
        }
    };
    while buf.len() < header_end + content_length {
        let n = stream.read(&mut chunk).unwrap_or(0);
        if n == 0 {
            break;
        }
        buf.extend_from_slice(&chunk[..n]);
    }
    let body_bytes = &buf[header_end..(header_end + content_length).min(buf.len())];
    serde_json::from_slice(body_bytes).unwrap_or_default()
}

/// A `/v1/embeddings`-shaped mock that answers with one fixed 4-dim vector
/// *per input item* (matching the real contract — `embeddings/remote.rs`'s
/// `extract_embeddings`), until dropped. Returning a single embedding
/// regardless of batch size made every multi-symbol `oxide index` fail with
/// "embedder_unavailable" here — the same fix `remote_provider_degrade.rs`
/// already needed for the same reason.
fn spawn_embedding_server() -> (String, std::thread::JoinHandle<()>) {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let addr = listener.local_addr().unwrap();
    let handle = std::thread::spawn(move || {
        for stream in listener.incoming() {
            let Ok(mut stream) = stream else { break };
            let req = read_request_body(&mut stream);
            let count = req["input"].as_array().map(|a| a.len()).unwrap_or(1);
            let items: Vec<_> = (0..count)
                .map(|i| serde_json::json!({"embedding": [0.1, 0.2, 0.3, 0.4], "index": i}))
                .collect();
            let body = serde_json::json!({"data": items}).to_string();
            let response = format!(
                "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n{body}",
                body.len()
            );
            let _ = stream.write_all(response.as_bytes());
        }
    });
    (format!("http://{addr}"), handle)
}

fn run(home: &Path, cwd: &Path, args: &[&str], extra_env: &[(&str, &str)]) -> Output {
    let mut cmd = Command::new(env!("CARGO_BIN_EXE_oxide"));
    cmd.args(args)
        .current_dir(cwd)
        .env("HOME", home)
        .env_remove("XDG_CONFIG_HOME")
        .env_remove("OXIDE_EMBED_URL")
        .env_remove("OXIDE_EMBED_PROVIDER")
        .env_remove("OXIDE_EMBED_NATIVE")
        .stdin(Stdio::null());
    for (k, v) in extra_env {
        cmd.env(k, v);
    }
    cmd.output().unwrap()
}

fn config_toml(home: &Path) -> Option<String> {
    std::fs::read_to_string(home.join(".config/oxide/config.toml")).ok()
}

const PRIVACY_MARKERS: &[&str] = &[
    "Remote embeddings enabled",
    "will be sent to",
    "leave this machine",
    "leave your machine",
];

fn assert_clean_machine_output(output: &Output, context: &str) {
    let stdout = String::from_utf8_lossy(&output.stdout);
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(
        !stdout.contains('\x1b'),
        "{context}: stdout must never carry ANSI escapes: {stdout:?}"
    );
    for marker in PRIVACY_MARKERS {
        assert!(
            !stdout.contains(marker) && !stderr.contains(marker),
            "{context}: privacy-warning text must never leak into machine output ({marker:?} found)"
        );
    }
    serde_json::from_str::<serde_json::Value>(&stdout)
        .unwrap_or_else(|e| panic!("{context}: stdout was not valid JSON ({e}): {stdout:?}"));
}

#[test]
fn setup_yes_persists_consent_and_encrypted_key_then_index_and_search_json_stay_clean() {
    let home = tempfile::tempdir().unwrap();
    let repo = sample_repo();
    let (url, handle) = spawn_embedding_server();

    let setup = run(
        home.path(),
        repo.path(),
        &[
            "setup",
            "--provider",
            "openai-compatible",
            "--base-url",
            &url,
            "--model",
            "test-model",
            "--api-key-env",
            "OXIDE_TEST_SETUP_KEY",
            "--yes",
        ],
        &[("OXIDE_TEST_SETUP_KEY", "sk-test-secret-value")],
    );
    assert!(
        setup.status.success(),
        "oxide setup --yes should succeed against a live mock server: {}",
        String::from_utf8_lossy(&setup.stderr)
    );

    let cfg_text = config_toml(home.path()).expect("config.toml must exist after setup");
    assert!(cfg_text.contains("remote_consent_ack = true"));
    assert!(cfg_text.contains(r#"provider = "openai-compatible""#));
    assert!(
        !cfg_text.contains("sk-test-secret-value"),
        "config.toml must never contain the raw API key"
    );

    let creds_path = home.path().join(".config/oxide/credentials.enc");
    assert!(creds_path.is_file(), "credentials.enc must be written");
    let creds_bytes = std::fs::read(&creds_path).unwrap();
    assert!(
        !creds_bytes
            .windows(b"sk-test-secret-value".len())
            .any(|w| w == b"sk-test-secret-value"),
        "credentials.enc must never contain the plaintext API key"
    );

    let indexed = run(home.path(), repo.path(), &["index", ".", "--json"], &[]);
    assert!(
        indexed.status.success(),
        "index against the configured remote provider should succeed: stderr={} stdout={}",
        String::from_utf8_lossy(&indexed.stderr),
        String::from_utf8_lossy(&indexed.stdout)
    );
    assert_clean_machine_output(&indexed, "oxide index --json");

    let searched = run(
        home.path(),
        repo.path(),
        &["search", "refresh token", "--json"],
        &[],
    );
    assert!(searched.status.success());
    assert_clean_machine_output(&searched, "oxide search --json");

    drop(handle); // accept loop exits once the listener is dropped on process exit
}

#[test]
fn declined_confirmation_leaves_config_and_credentials_untouched() {
    let home = tempfile::tempdir().unwrap();
    let repo = sample_repo();
    let (url, _handle) = spawn_embedding_server();

    let mut cmd = Command::new(env!("CARGO_BIN_EXE_oxide"));
    cmd.args([
        "setup",
        "--provider",
        "openai-compatible",
        "--base-url",
        &url,
        "--model",
        "test-model",
        "--api-key-env",
        "OXIDE_TEST_SETUP_KEY",
    ])
    .current_dir(repo.path())
    .env("HOME", home.path())
    .env_remove("XDG_CONFIG_HOME")
    .env("OXIDE_TEST_SETUP_KEY", "sk-test-secret-value")
    .stdin(Stdio::piped())
    .stdout(Stdio::piped())
    .stderr(Stdio::piped());
    let mut child = cmd.spawn().unwrap();
    child.stdin.take().unwrap().write_all(b"n\n").unwrap();
    let output = child.wait_with_output().unwrap();

    assert!(
        output.status.success(),
        "declining the confirmation is not an error: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(
        config_toml(home.path()).is_none(),
        "a declined confirmation must not write config.toml"
    );
    assert!(
        !home.path().join(".config/oxide/credentials.enc").exists(),
        "a declined confirmation must not write credentials.enc"
    );
}

#[test]
fn unreachable_provider_leaves_config_and_credentials_untouched() {
    let home = tempfile::tempdir().unwrap();
    let repo = sample_repo();
    // Bind and immediately drop: the port is valid but nothing answers.
    let dead_url = {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        format!("http://{}", listener.local_addr().unwrap())
    };

    let setup = run(
        home.path(),
        repo.path(),
        &[
            "setup",
            "--provider",
            "openai-compatible",
            "--base-url",
            &dead_url,
            "--model",
            "test-model",
            "--api-key-env",
            "OXIDE_TEST_SETUP_KEY",
            "--yes",
        ],
        &[("OXIDE_TEST_SETUP_KEY", "sk-test-secret-value")],
    );
    assert!(
        !setup.status.success(),
        "setup against an unreachable endpoint must fail, not silently save"
    );
    assert!(
        config_toml(home.path()).is_none(),
        "a failed live test must not write config.toml"
    );
    assert!(
        !home.path().join(".config/oxide/credentials.enc").exists(),
        "a failed live test must not write credentials.enc"
    );
}
