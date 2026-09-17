//! Covers the audit's capability-fallback gap with a deterministic fake
//! server instead of depending on `ty`'s specific capability set.

use oxide::lsp::LspClient;
use std::time::Duration;

fn python3_available() -> bool {
    std::process::Command::new("python3")
        .arg("--version")
        .output()
        .map(|o| o.status.success())
        .unwrap_or(false)
}

#[test]
fn references_call_against_a_server_lacking_the_capability_degrades_cleanly() {
    if !python3_available() {
        eprintln!("skipping: python3 not on PATH");
        return;
    }
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path();
    std::fs::write(root.join("a.py"), "def f():\n    pass\n").unwrap();

    let script = concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/fixtures/fake_lsp_no_references/server.py"
    );
    let mut client = LspClient::spawn_raw(
        "python3",
        &[script],
        root,
        Duration::from_secs(5),
        Duration::from_secs(2),
    )
    .expect("fake server must spawn and complete initialize");

    let uri = client.ensure_open("a.py").unwrap();
    let pos = oxide::lsp::client::symbol_position("def f():\n    pass\n", 1, "f").unwrap();
    let result = client.references(&uri, pos);
    assert!(
        result.is_err(),
        "the fake server's -32601 must surface as Err, not a fabricated empty Ok"
    );
    // `enrich_seeds` already wraps every call in `if let Ok(...)` — this
    // pins the client-level contract that sits on: a missing-capability
    // error is a plain `Err`, indistinguishable in kind from a timeout, so
    // existing degrade-and-continue handling covers it with no new branch.

    client.close();
}
