//! **Experimental, rejected.** FTS5 trigram challenger for issue #6's
//! literal-search Pareto gate — measured against `oxide::literal` (the
//! shipped control) in `../README.md`, which also records the verdict:
//! **REJECT**, kept here only as the reproducible evidence and the
//! one-line-schema-addition point if the calculus in that verdict's
//! reconsideration triggers ever changes.
//!
//! This crate is not part of OXIDE's build in any way — see `Cargo.toml`'s
//! top comment for why it lives here instead of `src/`, and why (unlike
//! `docs/storage-backend-eval/spike`) it takes a path dependency on the
//! real `oxide` crate rather than reimplementing the control a second time.
//!
//! ## Design
//!
//! One SQLite database (path chosen by the caller — obviously never
//! `.oxide/`, this is not part of any production index) holds a single
//! FTS5 virtual table, one row per line:
//!
//! ```sql
//! CREATE VIRTUAL TABLE lines USING fts5(
//!   file UNINDEXED, line_no UNINDEXED, content,
//!   tokenize = 'trigram case_sensitive 1'
//! );
//! ```
//!
//! `case_sensitive 1` is deliberate and not the tokenizer's default: FTS5's
//! `trigram` tokenizer is case-*insensitive* unless told otherwise, and the
//! control (`oxide::literal`, matching `rg -F`'s default) is case-sensitive.
//! Building the challenger with the default would make its correctness
//! measurement compare two different query semantics, not two
//! implementations of the same one — see `../README.md`'s correctness
//! section for what changed when this was caught.
//!
//! A MATCH query only narrows candidate *lines*; it does not report the
//! matching byte offset, and (being trigram-based) a naive per-trigram
//! query can select lines containing all the right trigrams without the
//! full substring in order. Both gaps are closed the same way: each
//! candidate line's stored `content` is re-scanned with a byte-exact
//! `find` (below), which also produces the column and re-validates the
//! match rather than trusting FTS5's candidate set blindly. This is
//! standard practice for trigram-accelerated substring search (the index
//! narrows a corpus-sized scan to a match-sized one; it is not the final
//! authority on whether a candidate is real).
//!
//! `find`/`truncate_snippet`/`MAX_MATCHES_PER_FILE`/`SCAN_SAFETY_CAP` below
//! are duplicated from `oxide::literal`'s private items, not imported —
//! they're private to that module because the shipped crate has no reason
//! to expose them, and this experimental crate has no business asking the
//! production crate to widen its API just to serve a rejected experiment.
//! The duplication is exactly what this evaluation's own parity tests
//! (`assert_eq!(control, challenger)` throughout) exist to catch if it ever
//! drifted from the original.
//!
//! ## Known correctness gap: non-UTF-8 files
//!
//! `oxide::literal` matches raw bytes and works on any file, valid UTF-8 or
//! not. FTS5 columns are text; feeding lossily-decoded (replacement-
//! charactered) content into the index means a pattern that only appears
//! byte-exactly in a non-UTF-8 file's original encoding can be invisible to
//! this challenger even though the control finds it. `build` skips such a
//! file outright rather than silently indexing a corrupted stand-in for
//! it. This is a real, not-easily-closed gap (closing it means a custom
//! FTS5 tokenizer over raw bytes) and is measured, not hidden, in the
//! benchmark report.

use anyhow::{ensure, Context, Result};
use oxide::literal::{LiteralHit, LiteralSearchResult, MAX_RESULTS};
use rusqlite::Connection;
use std::path::Path;
use std::time::Instant;

/// FTS5's trigram tokenizer cannot express a phrase shorter than three
/// bytes (there is no complete trigram to query on) — a structural
/// limitation of trigram indexing, not an implementation gap here. Callers
/// needing sub-3-byte literal search have no trigram-index answer; the
/// control has no such floor.
pub const MIN_PATTERN_LEN: usize = 3;

/// Duplicated from `oxide::literal`'s private constant of the same name and
/// value — see the module doc's note on duplication.
const MAX_MATCHES_PER_FILE: usize = 50;
/// Duplicated from `oxide::literal`'s private constant of the same name and
/// value — see the module doc's note on duplication.
const SCAN_SAFETY_CAP: usize = 4_000;
/// Duplicated from `oxide::literal`'s private constant of the same name and
/// value — see the module doc's note on duplication.
const MAX_SNIPPET_BYTES: usize = 300;

/// Duplicated from `oxide::literal`'s private function of the same name and
/// behavior — see the module doc's note on duplication.
fn find(haystack: &[u8], needle: &[u8]) -> Option<usize> {
    if needle.is_empty() || needle.len() > haystack.len() {
        return None;
    }
    haystack
        .windows(needle.len())
        .position(|window| window == needle)
}

/// Duplicated from `oxide::literal`'s private function of the same name and
/// behavior — see the module doc's note on duplication.
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

#[derive(Debug, Clone, Copy, Default)]
pub struct BuildStats {
    pub files_indexed: usize,
    pub files_skipped_non_utf8: usize,
    pub lines_indexed: usize,
    pub build_ms: u128,
    pub db_bytes: u64,
}

fn open(db_path: &Path) -> Result<Connection> {
    let conn = Connection::open(db_path).with_context(|| format!("opening {db_path:?}"))?;
    conn.execute_batch(
        "PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;\n\
         CREATE VIRTUAL TABLE IF NOT EXISTS lines USING fts5(\n\
           file UNINDEXED, line_no UNINDEXED, content,\n\
           tokenize = 'trigram case_sensitive 1'\n\
         );",
    )?;
    Ok(conn)
}

/// Full (re)build over every file `oxide::scanner::scan_repo_text` would
/// keep — the same file set `oxide::literal::search` scans, which is what
/// makes a later parity comparison meaningful rather than an artifact of
/// the two implementations disagreeing about *what* to search, not *how*.
pub fn build(root: &Path, db_path: &Path) -> Result<BuildStats> {
    let start = Instant::now();
    let _ = std::fs::remove_file(db_path);
    let _ = std::fs::remove_file(db_path.with_extension("db-wal"));
    let _ = std::fs::remove_file(db_path.with_extension("db-shm"));
    let mut conn = open(db_path)?;

    let files = oxide::scanner::scan_repo_text(root)?;
    let mut stats = BuildStats::default();
    let tx = conn.transaction()?;
    {
        let mut insert =
            tx.prepare("INSERT INTO lines(file, line_no, content) VALUES (?1, ?2, ?3)")?;
        for rel in &files {
            let full = root.join(rel);
            let Ok(bytes) = std::fs::read(&full) else {
                continue;
            };
            // A non-lossy decode: this challenger's known gap (module doc)
            // is to skip what it cannot represent, not to silently index a
            // corrupted stand-in for it.
            let Ok(text) = std::str::from_utf8(&bytes) else {
                stats.files_skipped_non_utf8 += 1;
                continue;
            };
            let display = rel.to_string_lossy().replace('\\', "/");
            for (idx, line) in text.split('\n').enumerate() {
                insert.execute(rusqlite::params![display, (idx + 1) as i64, line])?;
                stats.lines_indexed += 1;
            }
            stats.files_indexed += 1;
        }
    }
    tx.commit()?;
    conn.execute_batch("PRAGMA wal_checkpoint(TRUNCATE);")?;
    drop(conn);

    stats.build_ms = start.elapsed().as_millis();
    stats.db_bytes = std::fs::metadata(db_path).map(|m| m.len()).unwrap_or(0);
    Ok(stats)
}

/// Incremental update for one file: delete its existing rows, then
/// re-insert its current content (or insert nothing if the file is gone —
/// callers pass a path that may no longer exist, matching `oxide::literal`
/// never assuming a file list stays valid). Used both by `build`'s
/// real-world counterpart and by the benchmark's "touch one file" measure.
///
/// One transaction for the whole delete+reinsert, matching the "one
/// transaction per reparsed file" discipline `structural_relations`
/// already follows for the production index (OXIDE's AGENTS.md) —
/// measured need, not cargo-culted: an early version left this in
/// autocommit and a several-hundred-line file cost >100ms purely from one
/// fsync-adjacent commit per inserted line.
pub fn update_file(conn: &mut Connection, root: &Path, rel: &Path) -> Result<()> {
    let display = rel.to_string_lossy().replace('\\', "/");
    let full = root.join(rel);
    let tx = conn.transaction()?;
    tx.execute("DELETE FROM lines WHERE file = ?1", [&display])?;
    if let Ok(bytes) = std::fs::read(&full) {
        if let Ok(text) = std::str::from_utf8(&bytes) {
            let mut insert =
                tx.prepare("INSERT INTO lines(file, line_no, content) VALUES (?1, ?2, ?3)")?;
            for (idx, line) in text.split('\n').enumerate() {
                insert.execute(rusqlite::params![display, (idx + 1) as i64, line])?;
            }
        }
    }
    tx.commit()?;
    Ok(())
}

/// Mirrors `oxide::literal::search`'s contract exactly: same query
/// semantics (byte-exact, case-sensitive), same result type, same bounds
/// (`MAX_MATCHES_PER_FILE`/`SCAN_SAFETY_CAP`/[`MAX_RESULTS`], duplicated
/// from `oxide::literal` — see the module doc), same `(file, line,
/// column)` ordering. The one behavioral difference callers must know
/// about is [`MIN_PATTERN_LEN`].
pub fn search(db_path: &Path, pattern: &str, limit: usize) -> Result<LiteralSearchResult> {
    ensure!(
        !pattern.is_empty(),
        "trigram search pattern must not be empty"
    );
    ensure!(
        pattern.len() >= MIN_PATTERN_LEN,
        "trigram index cannot query patterns shorter than {MIN_PATTERN_LEN} bytes (got {} bytes: {pattern:?})",
        pattern.len()
    );
    let limit = limit.min(MAX_RESULTS);
    let conn = Connection::open(db_path).with_context(|| format!("opening {db_path:?}"))?;
    let quoted = format!("\"{}\"", pattern.replace('"', "\"\""));
    let mut stmt = conn.prepare(
        "SELECT file, line_no, content FROM lines WHERE lines MATCH ?1 ORDER BY file, line_no",
    )?;
    let rows = stmt.query_map([&quoted], |r| {
        Ok((
            r.get::<_, String>(0)?,
            r.get::<_, i64>(1)?,
            r.get::<_, String>(2)?,
        ))
    })?;

    let needle = pattern.as_bytes();
    let mut hits = Vec::new();
    let mut truncated = false;
    let mut current_file: Option<String> = None;
    let mut file_matches = 0usize;
    for row in rows {
        let (file, line_no, content) = row?;
        if current_file.as_deref() != Some(file.as_str()) {
            current_file = Some(file.clone());
            file_matches = 0;
        }
        if file_matches >= MAX_MATCHES_PER_FILE {
            continue;
        }
        let line_bytes = content.as_bytes();
        let mut cursor = 0usize;
        while let Some(pos) = find(&line_bytes[cursor..], needle) {
            let match_start = cursor + pos;
            hits.push(LiteralHit {
                file: file.clone(),
                line: line_no as u32,
                column: (match_start + 1) as u32,
                snippet: truncate_snippet(line_bytes),
            });
            file_matches += 1;
            if hits.len() >= SCAN_SAFETY_CAP {
                truncated = true;
                hits.truncate(limit.min(hits.len()));
                return Ok(LiteralSearchResult { hits, truncated });
            }
            if file_matches >= MAX_MATCHES_PER_FILE {
                truncated = true;
                break;
            }
            cursor = match_start + needle.len();
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
    fn finds_matches_with_line_and_column_matching_the_control() {
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
        let db = tmp.path().join("trigram.db");

        let stats = build(root, &db).unwrap();
        assert_eq!(stats.files_indexed, 1);
        assert_eq!(stats.files_skipped_non_utf8, 0);

        let control = oxide::literal::search(root, "needle", 100).unwrap();
        let challenger = search(&db, "needle", 100).unwrap();
        assert_eq!(control, challenger);
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
        let db = tmp.path().join("trigram.db");
        build(root, &db).unwrap();

        let result = search(&db, "flaky_widget", 100).unwrap();
        let files: Vec<&str> = result.hits.iter().map(|h| h.file.as_str()).collect();
        assert!(files.contains(&"README.md"), "{files:?}");
        assert!(files.contains(&"config.toml"), "{files:?}");
    }

    #[test]
    fn respects_ignore_policy_and_denylist_same_as_control() {
        let tmp = tempfile::tempdir().unwrap();
        let root = tmp.path();
        write(&root.join("keep.txt"), "marker\n");
        write(&root.join("ignored/skip.txt"), "marker\n");
        write(&root.join("node_modules/pkg/skip.txt"), "marker\n");
        write(&root.join(".gitignore"), "/ignored/\n");
        std::process::Command::new("git")
            .args(["init", "-q"])
            .current_dir(root)
            .status()
            .unwrap();
        let db = tmp.path().join("trigram.db");
        build(root, &db).unwrap();

        let result = search(&db, "marker", 100).unwrap();
        let files: Vec<&str> = result.hits.iter().map(|h| h.file.as_str()).collect();
        assert_eq!(files, vec!["keep.txt"]);
    }

    #[test]
    fn case_sensitive_by_construction_unlike_the_tokenizer_default() {
        let tmp = tempfile::tempdir().unwrap();
        let root = tmp.path();
        write(&root.join("a.txt"), "Needle\nneedle\n");
        std::process::Command::new("git")
            .args(["init", "-q"])
            .current_dir(root)
            .status()
            .unwrap();
        let db = tmp.path().join("trigram.db");
        build(root, &db).unwrap();

        let result = search(&db, "needle", 100).unwrap();
        assert_eq!(result.hits.len(), 1, "{:?}", result.hits);
        assert_eq!(result.hits[0].line, 2);

        let control = oxide::literal::search(root, "needle", 100).unwrap();
        assert_eq!(control, result);
    }

    #[test]
    fn skips_non_utf8_files_and_reports_the_gap() {
        let tmp = tempfile::tempdir().unwrap();
        let root = tmp.path();
        let mut bytes = b"needle before\n".to_vec();
        bytes.push(0xFF);
        bytes.push(0xFE);
        bytes.extend_from_slice(b" needle after\n");
        std::fs::create_dir_all(root.join("src")).unwrap();
        std::fs::write(root.join("src/latin.txt"), &bytes).unwrap();
        std::process::Command::new("git")
            .args(["init", "-q"])
            .current_dir(root)
            .status()
            .unwrap();
        let db = tmp.path().join("trigram.db");

        let stats = build(root, &db).unwrap();
        assert_eq!(stats.files_indexed, 0);
        assert_eq!(stats.files_skipped_non_utf8, 1);

        let result = search(&db, "needle", 100).unwrap();
        assert!(
            result.hits.is_empty(),
            "known gap: non-UTF-8 content is not indexed, {:?}",
            result.hits
        );
        // The control has no such gap.
        let control = oxide::literal::search(root, "needle", 100).unwrap();
        assert_eq!(control.hits.len(), 2);
    }

    #[test]
    fn rejects_patterns_shorter_than_the_trigram_floor() {
        let tmp = tempfile::tempdir().unwrap();
        let root = tmp.path();
        write(&root.join("a.txt"), "ab\n");
        std::process::Command::new("git")
            .args(["init", "-q"])
            .current_dir(root)
            .status()
            .unwrap();
        let db = tmp.path().join("trigram.db");
        build(root, &db).unwrap();

        assert!(search(&db, "a", 10).is_err());
        assert!(search(&db, "ab", 10).is_err());
    }

    #[test]
    fn incremental_update_reflects_an_edit_without_a_full_rebuild() {
        let tmp = tempfile::tempdir().unwrap();
        let root = tmp.path();
        write(&root.join("src/a.py"), "x = 1\n");
        std::process::Command::new("git")
            .args(["init", "-q"])
            .current_dir(root)
            .status()
            .unwrap();
        let db = tmp.path().join("trigram.db");
        build(root, &db).unwrap();
        assert!(search(&db, "needle_marker", 10).unwrap().hits.is_empty());

        write(&root.join("src/a.py"), "needle_marker = 1\n");
        let mut conn = Connection::open(&db).unwrap();
        update_file(&mut conn, root, Path::new("src/a.py")).unwrap();
        drop(conn);

        let result = search(&db, "needle_marker", 10).unwrap();
        assert_eq!(result.hits.len(), 1);
        assert_eq!(result.hits[0].file, "src/a.py");
    }

    #[test]
    fn truncates_deterministically_when_over_limit_same_as_control() {
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
        let db = tmp.path().join("trigram.db");
        build(root, &db).unwrap();

        let challenger = search(&db, "marker", 2).unwrap();
        let control = oxide::literal::search(root, "marker", 2).unwrap();
        assert_eq!(control, challenger);
    }
}
