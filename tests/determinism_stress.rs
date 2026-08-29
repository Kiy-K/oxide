//! Phase 1.1 item 7: determinism stress gate.
//!
//! The ranking-nondeterminism bug fixed earlier in Phase 1.1 (deterministic
//! tie-break in RRF/lexical/vector ranking, commit a8c5aeb) proves this
//! needs a standing gate, not a one-off regression test. These tests run
//! `search`/`context`/`index` many times as **separate processes** (each
//! with its own fresh HashMap seed, thread pool, etc.) against the same
//! on-disk index and compare raw stdout bytes — not just parsed-JSON
//! equality, which would hide key-ordering or whitespace drift — so any
//! reintroduced nondeterminism shows up immediately instead of flapping
//! occasionally in CI.
//!
//! Every scenario deliberately includes several *tied* symbols (identical
//! bodies/signatures) across files, since ties are exactly where iteration-
//! order-dependent bugs hide: a stable sort only proves something once its
//! inputs already differ.

use serde_json::Value;
use std::path::Path;
use std::process::{Command, Output};

/// Restarts per scenario. Within the task's 50-100 range; kept at the low
/// end so the full gate (4 scenarios x 2 commands) stays fast in CI.
const RESTARTS: usize = 60;

fn write(root: &Path, relative: &str, source: &str) {
    let path = root.join(relative);
    std::fs::create_dir_all(path.parent().unwrap()).unwrap();
    std::fs::write(path, source).unwrap();
}

fn run(root: &Path, args: &[&str]) -> Output {
    Command::new(env!("CARGO_BIN_EXE_oxide"))
        .args(args)
        .current_dir(root)
        .output()
        .unwrap()
}

fn json_stdout(output: &Output) -> Value {
    assert!(
        output.status.success(),
        "stderr: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    serde_json::from_slice(&output.stdout).unwrap()
}

/// Seeds a repo with several tied-score symbol pairs across files (same
/// body/signature shape, different names) so ranking has real ties to
/// break, plus a couple of cross-file references so RRF/hybrid fusion has
/// something to fuse.
fn seed_tied_repo(root: &Path) {
    for i in 0..4 {
        write(
            root,
            &format!("src/widget_{i}.py"),
            &format!(
                "def widget_{i}():\n    return do_widget_thing()\n\ndef helper_{i}():\n    return widget_{i}()\n"
            ),
        );
    }
    write(
        root,
        "src/shared.py",
        "def do_widget_thing():\n    return 1\n",
    );
    write(
        root,
        "lib/comp.tsx",
        "export function Widget(): number {\n  return 1;\n}\n",
    );
}

/// Runs `search` and `context` `RESTARTS` times each as separate processes
/// and asserts every raw stdout byte string is identical to the first.
fn assert_stable_across_restarts(root: &Path, label: &str) {
    let search_args = ["search", "widget", "--limit", "10", "--json"];
    let first_search = run(root, &search_args);
    assert!(
        first_search.status.success(),
        "{label} search: {}",
        String::from_utf8_lossy(&first_search.stderr)
    );
    for i in 0..RESTARTS {
        let out = run(root, &search_args);
        assert_eq!(
            out.stdout, first_search.stdout,
            "{label}: search output diverged at restart {i}"
        );
    }

    let context_args = [
        "context",
        "--task",
        "understand the widget helpers",
        "--budget-tokens",
        "512",
        "--json",
    ];
    let first_context = run(root, &context_args);
    assert!(
        first_context.status.success(),
        "{label} context: {}",
        String::from_utf8_lossy(&first_context.stderr)
    );
    for i in 0..RESTARTS {
        let out = run(root, &context_args);
        assert_eq!(
            out.stdout, first_context.stdout,
            "{label}: context output diverged at restart {i}"
        );
    }

    // Index result summaries: on an already-current repo, re-indexing must
    // report byte-identical counts across restarts too.
    let index_args = ["index", ".", "--json"];
    let first_index = run(root, &index_args);
    assert!(first_index.status.success());
    for i in 0..RESTARTS.min(20) {
        let out = run(root, &index_args);
        assert_eq!(
            out.stdout, first_index.stdout,
            "{label}: index summary diverged at restart {i}"
        );
    }
}

#[test]
fn deterministic_after_fresh_rebuild() {
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path();
    seed_tied_repo(root);
    json_stdout(&run(root, &["index", ".", "--json"]));

    assert_stable_across_restarts(root, "fresh rebuild");
}

#[test]
fn deterministic_after_incremental_edit() {
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path();
    seed_tied_repo(root);
    json_stdout(&run(root, &["index", ".", "--json"]));

    // Body-only edit to one of the tied symbols, reindexed once.
    write(
        root,
        "src/widget_0.py",
        "def widget_0():\n    return do_widget_thing() + 1\n\ndef helper_0():\n    return widget_0()\n",
    );
    json_stdout(&run(root, &["index", ".", "--json"]));

    assert_stable_across_restarts(root, "incremental edit");
}

#[test]
fn deterministic_after_delete_and_readd() {
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path();
    seed_tied_repo(root);
    json_stdout(&run(root, &["index", ".", "--json"]));

    std::fs::remove_file(root.join("src/widget_1.py")).unwrap();
    json_stdout(&run(root, &["index", ".", "--json"]));
    write(
        root,
        "src/widget_1.py",
        "def widget_1():\n    return do_widget_thing()\n\ndef helper_1():\n    return widget_1()\n",
    );
    json_stdout(&run(root, &["index", ".", "--json"]));

    assert_stable_across_restarts(root, "delete/re-add");
}

#[test]
fn deterministic_after_rename() {
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path();
    seed_tied_repo(root);
    json_stdout(&run(root, &["index", ".", "--json"]));

    std::fs::remove_file(root.join("src/widget_2.py")).unwrap();
    write(
        root,
        "src/renamed_widget_2.py",
        "def widget_2():\n    return do_widget_thing()\n\ndef helper_2():\n    return widget_2()\n",
    );
    json_stdout(&run(root, &["index", ".", "--json"]));

    assert_stable_across_restarts(root, "rename");
}
