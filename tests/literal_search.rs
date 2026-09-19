//! Correctness/UX matrix for `oxide search --mode literal` (issue #6's
//! control-arm surface) plus a parity check against `rg -F` restricted to
//! exactly the file set OXIDE's own ignore policy would keep. This file
//! only exercises the CLI process; `src/literal.rs`'s own `#[cfg(test)]`
//! module covers the scan's unit-level behavior (byte-exactness,
//! determinism, ignore policy).

use serde_json::Value;
use std::path::Path;
use std::process::{Command, Output};

fn write(root: &Path, relative: &str, source: &[u8]) {
    let path = root.join(relative);
    std::fs::create_dir_all(path.parent().unwrap()).unwrap();
    std::fs::write(path, source).unwrap();
}

fn git_init(root: &Path) {
    let status = Command::new("git")
        .args(["init", "-q"])
        .current_dir(root)
        .status()
        .unwrap();
    assert!(status.success());
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
    serde_json::from_slice(&output.stdout).unwrap()
}

#[test]
fn literal_search_needs_no_index() {
    let tmp = tempfile::tempdir().unwrap();
    write(tmp.path(), "src/a.py", b"def marker_fn():\n    pass\n");
    git_init(tmp.path());

    // No `oxide index` call at all.
    let result = json_stdout(&run(
        tmp.path(),
        &["search", "marker_fn", "--mode", "literal", "--json"],
    ));
    let hits = result["hits"].as_array().unwrap();
    assert_eq!(hits.len(), 1);
    assert_eq!(hits[0]["file"], "src/a.py");
    assert_eq!(hits[0]["line"], 1);
}

#[test]
fn json_shape_is_file_line_column_snippet_no_symbol_fields() {
    let tmp = tempfile::tempdir().unwrap();
    write(tmp.path(), "notes.txt", b"alpha marker beta\n");
    git_init(tmp.path());

    let result = json_stdout(&run(
        tmp.path(),
        &["search", "marker", "--mode", "literal", "--json"],
    ));
    let hit = &result["hits"].as_array().unwrap()[0];
    let mut keys: Vec<&str> = hit
        .as_object()
        .unwrap()
        .keys()
        .map(String::as_str)
        .collect();
    keys.sort();
    assert_eq!(keys, ["column", "file", "line", "snippet"]);
    assert!(hit.get("qualified_name").is_none());
    assert!(hit.get("score").is_none());
}

#[test]
fn no_matches_returns_empty_bounded_result_not_an_error() {
    let tmp = tempfile::tempdir().unwrap();
    write(tmp.path(), "src/a.py", b"nothing interesting here\n");
    git_init(tmp.path());

    let output = run(
        tmp.path(),
        &[
            "search",
            "no_such_string_anywhere",
            "--mode",
            "literal",
            "--json",
        ],
    );
    let result = json_stdout(&output);
    assert_eq!(result["hits"].as_array().unwrap().len(), 0);
    assert_eq!(result["truncated"], false);
}

#[test]
fn empty_repository_is_not_an_error() {
    let tmp = tempfile::tempdir().unwrap();
    git_init(tmp.path());
    let result = json_stdout(&run(
        tmp.path(),
        &["search", "anything", "--mode", "literal", "--json"],
    ));
    assert_eq!(result["hits"].as_array().unwrap().len(), 0);
}

#[test]
fn empty_pattern_is_an_actionable_error_not_a_match_everything_scan() {
    let tmp = tempfile::tempdir().unwrap();
    write(tmp.path(), "src/a.py", b"x = 1\n");
    git_init(tmp.path());

    let output = run(tmp.path(), &["search", "", "--mode", "literal", "--json"]);
    assert!(!output.status.success());
    let error: Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(error["error"]["code"], "invalid_configuration");
}

#[test]
fn quoted_substrings_with_special_characters_match_literally() {
    let tmp = tempfile::tempdir().unwrap();
    write(
        tmp.path(),
        "src/re.py",
        b"pattern = \"a.b*c(d)\"\ndef a_x_b_c():\n    pass\n",
    );
    git_init(tmp.path());

    let result = json_stdout(&run(
        tmp.path(),
        &["search", "a.b*c(d)", "--mode", "literal", "--json"],
    ));
    let hits = result["hits"].as_array().unwrap();
    assert_eq!(hits.len(), 1, "{hits:?}");
    assert_eq!(hits[0]["file"], "src/re.py");
    // A regex would also spuriously match `a_x_b_c` via `.` and `*`; a
    // literal scan must not.
    assert!(!hits
        .iter()
        .any(|h| h["snippet"].as_str().unwrap().contains("a_x_b_c")));
}

#[test]
fn non_utf8_files_are_scanned_without_crashing() {
    let tmp = tempfile::tempdir().unwrap();
    let mut bytes = b"needle_marker\n".to_vec();
    bytes.push(0xFF);
    bytes.push(0xFE);
    bytes.extend_from_slice(b" more text\n");
    write(tmp.path(), "src/latin1.txt", &bytes);
    git_init(tmp.path());

    let output = run(
        tmp.path(),
        &["search", "needle_marker", "--mode", "literal", "--json"],
    );
    let result = json_stdout(&output);
    assert_eq!(result["hits"].as_array().unwrap().len(), 1);
}

#[test]
fn unknown_mode_is_rejected_and_literal_is_listed_in_the_error() {
    let tmp = tempfile::tempdir().unwrap();
    write(tmp.path(), "src/a.py", b"x = 1\n");
    git_init(tmp.path());

    let output = run(tmp.path(), &["search", "x", "--mode", "bogus"]);
    assert!(!output.status.success());
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(stderr.contains("literal"), "{stderr}");
}

/// Parity against `rg -F`, scoped to exactly the files OXIDE's own ignore
/// walk (`oxide::scanner::scan_repo_text` — the same function `oxide search
/// --mode literal` uses) would keep. Comparing against a bare `rg -F` over
/// the whole tree would just measure the two tools' differing ignore-policy
/// defaults, not the matcher. Skips (does not fail) if `rg` isn't installed.
#[test]
fn matches_ripgrep_over_the_same_file_set() {
    let Ok(rg_version) = Command::new("rg").arg("--version").output() else {
        eprintln!("skipping: rg not installed");
        return;
    };
    assert!(rg_version.status.success());

    let tmp = tempfile::tempdir().unwrap();
    write(
        tmp.path(),
        "src/auth.py",
        b"class AuthService:\n    def refresh_token(self, token):\n        return validate_refresh_token(token)\n",
    );
    write(tmp.path(), "README.md", b"See refresh_token for details.\n");
    write(
        tmp.path(),
        "docs/notes.txt",
        b"refresh_token refresh_token\nno match here\n",
    );
    write(tmp.path(), "node_modules/pkg/skip.js", b"refresh_token\n");
    git_init(tmp.path());

    let pattern = "refresh_token";
    let oxide_result = json_stdout(&run(
        tmp.path(),
        &[
            "search", pattern, "--mode", "literal", "--json", "--limit", "200",
        ],
    ));
    let oxide_files: std::collections::BTreeSet<String> = oxide_result["hits"]
        .as_array()
        .unwrap()
        .iter()
        .map(|h| h["file"].as_str().unwrap().to_string())
        .collect();

    // OXIDE's own kept-file set, exactly as `search --mode literal` scans it.
    let kept_files = oxide::scanner::scan_repo_text(tmp.path()).unwrap();
    assert!(
        !kept_files.iter().any(|p| p.starts_with("node_modules")),
        "sanity: node_modules must already be denylisted"
    );

    let output = Command::new("rg")
        .args(["-F", "--no-config", "--line-number", "--column", pattern])
        .args(&kept_files)
        .current_dir(tmp.path())
        .output()
        .unwrap();
    let rg_stdout = String::from_utf8_lossy(&output.stdout);
    let mut rg_result_files = std::collections::BTreeSet::new();
    for line in rg_stdout.lines() {
        if let Some((file, _rest)) = line.split_once(':') {
            rg_result_files.insert(file.replace('\\', "/"));
        }
    }

    assert_eq!(
        oxide_files, rg_result_files,
        "oxide and `rg -F` over the same file set must agree on which files match"
    );
}

/// Issue #6's control-arm claim is "zero index/DB cost" for the native
/// scan, not just "works without one" — this pins that a repository with
/// no `.oxide` directory stays that way after a literal search.
#[test]
fn literal_search_never_creates_an_oxide_directory() {
    let tmp = tempfile::tempdir().unwrap();
    write(tmp.path(), "src/a.py", b"def marker_fn():\n    pass\n");
    git_init(tmp.path());

    assert!(!tmp.path().join(".oxide").exists());
    json_stdout(&run(
        tmp.path(),
        &["search", "marker_fn", "--mode", "literal", "--json"],
    ));
    assert!(
        !tmp.path().join(".oxide").exists(),
        "literal search must not create an index"
    );
}

/// The other half of "zero update cost": on a repository that already has
/// an index, literal search must not touch it at all — not even a
/// WAL/SHM side-effect, since (unlike `SqliteStore::open_read_only`,
/// see `tests/cli_e2e.rs::read_only_commands_never_modify_index_db_content`)
/// `RepositoryService::search_literal` never opens the database in the
/// first place.
#[test]
fn literal_search_never_touches_an_existing_index_db() {
    let tmp = tempfile::tempdir().unwrap();
    write(tmp.path(), "src/a.py", b"def marker_fn():\n    pass\n");
    git_init(tmp.path());

    let indexed = Command::new(env!("CARGO_BIN_EXE_oxide"))
        .args(["index", ".", "--json"])
        .env("OXIDE_EMBED_NATIVE", "hashed")
        .current_dir(tmp.path())
        .output()
        .unwrap();
    assert!(indexed.status.success(), "{:?}", indexed.stderr);

    let db = tmp.path().join(".oxide").join("index.db");
    let bytes_before = std::fs::read(&db).unwrap();
    let dir_entries_before: std::collections::BTreeSet<String> =
        std::fs::read_dir(tmp.path().join(".oxide"))
            .unwrap()
            .map(|e| e.unwrap().file_name().to_string_lossy().into_owned())
            .collect();

    json_stdout(&run(
        tmp.path(),
        &["search", "marker_fn", "--mode", "literal", "--json"],
    ));

    let bytes_after = std::fs::read(&db).unwrap();
    assert_eq!(
        bytes_before, bytes_after,
        "index.db content must be byte-identical after a literal search"
    );
    let dir_entries_after: std::collections::BTreeSet<String> =
        std::fs::read_dir(tmp.path().join(".oxide"))
            .unwrap()
            .map(|e| e.unwrap().file_name().to_string_lossy().into_owned())
            .collect();
    assert_eq!(
        dir_entries_before, dir_entries_after,
        "literal search must not create WAL/SHM or any other file under .oxide"
    );
}
