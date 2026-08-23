//! Review context end-to-end: real git repo, real diff, related-test surfacing.

use oxide::embeddings::HashedEmbedder;
use oxide::index::{update_index, SqliteStore};
use std::path::Path;

fn git(dir: &Path, args: &[&str]) {
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

#[test]
fn review_finds_changed_symbols_and_related_test() {
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path();

    // Commit 1: retry policy + a client that uses it + a test.
    write(
        root.join("src/retry.py"),
        "class RetryPolicy:\n    def should_retry(self, attempt):\n        return attempt < 3\n",
    );
    write(
        root.join("src/client.py"),
        "from src.retry import RetryPolicy\n\nclass ApiClient:\n    policy = RetryPolicy()\n    def get(self, url):\n        return self.policy.should_retry(1)\n",
    );
    write(
        root.join("tests/test_retry.py"),
        "from src.retry import RetryPolicy\n\ndef test_should_retry_allows_three():\n    assert RetryPolicy().should_retry(1)\n",
    );
    git(root, &["init", "-q"]);
    git(root, &["add", "."]);
    git(root, &["commit", "-qm", "base"]);

    // Commit 2: change one method inside RetryPolicy.
    write(
        root.join("src/retry.py"),
        "class RetryPolicy:\n    def should_retry(self, attempt):\n        return attempt < 5\n",
    );
    git(root, &["add", "."]);
    git(root, &["commit", "-qm", "bump attempts"]);

    // Index at HEAD (the changed state).
    let mut store = SqliteStore::open(Path::new(":memory:")).unwrap();
    update_index(root, &mut store, &HashedEmbedder::default()).unwrap();

    let ctx = oxide::review::build_review_context(root, &store, &HashedEmbedder::default(), "HEAD~1")
        .unwrap();

    let changed: Vec<&str> = ctx
        .changed_symbols
        .iter()
        .map(|c| c.symbol.qualified_name.as_str())
        .collect();
    assert!(changed.contains(&"RetryPolicy.should_retry"), "{changed:?}");
    assert!(!changed.contains(&"ApiClient.get"), "unchanged symbol must not be flagged: {changed:?}");

    let related: Vec<&str> = ctx
        .related
        .iter()
        .map(|h| h.symbol.qualified_name.as_str())
        .collect();
    assert!(
        related.iter().any(|r| r.contains("test_should_retry")),
        "review context must include the related test: {related:?}"
    );
}

fn write(path: impl AsRef<std::path::Path>, content: &str) {
    let path = path.as_ref();
    std::fs::create_dir_all(path.parent().unwrap()).unwrap();
    std::fs::write(path, content).unwrap();
}
