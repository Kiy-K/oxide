//! Requires `ty` on `$PATH` (`oxide lsp install ty`, Task 11 of this plan).
//! Reproduces the audit's bug directly: a file edited between two calls
//! against the same cached `LspClient` must not keep serving the first
//! call's `didOpen` snapshot.

use oxide::lsp::LspClient;
use std::time::Duration;

fn ty_available() -> bool {
    std::process::Command::new("ty")
        .arg("--version")
        .output()
        .map(|o| o.status.success())
        .unwrap_or(false)
}

#[test]
fn edit_between_two_ensure_open_calls_is_visible_to_the_server() {
    if !ty_available() {
        eprintln!("skipping: `ty` not on PATH");
        return;
    }
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path();
    std::fs::write(root.join("a.py"), "def old_name():\n    pass\n").unwrap();

    let mut client = LspClient::spawn("ty", root, Duration::from_secs(10), Duration::from_secs(3))
        .expect("ty must spawn");

    let uri = client.ensure_open("a.py").unwrap();
    let pos =
        oxide::lsp::client::symbol_position("def old_name():\n    pass\n", 1, "old_name").unwrap();
    let first = client.definition(&uri, pos).unwrap();
    assert!(!first.is_empty(), "must resolve old_name before the edit");

    std::fs::write(root.join("a.py"), "def new_name():\n    pass\n").unwrap();
    client.ensure_open("a.py").unwrap(); // must send didChange, not a no-op

    let new_pos =
        oxide::lsp::client::symbol_position("def new_name():\n    pass\n", 1, "new_name").unwrap();
    let after_edit = client.definition(&uri, new_pos).unwrap();
    assert!(
        !after_edit.is_empty(),
        "server must see post-edit content, not stale pre-edit text"
    );

    client.close();
}
