//! Thin git integration: repo detection and unified-diff parsing via the `git`
//! binary. No heavy git library needed for v0.1's two operations.

use anyhow::{Context, Result};
use std::collections::HashMap;
use std::path::Path;
use std::process::Command;

pub fn is_repo(path: &Path) -> bool {
    Command::new("git")
        .args(["rev-parse", "--git-dir"])
        .current_dir(path)
        .output()
        .map(|o| o.status.success())
        .unwrap_or(false)
}

/// One changed file in a diff: path plus 1-based inclusive line ranges of the
/// ADDED/modified lines (new-file coordinates).
#[derive(Debug, Clone, serde::Serialize)]
pub struct FileDelta {
    pub file: String,
    pub added: Vec<(u32, u32)>,
}

fn run_git(repo: &Path, args: &[&str]) -> Result<String> {
    let out = Command::new("git")
        .args(args)
        .current_dir(repo)
        .output()
        .with_context(|| format!("git {}", args.join(" ")))?;
    if !out.status.success() {
        anyhow::bail!(
            "git {} failed: {}",
            args.join(" "),
            String::from_utf8_lossy(&out.stderr)
        );
    }
    Ok(String::from_utf8_lossy(&out.stdout).into_owned())
}

/// Raw `git diff --unified=0` text for `range` (see [`diff_files`]'s range
/// convention). Exposed separately so a caller needing more than just
/// [`parse_unified`]'s added-range view (e.g. [`deleted_files`]) doesn't pay
/// for a second `git diff` subprocess call.
///
/// `--src-prefix=a/ --dst-prefix=b/` is not cosmetic: [`parse_unified`] and
/// [`deleted_files`] both match header lines against a literal `a/`/`b/`
/// prefix. Without forcing it, a user's own `diff.mnemonicprefix` (prefixes
/// become `c/`/`w/`/`i/`/`o/`) or `diff.noprefix` (no prefix at all) —
/// either a real, commonly recommended git setting — silently breaks that
/// match, and every changed file in the diff evidence goes missing with no
/// error. This flag overrides both config settings unconditionally.
///
/// [`HUNK_ARGS`] pins the diff config settings measured to reshape the
/// hunks, for the same reason: changed symbols, `added_lines` and
/// review/context output should not depend on the host's git config (#35,
/// `docs/git-diff-algorithm-eval/`). Some host settings still reshape the
/// diff; see [`HUNK_ARGS`].
pub fn diff_text(repo: &Path, range: &str) -> Result<String> {
    let mut args = vec![
        "diff",
        "--unified=0",
        "--no-color",
        "--src-prefix=a/",
        "--dst-prefix=b/",
    ];
    args.extend(HUNK_ARGS);
    args.push(if range.is_empty() { "HEAD" } else { range });
    run_git(repo, &args)
}

/// Overrides for the `git diff` config settings measured to move
/// [`parse_unified`]'s added ranges (`docs/git-diff-algorithm-eval/`). Each
/// value is git's own default (on git >= 2.14), so a default-config host's
/// output is byte-identical with or without these flags. `--indent-heuristic`
/// needs git >= 2.11; older git rejects it and the diff fails.
/// - `diff.algorithm`: myers. No algorithm attributes changed symbols more
///   accurately; myers is what default-config hosts already produce.
/// - `diff.indentHeuristic`: on.
/// - `diff.interHunkContext`: 0. Anything else fuses `-U0` hunks, and the
///   unchanged lines between them would be counted as added.
/// - `diff.renames`: plain rename detection; `false` turns a renamed file
///   into an all-added file, `copies` re-attributes copied lines.
/// - `diff.external` / textconv drivers: replace the unified diff with
///   arbitrary output (or stall on a slow converter).
///
/// Not pinned, and measured to still reshape or empty the diff:
/// `GIT_DIFF_OPTS` (overrides `--unified=0`), `diff.renameLimit`,
/// `diff.submodule`, binary handling (`-diff`/`binary` attributes from
/// `core.attributesFile`, `$XDG_CONFIG_HOME/git/attributes`, the system
/// attributes file or `.git/info/attributes`; `diff.<driver>.binary`;
/// `core.bigFileThreshold`), clean filters and `core.autocrlf` on CRLF files,
/// and `diff.relative` when the OXIDE root is a subdirectory of the git repo.
/// `core.quotePath` needs no pin: [`header_path`] decodes quoted paths (#37).
///
/// Host attribute files are deliberately honoured: they also carry
/// conversion attributes (clean filters, `text`/`eol`) that git applies when
/// comparing the worktree, and hiding them reported false changes. Resetting
/// `core.bigFileThreshold` would undo a repo's own size limit and make git
/// emit full patches for large files OXIDE cannot index.
const HUNK_ARGS: [&str; 6] = [
    "--diff-algorithm=myers",
    "--indent-heuristic",
    "--inter-hunk-context=0",
    "--find-renames",
    "--no-ext-diff",
    "--no-textconv",
];

/// Parse `git diff --unified=0` into per-file deltas of added lines
/// (new-file coordinates). `range` empty = worktree vs HEAD; `A..B` explicit;
/// a single rev `R` means "everything since R" (`git diff R`).
pub fn diff_files(repo: &Path, range: &str) -> Result<Vec<FileDelta>> {
    Ok(parse_unified(&diff_text(repo, range)?))
}

pub fn parse_unified(text: &str) -> Vec<FileDelta> {
    let mut files: HashMap<String, FileDelta> = HashMap::new();
    let mut cur: Option<String> = None;
    let mut in_hunk = false;
    for line in text.lines() {
        let header = header_line(line, &mut in_hunk);
        // A deleted file's diff section has no `+++ b/...` line at all, so
        // without resetting `cur` it kept whatever the *previous* file
        // section set it to — every hunk inside a deleted file's section was
        // silently attributed to the prior file once zero-count hunks
        // stopped being filtered out below. A quoted path that does not
        // decode resets it for the same reason.
        if let Some(label) = header.and_then(|l| l.strip_prefix("+++ ")) {
            match header_path(label, "b/") {
                Some(Header::File(path)) => cur = Some(path),
                Some(Header::DevNull | Header::Unreadable) => cur = None,
                None => {}
            }
        } else if line.starts_with("@@") {
            let Some(target) = cur.clone() else { continue };
            if let Some((_, new_start, new_count)) = parse_hunk_header(line) {
                let entry = files.entry(target.clone()).or_insert(FileDelta {
                    file: target.clone(),
                    added: vec![],
                });
                if new_count > 0 {
                    entry
                        .added
                        .push((new_start.max(1), (new_start + new_count - 1).max(1)));
                } else {
                    // Pure deletion (`+N,0`): nothing survives at
                    // `new_start` in the new file, so record a small
                    // window around the insertion point instead — best
                    // effort attribution to the enclosing/adjacent symbol.
                    // ponytail: heuristic; a symbol deleted in full has
                    // nothing left in the current file to attribute to at
                    // all — naming it needs parsing the old blob too,
                    // which is out of scope here.
                    let point = new_start.max(1);
                    entry
                        .added
                        .push((point.saturating_sub(1).max(1), point + 1));
                }
            }
        }
    }
    let mut out: Vec<FileDelta> = files.into_values().collect();
    out.sort_by(|a, b| a.file.cmp(&b.file));
    out
}

/// Paths of files deleted entirely by this diff. `parse_unified` never
/// records them (a deleted file has no current-state symbols to attribute
/// anything to), but the path itself is still a fact worth surfacing.
pub fn deleted_files(text: &str) -> Vec<String> {
    let mut out = Vec::new();
    let mut pending: Option<String> = None;
    let mut in_hunk = false;
    for line in text.lines() {
        let Some(line) = header_line(line, &mut in_hunk) else {
            continue;
        };
        if let Some(label) = line.strip_prefix("--- ") {
            pending = match header_path(label, "a/") {
                Some(Header::File(path)) => Some(path),
                _ => None,
            };
        } else if let Some(label) = line.strip_prefix("+++ ") {
            match header_path(label, "b/") {
                Some(Header::DevNull) => out.extend(pending.take()),
                _ => pending = None,
            }
        }
    }
    out.sort();
    out.dedup();
    out
}

/// `line` if it can be a file header line, `None` inside a hunk body. Under
/// `-U0` a removed line `-- x` or an added line `++ x` prints as `--- x` or
/// `+++ x`, so only the lines between a `diff ` line and the file's first
/// `@@` are headers. No body line starts with `diff ` (body lines start with
/// ` `, `+`, `-` or `\`) and no header line starts with `@@`.
fn header_line<'a>(line: &'a str, in_hunk: &mut bool) -> Option<&'a str> {
    if line.starts_with("diff ") {
        *in_hunk = false;
    } else if line.starts_with("@@") {
        *in_hunk = true;
    }
    (!*in_hunk).then_some(line)
}

/// What a `--- `/`+++ ` diff header names.
enum Header {
    DevNull,
    /// Repo-relative path, the same string the index stores as `Symbol::file`.
    File(String),
    /// A quoted label that is malformed or does not decode to a UTF-8 path
    /// under the expected prefix. OXIDE indexes only UTF-8 paths
    /// (`scanner::is_denied`), so no indexed file can be meant; a lossy path
    /// could match the wrong one.
    Unreadable,
}

/// The one reading of a header label (the text after `--- `/`+++ `) shared
/// by [`parse_unified`] and [`deleted_files`]. `prefix` is `a/` or `b/`,
/// which `diff_text` forces. Git C-quotes the whole label, prefix included,
/// when the path holds `"`, `\`, a control character, or (with the default
/// `core.quotePath`) a byte >= 0x80 (#37); the label is decoded before the
/// prefix is removed. `None` is a line no header form matches, which leaves
/// the caller's state unchanged as before.
fn header_path(label: &str, prefix: &str) -> Option<Header> {
    if label.starts_with("/dev/null") {
        return Some(Header::DevNull);
    }
    if let Some(path) = label.strip_prefix(prefix) {
        return Some(Header::File(path.trim().to_string()));
    }
    if !label.starts_with('"') {
        return None;
    }
    let path = unquote_c_style(label)
        .and_then(|bytes| String::from_utf8(bytes).ok())
        .and_then(|path| Some(Header::File(path.strip_prefix(prefix)?.to_string())));
    Some(path.unwrap_or(Header::Unreadable))
}

/// Git's `unquote_c_style` (`quote.c`): the escapes `\a \b \f \n \r \t \v
/// \\ \"` and three-digit octal `\[0-3][0-7][0-7]`, decoded to bytes; any
/// other escape is an error, as in git. Only whitespace may follow the
/// closing quote: git appends a tab to a header label containing a space.
fn unquote_c_style(label: &str) -> Option<Vec<u8>> {
    let mut bytes = label.strip_prefix('"')?.bytes();
    let mut out = Vec::new();
    loop {
        match bytes.next()? {
            b'"' => break,
            b'\\' => {}
            byte => {
                out.push(byte);
                continue;
            }
        }
        out.push(match bytes.next()? {
            b'a' => 0x07,
            b'b' => 0x08,
            b'f' => 0x0c,
            b'n' => b'\n',
            b'r' => b'\r',
            b't' => b'\t',
            b'v' => 0x0b,
            byte @ (b'\\' | b'"') => byte,
            first @ b'0'..=b'3' => {
                let mut value = first - b'0';
                for _ in 0..2 {
                    let digit = bytes.next().filter(|d| (b'0'..=b'7').contains(d))?;
                    value = value << 3 | (digit - b'0');
                }
                value
            }
            _ => return None,
        });
    }
    bytes.all(|b| b.is_ascii_whitespace()).then_some(out)
}

/// One commit's metadata: subject line only (never the body — commit
/// messages are provenance, not a retrieval input), UTC `Z`-suffixed
/// timestamp.
#[derive(Debug, Clone, serde::Serialize)]
pub struct CommitMeta {
    pub sha: String,
    pub short_sha: String,
    pub message: String,
    pub author: String,
    pub committed_at: String,
}

/// The `limit` most recent commits at `HEAD`, newest first. Bounded,
/// local-only (`git log -n limit`), no merge commits (their file list is
/// ambiguous and irrelevant to commit-metadata provenance). Returns an
/// empty list rather than erroring when there is no `HEAD` yet (a fresh
/// repo) — commit metadata is advisory, never worth failing a query over.
pub fn recent_commits(repo: &Path, limit: usize) -> Result<Vec<CommitMeta>> {
    recent_commits_in_range(repo, "", limit)
}

/// [`recent_commits`], scoped to `range` instead of "most recent at HEAD" —
/// what `oxide review` wants: commits *in this diff*, not just recently.
///
/// `range`'s convention matches [`diff_text`]'s (empty = nothing extra,
/// `A..B` explicit, a single rev `R` means "everything since `R`") but a
/// bare `R` needs translating to `R..` before it reaches `git log`: `git
/// diff R` compares R's tree to the current state (so bare-R already means
/// "since R" there), while `git log R` walks R's *ancestors* — the opposite
/// direction for the identical spelling. `R..` is git's own shorthand for
/// `R..HEAD`.
pub fn recent_commits_in_range(repo: &Path, range: &str, limit: usize) -> Result<Vec<CommitMeta>> {
    let mut args: Vec<String> = vec![
        "log".into(),
        "--no-merges".into(),
        "-n".into(),
        limit.to_string(),
        "--date=iso-strict-local".into(),
        "--format=%H%x1f%h%x1f%s%x1f%an%x1f%cd".into(),
    ];
    if !range.is_empty() {
        let log_range = if range.contains("..") {
            range.to_string()
        } else {
            format!("{range}..")
        };
        args.push(log_range);
    }
    // `--date=iso-strict-local` + `TZ=UTC` makes git itself normalize the
    // date to a `+00:00` offset; `parse_commit_line` turns that into `Z` —
    // one string op, no date-arithmetic crate needed for one field.
    let out = Command::new("git")
        .args(&args)
        .env("TZ", "UTC")
        .current_dir(repo)
        .output()
        .with_context(|| format!("git {}", args.join(" ")))?;
    if !out.status.success() {
        // No HEAD yet, or `range` doesn't resolve (e.g. `HEAD~1` on a
        // single-commit repo): degrade to empty, this is advisory metadata.
        return Ok(Vec::new());
    }
    Ok(String::from_utf8_lossy(&out.stdout)
        .lines()
        .filter_map(parse_commit_line)
        .collect())
}

fn parse_commit_line(line: &str) -> Option<CommitMeta> {
    let mut parts = line.splitn(5, '\u{1f}');
    let sha = parts.next()?.to_string();
    let short_sha = parts.next()?.to_string();
    let message = parts.next()?.to_string();
    let author = parts.next()?.to_string();
    let date = parts.next()?.to_string();
    let committed_at = match date.strip_suffix("+00:00") {
        Some(s) => format!("{s}Z"),
        None => date,
    };
    Some(CommitMeta {
        sha,
        short_sha,
        message,
        author,
        committed_at,
    })
}

/// Raw `(file, commit_sha)` pairs for the co-change signal: for the last
/// `window` non-merge commits that touched `file`, every file each of
/// those commits changed (including `file` itself — callers filter that
/// out). A commit touching more than `max_files_per_commit` files is
/// dropped entirely, not truncated — a mass reformat/dependency bump is
/// noise, and a partial file list from one is still noise.
///
/// Two bounded calls, never a full-history scan: `git log --name-only --
/// file` only ever lists the *pathspec-matched* file itself for each
/// commit, never that commit's other files (verified against real git
/// output — this is not the "co-change" signal it looks like), so a first
/// call gets the bounded list of commit SHAs that touched `file`, and a
/// second, single batched `git diff-tree --stdin` call gets every one of
/// those commits' full (unrestricted) file lists at once — one process
/// spawn regardless of how many commits are in the window, rather than one
/// per commit.
pub fn co_change_raw(
    repo: &Path,
    file: &str,
    window: usize,
    max_files_per_commit: usize,
) -> Result<Vec<(String, String)>> {
    let sha_out = Command::new("git")
        .args([
            "log",
            "--no-merges",
            "-n",
            &window.to_string(),
            "--format=%H",
            "--",
            file,
        ])
        .current_dir(repo)
        .output()
        .with_context(|| format!("git log --format=%H -- {file}"))?;
    if !sha_out.status.success() {
        return Ok(Vec::new());
    }
    let shas: Vec<String> = String::from_utf8_lossy(&sha_out.stdout)
        .lines()
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
        .collect();
    if shas.is_empty() {
        return Ok(Vec::new());
    }

    // Deliberately no `--root`: the repository's initial commit (or an
    // import/vendoring commit with no meaningful prior state) touching N
    // files together is not a real co-change signal — every file was
    // "added together" once, which is not evidence they are repeatedly
    // maintained together. Omitting `--root` makes diff-tree silently skip
    // any such parentless commit's file list entirely, which is exactly the
    // exclusion wanted here (found via Codex review: a root/import commit
    // could otherwise manufacture a spurious 1.0 coupling strength between
    // two files whose only other shared commit was coincidental).
    let mut child = Command::new("git")
        .args(["diff-tree", "--name-only", "-r", "--stdin"])
        .current_dir(repo)
        .stdin(std::process::Stdio::piped())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped())
        .spawn()
        .context("git diff-tree --stdin")?;
    {
        use std::io::Write;
        let stdin = child.stdin.as_mut().expect("stdin was piped");
        stdin.write_all(shas.join("\n").as_bytes())?;
    }
    let out = child.wait_with_output().context("git diff-tree --stdin")?;
    if !out.status.success() {
        return Ok(Vec::new());
    }

    // Default `diff-tree` output (no `--format`, since `--no-commit-id`
    // suppresses the commit-id line entirely rather than replacing it) is
    // one 40-hex-char commit-id line followed by its changed files, with no
    // blank-line separator between commits — so a 40-hex-char line is the
    // block boundary.
    let mut pairs = Vec::new();
    let mut cur_sha: Option<String> = None;
    let mut cur_files: Vec<String> = Vec::new();
    for line in String::from_utf8_lossy(&out.stdout).lines() {
        if is_commit_sha(line) {
            flush_commit(&cur_sha, &cur_files, max_files_per_commit, &mut pairs);
            cur_sha = Some(line.to_string());
            cur_files.clear();
        } else if !line.trim().is_empty() {
            cur_files.push(line.to_string());
        }
    }
    flush_commit(&cur_sha, &cur_files, max_files_per_commit, &mut pairs);
    Ok(pairs)
}

fn is_commit_sha(line: &str) -> bool {
    line.len() == 40 && line.bytes().all(|b| b.is_ascii_hexdigit())
}

fn flush_commit(
    sha: &Option<String>,
    files: &[String],
    max_files_per_commit: usize,
    pairs: &mut Vec<(String, String)>,
) {
    let Some(sha) = sha else { return };
    if files.len() > max_files_per_commit {
        return;
    }
    for f in files {
        pairs.push((f.clone(), sha.clone()));
    }
}

/// `@@ -l,s +l,s @@`
fn parse_hunk_header(line: &str) -> Option<(Option<u32>, u32, u32)> {
    let plus = line.split_whitespace().nth(2)?;
    let plus = plus.strip_prefix('+')?;
    let (start, count) = match plus.split_once(',') {
        Some((s, c)) => (
            s.parse::<u32>().ok()?,
            c.trim_end_matches('@').trim().parse::<u32>().ok()?,
        ),
        None => (plus.parse::<u32>().ok()?, 1),
    };
    Some((None, start.max(1), count))
}

#[cfg(test)]
mod tests {
    use super::*;

    const DIFF: &str = "\
diff --git a/src/auth.py b/src/auth.py
index aaa..bbb 100644
--- a/src/auth.py
+++ b/src/auth.py
@@ -10,0 +11,2 @@ def login():
+    token = refresh_token()
+    return token
@@ -40,1 +42,1 @@
-old
+new
diff --git a/new_file.ts b/new_file.ts
new file mode 100644
--- /dev/null
+++ b/new_file.ts
@@ -0,0 +1,3 @@
+export const x = 1;
";

    #[test]
    fn parses_added_ranges_and_multiple_files() {
        let deltas = parse_unified(DIFF);
        assert_eq!(deltas.len(), 2);
        let auth = deltas.iter().find(|d| d.file == "src/auth.py").unwrap();
        assert_eq!(auth.added, vec![(11, 12), (42, 42)]);
        let nf = deltas.iter().find(|d| d.file == "new_file.ts").unwrap();
        assert_eq!(nf.added, vec![(1, 3)]);
    }

    #[test]
    fn hunk_header_without_count_is_single_line() {
        assert_eq!(parse_hunk_header("@@ -5 +6 @@ ctx"), Some((None, 6, 1)));
    }

    #[test]
    fn real_git_diff_roundtrip() {
        let tmp = tempfile::tempdir().unwrap();
        let root = tmp.path();
        std::fs::write(root.join("a.py"), "def one():\n    pass\n").unwrap();
        git(root, &["init", "-q"]);
        git(root, &["add", "."]);
        git(
            root,
            &[
                "-c",
                "user.email=t@t",
                "-c",
                "user.name=t",
                "commit",
                "-qm",
                "init",
            ],
        );
        std::fs::write(
            root.join("a.py"),
            "def one():\n    pass\n\ndef two():\n    return 2\n",
        )
        .unwrap();
        let deltas = diff_files(root, "").unwrap();
        let d = deltas.iter().find(|d| d.file == "a.py").expect("delta");
        assert!(d.added.windows(2).any(|w| w[0].1 + 1 == w[1].0) || !d.added.is_empty());
    }

    /// `diff.mnemonicprefix` is a real, commonly recommended git setting
    /// (renders headers as `c/`/`w/` instead of `a/`/`b/`) that a repo or a
    /// user's global config can set. Before `diff_text` forced
    /// `--src-prefix=a/ --dst-prefix=b/`, this silently zeroed out every
    /// changed file `parse_unified`/`deleted_files` could see — no error,
    /// just empty diff evidence. CI's own runners never set this, so
    /// without a test setting it explicitly (rather than relying on the
    /// ambient environment), a regression here would pass CI while still
    /// breaking `oxide review --diff`/`oxide query --git` for real users.
    #[test]
    fn real_git_diff_roundtrip_survives_mnemonicprefix_config() {
        let tmp = tempfile::tempdir().unwrap();
        let root = tmp.path();
        std::fs::write(root.join("a.py"), "def one():\n    pass\n").unwrap();
        git(root, &["init", "-q"]);
        git(root, &["config", "diff.mnemonicprefix", "true"]);
        git(root, &["add", "."]);
        git(
            root,
            &[
                "-c",
                "user.email=t@t",
                "-c",
                "user.name=t",
                "commit",
                "-qm",
                "init",
            ],
        );
        std::fs::write(
            root.join("a.py"),
            "def one():\n    pass\n\ndef two():\n    return 2\n",
        )
        .unwrap();
        let deltas = diff_files(root, "").unwrap();
        let d = deltas.iter().find(|d| d.file == "a.py").expect("delta");
        assert!(!d.added.is_empty());
        assert_eq!(
            deleted_files(&diff_text(root, "").unwrap()),
            Vec::<String>::new()
        );
    }

    /// #35: each `HUNK_ARGS` flag neutralizes its config key. One case per
    /// key, each with only that key set, checks three things: before the key
    /// is set, an unpinned `git diff` gives `expected` (git's default); the
    /// key makes the unpinned diff differ (so the case can fail); and
    /// `diff_files` still gives `expected`. Dropping any flag fails its case.
    /// The settings `HUNK_ARGS` lists as unpinned are not covered; a runner
    /// that exports `GIT_DIFF_OPTS`, or whose system attributes file marks
    /// `*.py` binary, fails the pinned check.
    #[test]
    fn each_hunk_arg_neutralizes_its_config_key() {
        type Setup = fn(&Path) -> (&'static str, Vec<(u32, u32)>);
        let cases: [(&str, &str, Setup); 6] = [
            ("diff.algorithm", "histogram", retry_case),
            ("diff.indentHeuristic", "false", indent_case),
            ("diff.interHunkContext", "3", nearby_hunks_case),
            ("diff.renames", "false", rename_case),
            ("diff.external", "false", nearby_hunks_case),
            (
                "diff.up.textconv",
                "awk 'NR==1{print \"x\"}1'",
                textconv_case,
            ),
        ];
        for (key, value, setup) in cases {
            let tmp = tempfile::tempdir().unwrap();
            let root = tmp.path();
            git(root, &["init", "-q"]);
            // A repo-local empty `core.attributesFile` hides the user-level
            // attribute file from both the control and `diff_files`, which
            // deliberately honours host attributes.
            let no_attrs = root.join(".git/no-attributes");
            std::fs::write(&no_attrs, "").unwrap();
            let no_attrs = no_attrs.to_str().unwrap();
            git(root, &["config", "core.attributesFile", no_attrs]);
            let (range, expected) = setup(root);
            let expected = vec![expected];
            assert_eq!(
                unpinned(root, range),
                Some(expected.clone()),
                "{key}: default"
            );
            git(root, &["config", key, value]);
            assert_ne!(
                unpinned(root, range),
                Some(expected.clone()),
                "{key}: no bite"
            );
            let pinned = diff_files(root, range).unwrap();
            let pinned: Vec<_> = pinned.into_iter().map(|d| d.added).collect();
            assert_eq!(pinned, expected, "{key}: pinned");
        }
    }

    /// Attribute files outside the repo carry conversion attributes git applies
    /// to the worktree side of the diff; `diff_text` must keep honouring them.
    /// Here `core.attributesFile` binds a clean filter that drops a local-only
    /// line, so git sees no logical change and neither may `diff_text`.
    #[test]
    fn diff_text_honours_host_conversion_attributes() {
        let tmp = tempfile::tempdir().unwrap();
        let root = tmp.path();
        git(root, &["init", "-q"]);
        let attrs = root.join("host.attributes");
        std::fs::write(&attrs, "*.py filter=strip\n").unwrap();
        git(
            root,
            &["config", "core.attributesFile", attrs.to_str().unwrap()],
        );
        git(
            root,
            &["config", "filter.strip.clean", "grep -v LOCAL-ONLY"],
        );
        git(root, &["config", "filter.strip.smudge", "cat"]);
        write_and_commit(root, "a.py", "def a():\n    return 1\n");
        let edited = "def a():\n    return 1\nx = 1  # LOCAL-ONLY\n";
        std::fs::write(root.join("a.py"), edited).unwrap();
        assert!(diff_files(root, "").unwrap().is_empty());
    }

    /// `diff_text` without its overrides, isolated from the host's git config
    /// and attribute files (system, `$XDG_CONFIG_HOME/git/attributes`) so only
    /// the repo-local key under test can change it; `None` when git
    /// fails (`diff.external=false`).
    fn unpinned(root: &Path, range: &str) -> Option<Vec<Vec<(u32, u32)>>> {
        let rev = if range.is_empty() { "HEAD" } else { range };
        let out = Command::new("git")
            .args(["diff", "--unified=0", "--no-color", "--src-prefix=a/"])
            .args(["--dst-prefix=b/", rev])
            .env("GIT_CONFIG_NOSYSTEM", "1")
            .env("GIT_CONFIG_GLOBAL", "/dev/null")
            .env("GIT_CONFIG_COUNT", "0")
            .env("GIT_ATTR_NOSYSTEM", "1")
            .env("XDG_CONFIG_HOME", root.join(".no-xdg"))
            .env("HOME", root.join(".no-home"))
            .env_remove("GIT_DIFF_OPTS")
            .env_remove("GIT_EXTERNAL_DIFF")
            .current_dir(root)
            .output()
            .unwrap();
        out.status.success().then(|| {
            let deltas = parse_unified(&String::from_utf8_lossy(&out.stdout));
            deltas.into_iter().map(|d| d.added).collect()
        })
    }

    fn write_and_commit(root: &Path, file: &str, text: &str) {
        std::fs::write(root.join(file), text).unwrap();
        git(root, &["add", "."]);
        commit(root, "base");
    }

    /// The `candidate_output_golden` `retry.py` state: myers yields one hunk,
    /// histogram keeps blank line 26 as context and splits it in two.
    fn retry_case(root: &Path) -> (&'static str, Vec<(u32, u32)>) {
        let full = std::fs::read_to_string(
            Path::new(env!("CARGO_MANIFEST_DIR")).join("fixtures/py_repo/oxidepy/retry.py"),
        )
        .unwrap();
        let lines: Vec<&str> = full.lines().collect();
        let half = lines[..lines.len() / 2].join("\n");
        write_and_commit(root, "retry.py", &format!("{half}\n# rev 1\n\n# rev 2\n"));
        std::fs::write(root.join("retry.py"), &full).unwrap();
        ("", vec![(23, 45)])
    }

    /// A repeated block: the indent heuristic slides the insertion up a line.
    fn indent_case(root: &Path) -> (&'static str, Vec<(u32, u32)>) {
        let block = "    a = 1\n\n    b = 2\n";
        write_and_commit(root, "a.py", &format!("def f():\n{block}    return a\n"));
        let text = format!("def f():\n{block}{block}    return a\n");
        std::fs::write(root.join("a.py"), text).unwrap();
        ("", vec![(4, 6)])
    }

    /// Two edits two lines apart: separate `-U0` hunks unless fused.
    fn nearby_hunks_case(root: &Path) -> (&'static str, Vec<(u32, u32)>) {
        write_and_commit(root, "n.py", "a = 1\nb = 2\nc = 3\nd = 4\n");
        std::fs::write(root.join("n.py"), "a = 10\nb = 2\nc = 3\nd = 40\n").unwrap();
        ("", vec![(1, 1), (4, 4)])
    }

    /// A renamed and edited file: only the edit is added when renames are found.
    fn rename_case(root: &Path) -> (&'static str, Vec<(u32, u32)>) {
        let text: String = (1..=20).map(|i| format!("x{i} = {i}\n")).collect();
        write_and_commit(root, "old.py", &text);
        git(root, &["mv", "old.py", "new.py"]);
        std::fs::write(root.join("new.py"), format!("{text}y = 1\n")).unwrap();
        git(root, &["add", "."]);
        commit(root, "rename");
        ("HEAD~1..HEAD", vec![(21, 21)])
    }

    /// A textconv driver bound through `.git/info/attributes` (per-clone, not
    /// repo content); the converter prepends a line.
    fn textconv_case(root: &Path) -> (&'static str, Vec<(u32, u32)>) {
        std::fs::write(root.join(".git/info/attributes"), "*.py diff=up\n").unwrap();
        nearby_hunks_case(root)
    }

    #[test]
    fn deleted_file_section_does_not_leak_into_previous_file() {
        let diff = "\
diff --git a/src/a.py b/src/a.py
--- a/src/a.py
+++ b/src/a.py
@@ -1,2 +1,3 @@
+extra
diff --git a/deleted.py b/deleted.py
deleted file mode 100644
--- a/deleted.py
+++ /dev/null
@@ -1,3 +0,0 @@
-x
-y
-z
diff --git a/src/c.py b/src/c.py
--- a/src/c.py
+++ b/src/c.py
@@ -10,0 +11,1 @@
+more
";
        let deltas = parse_unified(diff);
        let files: Vec<&str> = deltas.iter().map(|d| d.file.as_str()).collect();
        assert_eq!(files, vec!["src/a.py", "src/c.py"], "{deltas:?}");
        let a = deltas.iter().find(|d| d.file == "src/a.py").unwrap();
        assert_eq!(
            a.added,
            vec![(1, 3)],
            "deleted.py's hunk must not leak in here"
        );
        assert_eq!(deleted_files(diff), vec!["deleted.py".to_string()]);
    }

    /// One modified file per header label, each followed by one hunk.
    fn modified(labels: &[&str]) -> String {
        labels
            .iter()
            .enumerate()
            .map(|(i, l)| {
                let n = i + 1;
                format!("diff --git a/x b/x\n--- a/x\n+++ {l}\n@@ -{i},0 +{n},1 @@\n+x\n")
            })
            .collect()
    }

    fn files(text: &str) -> Vec<(String, Vec<(u32, u32)>)> {
        parse_unified(text)
            .into_iter()
            .map(|d| (d.file, d.added))
            .collect()
    }

    /// #37: git C-quotes a header path holding bytes >= 0x80 (with the
    /// default `core.quotePath`), `"`, `\` or control characters. The quoted
    /// form used to be skipped, so its hunks fell to the previous file.
    #[test]
    fn quoted_header_paths_are_decoded() {
        let text = modified(&[
            "b/plain.py",
            r#""b/caf\303\251.py""#,
            r#""b/q\"uote.py""#,
            r#""b/back\\slash.py""#,
            r#""b/c\a\b\t\n\v\f\r\001\177.py""#,
            "\"b/caf\\303\\251 space.py\"\t",
        ]);
        let want = [
            ("b/back\\slash.py", 4),
            ("b/c\x07\x08\t\n\x0b\x0c\r\x01\x7f.py", 5),
            ("b/caf\u{e9} space.py", 6),
            ("b/caf\u{e9}.py", 2),
            ("b/plain.py", 1),
            ("b/q\"uote.py", 3),
        ];
        let want: Vec<_> = want
            .iter()
            .map(|(f, n)| (f[2..].to_string(), vec![(*n, *n)]))
            .collect();
        assert_eq!(files(&text), want);
    }

    /// A quoted header that does not decode, or decodes to bytes that are
    /// not UTF-8, names no file: its hunks are dropped, never attributed to
    /// the previous file and never matched through a lossy path. OXIDE only
    /// indexes UTF-8 paths (`scanner::is_denied`), so nothing is lost.
    #[test]
    fn unreadable_quoted_header_paths_name_no_file() {
        for bad in [
            r#""b/caf\303\251.py"#,
            r#""b/x\q.py""#,
            r#""b/x\4001.py""#,
            r#""b/x\30""#,
            r#""b/x\""#,
            r#""b/x.py" junk"#,
            r#""a/x.py""#,
            r#""b/\377.py""#,
            r#""b/caf\303.py""#,
            "\"",
            "\"\\",
        ] {
            let text = modified(&["b/prev.py", bad]);
            let want = vec![("prev.py".to_string(), vec![(1, 1)])];
            assert_eq!(files(&text), want, "{bad}");
            // The same label on the `---` side, with the other prefix swapped in.
            let old = match bad.strip_prefix("\"a/") {
                Some(rest) => format!("\"b/{rest}"),
                None => bad.replacen("b/", "a/", 1),
            };
            let deleted = format!("--- {old}\n+++ /dev/null\n");
            assert!(deleted_files(&deleted).is_empty(), "{old}");
        }
    }

    #[test]
    fn quoted_header_paths_keep_add_delete_and_rename_semantics() {
        let text = r#"diff --git "a/caf\303\251.py" "a/caf\303\251.py"
deleted file mode 100644
--- "a/caf\303\251.py"
+++ /dev/null
@@ -1,2 +0,0 @@
-x
-y
diff --git a/old.py "b/n\303\251w.py"
similarity index 90%
rename from old.py
rename to "n\303\251w.py"
--- a/old.py
+++ "b/n\303\251w.py"
@@ -3,0 +4,1 @@
+z
diff --git "b/\303\251.py" "b/\303\251.py"
new file mode 100644
--- /dev/null
+++ "b/\303\251.py"
@@ -0,0 +1,2 @@
+a
+b
"#;
        let want = vec![
            ("n\u{e9}w.py".to_string(), vec![(4, 4)]),
            ("\u{e9}.py".to_string(), vec![(1, 2)]),
        ];
        assert_eq!(files(text), want);
        assert_eq!(deleted_files(text), vec!["caf\u{e9}.py".to_string()]);
    }

    /// Under `-U0` an added line `++ ...` prints as `+++ ...` and a removed
    /// line `-- ...` as `--- ...`. Inside a hunk these are content, not
    /// headers: they must not re-point or drop the file's later hunks, nor
    /// report a deletion.
    #[test]
    fn hunk_body_lines_that_look_like_headers_are_content() {
        let text = r#"diff --git a/doc.md b/doc.md
--- a/doc.md
+++ b/doc.md
@@ -1,0 +2,4 @@
+++ "b/other.py"
+++ "foo"
+++ b/other.py
+++ /dev/null
@@ -9,0 +13,1 @@
+tail
diff --git a/notes.sql b/notes.sql
--- a/notes.sql
+++ b/notes.sql
@@ -1,2 +1,1 @@
--- a/gone.py
+++ /dev/null
"#;
        let want = vec![
            ("doc.md".to_string(), vec![(2, 5), (13, 13)]),
            ("notes.sql".to_string(), vec![(1, 1)]),
        ];
        assert_eq!(files(text), want);
        assert!(deleted_files(text).is_empty());
    }

    /// Unquoted headers keep their old reading, including the trimmed tab git
    /// appends to a label with a space, and the `/dev/null` sides.
    #[test]
    fn unquoted_header_paths_are_unchanged() {
        let text = modified(&["b/a b.py\t", "b/c.py"]);
        let want = vec![
            ("a b.py".to_string(), vec![(1, 1)]),
            ("c.py".to_string(), vec![(2, 2)]),
        ];
        assert_eq!(files(&text), want);
        let text = "--- a/a b.py\t\n+++ /dev/null\n--- /dev/null\n+++ b/n.py\n";
        assert_eq!(deleted_files(text), vec!["a b.py".to_string()]);
    }

    /// #37 against real git: with `core.quotePath` on (git's default, set
    /// repo-locally in case the host turns it off), a non-ASCII path's hunks
    /// and its deletion both come back under the UTF-8 path.
    #[test]
    fn real_git_non_ascii_paths_roundtrip() {
        let tmp = tempfile::tempdir().unwrap();
        let root = tmp.path();
        git(root, &["init", "-q"]);
        git(root, &["config", "core.quotePath", "true"]);
        std::fs::write(root.join("gone\u{e9}.py"), "g = 1\n").unwrap();
        write_and_commit(root, "caf\u{e9}.py", "x = 1\ny = 2\n");
        std::fs::write(root.join("caf\u{e9}.py"), "x = 1\nz = 0\ny = 2\nw = 3\n").unwrap();
        std::fs::remove_file(root.join("gone\u{e9}.py")).unwrap();
        let text = diff_text(root, "").unwrap();
        assert!(text.contains(r#"+++ "b/caf\303\251.py""#), "{text}");
        assert_eq!(
            files(&text),
            vec![("caf\u{e9}.py".to_string(), vec![(2, 2), (4, 4)])]
        );
        assert_eq!(deleted_files(&text), vec!["gone\u{e9}.py".to_string()]);
    }

    /// Names Windows cannot hold: `"`, `\` and a tab, each quoted by git
    /// whatever `core.quotePath` says.
    #[cfg(unix)]
    #[test]
    fn real_git_special_character_paths_roundtrip() {
        let tmp = tempfile::tempdir().unwrap();
        let root = tmp.path();
        git(root, &["init", "-q"]);
        git(root, &["config", "core.quotePath", "false"]);
        let names = ["q\"uote.py", "back\\slash.py", "t\tab.py"];
        for name in names {
            std::fs::write(root.join(name), "x = 1\n").unwrap();
        }
        git(root, &["add", "."]);
        commit(root, "base");
        for name in names {
            std::fs::write(root.join(name), "x = 1\ny = 2\n").unwrap();
        }
        let mut want: Vec<_> = names
            .iter()
            .map(|n| (n.to_string(), vec![(2, 2)]))
            .collect();
        want.sort();
        assert_eq!(files(&diff_text(root, "").unwrap()), want);
    }

    #[test]
    fn recent_commits_and_co_change_are_deterministic() {
        let tmp = tempfile::tempdir().unwrap();
        let root = tmp.path();
        git(root, &["init", "-q"]);
        std::fs::write(root.join("a.py"), "a1\n").unwrap();
        std::fs::write(root.join("b.py"), "b1\n").unwrap();
        git(root, &["add", "."]);
        commit(root, "first");
        std::fs::write(root.join("a.py"), "a2\n").unwrap();
        std::fs::write(root.join("b.py"), "b2\n").unwrap();
        git(root, &["add", "."]);
        commit(root, "second: touch both");

        let commits = recent_commits(root, 10).unwrap();
        assert_eq!(commits.len(), 2);
        assert_eq!(commits[0].message, "second: touch both");
        assert!(
            commits[0].committed_at.ends_with('Z'),
            "{}",
            commits[0].committed_at
        );

        let pairs = co_change_raw(root, "a.py", 50, 20).unwrap();
        assert!(
            pairs.iter().any(|(f, _)| f == "b.py"),
            "a.py and b.py changed together: {pairs:?}"
        );

        // Same repo state, same result both times.
        let again = co_change_raw(root, "a.py", 50, 20).unwrap();
        assert_eq!(pairs, again);
    }

    fn commit(dir: &std::path::Path, message: &str) {
        git(
            dir,
            &[
                "-c",
                "user.email=t@t",
                "-c",
                "user.name=t",
                "commit",
                "-qm",
                message,
            ],
        );
    }

    fn git(dir: &std::path::Path, args: &[&str]) {
        let st = Command::new("git")
            .args(args)
            .current_dir(dir)
            .status()
            .unwrap();
        assert!(st.success(), "git {args:?}");
    }
}
