use serde_json::Value;
use std::path::Path;
use std::process::{Command, Output};

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
    assert!(
        output.stderr.is_empty(),
        "unexpected stderr: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    serde_json::from_slice(&output.stdout).unwrap()
}

#[test]
fn index_status_search_context_form_one_machine_readable_flow() {
    let tmp = tempfile::tempdir().unwrap();
    write(
        tmp.path(),
        "src/auth.py",
        "class AuthService:\n    def refresh_token(self, token):\n        return validate_refresh_token(token)\n\ndef validate_refresh_token(token):\n    return token\n",
    );
    write(
        tmp.path(),
        "tests/test_auth.py",
        "def test_refresh_token():\n    assert True\n",
    );

    let indexed = json_stdout(&run(tmp.path(), &["index", ".", "--json"]));
    assert_eq!(indexed["changed_files"], 2);
    assert_eq!(indexed["removed_files"], 0);
    assert!(indexed["embedded_symbols"].as_u64().unwrap() > 0);

    let status = json_stdout(&run(tmp.path(), &["status", ".", "--json"]));
    assert_eq!(status["index_exists"], true);
    assert_eq!(status["is_current"], true);
    assert_eq!(status["embedder_current"], true);
    assert_eq!(status["files"], 2);
    assert_eq!(
        status["root"].as_str().unwrap(),
        tmp.path().canonicalize().unwrap().to_str().unwrap()
    );
    assert!(status["supported_languages"]
        .as_array()
        .unwrap()
        .iter()
        .any(|v| v == "python"));

    let search = json_stdout(&run(tmp.path(), &["search", "refresh token", "--json"]));
    let hits = search.as_array().unwrap();
    assert!(!hits.is_empty());
    let hit = &hits[0];
    for field in [
        "id",
        "file",
        "qualified_name",
        "name",
        "kind",
        "language",
        "start_line",
        "end_line",
        "score",
        "reasons",
        "snippet",
    ] {
        assert!(hit.get(field).is_some(), "missing search field {field}");
    }
    assert!(hit["id"].as_str().unwrap().contains('#'));
    assert!(hit.get("references").is_none());
    assert!(hit.get("imports").is_none());

    let context = json_stdout(&run(
        tmp.path(),
        &[
            "context",
            "--task",
            "fix refresh token validation",
            "--budget-tokens",
            "128",
            "--json",
        ],
    ));
    assert_eq!(context["task"], "fix refresh token validation");
    assert!(context.get("query_used").is_none());
    assert!(context["used_tokens"].as_u64().unwrap() <= 128);
    assert!(context["items"].is_array());
}

#[test]
fn repeated_indexing_and_renames_report_incremental_work() {
    let tmp = tempfile::tempdir().unwrap();
    write(tmp.path(), "src/old.py", "def old_name():\n    return 1\n");

    let first = json_stdout(&run(tmp.path(), &["index", ".", "--json"]));
    assert_eq!(first["changed_files"], 1);

    let second = json_stdout(&run(tmp.path(), &["index", ".", "--json"]));
    assert_eq!(second["changed_files"], 0);
    assert_eq!(second["reused_files"], 1);
    assert_eq!(second["embedded_symbols"], 0);

    std::fs::rename(tmp.path().join("src/old.py"), tmp.path().join("src/new.py")).unwrap();
    let renamed = json_stdout(&run(tmp.path(), &["index", ".", "--json"]));
    assert_eq!(renamed["removed_files"], 1);
    assert_eq!(renamed["changed_files"], 1);
}

#[test]
fn status_describes_missing_index_and_search_fails_structurally() {
    let tmp = tempfile::tempdir().unwrap();
    std::fs::create_dir(tmp.path().join(".git")).unwrap();
    write(tmp.path(), "src/thing.py", "def thing():\n    return 1\n");

    let status = json_stdout(&run(tmp.path(), &["status", ".", "--json"]));
    assert_eq!(status["index_exists"], false);
    assert_eq!(status["embedder_current"], false);
    assert_eq!(status["is_current"], false);

    let output = run(tmp.path(), &["search", "thing", "--json"]);
    assert!(!output.status.success());
    assert!(output.stderr.is_empty());
    let error: Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(error["error"]["code"], "index_missing");
}

#[test]
fn empty_search_and_invalid_invocation_keep_distinct_contracts() {
    let tmp = tempfile::tempdir().unwrap();
    write(tmp.path(), "src/thing.py", "def thing():\n    return 1\n");
    json_stdout(&run(tmp.path(), &["index", ".", "--json"]));

    let empty = json_stdout(&run(
        tmp.path(),
        &[
            "search",
            "no-such-symbol-xyz",
            "--mode",
            "lexical",
            "--json",
        ],
    ));
    assert_eq!(empty, Value::Array(Vec::new()));

    let malformed = run(
        tmp.path(),
        &[
            "context",
            "--task",
            "x",
            "--budget-tokens",
            "nope",
            "--json",
        ],
    );
    assert_eq!(malformed.status.code(), Some(2));
    assert!(malformed.stdout.is_empty());
    assert!(String::from_utf8_lossy(&malformed.stderr).contains("invalid value"));
}

#[test]
fn unavailable_embedder_is_a_nonzero_structured_failure() {
    let tmp = tempfile::tempdir().unwrap();
    write(tmp.path(), "src/thing.py", "def thing():\n    return 1\n");

    let output = run(
        tmp.path(),
        &[
            "index",
            ".",
            "--embedder",
            "http://127.0.0.1:9/v1/embeddings",
            "--json",
        ],
    );
    assert!(!output.status.success());
    let error: Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(error["error"]["code"], "embedder_unavailable");
    let status = json_stdout(&run(tmp.path(), &["status", ".", "--json"]));
    assert_eq!(status["index_exists"], false);
}

#[test]
fn context_json_is_deterministic_for_same_index_and_configuration() {
    let tmp = tempfile::tempdir().unwrap();
    write(
        tmp.path(),
        "src/thing.py",
        "def thing():\n    return 1\n\ndef other():\n    return thing()\n",
    );
    json_stdout(&run(tmp.path(), &["index", ".", "--json"]));

    let args = [
        "context",
        "--task",
        "understand thing",
        "--budget-tokens",
        "256",
        "--json",
    ];
    let first = json_stdout(&run(tmp.path(), &args));
    let second = json_stdout(&run(tmp.path(), &args));
    assert_eq!(first, second);
}

#[test]
fn stale_status_and_no_source_error_are_explicit() {
    let tmp = tempfile::tempdir().unwrap();
    write(tmp.path(), "src/thing.py", "def thing():\n    return 1\n");
    json_stdout(&run(tmp.path(), &["index", ".", "--json"]));

    write(tmp.path(), "src/thing.py", "def thing():\n    return 2\n");
    let stale = json_stdout(&run(tmp.path(), &["status", ".", "--json"]));
    assert_eq!(stale["index_exists"], true);
    assert_eq!(stale["is_current"], false);

    let empty = tempfile::tempdir().unwrap();
    let output = run(empty.path(), &["index", ".", "--json"]);
    assert!(!output.status.success());
    assert!(output.stderr.is_empty());
    let error: Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(error["error"]["code"], "no_source_files");
}

#[test]
fn invalid_repository_path_is_structured() {
    let tmp = tempfile::tempdir().unwrap();
    let missing = tmp.path().join("does-not-exist");
    let output = run(tmp.path(), &["status", missing.to_str().unwrap(), "--json"]);
    assert!(!output.status.success());
    assert!(output.stderr.is_empty());
    let error: Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(error["error"]["code"], "repository_not_found");
}

#[test]
fn search_context_review_and_stats_accept_an_explicit_repository_path() {
    // cwd for every invocation is an unrelated, non-repository directory:
    // if any command silently fell back to discovering from cwd instead of
    // honoring an explicit path, it would fail here rather than accidentally
    // succeed against the wrong repository.
    let elsewhere = tempfile::tempdir().unwrap();
    let repo = tempfile::tempdir().unwrap();
    write(repo.path(), "src/thing.py", "def thing():\n    return 1\n");
    for args in [
        vec!["init", "-q"],
        vec!["add", "."],
        vec![
            "-c",
            "user.email=t@t",
            "-c",
            "user.name=t",
            "commit",
            "-qm",
            "init",
        ],
    ] {
        let status = std::process::Command::new("git")
            .args(&args)
            .current_dir(repo.path())
            .status()
            .unwrap();
        assert!(status.success(), "git {args:?}");
    }

    let repo_str = repo.path().to_str().unwrap();
    json_stdout(&run(elsewhere.path(), &["index", repo_str, "--json"]));

    let search = json_stdout(&run(
        elsewhere.path(),
        &["search", "thing", "--path", repo_str, "--json"],
    ));
    assert!(!search.as_array().unwrap().is_empty());

    let context = json_stdout(&run(
        elsewhere.path(),
        &[
            "context",
            "--path",
            repo_str,
            "--task",
            "understand thing",
            "--budget-tokens",
            "128",
            "--json",
        ],
    ));
    assert!(!context["items"].as_array().unwrap().is_empty());

    let review = json_stdout(&run(
        elsewhere.path(),
        &["review", "--path", repo_str, "--diff", "HEAD", "--json"],
    ));
    assert!(review.get("changed_files").is_some());

    // Stats has no --json flag; assert it runs cleanly and reports non-zero
    // counts against the explicit repo rather than the unrelated cwd.
    let stats_out = run(elsewhere.path(), &["stats", repo_str]);
    assert!(stats_out.status.success(), "{stats_out:?}");
    let stdout = String::from_utf8_lossy(&stats_out.stdout);
    assert!(stdout.contains("files:      1"), "{stdout}");
}

#[test]
fn read_only_index_does_not_create_wal_or_schema_artifacts() {
    let tmp = tempfile::tempdir().unwrap();
    write(tmp.path(), "src/thing.py", "def thing():\n    return 1\n");
    json_stdout(&run(tmp.path(), &["index", ".", "--json"]));

    let db = tmp.path().join(".oxide").join("index.db");
    let wal = tmp.path().join(".oxide").join("index.db-wal");
    let shm = tmp.path().join(".oxide").join("index.db-shm");
    let size_before = std::fs::metadata(&db).unwrap().len();
    let mtime_before = std::fs::metadata(&db).unwrap().modified().unwrap();
    assert!(
        !wal.exists() && !shm.exists(),
        "a clean index write must not leave WAL/SHM artifacts behind"
    );

    std::thread::sleep(std::time::Duration::from_millis(50));
    json_stdout(&run(tmp.path(), &["status", ".", "--json"]));
    json_stdout(&run(
        tmp.path(),
        &["search", "thing", "--mode", "lexical", "--json"],
    ));
    json_stdout(&run(
        tmp.path(),
        &[
            "context",
            "--task",
            "thing",
            "--budget-tokens",
            "64",
            "--json",
        ],
    ));

    let size_after = std::fs::metadata(&db).unwrap().len();
    let mtime_after = std::fs::metadata(&db).unwrap().modified().unwrap();
    assert_eq!(
        size_before, size_after,
        "index file size changed during reads"
    );
    assert_eq!(
        mtime_before, mtime_after,
        "index file mtime changed during reads"
    );
    assert!(
        !wal.exists() && !shm.exists(),
        "read-only status/search/context must not create WAL/SHM artifacts"
    );
}

fn spawn_indexers(root: &Path, n: usize) -> Vec<Output> {
    let children: Vec<_> = (0..n)
        .map(|_| {
            Command::new(env!("CARGO_BIN_EXE_oxide"))
                .args(["index", ".", "--json"])
                .current_dir(root)
                .stdout(std::process::Stdio::piped())
                .stderr(std::process::Stdio::piped())
                .spawn()
                .unwrap()
        })
        .collect();
    children
        .into_iter()
        .map(|c| c.wait_with_output().unwrap())
        .collect()
}

#[test]
fn concurrent_reindexing_of_an_existing_index_all_succeed() {
    // Every writer takes an IMMEDIATE transaction and shares a busy_timeout,
    // so once an index already exists, concurrent re-indexers are expected
    // to succeed serially rather than fail with SQLITE_BUSY: this is the
    // realistic repeated-indexing scenario (an agent or a periodic job
    // re-running `oxide index` while another instance is doing the same).
    let tmp = tempfile::tempdir().unwrap();
    for i in 0..20 {
        write(
            tmp.path(),
            &format!("src/m{i}.py"),
            &format!("def f{i}():\n    return {i}\n"),
        );
    }
    json_stdout(&run(tmp.path(), &["index", ".", "--json"]));

    let outputs = spawn_indexers(tmp.path(), 4);
    for o in &outputs {
        let stderr = String::from_utf8_lossy(&o.stderr);
        assert!(!stderr.contains("panicked"), "must not panic: {stderr}");
    }
    for o in &outputs {
        assert!(
            o.status.success(),
            "concurrent re-index of an existing index must succeed: stdout={} stderr={}",
            String::from_utf8_lossy(&o.stdout),
            String::from_utf8_lossy(&o.stderr)
        );
    }

    let status = json_stdout(&run(tmp.path(), &["status", ".", "--json"]));
    assert_eq!(status["files"], 20);
    assert_eq!(status["is_current"], true);
}

#[test]
fn concurrent_first_time_indexing_never_panics_or_corrupts_state() {
    // Racing several *first-ever* indexers against a brand-new `.oxide`
    // directory is a narrower, harsher scenario than re-indexing an existing
    // one: creating the WAL-mode file and its schema for the first time can
    // still occasionally lose a race to a clean, structured "database is
    // locked" error even with busy_timeout + IMMEDIATE transactions (SQLite
    // does not retry every busy variant). This is not claimed to always
    // succeed, only to never panic, never silently corrupt the index, and to
    // always leave the loser with a clear, structured, retryable error.
    let tmp = tempfile::tempdir().unwrap();
    for i in 0..20 {
        write(
            tmp.path(),
            &format!("src/m{i}.py"),
            &format!("def f{i}():\n    return {i}\n"),
        );
    }

    let outputs = spawn_indexers(tmp.path(), 4);
    let mut any_success = false;
    for o in &outputs {
        let stderr = String::from_utf8_lossy(&o.stderr);
        assert!(!stderr.contains("panicked"), "must not panic: {stderr}");
        if o.status.success() {
            any_success = true;
        } else {
            // A loser must fail with a well-formed structured error on
            // stdout, not garbage, a silent empty success, or a crash.
            let stdout = String::from_utf8_lossy(&o.stdout);
            let parsed: Value = serde_json::from_str(&stdout)
                .unwrap_or_else(|e| panic!("non-JSON failure output: {stdout} ({e})"));
            assert!(parsed["error"]["code"].is_string(), "{stdout}");
        }
    }
    assert!(any_success, "at least one concurrent first-index must win");

    // A follow-up run must always converge to a consistent, fully-indexed
    // state regardless of how the race above played out.
    let after = json_stdout(&run(tmp.path(), &["index", ".", "--json"]));
    assert_eq!(after["scanned_files"], 20);
    assert_eq!(after["errored_files"], 0);
    let status = json_stdout(&run(tmp.path(), &["status", ".", "--json"]));
    assert_eq!(status["files"], 20);
    assert_eq!(status["is_current"], true);
}

#[test]
fn search_succeeds_while_another_process_is_indexing() {
    let tmp = tempfile::tempdir().unwrap();
    for i in 0..30 {
        write(
            tmp.path(),
            &format!("src/m{i}.py"),
            &format!("def target_{i}():\n    return {i}\n"),
        );
    }
    json_stdout(&run(tmp.path(), &["index", ".", "--json"]));

    // Touch every file so the next index has real reparse/embed work to do,
    // widening the window a concurrent read could land inside.
    for i in 0..30 {
        write(
            tmp.path(),
            &format!("src/m{i}.py"),
            &format!("def target_{i}():\n    return {}\n", i + 1),
        );
    }
    let indexer = Command::new(env!("CARGO_BIN_EXE_oxide"))
        .args(["index", ".", "--json"])
        .current_dir(tmp.path())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped())
        .spawn()
        .unwrap();

    for i in 0..30 {
        let out = run(tmp.path(), &["search", &format!("target_{i}"), "--json"]);
        assert!(
            out.status.success(),
            "read must succeed while a writer is indexing: {}",
            String::from_utf8_lossy(&out.stderr)
        );
    }

    let indexer_out = indexer.wait_with_output().unwrap();
    assert!(indexer_out.status.success(), "{indexer_out:?}");
}

#[test]
fn repository_path_with_spaces_and_unicode_is_indexable_and_readable() {
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path().join("repo with spaces and é");
    write(&root, "src/thing.py", "def thing():\n    return 1\n");

    json_stdout(&run(&root, &["index", ".", "--json"]));
    let status = json_stdout(&run(&root, &["status", ".", "--json"]));
    assert_eq!(status["index_exists"], true);

    let search = json_stdout(&run(&root, &["search", "thing", "--json"]));
    assert!(!search.as_array().unwrap().is_empty());

    let wal = root.join(".oxide").join("index.db-wal");
    let shm = root.join(".oxide").join("index.db-shm");
    assert!(
        !wal.exists() && !shm.exists(),
        "spaces/unicode in the path must not break the read-only URI encoding \
         path or silently fall back to creating WAL/SHM artifacts"
    );
}
