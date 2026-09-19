//! Literal repository search: a native, deterministic byte-substring scan
//! over every file OXIDE's ignore policy would keep — not just files with a
//! recognized language, and not FTS5 or an `rg` subprocess.
//!
//! This is the *control* arm for github.com/Kiy-K/oxide/issues/6: a trigram
//! or FTS5 index is only worth adding to OXIDE if it beats this scan by
//! enough (on correctness, latency, and storage) to earn its ~34MB cost.
//! Nothing here is a step toward that index; there is no regex support, and
//! **no FTS5/trigram code exists anywhere in this crate.** The experimental
//! FTS5 trigram challenger was built, measured, and rejected on cost and
//! correctness grounds — see `docs/literal-search-eval/README.md`'s verdict
//! — and its implementation lives entirely outside this crate's build, in
//! the standalone `docs/literal-search-eval/spike/` crate (its own
//! `Cargo.toml`, never a workspace member, never compiled by `cargo build`/
//! `cargo test` here). If that verdict is ever reconsidered per the report's
//! own triggers, the challenger's code is there to pick back up — not
//! dormant in production.
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

/// Matches kept per file before the rest of that file is skipped and the
/// scan moves to the next one. Bounds a single file (e.g. a generated
/// fixture repeating the same token on every line) from consuming the
/// whole result budget by itself, at the deliberate cost of an exact
/// global sorted-prefix guarantee: when any file has more than this many
/// matches, later (sort-after) files still contribute their own matches
/// into the returned set, so the output can include a later file's hit
/// while this file's matches beyond the cap never appear — see `search`'s
/// doc comment for the precise contract this implies.
///
/// This shape was deliberately kept, not a bug left unfixed: a Greptile
/// review of the first cut of this cap correctly found that exact
/// "ordered but non-prefix" property (reachable in this very repo —
/// `src/retrieval.rs` has 80+ occurrences of `"fn "`) and proposed making
/// the cap abort the *whole scan* instead of moving to the next file. A
/// follow-up Codex review of that fix caught what it costs: one
/// match-heavy file would then silently suppress every other file's
/// results entirely, which is strictly worse for literal search's actual
/// job (finding a string across a repo) than the ordering imprecision it
/// fixed. What *was* a genuine bug, fixed here: `truncated` used to be set
/// the instant a file's count reached this cap, even when that file had
/// *exactly* this many matches and nothing more — see the check-before-
/// record structure in `search` below, which only sets it when a match
/// beyond the cap is actually found. See `docs/literal-search-eval/
/// README.md`'s "Post-review correctness fix" section for the full
/// back-and-forth between both reviews and why this is the resolution.
const MAX_MATCHES_PER_FILE: usize = 50;

/// Global accumulation ceiling applied *during* the walk, independent of
/// the caller's requested limit and well above [`MAX_RESULTS`]. Files are
/// visited in the deterministic sorted order `scanner::scan_repo_text`
/// already returns, and matches within a file are found in line-then-column
/// order, so stopping once this many candidates have been accumulated always
/// keeps the same deterministic prefix — never a different subset depending
/// on scan timing.
///
/// Checked in `search` before pushing a match, not after — this cap being
/// well above [`MAX_RESULTS`] means a corpus large enough to reach it
/// always has real-match-count > any possible `limit`, so the final
/// `hits.len() > limit` check independently sets `truncated` regardless;
/// check-before-record here has no currently-observable behavior
/// difference from check-after-record, but matches the discipline
/// [`MAX_MATCHES_PER_FILE`] now follows (a Greptile review flagged the
/// inconsistency) and stays correct if that constant relationship ever
/// changes.
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
/// `pattern`, byte-exact regardless of the file's own encoding.
///
/// The returned set is sorted `(file, line, column)` and bounded to
/// `limit` (itself capped at [`MAX_RESULTS`]) — but **this is not
/// necessarily an exact prefix of the full, unbounded match list**: each
/// file contributes at most [`MAX_MATCHES_PER_FILE`] matches before the
/// scan moves to the next file, so when any one file has more matches
/// than that, a later file's hits can appear in the output while that
/// file's own matches beyond the cap do not. See [`MAX_MATCHES_PER_FILE`]'s
/// doc for why this tradeoff (multi-file coverage over strict global
/// ordering) is deliberate, not an oversight.
///
/// The match loop is `memchr::memmem` (SIMD-accelerated substring search —
/// the same primitive ripgrep itself uses), not a hand-rolled scan. One
/// `Finder` is built once per `search()` call and reused across every file
/// and line, rather than rebuilt on every match attempt the way the old
/// per-call `find` was. `docs/literal-search-eval/README.md`'s profiling
/// pass found this swap worthwhile — but also found that the matcher
/// alone isn't the whole story: line-splitting (`bytes.split(|&b| b ==
/// b'\n')`, itself a scalar scan) and per-hit allocation are comparable
/// remaining costs, not eliminated by this change.
pub fn search(root: &Path, pattern: &str, limit: usize) -> Result<LiteralSearchResult> {
    ensure!(
        !pattern.is_empty(),
        "literal search pattern must not be empty"
    );
    let limit = limit.min(MAX_RESULTS);
    let needle = pattern.as_bytes();
    let finder = memchr::memmem::Finder::new(needle);
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
        'file: for (idx, line) in bytes.split(|&b| b == b'\n').enumerate() {
            let mut cursor = 0usize;
            while let Some(pos) = finder.find(&line[cursor..]) {
                let match_start = cursor + pos;
                // Both caps are checked *before* recording, not after:
                // `truncated` must only fire when a match genuinely exists
                // beyond a cap, not merely because the cap's count was
                // reached. A second Greptile review caught that this
                // check-before-record discipline had only been applied to
                // `MAX_MATCHES_PER_FILE` — `SCAN_SAFETY_CAP` had the exact
                // same bug (a repository with *exactly* 4,000 matches and
                // no more would have been falsely reported as truncated).
                // Moving to the next file on the per-file cap
                // (`break 'file`, not `break 'files`) — rather than
                // aborting the whole scan — is deliberate: see
                // `MAX_MATCHES_PER_FILE`'s doc for why an earlier attempt
                // to make this a strict global sorted-prefix regressed
                // multi-file coverage instead. `SCAN_SAFETY_CAP` has no
                // such per-file/multi-file tradeoff to preserve — it is
                // purely a global output-size bound — so it can `break
                // 'files` immediately once a genuine excess match is found.
                if file_matches >= MAX_MATCHES_PER_FILE {
                    truncated = true;
                    break 'file;
                }
                if hits.len() >= SCAN_SAFETY_CAP {
                    truncated = true;
                    break 'files;
                }
                hits.push(LiteralHit {
                    file: display.clone(),
                    line: (idx + 1) as u32,
                    column: (match_start + 1) as u32,
                    snippet: truncate_snippet(line),
                });
                file_matches += 1;
                cursor = match_start + needle.len();
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

    /// Pins the deliberate (reviewed twice, see `MAX_MATCHES_PER_FILE`'s
    /// doc) per-file-fairness contract: a file with more matches than the
    /// cap has its excess dropped, but the scan still moves on to later
    /// files rather than aborting — so a later-sorting file's matches DO
    /// appear in the output alongside the capped file's first
    /// `MAX_MATCHES_PER_FILE`, and `truncated` reflects that something was
    /// dropped. A first attempt at fixing this area made it a strict
    /// global sorted-prefix instead (aborting the whole scan on any
    /// over-cap file); a Codex review of that attempt correctly flagged it
    /// as a coverage regression — one match-heavy file would then silently
    /// suppress every other file's results — so it was reverted in favor
    /// of this documented tradeoff.
    #[test]
    fn per_file_cap_skips_to_the_next_file_and_flags_truncated() {
        let tmp = tempfile::tempdir().unwrap();
        let root = tmp.path();
        // "a.txt" sorts before "b.txt"; give it more than the cap.
        let over_cap = "marker\n".repeat(MAX_MATCHES_PER_FILE + 10);
        write(&root.join("a.txt"), &over_cap);
        write(&root.join("b.txt"), "marker\n");
        std::process::Command::new("git")
            .args(["init", "-q"])
            .current_dir(root)
            .status()
            .unwrap();

        let result = search(root, "marker", MAX_RESULTS).unwrap();
        assert!(result.truncated, "a.txt has more matches than the cap");
        assert_eq!(result.hits.len(), MAX_MATCHES_PER_FILE + 1);
        let files: Vec<&str> = result.hits.iter().map(|h| h.file.as_str()).collect();
        assert_eq!(
            files.iter().filter(|&&f| f == "a.txt").count(),
            MAX_MATCHES_PER_FILE,
            "a.txt contributes exactly its cap: {files:?}"
        );
        assert!(
            files.contains(&"b.txt"),
            "a later file must still contribute — that's the whole point \
             of skipping to the next file instead of aborting: {files:?}"
        );
    }

    /// Regression: hitting the cap used to set `truncated = true`
    /// unconditionally, even when the file had exactly `MAX_MATCHES_PER_FILE`
    /// matches and nothing more — falsely telling a caller more results
    /// exist. The fix (check-before-record) only sets `truncated` when a
    /// real match is found beyond the cap.
    #[test]
    fn exactly_at_the_per_file_cap_does_not_falsely_report_truncated() {
        let tmp = tempfile::tempdir().unwrap();
        let root = tmp.path();
        let exactly_cap = "marker\n".repeat(MAX_MATCHES_PER_FILE);
        write(&root.join("a.txt"), &exactly_cap);
        std::process::Command::new("git")
            .args(["init", "-q"])
            .current_dir(root)
            .status()
            .unwrap();

        let result = search(root, "marker", MAX_RESULTS).unwrap();
        assert_eq!(result.hits.len(), MAX_MATCHES_PER_FILE);
        assert!(
            !result.truncated,
            "no match exists beyond the cap; truncated must stay false"
        );
    }

    // A second Greptile finding: `SCAN_SAFETY_CAP` had the exact same
    // check-after-record `truncated` bug `MAX_MATCHES_PER_FILE` did, fixed
    // above in `search` alongside it (both caps now check before pushing).
    // No regression test is added for it here: `search`'s very first line
    // clamps `limit` to `MAX_RESULTS` (200), which is always far below
    // `SCAN_SAFETY_CAP` (4,000), so any corpus large enough to reach the
    // safety cap always has real-match-count > limit already — the final
    // `if hits.len() > limit { truncated = true }` check independently and
    // correctly sets `truncated` in every such case regardless of what the
    // safety cap's own flag did. A tempdir-based test asserting otherwise
    // was written, run, and found to fail for exactly this reason (`hits`
    // comes back clamped to 200, never the constructed 4,000-match total)
    // — proving the bug is not externally observable via the public API
    // today, not that the fix is unverifiable. The fix is still correct
    // and worth keeping: it is the same discipline `MAX_MATCHES_PER_FILE`
    // now follows, and it would matter the moment either constant's
    // relationship to the other changed.
}
