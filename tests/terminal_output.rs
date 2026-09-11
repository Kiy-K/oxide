//! Where ANSI escapes may and may not appear.
//!
//! Human output is colored only on a terminal (or with `--color always`);
//! `--json`, pipes, redirects, `NO_COLOR`, `TERM=dumb`, and `--color never`
//! all get plain bytes. Every state also has to read correctly without
//! color, so the plain outputs are checked for their markers and words.
//!
//! Terminal cases run the binary under `script(1)` for a real pty; on a
//! machine without it those cases are skipped with a note, never faked.

use std::path::Path;
use std::process::{Command, Output};

const ESC: &str = "\x1b[";

fn write(root: &Path, relative: &str, source: &str) {
    let path = root.join(relative);
    std::fs::create_dir_all(path.parent().unwrap()).unwrap();
    std::fs::write(path, source).unwrap();
}

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

fn base_command(root: &Path) -> Command {
    let mut cmd = Command::new(env!("CARGO_BIN_EXE_oxide"));
    cmd.env("OXIDE_EMBED_NATIVE", "hashed")
        .env_remove("NO_COLOR")
        .env("TERM", "xterm-256color")
        .current_dir(root);
    cmd
}

/// Piped stdout and stderr — what a script, a pipeline, or CI sees.
fn piped(root: &Path, args: &[&str], env: &[(&str, &str)]) -> Output {
    let mut cmd = base_command(root);
    cmd.args(args);
    for (k, v) in env {
        cmd.env(k, v);
    }
    cmd.output().unwrap()
}

/// stdout and stderr as one stream from a pseudo-terminal, or `None` when
/// `script(1)` is unavailable.
fn on_pty(root: &Path, args: &[&str], env: &[(&str, &str)]) -> Option<String> {
    if !cfg!(unix) || Command::new("script").arg("--version").output().is_err() {
        eprintln!("skipping pty case: script(1) not available");
        return None;
    }
    let shell_words = std::iter::once(env!("CARGO_BIN_EXE_oxide"))
        .chain(args.iter().copied())
        .map(|a| format!("'{}'", a.replace('\'', "'\\''")))
        .collect::<Vec<_>>()
        .join(" ");
    let mut cmd = {
        let mut c = Command::new("script");
        c.args(["-qec", &shell_words, "/dev/null"])
            .env("OXIDE_EMBED_NATIVE", "hashed")
            .env_remove("NO_COLOR")
            .env("TERM", "xterm-256color")
            .current_dir(root);
        for (k, v) in env {
            c.env(k, v);
        }
        c
    };
    let out = cmd.output().unwrap();
    Some(String::from_utf8_lossy(&out.stdout).into_owned())
}

fn text(bytes: &[u8]) -> String {
    String::from_utf8_lossy(bytes).into_owned()
}

/// Drop CSI sequences (`ESC [ ... <final byte>`: colors, clear-line, cursor
/// moves) so styled, redrawn text can be compared as words.
fn strip_ansi(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    let mut chars = s.chars().peekable();
    while let Some(c) = chars.next() {
        if c == '\x1b' && chars.peek() == Some(&'[') {
            chars.next();
            for c in chars.by_ref() {
                if ('\x40'..='\x7e').contains(&c) {
                    break;
                }
            }
        } else {
            out.push(c);
        }
    }
    out
}

#[test]
fn piped_and_json_output_never_contain_escapes() {
    let tmp = sample_repo();
    let indexed = piped(tmp.path(), &["index", "."], &[]);
    assert!(indexed.status.success(), "{}", text(&indexed.stderr));
    assert!(
        !text(&indexed.stdout).contains(ESC),
        "{}",
        text(&indexed.stdout)
    );
    let stderr = text(&indexed.stderr);
    assert!(
        !stderr.contains(ESC) && !stderr.contains('\r'),
        "redirected progress must be plain one-line-per-stage: {stderr:?}"
    );
    assert!(stderr.contains("Finalizing... done\n"), "{stderr:?}");

    for args in [
        vec!["status"],
        vec!["status", "-v"],
        vec!["query", "refresh token"],
        vec!["search", "refresh"],
        vec!["install", "--dry-run", "--agent", "claude"],
        // `--json` wins over an explicit request for color.
        vec!["status", "--json", "--color", "always"],
        vec!["index", ".", "--json", "--color", "always"],
        vec!["search", "refresh", "--json", "--color", "always"],
        vec!["query", "refresh token", "--json", "--color", "always"],
    ] {
        let out = piped(tmp.path(), &args, &[]);
        assert!(out.status.success(), "{args:?}: {}", text(&out.stderr));
        assert!(
            !text(&out.stdout).contains(ESC) && !text(&out.stderr).contains(ESC),
            "`oxide {}` leaked escapes:\n{}",
            args.join(" "),
            text(&out.stdout)
        );
    }
    // The JSON stays parseable with `--color always` on: color is not a
    // schema switch.
    let out = piped(tmp.path(), &["status", "--json", "--color", "always"], &[]);
    serde_json::from_slice::<serde_json::Value>(&out.stdout).expect("valid JSON");

    // Errors on a piped stderr are plain too, and the "error:" prefix and
    // the next command survive without color.
    let missing = tempfile::tempdir().unwrap();
    std::fs::create_dir(missing.path().join(".git")).unwrap();
    let err = piped(missing.path(), &["search", "x"], &[]);
    assert!(!err.status.success());
    let stderr = text(&err.stderr);
    assert!(!stderr.contains(ESC), "{stderr}");
    assert!(stderr.starts_with("error: "), "{stderr}");
    assert!(stderr.contains("Run:\n  oxide index"), "{stderr}");
}

#[test]
fn color_always_forces_escapes_even_when_piped() {
    let tmp = sample_repo();
    piped(tmp.path(), &["index", "."], &[]);
    let out = piped(tmp.path(), &["status", "--color", "always"], &[]);
    assert!(text(&out.stdout).contains(ESC), "{}", text(&out.stdout));
    // The flag is global: before or after the subcommand.
    let out = piped(tmp.path(), &["--color", "always", "status"], &[]);
    assert!(text(&out.stdout).contains(ESC), "{}", text(&out.stdout));
    // NO_COLOR is a session default; an explicit flag is more specific.
    let out = piped(
        tmp.path(),
        &["status", "--color", "always"],
        &[("NO_COLOR", "1")],
    );
    assert!(text(&out.stdout).contains(ESC), "{}", text(&out.stdout));
    let out = piped(tmp.path(), &["status", "--color", "sometimes"], &[]);
    assert_eq!(
        out.status.code(),
        Some(2),
        "unknown --color value is a usage error"
    );
}

#[test]
fn every_state_is_readable_without_color() {
    let tmp = sample_repo();
    let missing = text(&piped(tmp.path(), &["status"], &[]).stdout);
    assert!(missing.starts_with("✗ Index not found"), "{missing}");

    let indexed = text(&piped(tmp.path(), &["index", "."], &[]).stdout);
    assert!(indexed.starts_with("✓ Indexed "), "{indexed}");
    assert!(indexed.contains("symbols new"), "{indexed}");
    assert!(indexed.contains("embeddings written"), "{indexed}");

    let current = text(&piped(tmp.path(), &["status"], &[]).stdout);
    assert!(current.starts_with("✓ Index current"), "{current}");
    assert!(current.contains("✓ Semantic search ready"), "{current}");
    assert!(current.contains("· Supports Python"), "{current}");

    let unchanged = text(&piped(tmp.path(), &["index", "."], &[]).stdout);
    assert!(unchanged.starts_with("✓ Index current "), "{unchanged}");
    assert!(unchanged.contains("(nothing changed, "), "{unchanged}");

    write(tmp.path(), "src/auth.py", "def x():\n    return 1\n");
    let stale = text(&piped(tmp.path(), &["status"], &[]).stdout);
    assert!(stale.starts_with("! Index stale"), "{stale}");
    assert!(stale.ends_with("Run:\n  oxide index\n"), "{stale}");

    let query = text(
        &piped(
            tmp.path(),
            &["query", "refresh token", "--budget-tokens", "300"],
            &[],
        )
        .stdout,
    );
    assert!(
        query.starts_with("Relevant code for \"refresh token\""),
        "{query}"
    );
    assert!(
        query.contains("src/auth.py\n  "),
        "grouped by file:\n{query}"
    );
    assert!(query.contains("↳ "), "each item says why:\n{query}");
    assert!(query.contains("context tokens"), "{query}");
}

#[test]
fn a_terminal_gets_color_and_progress_unless_told_otherwise() {
    let tmp = sample_repo();
    let Some(indexed) = on_pty(tmp.path(), &["index", "."], &[]) else {
        return;
    };
    assert!(indexed.contains(ESC), "no color on a pty:\n{indexed}");
    assert!(
        indexed.contains('\r'),
        "no live redraw on a pty:\n{indexed:?}"
    );
    assert!(
        indexed
            .chars()
            .any(|c| ('\u{2800}'..='\u{28ff}').contains(&c)),
        "no spinner frame on a pty:\n{indexed:?}"
    );
    let plain = strip_ansi(&indexed);
    for stage in [
        "Loading semantic model... ready",
        "Scanning repository... 1 files",
        "Parsing... 1/1",
        "Embedding... ",
        "Finalizing... done",
    ] {
        assert!(plain.contains(stage), "missing stage `{stage}`:\n{plain}");
    }
    assert!(plain.contains("✓ Indexed "), "{plain}");

    let status = on_pty(tmp.path(), &["status"], &[]).unwrap();
    assert!(status.contains(ESC), "{status}");

    for (label, args, env) in [
        ("NO_COLOR", vec!["status"], vec![("NO_COLOR", "1")]),
        ("--color never", vec!["status", "--color", "never"], vec![]),
        ("TERM=dumb", vec!["status"], vec![("TERM", "dumb")]),
        (
            "TERM=dumb index",
            vec!["index", ".", "-e"],
            vec![("TERM", "dumb")],
        ),
        ("--json", vec!["status", "--json"], vec![]),
    ] {
        let out = on_pty(tmp.path(), &args, &env).unwrap();
        assert!(!out.contains(ESC), "{label} still colored:\n{out}");
    }
    // A CI environment is just a non-terminal: no color, no progress. (The
    // piped tests above are that case; here CI=1 on a real pty must not
    // change the terminal behavior either way.)
    let ci = on_pty(tmp.path(), &["status"], &[("CI", "1")]).unwrap();
    assert!(ci.contains("Index current"), "{ci}");

    // Progress goes to stderr: stdout alone stays exactly the summary.
    let out = piped(tmp.path(), &["index", "."], &[]);
    assert!(
        text(&out.stdout).starts_with("✓ Index current"),
        "{}",
        text(&out.stdout)
    );
    assert!(
        !text(&out.stdout).contains("..."),
        "stage lines leaked to stdout"
    );
}
