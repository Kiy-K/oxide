//! Pins the audit's determinism requirement: workers may finish in any
//! order, but the merged output must be byte-identical regardless.
//!
//! Not fully exercised until Task 8 wires `context.rs` to
//! `EvidenceCoordinator::collect()` — until then this test still passes
//! (the pre-refactor code path is already deterministic and doesn't read
//! `OXIDE_EVIDENCE_ARTIFICIAL_DELAY_MS` at all), it just isn't proving
//! anything about the coordinator's own merge order yet.

use std::process::Command;

fn run_query(repo: &str, extra_env: &[(&str, &str)]) -> String {
    let mut cmd = Command::new(env!("CARGO_BIN_EXE_oxide"));
    cmd.args(["query", "where is retry logic", "--json", "--path", repo])
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

#[test]
fn completion_order_never_changes_final_json_output() {
    let baseline = run_query("fixtures/py_repo", &[]);

    let git_slow = run_query(
        "fixtures/py_repo",
        &[("OXIDE_EVIDENCE_ARTIFICIAL_DELAY_MS", "git=40,lsp=0")],
    );
    let lsp_slow = run_query(
        "fixtures/py_repo",
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
