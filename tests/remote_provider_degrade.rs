//! Pins the one behavior change Task 6 makes to `Service::search`/`context`/
//! `review`: a *remote* embedding provider going unavailable mid-call still
//! returns the lexical-still-good result (never a hard error), while a
//! *local* provider (a self-hosted `HttpEmbedder` via `$OXIDE_EMBED_URL`)
//! keeps today's unchanged hard-fail behavior. Runs against
//! `RepositoryService` directly (see `service_hardening.rs`'s doc comment
//! for why), with a real local HTTP server standing in for both "remote"
//! and "local self-hosted" — the distinction under test is
//! `EmbeddingProvider::is_remote()`, not physical distance.
//!
//! The remote scenario configures its provider the way `oxide setup` does —
//! writing `UserConfig`/credentials to a temp `$HOME` with `dimensions`
//! already recorded — rather than the raw `$OXIDE_EMBED_PROVIDER` env-var
//! opt-in. That distinction matters: only the persisted-config path skips
//! the construction-time dimension probe (`OpenAiCompatibleEmbedder::new`'s
//! doc comment), which is what lets `RepositoryService::search` reach the
//! `is_remote()` degrade check at all instead of failing at construction,
//! before ever getting there, while the provider is down. The raw env-var
//! opt-in intentionally keeps today's probe-or-fail contract (same as
//! `$OXIDE_EMBED_URL`) for a config with no persisted dimension to trust.
//!
//! All env-var mutation happens sequentially inside one `#[test]` function
//! to avoid racing other tests in this same process (env vars are
//! process-global); this file has exactly one test for that reason.

use oxide::index::IndexOptions;
use oxide::retrieval::{RetrievalMode, SearchMode};
use oxide::service::{RepositoryService, SearchRequest};
use oxide::user_config::UserConfig;
use std::io::{Read, Write};
use std::net::TcpListener;
use std::path::Path;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;

fn write(path: &Path, content: &str) {
    std::fs::create_dir_all(path.parent().unwrap()).unwrap();
    std::fs::write(path, content).unwrap();
}

fn sample_repo() -> tempfile::TempDir {
    let tmp = tempfile::tempdir().unwrap();
    std::fs::create_dir(tmp.path().join(".git")).unwrap();
    write(
        &tmp.path().join("src/auth.py"),
        "class AuthService:\n    def refresh_token(self, token):\n        return validate_refresh_token(token)\n\ndef validate_refresh_token(token):\n    return token is not None\n",
    );
    tmp
}

/// Reads one full HTTP/1.1 request (headers + `Content-Length` body bytes)
/// off `stream` and returns its parsed JSON body — required to fully drain
/// the request before responding (an early close can otherwise race the
/// client still writing its body), and to answer with one embedding per
/// input rather than a single fixed one regardless of batch size.
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

/// A tiny `/v1/embeddings`-shaped server that answers every request with one
/// fixed 4-dim vector *per input item* (matching the real contract — see
/// `remote_embed.rs`'s `extract_embeddings`) until `alive` is cleared, at
/// which point its accept loop stops — a later connection to the same
/// address gets refused, the same "endpoint went away" shape a real
/// provider outage has.
fn spawn_embedding_server() -> (String, Arc<AtomicBool>, std::thread::JoinHandle<()>) {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    listener.set_nonblocking(true).unwrap();
    let addr = listener.local_addr().unwrap();
    let alive = Arc::new(AtomicBool::new(true));
    let alive_thread = alive.clone();
    let handle = std::thread::spawn(move || {
        while alive_thread.load(Ordering::Relaxed) {
            match listener.accept() {
                Ok((mut stream, _)) => {
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
                Err(_) => std::thread::sleep(std::time::Duration::from_millis(5)),
            }
        }
    });
    (format!("http://{addr}"), alive, handle)
}

fn clear_embedding_env() {
    for var in [
        "OXIDE_EMBED_URL",
        "OXIDE_EMBED_MODEL",
        "OXIDE_EMBED_PROVIDER",
        "OXIDE_EMBED_BASE_URL",
        "OXIDE_EMBED_NATIVE",
        "OXIDE_OPENAI_COMPATIBLE_API_KEY",
    ] {
        // SAFETY: this test is the only one in this file/process touching
        // these vars, and every mutation happens sequentially on this one
        // thread — see the module doc comment.
        unsafe { std::env::remove_var(var) };
    }
}

fn search_request() -> SearchRequest {
    SearchRequest {
        limit: 10,
        mode: SearchMode::Hybrid,
        expand: false,
        retrieval_mode: RetrievalMode::Balanced,
        blast_radius: false,
    }
}

#[test]
fn remote_provider_degrades_to_lexical_while_local_still_hard_fails() {
    // ---- remote (openai-compatible), configured the way `oxide setup` would ----
    let remote_repo = sample_repo();
    let (remote_url, remote_alive, remote_handle) = spawn_embedding_server();
    clear_embedding_env();
    let fake_home = tempfile::tempdir().unwrap();
    // SAFETY: see clear_embedding_env's comment — sequential, single thread.
    unsafe {
        std::env::set_var("HOME", fake_home.path());
        std::env::remove_var("XDG_CONFIG_HOME");
    }
    let config_dir = fake_home.path().join(".config").join("oxide");
    // Mirrors `cmd_setup`'s own two-step exactly: probe once while the
    // provider is reachable to learn its dimension, then persist it —
    // that's what lets a *later* construction (inside `remote_service.index`
    // below) skip the probe entirely.
    let dim = oxide::remote_embed::build(
        "openai-compatible",
        "test-model",
        Some(&remote_url),
        None,
        None,
        "test-key",
    )
    .expect("provider builds while the server is up")
    .dim();
    oxide::credentials::set_key(&config_dir, "openai-compatible", "test-key").unwrap();
    UserConfig {
        provider: Some("openai-compatible".to_string()),
        model: Some("test-model".to_string()),
        base_url: Some(remote_url.clone()),
        vector_dim: Some(dim),
        remote_consent_ack: true,
        remote_consent_at: Some(oxide::user_config::now_iso8601()),
    }
    .save(&config_dir)
    .unwrap();

    let remote_service = RepositoryService::discover(Some(remote_repo.path().to_str().unwrap()))
        .expect("discover remote repo");
    remote_service
        .index(None, &IndexOptions::default())
        .expect("index while the remote provider is up");

    remote_alive.store(false, Ordering::Relaxed);
    // Wake the accept loop rather than waiting for its next poll interval.
    let _ = std::net::TcpStream::connect(remote_url.trim_start_matches("http://"));
    remote_handle.join().unwrap();

    let degraded = remote_service.search("refresh_token", search_request());
    assert!(
        degraded.is_ok(),
        "a remote provider outage must degrade to the lexical result, not error: {degraded:?}"
    );
    let hits = degraded.unwrap();
    assert!(
        !hits.is_empty(),
        "lexical matching on 'refresh_token' must still find the sample function"
    );

    // ---- local (self-hosted HttpEmbedder via $OXIDE_EMBED_URL): outage still hard-fails ----
    let local_repo = sample_repo();
    let (local_url, local_alive, local_handle) = spawn_embedding_server();
    clear_embedding_env();
    unsafe {
        std::env::set_var("OXIDE_EMBED_URL", &local_url);
        std::env::set_var("OXIDE_EMBED_MODEL", "test-model");
    }
    let local_service = RepositoryService::discover(Some(local_repo.path().to_str().unwrap()))
        .expect("discover local repo");
    local_service
        .index(None, &IndexOptions::default())
        .expect("index while the local endpoint is up");

    local_alive.store(false, Ordering::Relaxed);
    let _ = std::net::TcpStream::connect(local_url.trim_start_matches("http://"));
    local_handle.join().unwrap();

    let hard_failed = local_service.search("refresh_token", search_request());
    assert!(
        hard_failed.is_err(),
        "a local/self-hosted provider outage must still hard-fail, unchanged: {hard_failed:?}"
    );
    assert_eq!(hard_failed.unwrap_err().code(), "embedder_unavailable");

    clear_embedding_env();
}
