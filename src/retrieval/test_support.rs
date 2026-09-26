//! Fixtures shared by the retrieval submodules' unit tests.

use crate::storage::SqliteStore;
use crate::symbols::{content_hash, Symbol, SymbolKind};

pub(super) fn sym(file: &str, qname: &str, kind: SymbolKind, sig: &str, refs: &[&str]) -> Symbol {
    let name = qname.rsplit('.').next().unwrap().to_string();
    Symbol {
        qualified_name: qname.into(),
        name,
        kind,
        language: if file.ends_with(".py") {
            crate::symbols::Language::Python
        } else {
            crate::symbols::Language::TypeScript
        },
        file: file.into(),
        start_line: 1,
        end_line: 5,
        content_hash: content_hash(sig),
        signature: sig.into(),
        imports: vec![],
        exported: true,
        parent: None,
        references: refs.iter().map(|s| s.to_string()).collect(),
        calls: Vec::new(),
        bases: Vec::new(),
        completeness: Default::default(),
    }
}

pub(super) fn seed_store() -> SqliteStore {
    let store = SqliteStore::open(std::path::Path::new(":memory:")).unwrap();
    store
}

/// Deterministic LCG so the parity tests below are reproducible without
/// a `rand` dependency.
pub(super) fn lcg(seed: &mut u64) -> u64 {
    *seed = seed
        .wrapping_mul(6364136223846793005)
        .wrapping_add(1442695040888963407);
    *seed >> 11
}

fn git(dir: &std::path::Path, args: &[&str]) {
    let st = std::process::Command::new("git")
        .args(args)
        .current_dir(dir)
        .env("GIT_AUTHOR_NAME", "t")
        .env("GIT_AUTHOR_EMAIL", "t@t")
        .env("GIT_COMMITTER_NAME", "t")
        .env("GIT_COMMITTER_EMAIL", "t@t")
        .status()
        .unwrap();
    assert!(st.success(), "git {args:?}");
}

fn copy_dir(from: &std::path::Path, to: &std::path::Path) {
    std::fs::create_dir_all(to).unwrap();
    for e in std::fs::read_dir(from).unwrap() {
        let e = e.unwrap();
        let dst = to.join(e.file_name());
        if e.file_type().unwrap().is_dir() {
            copy_dir(&e.path(), &dst);
        } else {
            std::fs::copy(e.path(), dst).unwrap();
        }
    }
}

/// A committed fixture repo in a temp git repo with a non-empty diff
/// against `HEAD` (`changed`'s second half was committed, then undone
/// with `reset --soft`), while the working tree — and so the index —
/// is exactly the fixture: `--git` and `review` get real changed seeds.
pub(super) fn fixture_repo(name: &str, changed: &str) -> tempfile::TempDir {
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path();
    copy_dir(
        &std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("fixtures")
            .join(name),
        root,
    );
    let full = std::fs::read_to_string(root.join(changed)).unwrap();
    let lines: Vec<&str> = full.lines().collect();
    std::fs::write(root.join(changed), lines[..lines.len() / 2].join("\n")).unwrap();
    git(root, &["init", "-q"]);
    git(root, &["add", "-A"]);
    git(root, &["commit", "-qm", "base"]);
    std::fs::write(root.join(changed), &full).unwrap();
    git(root, &["commit", "-qam", "change"]);
    git(root, &["reset", "-q", "--soft", "HEAD~1"]);
    tmp
}

pub(super) fn json(v: &impl serde::Serialize) -> String {
    serde_json::to_string(v).unwrap()
}
