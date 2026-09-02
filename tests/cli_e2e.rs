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
fn search_json_is_deterministic_across_process_runs_with_tied_scores() {
    // Two symbols with identical bodies/signatures tie on both lexical and
    // semantic score. Without a stable secondary sort key, their relative
    // order in the output depends on HashMap iteration order, which is
    // reseeded per process -- this must not flap across separate `oxide`
    // invocations against the same unchanged index.
    let tmp = tempfile::tempdir().unwrap();
    write(
        tmp.path(),
        "src/one.py",
        "def widget_one():\n    return do_widget_thing()\n",
    );
    write(
        tmp.path(),
        "src/two.py",
        "def widget_two():\n    return do_widget_thing()\n",
    );
    json_stdout(&run(tmp.path(), &["index", ".", "--json"]));

    let args = ["search", "widget", "--limit", "10", "--json"];
    let first = json_stdout(&run(tmp.path(), &args));
    for _ in 0..4 {
        let next = json_stdout(&run(tmp.path(), &args));
        assert_eq!(
            first, next,
            "search order must not flap across process runs"
        );
    }
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
fn json_errors_carry_a_machine_actionable_action_alongside_the_code() {
    let tmp = tempfile::tempdir().unwrap();
    let missing = tmp.path().join("does-not-exist");
    let output = run(tmp.path(), &["status", missing.to_str().unwrap(), "--json"]);
    let error: Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(error["error"]["code"], "repository_not_found");
    assert_eq!(
        error["error"]["action"], "stop",
        "a bad path is not retryable without changing the input"
    );

    std::fs::create_dir(tmp.path().join(".git")).unwrap();
    write(tmp.path(), "src/thing.py", "def thing():\n    return 1\n");
    let output = run(tmp.path(), &["search", "thing", "--json"]);
    let error: Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(error["error"]["code"], "index_missing");
    assert_eq!(error["error"]["action"], "index");
}

#[test]
fn corrupt_index_file_is_a_structured_error_not_a_panic() {
    let tmp = tempfile::tempdir().unwrap();
    write(tmp.path(), "src/thing.py", "def thing():\n    return 1\n");
    json_stdout(&run(tmp.path(), &["index", ".", "--json"]));

    // Overwrite the real SQLite file with garbage bytes.
    std::fs::write(
        tmp.path().join(".oxide").join("index.db"),
        b"not a database",
    )
    .unwrap();

    for args in [
        vec!["status", ".", "--json"],
        vec!["search", "thing", "--json"],
        vec![
            "context",
            "--task",
            "thing",
            "--budget-tokens",
            "64",
            "--json",
        ],
    ] {
        let output = run(tmp.path(), &args);
        let stderr = String::from_utf8_lossy(&output.stderr);
        assert!(!stderr.contains("panicked"), "{args:?}: {stderr}");
        assert!(!output.status.success(), "{args:?}");
        let error: Value = serde_json::from_slice(&output.stdout)
            .unwrap_or_else(|e| panic!("{args:?}: non-JSON output ({e}): {output:?}"));
        assert_eq!(error["error"]["code"], "index_unreadable", "{args:?}");
        assert_eq!(error["error"]["action"], "repair", "{args:?}");
    }
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
fn clean_index_write_leaves_no_wal_or_schema_artifacts() {
    // The writer side of the concurrency contract: after `oxide index`
    // closes its connection cleanly, WAL auto-checkpoints and removes its
    // -wal/-shm files. This is unrelated to read-only opening (below) and
    // must hold regardless of it.
    let tmp = tempfile::tempdir().unwrap();
    write(tmp.path(), "src/thing.py", "def thing():\n    return 1\n");
    json_stdout(&run(tmp.path(), &["index", ".", "--json"]));

    let wal = tmp.path().join(".oxide").join("index.db-wal");
    let shm = tmp.path().join(".oxide").join("index.db-shm");
    assert!(
        !wal.exists() && !shm.exists(),
        "a clean index write must not leave WAL/SHM artifacts behind"
    );
}

#[test]
fn read_only_commands_never_modify_index_db_content() {
    // Concurrency contract (Phase 1.1 item 3): read-only OXIDE commands are
    // safe during concurrent indexing and never modify the database
    // themselves, but SQLite may create/touch normal WAL/SHM state as any
    // WAL reader does (see `SqliteStore::open_read_only`'s doc comment for
    // why `immutable=1` was dropped). What must hold is that `index.db`'s
    // own indexed *content* is byte-identical before and after any number
    // of read-only commands — presence of -wal/-shm is no longer asserted.
    let tmp = tempfile::tempdir().unwrap();
    write(tmp.path(), "src/thing.py", "def thing():\n    return 1\n");
    json_stdout(&run(tmp.path(), &["index", ".", "--json"]));

    let db = tmp.path().join(".oxide").join("index.db");
    let bytes_before = std::fs::read(&db).unwrap();

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

    let bytes_after = std::fs::read(&db).unwrap();
    assert_eq!(
        bytes_before, bytes_after,
        "index.db content must be byte-identical after read-only commands"
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
    // hit a "database is locked" error even with busy_timeout set (SQLite's
    // busy handler does not retry every SQLITE_BUSY/LOCKED variant).
    // `SqliteStore::open` now retries schema init with a short bounded
    // backoff specifically for this case (Phase 1.1 item 4), so this is not
    // claimed to always succeed for every racer, but a loser must always
    // get a documented *retryable* structured error (`action: "retry"`, not
    // "repair" — this is transient contention, not corruption), never
    // panic, and never leave a partial/corrupt schema behind.
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
            // A loser must fail with a well-formed, retryable structured
            // error on stdout, not garbage, a silent empty success, a
            // crash, or a "go delete your index" repair verdict over what
            // is really just lock contention.
            let stdout = String::from_utf8_lossy(&o.stdout);
            let parsed: Value = serde_json::from_str(&stdout)
                .unwrap_or_else(|e| panic!("non-JSON failure output: {stdout} ({e})"));
            assert!(parsed["error"]["code"].is_string(), "{stdout}");
            assert_eq!(
                parsed["error"]["action"], "retry",
                "a cold-start lock loser must be told to retry, not repair: {stdout}"
            );
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

    let context = json_stdout(&run(
        &root,
        &[
            "context",
            "--task",
            "thing",
            "--budget-tokens",
            "64",
            "--json",
        ],
    ));
    assert!(!context["items"].as_array().unwrap().is_empty());
}

#[test]
fn sustained_concurrent_read_write_stress_never_corrupts_or_panics() {
    // Item 3 stress gate: repeatedly interleave a live writer (`oxide
    // index`, doing real reparse/embed work each round) with bursts of
    // concurrent readers (`status`/`search`), across several rounds, to
    // widen the scheduling window beyond a single lucky pass. Plain
    // `SQLITE_OPEN_READ_ONLY` WAL readers must never block on, corrupt, or
    // be corrupted by the writer, and must never panic.
    let tmp = tempfile::tempdir().unwrap();
    for i in 0..15 {
        write(
            tmp.path(),
            &format!("src/m{i}.py"),
            &format!("def target_{i}():\n    return {i}\n"),
        );
    }
    json_stdout(&run(tmp.path(), &["index", ".", "--json"]));

    for round in 0..6 {
        // Touch every file so this round's writer has real reparse/embed
        // work, not a fast no-op that closes before readers can race it.
        for i in 0..15 {
            write(
                tmp.path(),
                &format!("src/m{i}.py"),
                &format!("def target_{i}():\n    return {}\n", i + round),
            );
        }
        let writer = Command::new(env!("CARGO_BIN_EXE_oxide"))
            .args(["index", ".", "--json"])
            .current_dir(tmp.path())
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::piped())
            .spawn()
            .unwrap();

        let readers: Vec<_> = (0..8)
            .map(|i| {
                let root = tmp.path().to_path_buf();
                std::thread::spawn(move || {
                    run(&root, &["search", &format!("target_{i}"), "--json"])
                })
            })
            .collect();
        for (i, r) in readers.into_iter().enumerate() {
            let out = r.join().unwrap();
            let stderr = String::from_utf8_lossy(&out.stderr);
            assert!(
                !stderr.contains("panicked"),
                "round {round} reader {i}: {stderr}"
            );
            assert!(
                out.status.success(),
                "round {round} reader {i} must succeed against a live writer: {stderr}"
            );
        }

        let writer_out = writer.wait_with_output().unwrap();
        let stderr = String::from_utf8_lossy(&writer_out.stderr);
        assert!(
            !stderr.contains("panicked"),
            "round {round} writer: {stderr}"
        );
        assert!(
            writer_out.status.success(),
            "round {round} writer: {stderr}"
        );
    }

    // The index must still be fully consistent and readable after the
    // stress: correct file count and no leftover writer-side lock state.
    let status = json_stdout(&run(tmp.path(), &["status", ".", "--json"]));
    assert_eq!(status["files"], 15);
    assert_eq!(status["is_current"], true);
}

/// CLI-level contract for `oxide index -a/-g/-e`: complements
/// `tests/index_scope_flags.rs`'s library-level coverage of the same
/// semantics by exercising the actual flag parsing and JSON field names an
/// agent would see over the CLI.
#[test]
fn index_scope_flags_are_wired_through_the_cli() {
    let tmp = tempfile::tempdir().unwrap();
    write(
        tmp.path(),
        "src/a.py",
        "def a():\n    return b()\n\ndef b():\n    return 1\n",
    );
    json_stdout(&run(tmp.path(), &["index", ".", "--json"]));

    // Default second run: fully incremental, nothing to do.
    let plain = json_stdout(&run(tmp.path(), &["index", ".", "--json"]));
    assert_eq!(plain["changed_files"], 0);
    assert_eq!(plain["embedded_symbols"], 0);
    assert_eq!(plain["relations_refreshed_symbols"], 0);

    // -g alone: relations refreshed, nothing reparsed or re-embedded.
    let graph = json_stdout(&run(tmp.path(), &["index", ".", "-g", "--json"]));
    assert_eq!(graph["changed_files"], 0);
    assert_eq!(graph["embedded_symbols"], 0);
    assert!(graph["relations_refreshed_symbols"].as_u64().unwrap() > 0);

    // -e alone: every embedding recomputed, nothing reparsed or graph-refreshed.
    let embeddings = json_stdout(&run(tmp.path(), &["index", ".", "-e", "--json"]));
    assert_eq!(embeddings["changed_files"], 0);
    assert_eq!(embeddings["relations_refreshed_symbols"], 0);
    assert!(embeddings["embedded_symbols"].as_u64().unwrap() > 0);
    assert_eq!(embeddings["reused_embeddings"], 0);

    // --all: full reparse forced, even with nothing changed on disk.
    let all_json = json_stdout(&run(tmp.path(), &["index", ".", "--all", "--json"]));
    assert!(all_json["changed_files"].as_u64().unwrap() > 0);
    assert_eq!(all_json["reused_embeddings"], 0);

    // -a in human (non-JSON) mode reports the base/graph stage before
    // warning and continuing into semantic indexing — two summaries, one
    // warning line, in that order.
    let out = run(tmp.path(), &["index", ".", "-a"]);
    assert!(
        out.status.success(),
        "{}",
        String::from_utf8_lossy(&out.stderr)
    );
    let stdout = String::from_utf8_lossy(&out.stdout);
    let indexed_lines = stdout.matches("indexed ").count();
    assert_eq!(
        indexed_lines, 2,
        "expected base-stage + final summary: {stdout}"
    );
    let stderr = String::from_utf8_lossy(&out.stderr);
    assert!(
        stderr.contains("continuing to semantic indexing"),
        "missing the CPU-time warning: {stderr}"
    );

    // Combining flags is accepted and each still does its own job.
    let combined = json_stdout(&run(tmp.path(), &["index", ".", "-g", "-e", "--json"]));
    assert_eq!(
        combined["changed_files"], 0,
        "-g -e together must not imply -a's forced reparse"
    );
    assert!(combined["relations_refreshed_symbols"].as_u64().unwrap() > 0);
    assert_eq!(combined["reused_embeddings"], 0);
}

#[test]
fn index_help_documents_the_rebuild_scope_flags() {
    let out = Command::new(env!("CARGO_BIN_EXE_oxide"))
        .args(["index", "--help"])
        .output()
        .unwrap();
    assert!(out.status.success());
    let help = String::from_utf8_lossy(&out.stdout);
    for flag in ["-a", "--all", "-g", "--graph", "-e", "--embeddings"] {
        assert!(help.contains(flag), "help text missing {flag}:\n{help}");
    }
}
