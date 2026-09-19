//! Literal repository search: a native, deterministic byte-substring scan
//! over every file OXIDE's ignore policy would keep — not just files with a
//! recognized language, and not FTS5 or an `rg` subprocess.
//!
//! This is the *control* arm for github.com/Kiy-K/oxide/issues/6: a trigram
//! or FTS5 index is only worth adding to OXIDE if it beats this scan by
//! enough (on correctness, latency, and storage) to earn its ~34MB cost.
//! Nothing here is a step toward that index; there is no regex support and
//! no trigram/FTS5 code, by design, until that evaluation says otherwise.
//!
//! Distinct from [`crate::retrieval::SearchMode`] and [`crate::service::
//! Evidence`] on purpose: a literal hit is a line in a file, not a symbol —
//! it has no qualified name, kind, or score, and `Evidence`'s shape would
//! have to fabricate all three. Keeping literal search as its own type
//! means `retrieval.rs` (benchmark-gated) never has to know this mode
//! exists, and this scan never touches the index at all — it works on an
//! unindexed repository, which is the whole point of a path that doesn't
//! need embeddings or a `.oxide` directory to answer "where does this
//! string appear".

use crate::scanner;
use anyhow::{ensure, Result};
use std::path::Path;

/// Hard ceiling on results returned to a caller, regardless of the
/// requested limit — mirrors `service::MAX_SEARCH_RESULTS`'s role for
/// symbol search.
pub const MAX_RESULTS: usize = 200;

/// Matches kept per file before the rest of that file is skipped. Bounds a
/// single file (e.g. a generated fixture repeating the same token on every
/// line) from crowding out every other file's matches.
const MAX_MATCHES_PER_FILE: usize = 50;

/// Global accumulation ceiling applied *during* the walk, independent of
/// the caller's requested limit and well above [`MAX_RESULTS`]. Files are
/// visited in the deterministic sorted order `scanner::scan_repo_text`
/// already returns, and matches within a file are found in line-then-column
/// order, so stopping once this many candidates have been accumulated always
/// keeps the same deterministic prefix — never a different subset depending
/// on scan timing.
const SCAN_SAFETY_CAP: usize = 4_000;

/// One line/column match. `column` is the 1-based byte offset of the match
/// start within its line (ripgrep's `--column` convention), not a character
/// count — see `tests/literal_search.rs` for the parity check against `rg`.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize)]
pub struct LiteralHit {
    pub file: String,
    pub line: u32,
    pub column: u32,
    /// The matched line, decoded lossily. Matching itself is byte-exact and
    /// works on non-UTF-8 files; only this display copy can lose fidelity
    /// (invalid sequences become U+FFFD), and only for the rare file that
    /// isn't UTF-8 to begin with.
    pub snippet: String,
}

#[derive(Debug, Clone, Default, PartialEq, Eq, serde::Serialize)]
pub struct LiteralSearchResult {
    pub hits: Vec<LiteralHit>,
    /// True when more matches existed than were returned — either the
    /// caller's `limit` or the internal safety cap cut the scan short.
    pub truncated: bool,
}

/// Longest snippet kept per hit, in bytes of the lossily-decoded line.
/// Independent of the match bound above: a single very long line (minified
/// output, a data file) shouldn't blow up output size just because the
/// pattern happens to appear in it.
const MAX_SNIPPET_BYTES: usize = 300;

fn find(haystack: &[u8], needle: &[u8]) -> Option<usize> {
    if needle.is_empty() || needle.len() > haystack.len() {
        return None;
    }
    haystack
        .windows(needle.len())
        .position(|window| window == needle)
}

fn truncate_snippet(line: &[u8]) -> String {
    let mut text = String::from_utf8_lossy(line).into_owned();
    if text.len() > MAX_SNIPPET_BYTES {
        let mut end = MAX_SNIPPET_BYTES;
        while !text.is_char_boundary(end) {
            end -= 1;
        }
        text.truncate(end);
        text.push('…');
    }
    text
}

/// Scan every file under `root` that OXIDE's ignore policy would keep
/// (`scanner::scan_repo_text`) for a literal, case-sensitive occurrence of
/// `pattern`, byte-exact regardless of the file's own encoding. Results are
/// sorted `(file, line, column)` and bounded to `limit` (itself capped at
/// [`MAX_RESULTS`]).
pub fn search(root: &Path, pattern: &str, limit: usize) -> Result<LiteralSearchResult> {
    ensure!(
        !pattern.is_empty(),
        "literal search pattern must not be empty"
    );
    let limit = limit.min(MAX_RESULTS);
    let needle = pattern.as_bytes();
    let files = scanner::scan_repo_text(root)?;

    let mut hits = Vec::new();
    let mut truncated = false;
    'files: for rel in &files {
        let full = root.join(rel);
        let Ok(bytes) = std::fs::read(&full) else {
            continue;
        };
        let display = rel.to_string_lossy().replace('\\', "/");
        let mut file_matches = 0usize;
        for (idx, line) in bytes.split(|&b| b == b'\n').enumerate() {
            let mut cursor = 0usize;
            while let Some(pos) = find(&line[cursor..], needle) {
                let match_start = cursor + pos;
                hits.push(LiteralHit {
                    file: display.clone(),
                    line: (idx + 1) as u32,
                    column: (match_start + 1) as u32,
                    snippet: truncate_snippet(line),
                });
                file_matches += 1;
                if hits.len() >= SCAN_SAFETY_CAP {
                    truncated = true;
                    break 'files;
                }
                if file_matches >= MAX_MATCHES_PER_FILE {
                    truncated = true;
                    break;
                }
                cursor = match_start + needle.len();
            }
            if file_matches >= MAX_MATCHES_PER_FILE {
                break;
            }
        }
    }

    if hits.len() > limit {
        hits.truncate(limit);
        truncated = true;
    }
    Ok(LiteralSearchResult { hits, truncated })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn write(path: &Path, content: &str) {
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(path, content).unwrap();
    }

    #[test]
    fn finds_matches_with_line_and_column() {
        let tmp = tempfile::tempdir().unwrap();
        let root = tmp.path();
        write(
            &root.join("src/a.py"),
            "def needle():\n    return needle_value\n",
        );
        std::process::Command::new("git")
            .args(["init", "-q"])
            .current_dir(root)
            .status()
            .unwrap();

        let result = search(root, "needle", 100).unwrap();
        assert!(!result.truncated);
        assert_eq!(result.hits.len(), 2);
        assert_eq!(result.hits[0].file, "src/a.py");
        assert_eq!(result.hits[0].line, 1);
        assert_eq!(result.hits[0].column, 5);
        assert_eq!(result.hits[1].line, 2);
        assert_eq!(result.hits[1].column, 12);
    }

    #[test]
    fn searches_files_a_symbol_index_would_never_parse() {
        let tmp = tempfile::tempdir().unwrap();
        let root = tmp.path();
        write(&root.join("README.md"), "TODO: fix flaky_widget\n");
        write(&root.join("config.toml"), "name = \"flaky_widget\"\n");
        std::process::Command::new("git")
            .args(["init", "-q"])
            .current_dir(root)
            .status()
            .unwrap();

        let result = search(root, "flaky_widget", 100).unwrap();
        let files: Vec<&str> = result.hits.iter().map(|h| h.file.as_str()).collect();
        assert!(files.contains(&"README.md"), "{files:?}");
        assert!(files.contains(&"config.toml"), "{files:?}");
    }

    #[test]
    fn respects_ignore_policy_and_denylist() {
        let tmp = tempfile::tempdir().unwrap();
        let root = tmp.path();
        write(&root.join("keep.txt"), "marker\n");
        write(&root.join("ignored/skip.txt"), "marker\n");
        write(&root.join("node_modules/pkg/skip.txt"), "marker\n");
        write(&root.join(".env"), "SECRET=marker\n");
        write(&root.join(".gitignore"), "/ignored/\n");
        std::process::Command::new("git")
            .args(["init", "-q"])
            .current_dir(root)
            .status()
            .unwrap();

        let result = search(root, "marker", 100).unwrap();
        let files: Vec<&str> = result.hits.iter().map(|h| h.file.as_str()).collect();
        assert_eq!(files, vec!["keep.txt"]);
    }

    #[test]
    fn matches_non_utf8_files_byte_exactly() {
        let tmp = tempfile::tempdir().unwrap();
        let root = tmp.path();
        std::fs::create_dir_all(root.join("src")).unwrap();
        let mut bytes = b"needle before\n".to_vec();
        bytes.extend_from_slice(&[0xFF, 0xFE]); // invalid UTF-8, not NUL
        bytes.extend_from_slice(b" needle after\n");
        std::fs::write(root.join("src/latin.txt"), &bytes).unwrap();
        std::process::Command::new("git")
            .args(["init", "-q"])
            .current_dir(root)
            .status()
            .unwrap();

        let result = search(root, "needle", 100).unwrap();
        assert_eq!(result.hits.len(), 2, "{:?}", result.hits);
    }

    #[test]
    fn empty_pattern_is_rejected() {
        let tmp = tempfile::tempdir().unwrap();
        assert!(search(tmp.path(), "", 10).is_err());
    }

    #[test]
    fn truncates_deterministically_when_over_limit() {
        let tmp = tempfile::tempdir().unwrap();
        let root = tmp.path();
        for i in 0..5 {
            write(&root.join(format!("f{i}.txt")), "marker\n");
        }
        std::process::Command::new("git")
            .args(["init", "-q"])
            .current_dir(root)
            .status()
            .unwrap();

        let first = search(root, "marker", 2).unwrap();
        let second = search(root, "marker", 2).unwrap();
        assert!(first.truncated);
        assert_eq!(first.hits, second.hits, "truncation must be deterministic");
        assert_eq!(first.hits.len(), 2);
        assert_eq!(first.hits[0].file, "f0.txt");
        assert_eq!(first.hits[1].file, "f1.txt");
    }
}
