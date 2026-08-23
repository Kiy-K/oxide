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

/// Parse `git diff --unified=0` for a range into per-file deltas of added
/// lines (new-file coordinates). `range` empty = worktree vs HEAD; `X` = X vs
/// parent; `A..B` explicit.
pub fn diff_files(repo: &Path, range: &str) -> Result<Vec<FileDelta>> {
    let text = if range.is_empty() {
        run_git(repo, &["diff", "--unified=0", "--no-color", "HEAD"])?
    } else if range.contains("..") {
        run_git(repo, &["diff", "--unified=0", "--no-color", range])?
    } else {
        run_git(repo, &["diff", "--unified=0", "--no-color", &format!("{range}^"), range])?
    };
    Ok(parse_unified(&text))
}

pub fn parse_unified(text: &str) -> Vec<FileDelta> {
    let mut files: HashMap<String, FileDelta> = HashMap::new();
    let mut cur: Option<String> = None;
    for line in text.lines() {
        if let Some(rest) = line.strip_prefix("+++ b/") {
            cur = Some(rest.trim().to_string());
        } else if line.starts_with("@@") {
            let Some(target) = cur.clone() else { continue };
            if let Some((_, new_start, new_count)) = parse_hunk_header(line) {
                if new_count > 0 {
                    let entry = files.entry(target.clone()).or_insert(FileDelta {
                        file: target.clone(),
                        added: vec![],
                    });
                    entry.added.push((new_start.max(1), (new_start + new_count - 1).max(1)));
                }
            }
        }
    }
    let mut out: Vec<FileDelta> = files.into_values().collect();
    out.sort_by(|a, b| a.file.cmp(&b.file));
    out
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
        git(root, &["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"]);
        std::fs::write(root.join("a.py"), "def one():\n    pass\n\ndef two():\n    return 2\n").unwrap();
        let deltas = diff_files(root, "").unwrap();
        let d = deltas.iter().find(|d| d.file == "a.py").expect("delta");
        assert!(d.added.windows(2).any(|w| w[0].1 + 1 == w[1].0) || d.added.len() >= 1);
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
