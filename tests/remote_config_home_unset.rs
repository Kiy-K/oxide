//! Regression test: `resolve_configured_remote`'s config-file branch used to
//! propagate `Paths::from_env()`'s error (no `$HOME`/`$USERPROFILE`) straight
//! through `open_embedder`'s `?`, which made every `oxide index`/`search`
//! fail outright on a HOME-less box — a minimal container, some systemd/cron
//! contexts — even for a user who never touched remote embeddings. Local
//! must stay zero-config regardless of whether a home directory exists to
//! hold `~/.config/oxide/config.toml`.
//!
//! Runs the real binary as a subprocess (not in-process) so clearing
//! `$HOME`/`$USERPROFILE` cannot affect any other test running concurrently
//! in this same process.

use serde_json::Value;
use std::path::Path;
use std::process::{Command, Output};

fn write(root: &Path, relative: &str, source: &str) {
    let path = root.join(relative);
    std::fs::create_dir_all(path.parent().unwrap()).unwrap();
    std::fs::write(path, source).unwrap();
}

fn run_without_home(root: &Path, args: &[&str]) -> Output {
    Command::new(env!("CARGO_BIN_EXE_oxide"))
        .args(args)
        // Offline/deterministic embedder, same as tests/cli_e2e.rs.
        .env("OXIDE_EMBED_NATIVE", "hashed")
        .env_remove("HOME")
        .env_remove("USERPROFILE")
        .env_remove("XDG_CONFIG_HOME")
        .current_dir(root)
        .output()
        .unwrap()
}

#[test]
fn local_indexing_and_search_work_with_no_home_directory_set() {
    let tmp = tempfile::tempdir().unwrap();
    write(
        tmp.path(),
        "src/auth.py",
        "def refresh_token(token):\n    return token is not None\n",
    );

    let indexed = run_without_home(tmp.path(), &["index", ".", "--json"]);
    assert!(
        indexed.status.success(),
        "indexing without $HOME must still succeed for the local embedder: {}",
        String::from_utf8_lossy(&indexed.stderr)
    );
    let indexed: Value = serde_json::from_slice(&indexed.stdout).unwrap();
    assert!(indexed["embedded_symbols"].as_u64().unwrap() > 0);

    let searched = run_without_home(tmp.path(), &["search", "refresh token", "--json"]);
    assert!(
        searched.status.success(),
        "search without $HOME must still succeed: {}",
        String::from_utf8_lossy(&searched.stderr)
    );
    let hits: Value = serde_json::from_slice(&searched.stdout).unwrap();
    assert!(!hits.as_array().unwrap().is_empty());
}
