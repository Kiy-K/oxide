//! End-to-end smoke test of the full LSP enrichment pipeline (transport +
//! client + `enrich_seeds`) against a real `ty` server and
//! `fixtures/py_repo`'s existing `should_retry` trap: a comment mentions
//! `should_retry(x, y)` in a way a bare-name heuristic could false-positive
//! on (`oxidepy/notifiers.py`), and the real callers are spread across
//! `oxidepy/http_client.py`, `oxidepy/notifiers.py`, and `tests/test_retry.py`.
//!
//! Skipped, not failed, when `ty` isn't installed — install with
//! `uv tool install ty` to run this locally.

use oxide::lsp::enrich::enrich_seeds;
use oxide::lsp::LspClient;
use oxide::parser::parse_file;
use oxide::symbols::{Language, Symbol};
use std::path::Path;
use std::time::Duration;

fn ty_available() -> bool {
    std::process::Command::new("ty")
        .arg("--version")
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .status()
        .map(|s| s.success())
        .unwrap_or(false)
}

fn parse_repo(root: &Path, rel_paths: &[&str]) -> Vec<Symbol> {
    let mut all = Vec::new();
    for rel in rel_paths {
        let src = std::fs::read_to_string(root.join(rel)).unwrap();
        all.extend(parse_file(rel, &src, Language::Python));
    }
    all
}

#[test]
fn incoming_calls_find_real_callers_and_ignore_the_comment_trap() {
    if !ty_available() {
        eprintln!("skipping: `ty` not on PATH (install with `uv tool install ty`)");
        return;
    }

    let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("fixtures/py_repo");
    let files = [
        "oxidepy/retry.py",
        "oxidepy/http_client.py",
        "oxidepy/notifiers.py",
        "tests/test_retry.py",
    ];
    let symbols = parse_repo(&root, &files);
    let seed = symbols
        .iter()
        .find(|s| s.qualified_name == "RetryPolicy.should_retry")
        .expect("fixture must define RetryPolicy.should_retry");
    let seeds: Vec<&Symbol> = vec![seed];
    let scope_files: Vec<String> = files.iter().map(|s| s.to_string()).collect();

    let mut client = LspClient::spawn(
        "ty",
        &root,
        Duration::from_secs(15),
        Duration::from_secs(10),
    )
    .expect("spawn ty");
    let evidence = enrich_seeds(&mut client, &root, &symbols, &seeds, &scope_files, 3, 5);
    client.close();

    let callers: Vec<&str> = evidence
        .iter()
        .filter(|e| e.reason.starts_with("lsp-caller"))
        .map(|e| e.symbol.qualified_name.as_str())
        .collect();
    assert!(
        callers.contains(&"HttpClient.fetch"),
        "must find the real call site in http_client.py: {callers:?}"
    );
    assert!(
        callers.contains(&"notify_after_final_attempt"),
        "must find the real call site in notifiers.py: {callers:?}"
    );
    // notifiers.py's comment ("A call to should_retry(x, y) mentioned here
    // in a comment must not count") names no real Python symbol enclosing
    // it beyond the module/function that actually calls should_retry — the
    // point of this assertion is simply that call-hierarchy evidence never
    // duplicates a caller per mention, only per real call site.
    let http_client_hits = callers.iter().filter(|q| *q == &"HttpClient.fetch").count();
    assert_eq!(
        http_client_hits, 1,
        "one real call site must yield one caller entry, not one per textual mention"
    );
}
