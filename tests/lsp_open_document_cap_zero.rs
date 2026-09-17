//! Regression for a real Codex-review finding: `$OXIDE_LSP_MAX_OPEN_DOCUMENTS=0`
//! made `evict_if_over_cap`'s `len() >= max` check true even on an empty
//! `opened` Vec, so the first `ensure_open` call panicked on
//! `Vec::remove(0)` against an empty Vec instead of just opening the
//! document.
//!
//! In its own file/process, not folded into `lsp_open_document_cap.rs`:
//! both set `$OXIDE_LSP_MAX_OPEN_DOCUMENTS`/`LOG_PATH`, process-global env
//! vars, and tests in the same binary run concurrently by default — folding
//! this in caused exactly that race the first time (confirmed empirically:
//! the sibling test's cap value leaked into this one's run and vice versa).
//!
//! Skipped, not failed, when `python3` isn't on PATH.

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
fn a_zero_document_cap_does_not_panic() {
    if !python3_available() {
        eprintln!("skipping: python3 not on PATH");
        return;
    }

    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path();
    write_file(root, "a.py", "x = 1\n");
    let log_path = tmp.path().join("log.txt");

    // SAFETY: this test binary's only env writes, set before the child
    // process (which inherits this process's environment) is spawned.
    unsafe {
        std::env::set_var("LOG_PATH", log_path.to_str().unwrap());
        std::env::set_var("OXIDE_LSP_MAX_OPEN_DOCUMENTS", "0");
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

    // Must not panic.
    client.ensure_open("a.py").unwrap();

    client.close();
    unsafe {
        std::env::remove_var("OXIDE_LSP_MAX_OPEN_DOCUMENTS");
        std::env::remove_var("LOG_PATH");
    }
}
