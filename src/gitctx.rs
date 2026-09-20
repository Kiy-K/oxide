//! Git-aware context assembly: current diff, recent commits, and bounded
//! co-change evidence — the shared domain layer between `context.rs`'s
//! `--git` opt-in and `review.rs`'s always-on diff view.
//!
//! Storage-agnostic by design: every function here takes a `symbols: &[Symbol]`
//! slice the caller already loaded (`context.rs` and `review.rs` both reuse
//! the sanctioned lazy full-corpus snapshot they load for `RelationGraph`
//! anyway) rather than touching `IndexBackend` itself — see AGENTS.md's
//! request-path invariant.

use crate::config::{
    GIT_COCHANGE_COMMIT_WINDOW, GIT_COCHANGE_MAX_FANOUT, GIT_COCHANGE_MAX_FILES_PER_COMMIT,
    GIT_COCHANGE_MAX_TARGET_FILES, GIT_RECENT_COMMITS_LIMIT,
};
use crate::gitutil::{self, CommitMeta, FileDelta};
use crate::symbols::{Symbol, SymbolKind};
use serde::Serialize;
use std::collections::{HashMap, HashSet};
use std::path::Path;

/// One symbol overlapping the current diff's added-line ranges.
#[derive(Debug, Clone, Serialize)]
pub struct ChangedSymbol {
    #[serde(flatten)]
    pub symbol: Symbol,
    pub added_lines: u32,
    pub reason: String,
}

/// One historically co-changed file pair. `strength` is the CodeScene-style
/// coupling ratio (`shared_commits / commits_touching_file`, within the
/// bounded window) rather than the raw `count` — a file that itself churns
/// constantly needs proportionally more *shared* commits to register,
/// which is what keeps a high-churn file from becoming a universal
/// neighbor for every other target file's co-change list.
#[derive(Debug, Clone, Serialize)]
pub struct CoChangeEntry {
    pub file: String,
    pub co_changed_with: String,
    pub count: usize,
    pub strength: f32,
}

/// Non-symbol git provenance: what JSON/MCP consumers see. Symbol-level
/// evidence (changed symbols, their callers, co-changed symbols) flows
/// through the normal candidate/item pipeline instead of being duplicated
/// here — see `context.rs`'s git block and `review.rs`'s existing
/// `changed_symbols` field.
#[derive(Debug, Clone, Serialize, Default)]
pub struct GitEvidence {
    pub range: String,
    pub changed_files: Vec<String>,
    pub recent_commits: Vec<CommitMeta>,
    pub co_change: Vec<CoChangeEntry>,
}

/// [`GitEvidence`] plus the symbol-level evidence a caller needs to build
/// candidates/items from, computed together so the diff is only parsed
/// once.
#[derive(Debug, Clone, Default)]
pub struct GitContext {
    pub evidence: GitEvidence,
    pub changed_symbols: Vec<ChangedSymbol>,
}

/// Symbols overlapping `deltas`' added-line ranges, in the current
/// (post-diff) state of `symbols`. Shared by `review.rs` and `context.rs`'s
/// `--git` stage so the diff→symbol mapping has exactly one implementation.
pub fn changed_symbols_for(deltas: &[FileDelta], symbols: &[Symbol]) -> Vec<ChangedSymbol> {
    let mut out = Vec::new();
    for d in deltas {
        // Overlap for every candidate first: a class's span necessarily
        // contains its own methods' spans, so an edit inside one method
        // overlaps both -- attributing the change to every containing
        // symbol independently double-counts one edit as two unrelated
        // "changed" seeds (EXPERIMENT: docs/evals/phase-4.2-typesafe).
        let hits: Vec<(&Symbol, u32)> = symbols
            .iter()
            .filter(|s| s.file == d.file && s.kind != SymbolKind::Module)
            .filter_map(|s| {
                let overlap = d
                    .added
                    .iter()
                    .map(|(a, b)| overlap_len(*a, *b, s.start_line, s.end_line))
                    .max()?;
                (overlap > 0).then_some((s, overlap))
            })
            .collect();
        for (s, overlap) in &hits {
            // Drop a container when a more specific symbol nested strictly
            // inside its span also overlaps this same delta -- attribute the
            // change to the innermost overlapping symbol only.
            let has_nested_hit = hits.iter().any(|(t, _)| {
                t.qualified_name != s.qualified_name
                    && t.start_line >= s.start_line
                    && t.end_line <= s.end_line
                    && (t.start_line, t.end_line) != (s.start_line, s.end_line)
            });
            if has_nested_hit {
                continue;
            }
            out.push(ChangedSymbol {
                symbol: (*s).clone(),
                added_lines: *overlap,
                reason: format!("changed in diff (+{overlap} lines)"),
            });
        }
    }
    out
}

/// Overlap length of two inclusive line ranges.
fn overlap_len(a1: u32, a2: u32, b1: u32, b2: u32) -> u32 {
    let lo = a1.max(b1);
    let hi = a2.min(b2);
    if hi >= lo {
        hi - lo + 1
    } else {
        0
    }
}

/// Assemble bounded git evidence for `range` (`gitutil::diff_text`'s
/// convention: empty = worktree vs HEAD, `A..B` explicit, a single rev `R`
/// = everything since `R`). Returns an error if `range` is unresolvable
/// (e.g., `HEAD~1` in a single-commit repo, or an invalid range syntax).
/// Degrades to an empty [`GitContext`] — never errors — when `repo_root`
/// isn't a git repo; this is provenance, not something a query should fail over.
pub fn build_git_context(
    repo_root: &Path,
    symbols: &[Symbol],
    range: &str,
) -> anyhow::Result<GitContext> {
    if !gitutil::is_repo(repo_root) {
        return Ok(GitContext::default());
    }
    let text = gitutil::diff_text(repo_root, range).map_err(|e| {
        if range == "HEAD~1" {
            anyhow::anyhow!(
                "no prior commit to diff against (repository has only one commit); pass an explicit --diff range instead"
            )
        } else {
            anyhow::anyhow!("invalid diff range '{range}': {e}")
        }
    })?;
    let deltas = gitutil::parse_unified(&text);

    let mut changed_files: Vec<String> = deltas.iter().map(|d| d.file.clone()).collect();
    changed_files.extend(gitutil::deleted_files(&text));
    changed_files.sort();
    changed_files.dedup();

    let recent_commits = if range.is_empty() {
        gitutil::recent_commits(repo_root, GIT_RECENT_COMMITS_LIMIT)
    } else {
        gitutil::recent_commits_in_range(repo_root, range, GIT_RECENT_COMMITS_LIMIT)
    }
    .unwrap_or_default();

    let co_change = co_change_for(repo_root, &deltas);
    let changed_symbols = changed_symbols_for(&deltas, symbols);

    Ok(GitContext {
        evidence: GitEvidence {
            range: if range.is_empty() {
                "HEAD".into()
            } else {
                range.into()
            },
            changed_files,
            recent_commits,
            co_change,
        },
        changed_symbols,
    })
}

/// Bounded co-change tally for `deltas`' changed files: the first
/// [`GIT_COCHANGE_MAX_TARGET_FILES`] (sorted by path) each get one bounded
/// `git log` scan via [`gitutil::co_change_raw`]; well-known noisy files
/// ([`is_noisy_cochange_file`]) are dropped as candidates entirely, pairs
/// below [`GIT_COCHANGE_MIN_SHARED`] shared commits are dropped as
/// coincidence, and the rest are ranked by coupling `strength` (not raw
/// `count`) and truncated to [`GIT_COCHANGE_MAX_FANOUT`].
fn co_change_for(repo_root: &Path, deltas: &[FileDelta]) -> Vec<CoChangeEntry> {
    let mut targets: Vec<&str> = deltas.iter().map(|d| d.file.as_str()).collect();
    targets.sort_unstable();
    targets.truncate(GIT_COCHANGE_MAX_TARGET_FILES);

    let mut out = Vec::new();
    for file in targets {
        let pairs = gitutil::co_change_raw(
            repo_root,
            file,
            GIT_COCHANGE_COMMIT_WINDOW,
            GIT_COCHANGE_MAX_FILES_PER_COMMIT,
        )
        .unwrap_or_default();

        // Denominator for the coupling ratio: how many distinct (non-merge,
        // not-mass-commit) commits touching `file` were actually tallied —
        // CodeScene's temporal-coupling normalization is `shared /
        // min(commits_A, commits_B)`; this uses only the `file` side of
        // that ratio, since `other`'s own total isn't cheaply knowable
        // without a second bounded `git log` call per candidate. Still what
        // stops a target file that itself churns heavily within the window
        // from inflating every pair's apparent strength.
        let commits_scanned: HashSet<&str> = pairs.iter().map(|(_, sha)| sha.as_str()).collect();
        let denom = commits_scanned.len().max(1) as f32;

        let mut counts: HashMap<String, usize> = HashMap::new();
        for (other, _sha) in &pairs {
            if other != file && !is_noisy_cochange_file(other) {
                *counts.entry(other.clone()).or_insert(0) += 1;
            }
        }
        let mut ranked: Vec<(String, usize)> = counts
            .into_iter()
            .filter(|(_, count)| *count >= GIT_COCHANGE_MIN_SHARED)
            .collect();
        ranked.sort_by(|a, b| {
            let sa = a.1 as f32 / denom;
            let sb = b.1 as f32 / denom;
            sb.partial_cmp(&sa)
                .unwrap_or(std::cmp::Ordering::Equal)
                .then_with(|| a.0.cmp(&b.0))
        });
        ranked.truncate(GIT_COCHANGE_MAX_FANOUT);

        for (other, count) in ranked {
            out.push(CoChangeEntry {
                file: file.to_string(),
                co_changed_with: other,
                count,
                strength: count as f32 / denom,
            });
        }
    }
    out
}

/// Fewer than this many shared commits (within the bounded window) is
/// treated as coincidence rather than signal. Deliberately small —
/// [`GIT_COCHANGE_COMMIT_WINDOW`] itself is a small bounded window, unlike
/// enterprise-scale temporal-coupling tools that scan full project history.
const GIT_COCHANGE_MIN_SHARED: usize = 2;

/// Well-known files that change alongside nearly everything and would
/// otherwise become a "universal neighbor" in every co-change list:
/// lockfiles (regenerated by any dependency-touching commit) and
/// changelogs (touched by nearly every release commit). Not exhaustive by
/// design — a fixed, cheap stoplist, not a learned or configurable one.
fn is_noisy_cochange_file(path: &str) -> bool {
    const NOISY_BASENAMES: &[&str] = &[
        "package-lock.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "Cargo.lock",
        "poetry.lock",
        "Pipfile.lock",
        "Gemfile.lock",
        "composer.lock",
        "go.sum",
    ];
    const NOISY_PREFIXES: &[&str] = &["CHANGELOG", "CHANGES"];

    let base = path.rsplit('/').next().unwrap_or(path);
    if NOISY_BASENAMES.contains(&base) {
        return true;
    }
    let upper = base.to_ascii_uppercase();
    NOISY_PREFIXES.iter().any(|p| upper.starts_with(p))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::symbols::{Language, SymbolKind};

    fn sym(file: &str, name: &str, kind: SymbolKind, start: u32, end: u32) -> Symbol {
        Symbol {
            file: file.to_string(),
            name: name.to_string(),
            qualified_name: name.to_string(),
            kind,
            language: Language::Python,
            start_line: start,
            end_line: end,
            content_hash: 0,
            signature: String::new(),
            imports: Vec::new(),
            exported: false,
            parent: None,
            references: Vec::new(),
            calls: Vec::new(),
            bases: Vec::new(),
        }
    }

    fn delta(file: &str, added: Vec<(u32, u32)>) -> FileDelta {
        FileDelta {
            file: file.to_string(),
            added,
        }
    }

    fn git(root: &std::path::Path, args: &[&str]) {
        let st = std::process::Command::new("git")
            .args(args)
            .current_dir(root)
            .env("GIT_AUTHOR_NAME", "t")
            .env("GIT_AUTHOR_EMAIL", "t@t")
            .env("GIT_COMMITTER_NAME", "t")
            .env("GIT_COMMITTER_EMAIL", "t@t")
            .status()
            .unwrap();
        assert!(st.success(), "git {args:?}");
    }

    fn commit(root: &std::path::Path, msg: &str) {
        git(root, &["commit", "-qm", msg]);
    }

    #[test]
    fn attributes_modify_and_add_to_the_right_symbol() {
        let symbols = vec![
            sym("a.py", "Foo", SymbolKind::Class, 1, 10),
            sym("a.py", "Foo.bar", SymbolKind::Method, 2, 4),
            sym("a.py", "Foo.baz", SymbolKind::Method, 6, 8),
        ];
        let deltas = vec![delta("a.py", vec![(3, 3)])];
        let changed = changed_symbols_for(&deltas, &symbols);
        let names: Vec<&str> = changed.iter().map(|c| c.symbol.name.as_str()).collect();
        assert!(names.contains(&"Foo.bar"), "{names:?}");
        assert!(!names.contains(&"Foo.baz"), "{names:?}");
    }

    #[test]
    fn nested_containers_are_not_double_counted_as_independent_seeds() {
        // A class's span necessarily contains its own method's span. Before
        // this fix, both the class and the method were reported as
        // independently "changed" for one edit inside the method, which let
        // review.rs's scoring loop count every other class member twice
        // (once as a `child` of the class seed, once as a `sibling` of the
        // method seed) for what was really one edit.
        let symbols = vec![
            sym("a.py", "Foo", SymbolKind::Class, 1, 10),
            sym("a.py", "Foo.bar", SymbolKind::Method, 2, 4),
        ];
        let deltas = vec![delta("a.py", vec![(3, 3)])];
        let changed = changed_symbols_for(&deltas, &symbols);
        let names: Vec<&str> = changed.iter().map(|c| c.symbol.name.as_str()).collect();
        assert_eq!(
            names,
            vec!["Foo.bar"],
            "the enclosing class must not be seeded independently when a \
             nested symbol already covers the same edit"
        );
    }

    #[test]
    fn a_class_level_edit_outside_any_method_still_seeds_the_class() {
        // An edit that falls outside every method's span (e.g. a new field,
        // or the class signature line) must still attribute to the class --
        // innermost-only attribution must not suppress a genuinely
        // class-level change just because the class also has methods.
        let symbols = vec![
            sym("a.py", "Foo", SymbolKind::Class, 1, 10),
            sym("a.py", "Foo.bar", SymbolKind::Method, 5, 8),
        ];
        let deltas = vec![delta("a.py", vec![(2, 2)])];
        let changed = changed_symbols_for(&deltas, &symbols);
        let names: Vec<&str> = changed.iter().map(|c| c.symbol.name.as_str()).collect();
        assert_eq!(names, vec!["Foo"]);
    }

    #[test]
    fn two_independently_edited_methods_both_remain_seeds() {
        // Two separate hunks touching two different methods of the same
        // class must both surface -- innermost attribution is decided per
        // overlapping hit, not by dropping every method once any one
        // container is involved.
        let symbols = vec![
            sym("a.py", "Foo", SymbolKind::Class, 1, 20),
            sym("a.py", "Foo.bar", SymbolKind::Method, 2, 5),
            sym("a.py", "Foo.baz", SymbolKind::Method, 10, 13),
        ];
        let deltas = vec![delta("a.py", vec![(3, 3), (11, 11)])];
        let changed = changed_symbols_for(&deltas, &symbols);
        let mut names: Vec<&str> = changed.iter().map(|c| c.symbol.name.as_str()).collect();
        names.sort();
        assert_eq!(names, vec!["Foo.bar", "Foo.baz"]);
    }

    #[test]
    fn partial_deletion_attributes_to_enclosing_symbol() {
        // A pure-deletion hunk (`gitutil::parse_unified`'s ±1 window) at the
        // insertion point inside `Foo.bar`'s body.
        let symbols = vec![sym("a.py", "Foo.bar", SymbolKind::Method, 2, 6)];
        let deltas = vec![delta("a.py", vec![(3, 5)])]; // e.g. new_start=4 -> (3,5)
        let changed = changed_symbols_for(&deltas, &symbols);
        assert_eq!(changed.len(), 1);
        assert_eq!(changed[0].symbol.name, "Foo.bar");
    }

    #[test]
    fn whole_symbol_deletion_yields_no_changed_symbol() {
        // The deleted symbol no longer exists in the current-state slice at
        // all — degrade to nothing mapped, not a crash.
        let symbols = vec![sym("a.py", "Foo.other", SymbolKind::Method, 20, 30)];
        let deltas = vec![delta("a.py", vec![(3, 5)])];
        let changed = changed_symbols_for(&deltas, &symbols);
        assert!(changed.is_empty());
    }

    #[test]
    fn non_git_repo_degrades_to_empty_context() {
        let tmp = tempfile::tempdir().unwrap();
        let ctx = build_git_context(tmp.path(), &[], "").unwrap();
        assert!(ctx.changed_symbols.is_empty());
        assert!(ctx.evidence.changed_files.is_empty());
        assert!(ctx.evidence.recent_commits.is_empty());
        assert!(ctx.evidence.co_change.is_empty());
    }

    #[test]
    fn noisy_cochange_files_are_recognized() {
        assert!(is_noisy_cochange_file("Cargo.lock"));
        assert!(is_noisy_cochange_file("frontend/package-lock.json"));
        assert!(is_noisy_cochange_file("CHANGELOG.md"));
        assert!(is_noisy_cochange_file("docs/CHANGES.rst"));
        assert!(!is_noisy_cochange_file("src/retrieval.rs"));
        assert!(!is_noisy_cochange_file("Cargo.toml"));
    }

    #[test]
    fn invalid_range_returns_an_error_not_an_empty_context() {
        let tmp = tempfile::tempdir().unwrap();
        let root = tmp.path();
        git(root, &["init", "-q"]);
        std::fs::write(root.join("a.py"), "a\n").unwrap();
        git(root, &["add", "."]);
        commit(root, "init");
        let err = build_git_context(root, &[], "nonexistent-garbage-range-zzz").unwrap_err();
        assert!(err.to_string().contains("invalid diff range"), "{err}");
    }

    #[test]
    fn fresh_single_commit_repo_head_tilde_1_gets_a_truthful_message() {
        let tmp = tempfile::tempdir().unwrap();
        let root = tmp.path();
        git(root, &["init", "-q"]);
        std::fs::write(root.join("a.py"), "a\n").unwrap();
        git(root, &["add", "."]);
        commit(root, "only commit");
        let err = build_git_context(root, &[], "HEAD~1").unwrap_err();
        assert!(
            err.to_string().contains("no prior commit to diff against"),
            "{err}"
        );
    }

    #[test]
    fn valid_range_still_returns_ok() {
        let tmp = tempfile::tempdir().unwrap();
        let root = tmp.path();
        git(root, &["init", "-q"]);
        std::fs::write(root.join("a.py"), "a\n").unwrap();
        git(root, &["add", "."]);
        commit(root, "first");
        std::fs::write(root.join("a.py"), "a\nb\n").unwrap();
        let ctx = build_git_context(root, &[], "").unwrap();
        assert!(!ctx.evidence.changed_files.is_empty());
    }
}
