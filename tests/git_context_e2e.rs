//! Git-aware context edge cases: staged/unstaged, renames, deletions, merge
//! commits, shallow clones, detached HEAD, no-git repos, determinism, and
//! incremental-index interaction.
//!
//! `src/gitctx.rs`'s and `src/gitutil.rs`'s own unit tests pin the mapping
//! and parsing logic against synthetic inputs; this file pins the same
//! contracts against a *real* git repo and (for the CLI-facing cases) the
//! real `oxide` binary, the way `tests/review_e2e.rs` and
//! `tests/blast_radius_e2e.rs` already do for their features.

use oxide::embeddings::HashedEmbedder;
use oxide::gitctx::build_git_context;
use oxide::index::{update_index, SqliteStore};
use serde_json::Value;
use std::path::Path;
use std::process::{Command, Output};

fn write(path: impl AsRef<Path>, content: &str) {
    let path = path.as_ref();
    std::fs::create_dir_all(path.parent().unwrap()).unwrap();
    std::fs::write(path, content).unwrap();
}

fn git(dir: &Path, args: &[&str]) {
    let st = Command::new("git")
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

fn git_ok(dir: &Path, args: &[&str]) -> bool {
    Command::new("git")
        .args(args)
        .current_dir(dir)
        .env("GIT_AUTHOR_NAME", "t")
        .env("GIT_AUTHOR_EMAIL", "t@t")
        .env("GIT_COMMITTER_NAME", "t")
        .env("GIT_COMMITTER_EMAIL", "t@t")
        .status()
        .unwrap()
        .success()
}

/// Index `root` in-memory and return the current symbol set — enough for
/// `build_git_context`'s `symbols` parameter without going through
/// `RetrievalEngine`.
fn indexed_symbols(root: &Path) -> Vec<oxide::symbols::Symbol> {
    let mut store = SqliteStore::open(Path::new(":memory:")).unwrap();
    update_index(root, &mut store, &HashedEmbedder::default()).unwrap();
    oxide::storage::IndexBackend::all_symbols(&store).unwrap()
}

fn run(root: &Path, args: &[&str]) -> Output {
    Command::new(env!("CARGO_BIN_EXE_oxide"))
        .args(args)
        .env("OXIDE_EMBED_NATIVE", "hashed")
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

// ---------------------------------------------------------------- staged/unstaged

#[test]
fn staged_and_unstaged_changes_both_surface() {
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path();
    write(root.join("a.py"), "def one():\n    return 1\n");
    write(root.join("b.py"), "def two():\n    return 2\n");
    git(root, &["init", "-q"]);
    git(root, &["add", "."]);
    git(root, &["commit", "-qm", "base"]);

    // a.py: staged change. b.py: unstaged change.
    write(root.join("a.py"), "def one():\n    return 11\n");
    git(root, &["add", "a.py"]);
    write(root.join("b.py"), "def two():\n    return 22\n");

    let symbols = indexed_symbols(root);
    let ctx = build_git_context(root, &symbols, "").unwrap();
    let mut files = ctx.evidence.changed_files.clone();
    files.sort();
    assert_eq!(
        files,
        vec!["a.py".to_string(), "b.py".to_string()],
        "{ctx:?}"
    );
}

// ----------------------------------------------------------------- renames

#[test]
fn rename_attributes_to_the_new_path() {
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path();
    write(
        root.join("old_name.py"),
        "class Widget:\n    def render(self):\n        return 1\n",
    );
    git(root, &["init", "-q"]);
    git(root, &["add", "."]);
    git(root, &["commit", "-qm", "base"]);

    git(root, &["mv", "old_name.py", "new_name.py"]);
    write(
        root.join("new_name.py"),
        "class Widget:\n    def render(self):\n        return 2\n",
    );

    let symbols = indexed_symbols(root);
    let ctx = build_git_context(root, &symbols, "").unwrap();
    assert!(
        ctx.evidence
            .changed_files
            .iter()
            .any(|f| f == "new_name.py"),
        "{:?}",
        ctx.evidence.changed_files
    );
    let names: Vec<&str> = ctx
        .changed_symbols
        .iter()
        .map(|c| c.symbol.qualified_name.as_str())
        .collect();
    assert!(names.contains(&"Widget.render"), "{names:?}");
}

// --------------------------------------------------------------- deletions

#[test]
fn partial_deletion_in_a_real_repo_attributes_to_the_enclosing_symbol() {
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path();
    write(
        root.join("a.py"),
        "class Foo:\n    def bar(self):\n        x = 1\n        y = 2\n        return x + y\n",
    );
    git(root, &["init", "-q"]);
    git(root, &["add", "."]);
    git(root, &["commit", "-qm", "base"]);

    // Delete one line from inside Foo.bar's body; the method itself survives.
    write(
        root.join("a.py"),
        "class Foo:\n    def bar(self):\n        x = 1\n        return x\n",
    );

    let symbols = indexed_symbols(root);
    let ctx = build_git_context(root, &symbols, "").unwrap();
    let names: Vec<&str> = ctx
        .changed_symbols
        .iter()
        .map(|c| c.symbol.qualified_name.as_str())
        .collect();
    assert!(names.contains(&"Foo.bar"), "{names:?}");
}

#[test]
fn whole_file_deletion_is_listed_but_maps_to_no_symbol() {
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path();
    write(root.join("gone.py"), "def doomed():\n    return 1\n");
    write(root.join("keep.py"), "def survives():\n    return 2\n");
    git(root, &["init", "-q"]);
    git(root, &["add", "."]);
    git(root, &["commit", "-qm", "base"]);

    std::fs::remove_file(root.join("gone.py")).unwrap();
    git(root, &["add", "-A"]);

    // Index reflects the post-deletion state, same as the working tree.
    let symbols = indexed_symbols(root);
    let ctx = build_git_context(root, &symbols, "").unwrap();
    assert!(
        ctx.evidence.changed_files.iter().any(|f| f == "gone.py"),
        "deleted file must still be surfaced: {:?}",
        ctx.evidence.changed_files
    );
    assert!(
        ctx.changed_symbols
            .iter()
            .all(|c| c.symbol.file != "gone.py"),
        "a deleted file's symbols no longer exist in the current index: {:?}",
        ctx.changed_symbols
    );
}

// ------------------------------------------------------------ merge commits

#[test]
fn merge_commit_neither_crashes_nor_pollutes_co_change() {
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path();
    write(root.join("a.py"), "x = 1\n");
    git(root, &["init", "-q"]);
    git(root, &["checkout", "-qb", "main"]);
    git(root, &["add", "."]);
    git(root, &["commit", "-qm", "base"]);

    git(root, &["checkout", "-qb", "feature"]);
    write(root.join("b.py"), "y = 2\n");
    git(root, &["add", "."]);
    git(root, &["commit", "-qm", "add b"]);

    git(root, &["checkout", "-q", "main"]);
    write(root.join("c.py"), "z = 3\n");
    git(root, &["add", "."]);
    git(root, &["commit", "-qm", "add c"]);

    assert!(
        git_ok(root, &["merge", "-q", "--no-edit", "feature"]),
        "merge must succeed (no conflict expected)"
    );

    write(root.join("a.py"), "x = 11\n");
    let symbols = indexed_symbols(root);
    // Must not panic/error despite a merge commit in history.
    let ctx = build_git_context(root, &symbols, "").unwrap();
    assert!(ctx.evidence.changed_files.contains(&"a.py".to_string()));
    // --no-merges means the merge commit itself never appears as "recent".
    assert!(
        ctx.evidence
            .recent_commits
            .iter()
            .all(|c| !c.message.to_lowercase().starts_with("merge")),
        "{:?}",
        ctx.evidence.recent_commits
    );
}

/// Regression for a Codex-review MAJOR: the repo's root (parentless) commit
/// touching several files together used to manufacture a spurious co-change
/// pair — "added together once" is not the same signal as "repeatedly
/// maintained together". `co_change_raw` no longer passes `--root` to
/// `git diff-tree`, so a parentless commit's file list is silently skipped
/// rather than counted.
#[test]
fn root_commit_does_not_manufacture_a_co_change_pair() {
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path();
    // Root commit: a.py and b.py added together — no maintenance signal.
    write(root.join("a.py"), "x = 1\n");
    write(root.join("b.py"), "y = 1\n");
    git(root, &["init", "-q"]);
    git(root, &["add", "."]);
    git(root, &["commit", "-qm", "root: add a and b together"]);

    // A later commit touches only a.py — never again alongside b.py.
    write(root.join("a.py"), "x = 2\n");
    git(root, &["add", "."]);
    git(root, &["commit", "-qm", "touch a alone"]);

    write(root.join("a.py"), "x = 3\n");
    let symbols = indexed_symbols(root);
    let ctx = build_git_context(root, &symbols, "").unwrap();
    assert!(
        ctx.evidence
            .co_change
            .iter()
            .all(|e| e.co_changed_with != "b.py"),
        "the root commit's joint add must not count as co-change: {:?}",
        ctx.evidence.co_change
    );
}

// -------------------------------------------------------------- shallow clones

#[test]
fn shallow_clone_does_not_error() {
    let origin = tempfile::tempdir().unwrap();
    let origin_root = origin.path();
    git(origin_root, &["init", "-q"]);
    for i in 0..5 {
        write(origin_root.join("a.py"), &format!("x = {i}\n"));
        git(origin_root, &["add", "."]);
        git(origin_root, &["commit", "-qm", &format!("commit {i}")]);
    }

    let shallow = tempfile::tempdir().unwrap();
    // `--depth` is silently ignored for a plain local path (git treats it as
    // a hardlink-able local clone); `file://` forces the real shallow-clone
    // transport so this test exercises what it claims to.
    let st = Command::new("git")
        .args([
            "clone",
            "-q",
            "--depth",
            "1",
            &format!("file://{}", origin_root.display()),
            shallow.path().to_str().unwrap(),
        ])
        .status()
        .unwrap();
    assert!(st.success());
    assert!(
        shallow.path().join(".git/shallow").exists(),
        "clone must actually be shallow for this test to mean anything"
    );
    // A shallow clone needs an identity for the working-tree edit below.
    git(shallow.path(), &["config", "user.email", "t@t"]);
    git(shallow.path(), &["config", "user.name", "t"]);

    write(shallow.path().join("a.py"), "x = 999\n");
    let symbols = indexed_symbols(shallow.path());
    // Must not error even though history only goes back one commit.
    let ctx = build_git_context(shallow.path(), &symbols, "").unwrap();
    assert!(ctx.evidence.changed_files.contains(&"a.py".to_string()));
}

// -------------------------------------------------------------- detached HEAD

#[test]
fn detached_head_works() {
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path();
    write(root.join("a.py"), "x = 1\n");
    git(root, &["init", "-q"]);
    git(root, &["add", "."]);
    git(root, &["commit", "-qm", "base"]);
    write(root.join("a.py"), "x = 2\n");
    git(root, &["add", "."]);
    git(root, &["commit", "-qm", "second"]);

    git(root, &["checkout", "-q", "HEAD~1"]);
    assert!(
        !git_ok(root, &["symbolic-ref", "-q", "HEAD"]),
        "must actually be detached for this test to mean anything"
    );

    write(root.join("a.py"), "x = 3\n");
    let symbols = indexed_symbols(root);
    let ctx = build_git_context(root, &symbols, "").unwrap();
    assert!(ctx.evidence.changed_files.contains(&"a.py".to_string()));
}

// -------------------------------------------------------------- no-git repos

#[test]
fn non_git_directory_degrades_for_both_query_and_review() {
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path();
    write(root.join("a.py"), "def f():\n    return 1\n");
    assert!(run(root, &["index", ".", "--json"]).status.success());

    let query = json_stdout(&run(root, &["query", "f function", "--git", "--json"]));
    let git_field = &query["git"];
    assert!(git_field.is_object(), "{query:#}");
    assert_eq!(git_field["changed_files"], serde_json::json!([]));

    let review = run(root, &["review", "--json"]);
    assert!(
        review.status.success(),
        "review on a non-git directory must degrade, not error: {}",
        String::from_utf8_lossy(&review.stderr)
    );
    let review_json: Value = serde_json::from_slice(&review.stdout).unwrap();
    assert_eq!(review_json["changed_files"], serde_json::json!([]));
}

// --------------------------------------------------------------- determinism

#[test]
fn git_query_output_is_deterministic_across_repeated_runs() {
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path();
    write(
        root.join("src/store.py"),
        "class TokenStore:\n    def refresh(self, token):\n        return token\n",
    );
    write(
        root.join("src/client.py"),
        "from src.store import TokenStore\n\n\ndef handle(request):\n    return TokenStore().refresh(request)\n",
    );
    git(root, &["init", "-q"]);
    git(root, &["add", "."]);
    git(root, &["commit", "-qm", "base"]);
    write(
        root.join("src/store.py"),
        "class TokenStore:\n    def refresh(self, token):\n        return token.strip()\n",
    );
    assert!(run(root, &["index", ".", "--json"]).status.success());

    let first = json_stdout(&run(root, &["query", "refresh a token", "--git", "--json"]));
    let second = json_stdout(&run(root, &["query", "refresh a token", "--git", "--json"]));
    assert_eq!(
        first, second,
        "identical repo state must produce identical output"
    );
}

#[test]
fn absent_git_flag_leaves_query_json_byte_identical() {
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path();
    write(root.join("a.py"), "def f():\n    return 1\n");
    git(root, &["init", "-q"]);
    git(root, &["add", "."]);
    git(root, &["commit", "-qm", "base"]);
    write(root.join("a.py"), "def f():\n    return 2\n");
    assert!(run(root, &["index", ".", "--json"]).status.success());

    let without = run(root, &["query", "the f function", "--json"]);
    let text = String::from_utf8(without.stdout).unwrap();
    assert!(
        !text.contains("\"git\""),
        "query --json must carry no git field when --git is absent: {text}"
    );
}

// ------------------------------------------------------ incremental index interaction

#[test]
fn git_evidence_reflects_the_currently_indexed_working_tree_state() {
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path();
    write(
        root.join("a.py"),
        "def handler():\n    return old_helper()\n\ndef old_helper():\n    return 1\n",
    );
    git(root, &["init", "-q"]);
    git(root, &["add", "."]);
    git(root, &["commit", "-qm", "base"]);
    assert!(run(root, &["index", ".", "--json"]).status.success());

    // Uncommitted rename of the helper + incremental reindex: the index and
    // the diff must agree on the *current* symbol name, not the committed one.
    write(
        root.join("a.py"),
        "def handler():\n    return new_helper()\n\ndef new_helper():\n    return 1\n",
    );
    assert!(run(root, &["index", ".", "--json"]).status.success());

    let query = json_stdout(&run(
        root,
        &[
            "query",
            "handler function",
            "--profile",
            "fast",
            "--git",
            "--json",
        ],
    ));
    let names: Vec<String> = query["items"]
        .as_array()
        .unwrap()
        .iter()
        .filter_map(|it| it["qualified_name"].as_str().map(str::to_string))
        .collect();
    assert!(
        names.iter().any(|n| n == "new_helper" || n == "handler"),
        "incrementally-reindexed current state must be what git evidence maps against: {names:?}"
    );
    assert!(
        !names.iter().any(|n| n == "old_helper"),
        "the pre-rename name must not appear — it no longer exists in the current index: {names:?}"
    );
    assert!(
        query["items"]
            .as_array()
            .unwrap()
            .iter()
            .flat_map(|item| item["reasons"].as_array().unwrap())
            .any(|reason| reason.as_str().unwrap().starts_with("git-")),
        "Fast mode must preserve explicitly requested Git evidence: {query}"
    );
}

// -------------------------------------------------- callers_of() must be scoped

/// Regression for a Codex-review BLOCKER: `context.rs`'s git block called
/// `graph.callers_of()` (repo-wide, bare-name matching, no scope resolution
/// — AGENTS.md) with only an item-count cap, no file-scope filter. A changed
/// symbol with a common bare name could pull an unrelated caller of a
/// same-named-but-different symbol from anywhere in the repo. This sets up
/// exactly that ambiguity — two files each define their own `helper()` and
/// call it — and asserts the out-of-scope caller is never tagged with the
/// git-derived reason (it may still legitimately appear via ordinary
/// retrieval; that isn't what this pins).
///
/// A corpus of only a handful of symbols makes every symbol a weak-but-equal
/// hybrid-search "primary" (nothing to rank below, in a repo this small —
/// confirmed empirically before writing this padding in), which would put
/// every file in the seed pool regardless of the fix. Enough padding files
/// with partial lexical overlap on the query outrank the truly-unrelated
/// file's exact-zero score, so it falls out of the seed pool for real.
#[test]
fn git_caller_expansion_is_scoped_to_the_seed_pool_not_repo_wide() {
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path();
    write(root.join("src/target.py"), "def helper():\n    return 1\n");
    // In scope: the query text below matches this file/function directly,
    // and it's a genuine caller of the changed `helper`.
    write(
        root.join("src/caller_in_scope.py"),
        "from src.target import helper\n\n\ndef process_widget_request():\n    return helper()\n",
    );
    // Out of scope: shares no tokens with the query text at all, so with
    // enough padding it must not become a seed on its own — its only route
    // in would be an unscoped `callers_of(\"helper\")` bare-name match,
    // which is exactly what the fix removes.
    write(
        root.join("unrelated/other_module.py"),
        "def helper():\n    return 99\n\n\ndef totally_unconnected_function():\n    return helper()\n",
    );
    // Padding: partial lexical overlap on "process"/"widget"/"request"
    // outranks other_module.py's exact-zero score, pushing it out of the
    // default `max_candidates` (16) seed window.
    for i in 0..20 {
        write(
            root.join(format!("padding_{i}.py")),
            &format!(
                "def process_something_{i}():\n    return widget_request_helper_{i}()\n\ndef widget_request_helper_{i}():\n    return {i}\n"
            ),
        );
    }
    git(root, &["init", "-q"]);
    git(root, &["add", "."]);
    git(root, &["commit", "-qm", "base"]);
    assert!(run(root, &["index", ".", "--json"]).status.success());

    // Uncommitted change to the target `helper` — the git-changed seed.
    write(root.join("src/target.py"), "def helper():\n    return 2\n");
    assert!(run(root, &["index", ".", "--json"]).status.success());

    // Deliberately no "helper" token here — see the comment above.
    let query = json_stdout(&run(
        root,
        &["query", "process widget request", "--git", "--json"],
    ));
    let items = query["items"].as_array().unwrap();
    let reasons_for = |name: &str| -> Vec<String> {
        items
            .iter()
            .filter(|it| it["qualified_name"] == name)
            .flat_map(|it| {
                it["reasons"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .map(|r| r.as_str().unwrap().to_string())
            })
            .collect()
    };
    assert!(
        reasons_for("process_widget_request")
            .iter()
            .any(|r| r.starts_with("git-caller-of-changed")),
        "the in-scope caller should surface via git evidence: {:?}",
        reasons_for("process_widget_request")
    );
    let out_of_scope_reasons = reasons_for("totally_unconnected_function");
    assert!(
        !out_of_scope_reasons
            .iter()
            .any(|r| r.starts_with("git-caller-of-changed")),
        "an out-of-scope same-named caller must never be tagged git-caller-of-changed: {out_of_scope_reasons:?}"
    );
}
