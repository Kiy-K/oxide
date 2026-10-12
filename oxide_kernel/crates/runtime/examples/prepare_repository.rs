//! Test fixture preparation for src/client.test.ts, not a product command:
//! registers `<root>` in the XDG data location (as `oxide-runtime` resolves
//! it) and, unless `--register-only`, runs the Phase 2B full rebuild. Prints
//! the registry entry and published key as one JSON line.
//!
//! `cargo run --example prepare_repository -- <root> [--register-only]`

use oxide_runtime::capture::Scope;
use oxide_runtime::repository::{DataLocation, RepositorySession};
use serde_json::json;

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let (root, register_only) = match args.as_slice() {
        [root] => (root, false),
        [root, flag] if flag == "--register-only" => (root, true),
        _ => panic!("usage: prepare_repository <root> [--register-only]"),
    };
    let location = DataLocation::linux().expect("data location");
    let mut session = RepositorySession::open(root.as_ref(), &location).expect("register");
    let key = (!register_only).then(|| session.rebuild(&Scope::default()).expect("rebuild").key);
    println!(
        "{}",
        json!({
            "repo_id": session.repo_id.as_str(),
            "store_directory": session.store_directory,
            "snapshot": key.as_ref().map(|k| k.snapshot.as_str()),
            "derivation": key.as_ref().map(|k| k.derivation.as_str()),
        })
    );
}
