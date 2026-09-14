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
pub fn diff_text(repo: &Path, range: &str) -> Result<String> {
    if range.is_empty() {
        run_git(repo, &["diff", "--unified=0", "--no-color", "HEAD"])
    } else {
        run_git(repo, &["diff", "--unified=0", "--no-color", range])
    }
}

/// Parse `git diff --unified=0` into per-file deltas of added lines
/// (new-file coordinates). `range` empty = worktree vs HEAD; `A..B` explicit;
/// a single rev `R` means "everything since R" (`git diff R`).
pub fn diff_files(repo: &Path, range: &str) -> Result<Vec<FileDelta>> {
    Ok(parse_unified(&diff_text(repo, range)?))
}

pub fn parse_unified(text: &str) -> Vec<FileDelta> {
    let mut files: HashMap<String, FileDelta> = HashMap::new();
    let mut cur: Option<String> = None;
    for line in text.lines() {
        // A deleted file's diff section has no `+++ b/...` line at all, so
        // without this arm `cur` kept whatever the *previous* file section
        // set it to — every hunk inside a deleted file's section was
        // silently attributed to the prior file once zero-count hunks
        // stopped being filtered out below.
        if line.starts_with("+++ /dev/null") {
            cur = None;
        } else if let Some(rest) = line.strip_prefix("+++ b/") {
            cur = Some(rest.trim().to_string());
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
    for line in text.lines() {
        if let Some(rest) = line.strip_prefix("--- a/") {
            pending = Some(rest.trim().to_string());
        } else if line.starts_with("+++ /dev/null") {
            if let Some(p) = pending.take() {
                out.push(p);
            }
        } else if line.starts_with("--- ") || line.starts_with("+++ ") {
            pending = None;
        }
    }
    out.sort();
    out.dedup();
    out
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

    // `--root` makes the very first commit in history (no parent) still
    // show a diff — otherwise diff-tree silently skips it.
    let mut child = Command::new("git")
        .args(["diff-tree", "--name-only", "-r", "--root", "--stdin"])
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
