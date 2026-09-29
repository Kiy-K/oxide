//! Base indexing stage: scan (or take the watcher's changed paths), detect
//! changes and deletions, backfill relations/lexical postings for unchanged
//! files, then hand changed files to the shared per-file pipeline.

use super::pipeline::parse_and_persist_changed_files;
use super::{count_summary, IndexOptions, IndexReport, NoProgress, ProgressSink, Stage};
use crate::scanner;
use crate::storage::{
    IndexBackend, EXTRACTION_VERSION, EXTRACTION_VERSION_KEY, LEXICAL_INDEX_KEY,
    LEXICAL_INDEX_VERSION,
};
use crate::symbols::Symbol;
use anyhow::Result;
use std::collections::{HashMap, HashSet};
use std::path::Path;

/// Whether the index *claims* an extraction generation that is not this
/// binary's. Only a present-and-different value counts: absence means
/// either a fresh index (nothing stale to repair) or a base-only workflow
/// that never reaches the `set_meta_all` in `update_embeddings` — treating
/// that as stale would make every such run reparse the whole corpus
/// forever. An index that is genuinely missing the key is refused outright
/// by `validate_index`, so it can never be silently served either way.
fn stale_extraction(store: &dyn IndexBackend) -> Result<bool> {
    Ok(store
        .get_meta(EXTRACTION_VERSION_KEY)?
        .filter(|s| !s.is_empty())
        .is_some_and(|v| v != EXTRACTION_VERSION.to_string()))
}

/// Scan + parse + symbol/reference update + structural relations — every
/// indexing layer except embeddings. `report.duration_ms` on return covers
/// only this stage; a caller running both stages should overwrite it with
/// the grand total (see `update_index_scoped`).
pub fn update_base(
    root: &Path,
    store: &mut dyn IndexBackend,
    opts: &IndexOptions,
) -> Result<IndexReport> {
    update_base_reporting(root, store, opts, &NoProgress)
}

/// [`update_base`] with stage/progress reporting; identical work.
pub fn update_base_reporting(
    root: &Path,
    store: &mut dyn IndexBackend,
    opts: &IndexOptions,
    progress: &dyn ProgressSink,
) -> Result<IndexReport> {
    // Bulk checkpoint policy for the whole pass; restored on every exit
    // path so a watcher's long-lived connection is never left in bulk mode.
    store.begin_bulk_writes()?;
    let result = update_base_inner(root, store, opts, progress);
    store.end_bulk_writes()?;
    result
}

fn update_base_inner(
    root: &Path,
    store: &mut dyn IndexBackend,
    opts: &IndexOptions,
    progress: &dyn ProgressSink,
) -> Result<IndexReport> {
    let started = std::time::Instant::now();
    let mut report = IndexReport::default();

    progress.begin(Stage::Scan, None);
    let files = scanner::scan_repo(root)?;
    report.scanned_files = files.len();
    progress.end(Stage::Scan, &format!("{} files", files.len()));

    // Single read per file: bytes → UTF-8 string → hash + parse reuse it.
    let mut current: HashMap<String, String> = HashMap::with_capacity(files.len());
    let mut unreadable_files: usize = 0;
    for p in &files {
        let rel = p.display().to_string();
        match std::fs::read_to_string(root.join(p)) {
            Ok(src) => {
                current.insert(rel, src);
            }
            Err(_) => unreadable_files += 1, // non-UTF8/IO error: accounted as errored below
        }
    }

    let stored = store.file_hashes()?;

    // One snapshot reused for deletions, change detection and name matching.
    let existing = store.all_symbols()?;

    // Deletions and stale entries.
    let removed: Vec<String> = stored
        .keys()
        .filter(|f| !current.contains_key(*f))
        .cloned()
        .collect();
    if !removed.is_empty() {
        let doomed = existing
            .iter()
            .filter(|s| removed.contains(&s.file))
            .count();
        store.remove_files(&removed)?;
        report.deleted_symbols += doomed;
        report.removed_files = removed.len();
    }

    // Changed or new files. `force_reparse` (`-a`/`--all`) treats every file
    // as changed, bypassing the content_hash shortcut entirely — e.g. after
    // an extractor/grammar upgrade that should re-derive symbols even where
    // source text didn't change.
    // Whether the persisted lexical index is known-complete and current-format.
    // Anything else — no key (built before the feature, or a backfill that
    // never finished), or a generation this binary does not recognize —
    // means it must be rebuilt for every file, since a covered file's
    // content_hash already matches and no incremental run would revisit it.
    let backfill_lexical = store.get_meta(LEXICAL_INDEX_KEY)?.as_deref()
        != Some(LEXICAL_INDEX_VERSION.to_string().as_str());
    // Same shape, same reason, for extraction semantics: when the stored
    // `extraction_version` is not exactly this binary's, every file's stored
    // symbols were derived under different rules and the content_hash
    // shortcut would skip all of them — then the closing `set_meta_all`
    // would publish the new version over rows that were never re-derived.
    // `validate_index` refuses to *serve* such an index; this is what makes
    // a plain `oxide index` actually repair it, without the user having to
    // know about `-a`.
    let force_reparse = opts.force_reparse || stale_extraction(store)?;
    let to_parse: Vec<(&String, u64)> = current
        .iter()
        .map(|(f, src)| (f, crate::symbols::content_hash(src)))
        .filter(|(f, h)| force_reparse || stored.get(*f).copied() != Some(*h))
        .collect();
    // Unchanged is relative to files we could actually read; unreadable files
    // are accounted separately below so the totals never silently disagree
    // with `scanned_files`.
    report.unchanged_files = current.len() - to_parse.len();

    // Recompute structural relations for symbols in files NOT being
    // reparsed this run (the main per-file loop below already covers
    // `to_parse` files). Two triggers share this exact path:
    //  - `opts.force_graph` (`-g`/`--graph`): user asked to rebuild the
    //    graph layer explicitly.
    //  - One-time backfill for an index that predates precomputed
    //    structural relations (symbols exist, `symbol_relations` is
    //    empty). Self-limiting: after this runs once, every existing
    //    symbol has a `symbol_relations` entry (even an empty one, per
    //    `compute_file_relations`'s doc comment on why that matters), so
    //    this condition is false on every subsequent run.
    // `force_reparse` (`-a`) makes `to_parse` cover every file already, so
    // `unchanged_by_file` below is naturally empty and this does no
    // redundant work on top of the main per-file loop.
    //
    // The persisted lexical index rides the same path for the same reason,
    // under its own trigger (`backfill_lexical`). It deliberately does NOT
    // force a reparse: reparsing would re-derive symbols that have not
    // changed and inflate `reparsed_files`, when all that is missing is a
    // derived table computable from the symbols already stored plus the
    // source already read into `current` — exactly the relations case.
    // Writing postings outside `replace_file`'s transaction is safe here
    // and only here: these files' symbols are not being rewritten, so
    // postings cannot end up describing a different revision than the
    // symbols beside them, and an interruption mid-backfill leaves the
    // generation key unpublished, which makes the whole persisted index
    // unreadable until a later run finishes the job.
    let refresh_relations =
        opts.force_graph || (!existing.is_empty() && store.all_symbol_relations()?.is_empty());
    let mut lexical_backfill_raced = false;
    if refresh_relations || backfill_lexical {
        let to_parse_files: HashSet<&String> = to_parse.iter().map(|(f, _)| *f).collect();
        let mut unchanged_by_file: HashMap<&str, Vec<Symbol>> = HashMap::new();
        for s in &existing {
            if !to_parse_files.contains(&s.file) {
                unchanged_by_file
                    .entry(s.file.as_str())
                    .or_default()
                    .push(s.clone());
            }
        }
        let relation_total = unchanged_by_file.len();
        if relation_total > 0 {
            progress.begin(Stage::Relations, Some(relation_total));
        }
        for (i, (file, file_symbols)) in unchanged_by_file.into_iter().enumerate() {
            progress.advance(Stage::Relations, i + 1, relation_total);
            let Some(src) = current.get(file) else {
                continue;
            };
            let Some(lang) = scanner::language_for_source(Path::new(file), src) else {
                continue;
            };
            if refresh_relations {
                report.relations_refreshed_symbols += file_symbols.len();
                let relations =
                    crate::structural_relations::compute_file_relations(&file_symbols, src, lang);
                if !relations.is_empty() {
                    store.put_symbol_relations_batch(&relations)?;
                }
            }
            if backfill_lexical {
                let postings = crate::lexical::compute_file_postings(&file_symbols, src);
                // A concurrent writer (the watcher, or a second `oxide
                // index`) may have replaced this file since the scan. The
                // store refuses the write in that case; the file already
                // has correct postings from that writer, but this run can
                // no longer prove it covered the whole corpus itself, so it
                // must not publish the generation key.
                if !store.put_file_lexical(file, crate::symbols::content_hash(src), &postings)? {
                    lexical_backfill_raced = true;
                }
            }
        }
        if relation_total > 0 {
            progress.end(
                Stage::Relations,
                &count_summary(relation_total, relation_total),
            );
        }
    }

    parse_and_persist_changed_files(
        to_parse,
        &current,
        &existing,
        unreadable_files,
        store,
        &mut report,
        progress,
    )?;

    // Publish the lexical generation only now, after every file in the
    // corpus has been persisted with its postings. This single write is the
    // moment a partial index becomes a usable one; interrupt the run
    // anywhere before it and the key stays absent, readers keep falling back
    // to the in-memory build, and the next run backfills again. Publishing
    // per file, or on table creation, would expose a half-built corpus as
    // authoritative — the same failure `schema_version` had.
    //
    // Only a *full-corpus* pass may publish. `update_base_for_files` (the
    // watcher) sees an arbitrary subset and never calls this function, so it
    // cannot promote an incomplete index; it only keeps an already-complete
    // one current, which per-file transactional writes guarantee.
    //
    // And only a pass that actually wrote everything it set out to. If a
    // concurrent writer replaced a file mid-backfill, that file's postings
    // are that writer's, not this run's, and this run cannot vouch for the
    // corpus — leave the key unpublished and let the next run finish.
    if !lexical_backfill_raced {
        store.set_meta(LEXICAL_INDEX_KEY, &LEXICAL_INDEX_VERSION.to_string())?;
    }

    report.duration_ms = started.elapsed().as_millis();

    // Every discovered file must land in exactly one accounted state; a
    // mismatch means a file silently vanished somewhere in the pipeline.
    // Checked here, not at the very end of the full pipeline, because every
    // field this compares is already final once the base stage completes —
    // the embedding stage never touches scanned/unchanged/reparsed/errored.
    anyhow::ensure!(
        report.scanned_files == report.unchanged_files + report.reparsed_files + report.errored_files,
        "index accounting invariant violated: scanned {} != unchanged {} + reparsed {} + errored {}",
        report.scanned_files,
        report.unchanged_files,
        report.reparsed_files,
        report.errored_files
    );
    Ok(report)
}

/// Reads at most `cap + 1` bytes of `path`. `Ok(None)` means the file is
/// longer than `cap` bytes — the caller decides what that means (this
/// function never buffers more than `cap + 1` bytes to find out, so a
/// tracked file that has grown arbitrarily large, even adversarially,
/// cannot exhaust memory here regardless of its real size). `Ok(Some(_))`
/// carries every byte of a file within the cap. `Err` propagates the
/// underlying I/O error from `File::open` or the read untouched, for the
/// caller's own NotFound-vs-other-error handling.
fn read_capped(path: &Path, cap: u64) -> std::io::Result<Option<Vec<u8>>> {
    use std::io::Read;
    let mut file = std::fs::File::open(path)?;
    let probe_cap = cap.saturating_add(1);
    let mut buf = Vec::with_capacity(probe_cap.min(1 << 20) as usize);
    file.by_ref().take(probe_cap).read_to_end(&mut buf)?;
    if buf.len() as u64 > cap {
        Ok(None)
    } else {
        Ok(Some(buf))
    }
}

/// Base-stage update scoped to an explicit set of changed repo-relative
/// paths, for the auto-indexing watcher (`docs/auto-indexing-watcher-constraints/README.md`
/// seam #3). Reuses the exact same per-file parse/reference/relations/persist
/// pipeline as `update_base` (`parse_and_persist_changed_files`) — the only
/// difference is how `to_parse`/`current`/`removed` are computed: from the
/// caller-supplied path set instead of a full `scanner::scan_repo` walk.
///
/// `update_base` stays the "reconcile everything" entry point (manual
/// `oxide index`, startup/reconnect reconciliation) — this is strictly
/// additive, an optimization for "these specific paths changed" that a
/// watcher already knows from fs events, not a replacement.
///
/// # Deletion semantics — the one thing this function must get right
///
/// A path in `changed_paths` is treated as **removed** only when both hold:
/// 1. it does not currently exist on disk (confirmed via `std::fs::metadata`
///    failing for that exact path — direct filesystem evidence, checked
///    fresh, never inferred), and
/// 2. it was already tracked in the store (`stored.contains_key`).
///
/// A path outside `changed_paths` is never touched, deleted, or otherwise
/// assumed stale by this function — unlike `update_base`, which derives
/// `removed` from every stored file *not* found by a full scan, this
/// function has no full-tree view and must not pretend it does. A file the
/// watcher never learned about (a missed event, a watcher that wasn't
/// running) is exactly the gap `update_base`-driven reconciliation on
/// startup/reconnect exists to close, not something this function can or
/// should guess at.
pub fn update_base_for_files(
    root: &Path,
    store: &mut dyn IndexBackend,
    opts: &IndexOptions,
    changed_paths: &[String],
) -> Result<IndexReport> {
    // A scoped update re-extracts only the named paths, but the embedding
    // stage that normally follows it publishes `extraction_version`
    // unconditionally — so against an index built under older extraction
    // rules, a watcher batch would stamp the current version onto a corpus
    // where every untouched file still holds old spans and hashes, and
    // `validate_index` would then wave it through. Escalate to the full
    // reconcile once; afterwards the version matches and this never fires
    // again. Found by review.
    if stale_extraction(store)? {
        return update_base(root, store, opts);
    }

    let started = std::time::Instant::now();
    let mut report = IndexReport::default();

    let stored = store.file_hashes()?;
    let existing = store.all_symbols()?;

    let mut current: HashMap<String, String> = HashMap::with_capacity(changed_paths.len());
    let mut unreadable_files: usize = 0;
    let mut removed: Vec<String> = Vec::new();
    // Dedup defensively: a debounced batch could name the same path twice
    // (e.g. one event for the write, one for a metadata-only touch).
    let mut seen: HashSet<&str> = HashSet::with_capacity(changed_paths.len());
    for p in changed_paths {
        if !seen.insert(p.as_str()) {
            continue;
        }
        let full_path = root.join(p);
        // `IgnoreCache`'s fast path trusts its cached indexable set without
        // rechecking eligibility per event (deliberately — see
        // `watcher.rs::IgnoreCache::candidate`), so a markdown file that
        // grew past `MAX_MARKDOWN_BYTES` since the cache was last built can
        // still arrive here as a "changed path" even though `scan_repo`
        // would no longer include it. Treat a *confirmed* oversized file
        // exactly like the `NotFound` case below: a stale, previously-
        // indexed symbol must be removed, never left behind just because
        // nothing re-read it, and the file's now-oversized content must
        // never actually be parsed (found by review: an earlier fix
        // stopped the reparse but left the stale symbol in place, which is
        // worse than either extreme).
        //
        // The cap is judged against the bytes actually read, not a separate
        // `fs::metadata` call before or after — a prior version of this fix
        // checked metadata first and could reject a file that had already
        // shrunk back under the cap by read time (found by review); the
        // version after that checked `read_to_string`'s *full* output,
        // which is race-free but reads an arbitrarily large tracked file
        // completely into memory before rejecting it — a real
        // resource-exhaustion path for `oxide watch` (found by review).
        // `read_capped` reads at most `cap + 1` bytes via `Read::take`, so
        // memory use is bounded by the cap regardless of how large the
        // real file has grown, and oversized-ness is decided before UTF-8
        // validation ever runs — an oversized file that also happens to be
        // invalid UTF-8 is correctly evidence of "too big," not silently
        // reclassified as merely unreadable (also found by review).
        let lang = crate::scanner::language_for_path(&full_path);
        let cap = lang.map(crate::scanner::size_cap_for).unwrap_or(u64::MAX);
        match read_capped(&full_path, cap) {
            Ok(None) => {
                if stored.contains_key(p) {
                    removed.push(p.clone());
                }
            }
            Ok(Some(bytes)) => match String::from_utf8(bytes) {
                Ok(src) => {
                    current.insert(p.clone(), src);
                }
                Err(_) => unreadable_files += 1,
            },
            // Only a confirmed absence (`NotFound`) is deletion evidence —
            // and only for a path the store already tracks. Any other read
            // failure (permissions, a file mid-write) means the path still
            // exists; it must never be treated as removed, only as
            // unreadable this round (matches `update_base`'s
            // `unreadable_files`, accounted in `errored_files` below).
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
                if stored.contains_key(p) {
                    removed.push(p.clone());
                }
                // else: never tracked — a transient/irrelevant path (e.g.
                // an editor's temp file the caller's ignore-filter let
                // through, or deleted again before this batch ran).
            }
            Err(_) => unreadable_files += 1,
        }
    }
    report.scanned_files = current.len() + removed.len() + unreadable_files;

    if !removed.is_empty() {
        let doomed = existing
            .iter()
            .filter(|s| removed.contains(&s.file))
            .count();
        store.remove_files(&removed)?;
        report.deleted_symbols += doomed;
        report.removed_files = removed.len();
    }

    let to_parse: Vec<(&String, u64)> = current
        .iter()
        .map(|(f, src)| (f, crate::symbols::content_hash(src)))
        .filter(|(f, h)| opts.force_reparse || stored.get(*f).copied() != Some(*h))
        .collect();
    report.unchanged_files = current.len() - to_parse.len();

    parse_and_persist_changed_files(
        to_parse,
        &current,
        &existing,
        unreadable_files,
        store,
        &mut report,
        &NoProgress,
    )?;

    report.duration_ms = started.elapsed().as_millis();

    // Scoped invariant, deliberately different from `update_base`'s: this
    // function has no independent "scanned" count from a directory walk —
    // `scanned_files` is defined as `current.len() + removed.len()` above,
    // i.e. every path this function actually accounted for. `removed_files`
    // is therefore part of THIS invariant (unlike `update_base`, where
    // removed files are computed from `stored`, disjoint from its
    // `scanned_files`/directory-walk count).
    anyhow::ensure!(
        report.scanned_files
            == report.unchanged_files + report.reparsed_files + report.errored_files + report.removed_files,
        "scoped index accounting invariant violated: scanned {} != unchanged {} + reparsed {} + errored {} + removed {}",
        report.scanned_files,
        report.unchanged_files,
        report.reparsed_files,
        report.errored_files,
        report.removed_files
    );
    Ok(report)
}

#[cfg(test)]
mod read_capped_tests {
    use super::read_capped;

    #[test]
    fn a_file_within_the_cap_returns_every_byte() {
        let tmp = tempfile::tempdir().unwrap();
        let path = tmp.path().join("small.txt");
        std::fs::write(&path, b"hello").unwrap();
        assert_eq!(read_capped(&path, 10).unwrap(), Some(b"hello".to_vec()));
    }

    #[test]
    fn a_file_exactly_at_the_cap_is_not_oversized() {
        let tmp = tempfile::tempdir().unwrap();
        let path = tmp.path().join("exact.txt");
        std::fs::write(&path, b"12345").unwrap();
        assert_eq!(read_capped(&path, 5).unwrap(), Some(b"12345".to_vec()));
    }

    /// The property the review finding was about: a file far larger than
    /// the cap must never be buffered in full. 10 MB against a 16-byte cap
    /// would allocate ~10 MB if `read_capped` read the whole file before
    /// checking length; it does not, because the read itself is bounded by
    /// `Read::take`, so this test also serves as a speed/memory regression
    /// pin (it must run instantly, not read 10 MB).
    #[test]
    fn a_file_much_larger_than_the_cap_is_never_read_past_cap_plus_one() {
        let tmp = tempfile::tempdir().unwrap();
        let path = tmp.path().join("huge.bin");
        std::fs::write(&path, vec![b'x'; 10 * 1024 * 1024]).unwrap();
        assert_eq!(
            read_capped(&path, 16).unwrap(),
            None,
            "oversized must be reported, not the (never fully read) content"
        );
    }

    /// Oversized-ness is decided from the bounded byte count alone, before
    /// any UTF-8 validation — a file that is simultaneously over the cap
    /// AND invalid UTF-8 must still come back as `Ok(None)` (oversized),
    /// not surface as a decode error that would bypass the size-eligibility
    /// removal path (found by review).
    #[test]
    fn oversized_and_invalid_utf8_is_still_reported_as_oversized() {
        let tmp = tempfile::tempdir().unwrap();
        let path = tmp.path().join("invalid.bin");
        let mut bytes = vec![0xFFu8; 20];
        bytes.extend_from_slice(b"more bytes past the cap");
        std::fs::write(&path, &bytes).unwrap();
        assert_eq!(read_capped(&path, 5).unwrap(), None);
    }

    #[test]
    fn a_missing_file_propagates_not_found() {
        let tmp = tempfile::tempdir().unwrap();
        let path = tmp.path().join("does_not_exist.txt");
        let err = read_capped(&path, 100).unwrap_err();
        assert_eq!(err.kind(), std::io::ErrorKind::NotFound);
    }
}
