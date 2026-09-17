//! Compatibility gates for the evidence-coordinator refactor — see
//! docs/superpowers/specs/2026-09-15-evidence-coordinator-refactor-design.md's
//! "Compatibility gates" section. `lsp_enabled.json` requires `ty` on PATH;
//! the other four do not touch LSP.
//!
//! Comparison is structural (parsed JSON), not a plain string diff, for two
//! reasons: it's insensitive to incidental formatting, and it lets
//! `expect_fixture` normalize the whole volatile `git` subtree out before
//! comparing. `fixtures/py_repo` is a subdirectory of this actively-
//! developed repo, not an isolated git repo with its own frozen history —
//! `--git`'s `range: ""` (worktree vs HEAD) means `recent_commits` reflects
//! *this worktree's* real, ever-growing commit log, and `changed_files`/
//! `co_change` reflect whatever this worktree's working tree happens to
//! have uncommitted at test-run time (routine during active development —
//! confirmed empirically: this test failed on `git.changed_files` the first
//! time it ran against a mid-task uncommitted diff, even though `items`/
//! `omitted` — the actual coordinator output this gate exists to protect —
//! were byte-identical). Only `git.range` (a fixed `"HEAD"` string) stays in
//! the comparison; the rest of `git` is normalized away. The meaningful
//! proof this gate gives — that the evidence-coordinator refactor didn't
//! change retrieval/scoring/structure — lives entirely in `items`/
//! `omitted`, which stay fully compared.

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

fn expect_fixture(name: &str, args: &[&str]) {
    let expected_raw = std::fs::read_to_string(format!("fixtures/evidence_compat/{name}.json"))
        .unwrap_or_else(|_| panic!("missing fixture fixtures/evidence_compat/{name}.json"));
    let expected: serde_json::Value =
        serde_json::from_str(&expected_raw).expect("fixture must be valid JSON");
    let actual = run(args);
    assert_eq!(
        normalize(actual),
        normalize(expected),
        "condition `{name}` must stay structurally identical (git.recent_commits excluded — see module doc)"
    );
}

#[test]
fn default_query_is_byte_identical() {
    expect_fixture(
        "default",
        &[
            "query",
            "where is retry logic",
            "--json",
            "--path",
            "fixtures/py_repo",
        ],
    );
}

#[test]
fn git_disabled_is_byte_identical() {
    expect_fixture(
        "git_disabled",
        &[
            "query",
            "where is retry logic",
            "--json",
            "--path",
            "fixtures/py_repo",
        ],
    );
}

#[test]
fn lsp_disabled_is_byte_identical() {
    expect_fixture(
        "lsp_disabled",
        &[
            "query",
            "where is retry logic",
            "--json",
            "--path",
            "fixtures/py_repo",
        ],
    );
}

#[test]
fn git_enabled_is_byte_identical() {
    expect_fixture(
        "git_enabled",
        &[
            "query",
            "where is retry logic",
            "--git",
            "--json",
            "--path",
            "fixtures/py_repo",
        ],
    );
}

#[test]
fn lsp_enabled_is_byte_identical() {
    if !std::process::Command::new("ty")
        .arg("--version")
        .output()
        .map(|o| o.status.success())
        .unwrap_or(false)
    {
        eprintln!("skipping: `ty` not on PATH");
        return;
    }
    expect_fixture(
        "lsp_enabled",
        &[
            "query",
            "where is retry logic",
            "--lsp",
            "--json",
            "--path",
            "fixtures/py_repo",
        ],
    );
}
