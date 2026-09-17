//! Pins the audit's determinism requirement: workers may finish in any
//! order, but the merged output must be byte-identical regardless.
//!
//! `--git`/`--lsp` must both be passed for this to prove anything: with
//! neither flag, `EvidenceCoordinator::collect` never spawns `git_io`/
//! `lsp_io` at all (both are gated on `opts.git`/`opts.lsp`), so the
//! artificial-delay hook never fires and a version of this test without
//! the flags passes vacuously regardless of whether completion order is
//! actually handled correctly.

use std::process::Command;

fn ty_available() -> bool {
    Command::new("ty")
        .arg("--version")
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .status()
        .map(|s| s.success())
        .unwrap_or(false)
}

fn run_query(repo: &str, extra_args: &[&str], extra_env: &[(&str, &str)]) -> String {
    let mut cmd = Command::new(env!("CARGO_BIN_EXE_oxide"));
    cmd.args(["query", "where is retry logic", "--json", "--path", repo])
        .args(extra_args)
        .env("OXIDE_EMBED_NATIVE", "hashed");
    for (k, v) in extra_env {
        cmd.env(k, v);
    }
    let out = cmd.output().expect("oxide query must run");
    assert!(
        out.status.success(),
        "{}",
        String::from_utf8_lossy(&out.stderr)
    );
    String::from_utf8_lossy(&out.stdout).into_owned()
}

/// `--git` has no external dependency (git is always available in a repo
/// checkout), so this always exercises `git_io`'s real concurrent path.
#[test]
fn completion_order_never_changes_final_json_output_with_git() {
    let baseline = run_query("fixtures/py_repo", &["--git"], &[]);

    let git_slow = run_query(
        "fixtures/py_repo",
        &["--git"],
        &[("OXIDE_EVIDENCE_ARTIFICIAL_DELAY_MS", "git=40")],
    );

    assert_eq!(
        baseline, git_slow,
        "completion order must not change output"
    );
}

/// Requires a real `ty` server to actually spawn (a failed spawn leaves
/// `lsp_client` `None`, which short-circuits `lsp_io` the same way `--lsp`
/// being absent does) — skipped, not failed, when `ty` isn't installed.
#[test]
fn completion_order_never_changes_final_json_output_with_git_and_lsp() {
    if !ty_available() {
        eprintln!("skipping: `ty` not on PATH (install with `uv tool install ty`)");
        return;
    }

    let baseline = run_query("fixtures/py_repo", &["--git", "--lsp"], &[]);

    let git_slow = run_query(
        "fixtures/py_repo",
        &["--git", "--lsp"],
        &[("OXIDE_EVIDENCE_ARTIFICIAL_DELAY_MS", "git=40,lsp=0")],
    );
    let lsp_slow = run_query(
        "fixtures/py_repo",
        &["--git", "--lsp"],
        &[("OXIDE_EVIDENCE_ARTIFICIAL_DELAY_MS", "git=0,lsp=40")],
    );

    assert_eq!(
        baseline, git_slow,
        "completion order must not change output"
    );
    assert_eq!(
        baseline, lsp_slow,
        "completion order must not change output"
    );
}
