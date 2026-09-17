//! Regression test for hardening-pass item #1's bounded-document-set
//! requirement: `ensure_open` must `didClose` the least-recently-touched
//! open document once `$OXIDE_LSP_MAX_OPEN_DOCUMENTS` is exceeded, not
//! grow the tracked/resynced set without bound.
//!
//! Uses a small logging fake server (`fixtures/fake_lsp_logging/server.py`)
//! rather than a real `ty` session: this needs to observe exact wire
//! traffic (which URIs got `didOpen`/`didClose`, and in what order), which
//! no server's *responses* expose — a real server's protocol-level
//! behavior isn't what's under test here, OXIDE's own bookkeeping is.
//!
//! Skipped, not failed, when `python3` isn't on PATH — same optionality
//! contract as `lsp_capability_fallback.rs`.

use oxide::lsp::LspClient;
use std::path::Path;
use std::time::Duration;

fn python3_available() -> bool {
    std::process::Command::new("python3")
        .arg("--version")
        .output()
        .map(|o| o.status.success())
        .unwrap_or(false)
}

fn write_file(root: &Path, rel: &str, content: &str) {
    std::fs::write(root.join(rel), content).unwrap();
}

#[test]
fn ensure_open_closes_the_least_recently_touched_document_over_the_cap() {
    if !python3_available() {
        eprintln!("skipping: python3 not on PATH");
        return;
    }

    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path();
    for name in ["a.py", "b.py", "c.py"] {
        write_file(root, name, "x = 1\n");
    }
    let log_path = tmp.path().join("log.txt");

    // SAFETY: this test binary's only env writes, set before the child
    // process (which inherits this process's environment) is spawned, and
    // no other test in this file touches process env concurrently.
    unsafe {
        std::env::set_var("LOG_PATH", log_path.to_str().unwrap());
        std::env::set_var("OXIDE_LSP_MAX_OPEN_DOCUMENTS", "2");
    }

    let script = concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/fixtures/fake_lsp_logging/server.py"
    );
    let mut client = LspClient::spawn_raw(
        "python3",
        &[script],
        root,
        Duration::from_secs(5),
        Duration::from_secs(2),
    )
    .expect("fake server must spawn and complete initialize");

    client.ensure_open("a.py").unwrap(); // opened: [a]
    client.ensure_open("b.py").unwrap(); // opened: [a, b] -- at the cap (2)
    client.ensure_open("c.py").unwrap(); // over cap: a (least-recently-touched) is closed, c opens

    client.close();
    unsafe {
        std::env::remove_var("OXIDE_LSP_MAX_OPEN_DOCUMENTS");
        std::env::remove_var("LOG_PATH");
    }

    let log = std::fs::read_to_string(&log_path).unwrap();
    let opens_a = log
        .lines()
        .filter(|l| l.starts_with("textDocument/didOpen") && l.ends_with("a.py"))
        .count();
    let closes_a = log
        .lines()
        .filter(|l| l.starts_with("textDocument/didClose") && l.ends_with("a.py"))
        .count();
    let closes_b = log
        .lines()
        .filter(|l| l.starts_with("textDocument/didClose") && l.ends_with("b.py"))
        .count();
    let closes_c = log
        .lines()
        .filter(|l| l.starts_with("textDocument/didClose") && l.ends_with("c.py"))
        .count();

    assert_eq!(opens_a, 1, "a.py must have been didOpen'd once: {log}");
    assert_eq!(
        closes_a, 1,
        "a.py must be evicted (didClose'd) once b and c push it over the cap: {log}"
    );
    assert_eq!(
        closes_b, 0,
        "b.py must still be live (only a.py, the least-recently-touched, is evicted): {log}"
    );
    assert_eq!(
        closes_c, 0,
        "c.py must still be live (it's the document that was just opened): {log}"
    );

    // Eviction must happen BEFORE the new document's didOpen, not after —
    // ensure_open's own ordering contract (evict_if_over_cap runs first).
    let close_a_idx = log
        .lines()
        .position(|l| l.starts_with("textDocument/didClose") && l.ends_with("a.py"))
        .unwrap();
    let open_c_idx = log
        .lines()
        .position(|l| l.starts_with("textDocument/didOpen") && l.ends_with("c.py"))
        .unwrap();
    assert!(
        close_a_idx < open_c_idx,
        "a.py's didClose must precede c.py's didOpen: {log}"
    );
}
