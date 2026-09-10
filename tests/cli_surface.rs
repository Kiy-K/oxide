//! The human-facing CLI contract: what `--help` promises, what the public
//! commands are called, and what a first-run failure tells you to do next.
//!
//! `cli_e2e.rs` covers the machine-readable side (JSON shapes, error codes,
//! concurrency). This file covers the surface a person reads, plus the
//! compatibility aliases that keep the pre-v0.1 spellings working.

use serde_json::Value;
use std::path::Path;
use std::process::{Command, Output};

fn write(root: &Path, relative: &str, source: &str) {
    let path = root.join(relative);
    std::fs::create_dir_all(path.parent().unwrap()).unwrap();
    std::fs::write(path, source).unwrap();
}

fn run(root: &Path, args: &[&str]) -> Output {
    // Pin the offline hashed embedder so the suite stays hermetic: the
    // shipped default downloads ONNX weights on first load.
    Command::new(env!("CARGO_BIN_EXE_oxide"))
        .args(args)
        .env("OXIDE_EMBED_NATIVE", "hashed")
        .current_dir(root)
        .output()
        .unwrap()
}

fn stdout_of(output: &Output) -> String {
    String::from_utf8_lossy(&output.stdout).into_owned()
}

fn stderr_of(output: &Output) -> String {
    String::from_utf8_lossy(&output.stderr).into_owned()
}

/// A small repo with something recognisably auth-shaped in it, plus the
/// `.git` marker that makes flag-free discovery work from inside it.
fn sample_repo() -> tempfile::TempDir {
    let tmp = tempfile::tempdir().unwrap();
    std::fs::create_dir(tmp.path().join(".git")).unwrap();
    write(
        tmp.path(),
        "src/auth.py",
        "class AuthService:\n    def refresh_token(self, token):\n        return validate_refresh_token(token)\n\ndef validate_refresh_token(token):\n    return token is not None\n",
    );
    tmp
}

#[test]
fn top_level_help_names_every_public_command_and_a_quick_start() {
    let tmp = tempfile::tempdir().unwrap();
    let out = run(tmp.path(), &["--help"]);
    assert!(out.status.success());
    let help = stdout_of(&out);

    for command in [
        "index",
        "query",
        "search",
        "status",
        "watch",
        "install",
        "uninstall",
        "mcp",
        "review",
        "eval",
    ] {
        assert!(
            help.contains(&format!("  {command} ")),
            "top-level help does not list `{command}`:\n{help}"
        );
    }
    // The six first-run questions the help has to answer without any
    // architecture doc: index, ask, search, check, keep fresh, connect.
    for section in ["GET STARTED", "AGENTS", "QUICK START"] {
        assert!(help.contains(section), "missing section {section}:\n{help}");
    }
    assert!(help.contains("oxide index"), "no quick start:\n{help}");
    assert!(
        help.contains("oxide query \"Where is authentication handled?\""),
        "quick start does not show a real question:\n{help}"
    );
    // Internals must not leak into the first screen a newcomer reads.
    for leak in ["  stats", "--retrieval-mode", "  context"] {
        assert!(
            !help.contains(leak),
            "top-level help exposes the internal `{}`:\n{help}",
            leak.trim()
        );
    }
}

#[test]
fn every_public_subcommand_has_its_own_help() {
    let tmp = tempfile::tempdir().unwrap();
    for command in [
        "index",
        "query",
        "search",
        "status",
        "watch",
        "install",
        "uninstall",
        "mcp",
        "review",
        "eval",
    ] {
        let out = run(tmp.path(), &[command, "--help"]);
        assert!(
            out.status.success(),
            "`oxide {command} --help` failed: {}",
            stderr_of(&out)
        );
        assert!(
            stdout_of(&out).len() > 40,
            "`oxide {command} --help` is empty"
        );
    }
}

/// `query` answers a question; `search` looks up an expression. If the two
/// help texts do not say so, nothing else in the CLI will.
#[test]
fn query_and_search_help_distinguish_question_from_expression() {
    let tmp = tempfile::tempdir().unwrap();
    let query = stdout_of(&run(tmp.path(), &["query", "--help"]));
    assert!(query.contains("question"), "{query}");
    assert!(query.contains("task"), "{query}");

    let search = stdout_of(&run(tmp.path(), &["search", "--help"]));
    assert!(
        search.contains("oxide query"),
        "search help never points at query:\n{search}"
    );
    assert!(
        search.contains("lexical|semantic|hybrid"),
        "the advanced --mode is still documented:\n{search}"
    );
}

#[test]
fn query_takes_the_task_positionally_in_both_output_modes() {
    let tmp = sample_repo();
    run(tmp.path(), &["index", ".", "--json"]);

    let human = run(tmp.path(), &["query", "Where is authentication handled?"]);
    assert!(human.status.success(), "{}", stderr_of(&human));
    let text = stdout_of(&human);
    assert!(
        text.contains("Where is authentication handled?"),
        "human output does not echo the question:\n{text}"
    );
    assert!(
        text.contains("src/auth.py"),
        "human output has no code in it:\n{text}"
    );
    assert!(
        text.contains("token budget used"),
        "human output does not report the budget:\n{text}"
    );

    let json = run(
        tmp.path(),
        &["query", "Where is authentication handled?", "--json"],
    );
    assert!(json.status.success());
    let pack: Value = serde_json::from_slice(&json.stdout).unwrap();
    assert_eq!(pack["task"], "Where is authentication handled?");
    assert!(pack["items"].is_array());
}

/// `oxide context --task ...` is the pre-v0.1 spelling. It is retained as a
/// hidden alias and must stay byte-identical to the new command, because
/// `scripts/agent_eval/*` and any existing agent wrapper still call it.
#[test]
fn context_stays_a_byte_identical_compatibility_alias_for_query() {
    let tmp = sample_repo();
    run(tmp.path(), &["index", ".", "--json"]);

    let via_query = run(
        tmp.path(),
        &[
            "query",
            "fix refresh token validation",
            "--budget-tokens",
            "512",
            "--json",
        ],
    );
    let via_context = run(
        tmp.path(),
        &[
            "context",
            "--task",
            "fix refresh token validation",
            "--budget-tokens",
            "512",
            "--json",
        ],
    );
    assert!(via_query.status.success());
    assert!(via_context.status.success());
    assert_eq!(
        stdout_of(&via_query),
        stdout_of(&via_context),
        "the context alias diverged from query"
    );
}

/// `--profile` is the human spelling; `--retrieval-mode` is what scripts in
/// this repo already pass. Both must reach the same setting.
#[test]
fn profile_and_retrieval_mode_are_the_same_flag() {
    let tmp = sample_repo();
    run(tmp.path(), &["index", ".", "--json"]);

    let profile = run(
        tmp.path(),
        &["query", "refresh token", "--profile", "quality", "--json"],
    );
    let legacy = run(
        tmp.path(),
        &[
            "query",
            "refresh token",
            "--retrieval-mode",
            "quality",
            "--json",
        ],
    );
    assert!(profile.status.success(), "{}", stderr_of(&profile));
    assert!(legacy.status.success(), "{}", stderr_of(&legacy));
    assert_eq!(stdout_of(&profile), stdout_of(&legacy));

    let bad = run(tmp.path(), &["query", "x", "--profile", "turbo"]);
    assert!(!bad.status.success());
    assert!(
        stderr_of(&bad).contains("fast|balanced|quality"),
        "a bad profile must name the valid ones: {}",
        stderr_of(&bad)
    );
}

#[test]
fn a_missing_index_tells_a_human_exactly_what_to_run() {
    let tmp = sample_repo();

    for args in [
        vec!["search", "AuthService"],
        vec!["query", "where is auth"],
        vec!["review"],
    ] {
        let out = run(tmp.path(), &args);
        assert!(!out.status.success(), "{args:?} unexpectedly succeeded");
        let stderr = stderr_of(&out);
        assert!(
            stderr.contains("No OXIDE index was found"),
            "{args:?} did not explain what happened:\n{stderr}"
        );
        assert!(
            stderr.contains("Run:\n  oxide index"),
            "{args:?} did not say what to run next:\n{stderr}"
        );
    }
}

/// Discovery walks up for `.git`/`.oxide`. Outside both, the error has to
/// name the way out rather than restating the precondition.
#[test]
fn running_outside_a_repository_points_at_an_explicit_path() {
    let tmp = tempfile::tempdir().unwrap();
    write(tmp.path(), "thing.py", "def thing():\n    return 1\n");
    let out = run(tmp.path(), &["status"]);
    assert!(!out.status.success());
    let stderr = stderr_of(&out);
    assert!(stderr.contains("not inside a git repository"), "{stderr}");
    assert!(stderr.contains("Run:\n  oxide index ."), "{stderr}");
}

#[test]
fn status_is_readable_before_indexing_when_current_and_when_stale() {
    let tmp = sample_repo();

    let missing = stdout_of(&run(tmp.path(), &["status"]));
    assert!(missing.contains("OXIDE status"), "{missing}");
    assert!(missing.contains("Index"), "{missing}");
    assert!(missing.contains("not found"), "{missing}");
    assert!(
        missing.contains("Run:\n  oxide index"),
        "a missing index must say what to run:\n{missing}"
    );

    run(tmp.path(), &["index", ".", "--json"]);
    let current = stdout_of(&run(tmp.path(), &["status"]));
    assert!(current.contains("current"), "{current}");
    assert!(current.contains("Semantic"), "{current}");
    assert!(current.contains("Python"), "{current}");
    assert!(
        !current.contains("Run:"),
        "a healthy index must not nag:\n{current}"
    );

    write(
        tmp.path(),
        "src/auth.py",
        "class AuthService:\n    def refresh_token(self, token):\n        return None\n",
    );
    let stale = stdout_of(&run(tmp.path(), &["status"]));
    assert!(stale.contains("stale"), "{stale}");
    assert!(
        stale.contains("Run:\n  oxide index"),
        "a stale index must say what to run:\n{stale}"
    );
}

/// `stats` folded into `status --verbose`. The old command stays as a
/// hidden alias, and the verbose view must actually carry the extra facts.
#[test]
fn status_verbose_absorbs_stats_without_changing_the_json() {
    let tmp = sample_repo();
    run(tmp.path(), &["index", ".", "--json"]);

    let verbose = stdout_of(&run(tmp.path(), &["status", "--verbose"]));
    assert!(verbose.contains("Embedder"), "{verbose}");
    assert!(verbose.contains("Embeddings"), "{verbose}");
    assert!(verbose.contains("index.db"), "{verbose}");
    assert!(verbose.contains("Schema"), "{verbose}");

    // Still reachable for anything that scripted it before v0.1.
    let stats = run(tmp.path(), &["stats"]);
    assert!(stats.status.success(), "{}", stderr_of(&stats));
    assert!(stdout_of(&stats).contains("symbols:"));

    // --verbose is a rendering switch, never a schema switch.
    let plain: Value = serde_json::from_slice(&run(tmp.path(), &["status", "--json"]).stdout)
        .expect("status --json");
    let loud: Value = serde_json::from_slice(&run(tmp.path(), &["status", "-v", "--json"]).stdout)
        .expect("status -v --json");
    assert_eq!(plain, loud);
}

/// Every field the agent-facing contract documents must survive the CLI
/// reshuffle: renaming a flag must never rename a JSON key.
#[test]
fn json_output_keeps_its_pre_rename_shape() {
    let tmp = sample_repo();

    let indexed: Value =
        serde_json::from_slice(&run(tmp.path(), &["index", ".", "--json"]).stdout).unwrap();
    for field in [
        "scanned_files",
        "changed_files",
        "reused_files",
        "removed_files",
        "new_symbols",
        "embedded_symbols",
        "reused_embeddings",
        "relations_refreshed_symbols",
    ] {
        assert!(indexed.get(field).is_some(), "index JSON lost {field}");
    }

    let status: Value =
        serde_json::from_slice(&run(tmp.path(), &["status", "--json"]).stdout).unwrap();
    for field in [
        "root",
        "index_exists",
        "is_current",
        "embedder_current",
        "base_fresh",
        "pending_embeddings",
        "files",
        "symbols",
        "embeddings",
        "embedder",
        "supported_languages",
        "schema_version",
    ] {
        assert!(status.get(field).is_some(), "status JSON lost {field}");
    }

    let hits: Value =
        serde_json::from_slice(&run(tmp.path(), &["search", "refresh token", "--json"]).stdout)
            .unwrap();
    let hit = &hits.as_array().unwrap()[0];
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
        assert!(hit.get(field).is_some(), "search JSON lost {field}");
    }

    let pack: Value =
        serde_json::from_slice(&run(tmp.path(), &["query", "fix auth", "--json"]).stdout).unwrap();
    // `embedder` and `query_used` are deliberately serde(skip)'d out of the
    // pack; the rename must not accidentally add them either.
    for field in ["task", "budget_tokens", "used_tokens", "items", "omitted"] {
        assert!(pack.get(field).is_some(), "query JSON lost {field}");
    }
    for hidden in ["embedder", "query_used"] {
        assert!(
            pack.get(hidden).is_none(),
            "query JSON gained the internal field {hidden}"
        );
    }
}

#[test]
fn malformed_invocations_stay_clap_errors_with_exit_two() {
    let tmp = sample_repo();
    run(tmp.path(), &["index", ".", "--json"]);

    for args in [
        vec!["query", "x", "--budget-tokens", "nope"],
        vec!["search"],
        vec!["nonsense-command"],
        vec!["status", "--nonsense-flag"],
    ] {
        let out = run(tmp.path(), &args);
        assert_eq!(
            out.status.code(),
            Some(2),
            "{args:?} should be a usage error, got {:?}\n{}",
            out.status.code(),
            stderr_of(&out)
        );
        assert!(out.stdout.is_empty(), "{args:?} wrote to stdout");
    }

    // A question is required, and the failure says how to supply one.
    let out = run(tmp.path(), &["query"]);
    assert!(!out.status.success());
    assert!(
        stderr_of(&out).contains("oxide query \""),
        "{}",
        stderr_of(&out)
    );
}
