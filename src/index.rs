//! Incremental indexing: scan, parse, freshness, and embedding orchestration.

pub use crate::storage::{
    IndexBackend, IndexStats, ParsedFile, SqliteStore, SymbolRelations, EMBEDDING_MIGRATION_KEY,
    EXTRACTION_VERSION, LEXICAL_INDEX_KEY, LEXICAL_INDEX_VERSION, SCHEMA_VERSION,
};

use crate::embeddings::symbol_embed_text;
use crate::scanner;
use crate::symbols::Symbol;
use anyhow::Result;
use serde::Serialize;
use std::collections::{HashMap, HashSet};
use std::path::Path;

/// Outcome of one incremental run; surfaced by the CLI to show work avoided.
#[derive(Debug, Default, Clone, Serialize)]
pub struct IndexReport {
    pub scanned_files: usize,
    pub unchanged_files: usize,
    pub reparsed_files: usize,
    pub removed_files: usize,
    pub new_symbols: usize,
    pub changed_symbols: usize,
    pub deleted_symbols: usize,
    pub embedded_symbols: usize,
    pub reused_embeddings: usize,
    pub duration_ms: u128,
    /// Symbols whose embedding came back empty (endpoint failure): skipped,
    /// not stored, so a later healthy run re-embeds them.
    #[serde(default)]
    pub embed_failures: usize,
    /// Discovered files that could not be read/decoded (non-UTF8, IO error) or
    /// whose language resolution failed unexpectedly during parsing. Not
    /// stored; a later run retries them. Every discovered file must land in
    /// exactly one of unchanged_files + reparsed_files + errored_files.
    #[serde(default)]
    pub errored_files: usize,
    /// Symbols whose structural relations (`symbol_relations`) were
    /// recomputed even though their own file wasn't reparsed this run —
    /// nonzero only under `IndexOptions::force_graph` (`oxide index -g`) or
    /// the one-time legacy-index backfill this same code path also serves.
    #[serde(default)]
    pub relations_refreshed_symbols: usize,
}

/// Explicit rebuild scope for `oxide index`'s `-a`/`-g`/`-e` flags. Each
/// field only widens *which* symbols a stage recomputes — it never changes
/// what "stale" means or skips a stage's own required prerequisite work.
/// `Default` (all `false`) is the plain incremental contract every existing
/// caller of `update_index` already relies on, so adding this type changes
/// no existing behavior.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct IndexOptions {
    /// `-a`/`--all` only: reparse every file regardless of `content_hash`
    /// match, e.g. after upgrading OXIDE for an extractor/grammar fix that
    /// should re-derive symbols even where source text didn't change.
    pub force_reparse: bool,
    /// `-g`/`--graph`: recompute structural relations for every existing
    /// symbol, not just symbols in files reparsed this run.
    pub force_graph: bool,
    /// `-e`/`--embeddings`: recompute every symbol's embedding regardless
    /// of whether its stored embedding's hash already matches.
    pub force_embeddings: bool,
}

impl IndexOptions {
    /// `-a`/`--all`: every layer forced.
    pub fn all() -> Self {
        Self {
            force_reparse: true,
            force_graph: true,
            force_embeddings: true,
        }
    }
}

/// Run incremental indexing of the repo at `root` into `store`. Equivalent
/// to `update_index_scoped` with `IndexOptions::default()` — the plain
/// incremental contract every pre-existing caller of this function keeps
/// getting unchanged.
pub fn update_index(
    root: &Path,
    store: &mut dyn IndexBackend,
    embedder: &dyn crate::embeddings::EmbeddingProvider,
) -> Result<IndexReport> {
    update_index_scoped(root, store, embedder, &IndexOptions::default())
}

/// Like [`update_index`], but with explicit forced-rebuild scope. Runs the
/// base stage ([`update_base`]) then the embedding stage
/// ([`update_embeddings`]) back to back and returns one combined report —
/// the same single-call contract `update_index` has always had. Callers
/// that need to report progress *between* the two stages (`oxide index -a`)
/// should call `update_base`/`update_embeddings` directly instead.
pub fn update_index_scoped(
    root: &Path,
    store: &mut dyn IndexBackend,
    embedder: &dyn crate::embeddings::EmbeddingProvider,
    opts: &IndexOptions,
) -> Result<IndexReport> {
    let mut report = update_base(root, store, opts)?;
    update_embeddings(root, store, embedder, opts, &mut report)?;
    Ok(report)
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
    let started = std::time::Instant::now();
    let mut report = IndexReport::default();

    let files = scanner::scan_repo(root)?;
    report.scanned_files = files.len();

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
    let before_symbols: HashMap<u64, u64> =
        existing.iter().map(|s| (s.id(), s.content_hash)).collect();

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
    // know about `-a`. On a fresh index the key is absent and this is true,
    // which costs nothing: every file is new anyway.
    let stale_extraction = store.get_meta("extraction_version")?.as_deref()
        != Some(EXTRACTION_VERSION.to_string().as_str());
    let force_reparse = opts.force_reparse || stale_extraction;
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
        for (file, file_symbols) in unchanged_by_file {
            let (Some(src), Some(lang)) = (
                current.get(file),
                scanner::language_for_path(Path::new(file)),
            ) else {
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
    }

    parse_and_persist_changed_files(
        to_parse,
        &current,
        &existing,
        &before_symbols,
        unreadable_files,
        store,
        &mut report,
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
    let started = std::time::Instant::now();
    let mut report = IndexReport::default();

    let stored = store.file_hashes()?;
    let existing = store.all_symbols()?;
    let before_symbols: HashMap<u64, u64> =
        existing.iter().map(|s| (s.id(), s.content_hash)).collect();

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
        match std::fs::read_to_string(root.join(p)) {
            Ok(src) => {
                current.insert(p.clone(), src);
            }
            // Only a confirmed absence (`NotFound`) is deletion evidence —
            // and only for a path the store already tracks. Any other read
            // failure (non-UTF8, permissions, a file mid-write) means the
            // path still exists; it must never be treated as removed, only
            // as unreadable this round (matches `update_base`'s
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
        &before_symbols,
        unreadable_files,
        store,
        &mut report,
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

/// Shared parse → reference-resolve → structural-relations → persist
/// pipeline for a batch of changed files, extracted so `update_base`
/// (repo-wide) and `update_base_for_files` (fs-event-scoped, for the
/// auto-indexing watcher) share one implementation of the part that never
/// differs between them — only how `to_parse`/`current`/`existing` are
/// computed differs by caller. `existing` and `before_symbols` are always
/// repo-wide snapshots (`store.all_symbols()`) even when `to_parse` is
/// scoped: reference resolution needs the whole project's known names
/// regardless of how many files changed this run.
fn parse_and_persist_changed_files(
    to_parse: Vec<(&String, u64)>,
    current: &HashMap<String, String>,
    existing: &[Symbol],
    before_symbols: &HashMap<u64, u64>,
    unreadable_files: usize,
    store: &mut dyn IndexBackend,
    report: &mut IndexReport,
) -> Result<()> {
    // Parsing is pure CPU over independent files: fan out across a small
    // bounded pool (laptop-friendly cap) and collect in order.
    let workers = std::thread::available_parallelism()
        .map(|n| n.get())
        .unwrap_or(1)
        .clamp(1, 4);
    let chunk_size = to_parse.len().div_ceil(workers.max(1));
    let mut parsed: Vec<ParsedFile> = Vec::with_capacity(to_parse.len());
    let mut results: Vec<(Vec<ParsedFile>, usize)> = Vec::with_capacity(workers);
    std::thread::scope(|scope| {
        let mut handles = Vec::new();
        for (w, chunk) in to_parse.chunks(chunk_size.max(1)).enumerate() {
            let current = &current;
            results.push((Vec::new(), 0));
            handles.push(scope.spawn(move || {
                let mut out = Vec::with_capacity(chunk.len());
                // Files reaching here were already language-filtered by the
                // scanner; `unresolved` should stay 0 in practice, but a
                // future scanner bug must be counted, never silently dropped.
                let mut unresolved = 0usize;
                for (rel, hash) in chunk {
                    let lang = match scanner::language_for_path(Path::new(rel)) {
                        Some(l) => l,
                        None => {
                            unresolved += 1;
                            continue;
                        }
                    };
                    let src = &current[*rel];
                    let syms = crate::parser::parse_file(rel, src, lang);
                    out.push(ParsedFile {
                        file: (*rel).clone(),
                        hash: *hash,
                        src: src.clone(),
                        symbols: syms,
                    });
                }
                (w, out, unresolved)
            }));
        }
        for h in handles {
            let (w, out, unresolved) = h
                .join()
                .map_err(|_| anyhow::anyhow!("parse worker panicked"))?;
            results[w] = (out, unresolved);
        }
        Ok::<(), anyhow::Error>(())
    })?;
    let mut parse_unresolved: usize = 0;
    for (mut part, unresolved) in results {
        parsed.append(&mut part);
        parse_unresolved += unresolved;
    }
    report.reparsed_files = parsed.len();
    report.errored_files = unreadable_files + parse_unresolved;

    // Known bare definition names across the project (for reference matching).
    let mut known_names: HashSet<String> = existing
        .iter()
        .filter(|s| s.kind != crate::symbols::SymbolKind::Module)
        .map(|s| s.name.clone())
        .collect();
    for pf in &parsed {
        for s in &pf.symbols {
            if s.kind != crate::symbols::SymbolKind::Module {
                known_names.insert(s.name.clone());
            }
        }
    }
    // # ponytail: identifier-name intersection only; no scope analysis. Upgrade
    // path: per-language scoped resolution if false positives hurt retrieval.
    for pf in &mut parsed {
        // Whether this file's module symbol used the coarse "imports +
        // first line" hash (parser.rs) rather than the full-source hash:
        // parser.rs only takes the full-source path when there are no
        // concrete (non-Module) symbols at all — see `empty_before_module`
        // there. That full-source hash already changes on ANY body edit
        // (including comment-only edits with no declarations to anchor to,
        // where the module symbol is the file's only index representation)
        // and must be left alone; only the coarse formula needs the fix
        // below.
        let used_coarse_module_hash = pf
            .symbols
            .iter()
            .any(|s| s.kind != crate::symbols::SymbolKind::Module);
        for s in &mut pf.symbols {
            s.references = extract_references(s, &pf.src, &known_names);
            // The coarse module hash covers only imports + first line, but
            // its embedding input (`symbol_embed_text`) also includes `references`,
            // which are resolved here — one stage later, once whole-project
            // known names exist. A body-only edit that adds/removes an
            // in-file reference therefore changes `symbol_embed_text` without the
            // parser hash noticing. Recompute the module's content_hash as
            // the literal hash of its own `symbol_embed_text` now that references
            // are final, so the cache-invalidation key can never drift from
            // the actual embedding input (see AGENTS.md invariant) — but
            // only where the coarse formula was actually used.
            if s.kind == crate::symbols::SymbolKind::Module && used_coarse_module_hash {
                s.content_hash = crate::symbols::content_hash(&symbol_embed_text(s));
            }
        }
    }

    // `replace_file` deletes every existing symbol row for a changed file and
    // reinserts the freshly parsed set, so a symbol removed or renamed within
    // an otherwise-still-present file (not just a whole-file deletion) is a
    // real deletion too. Group the pre-edit snapshot by file so that delta is
    // counted, not just symbols new/changed_symbols above it.
    let mut existing_ids_by_file: HashMap<&str, HashSet<u64>> = HashMap::new();
    for s in existing {
        existing_ids_by_file
            .entry(s.file.as_str())
            .or_default()
            .insert(s.id());
    }

    for pf in &parsed {
        let mut new_ids: HashSet<u64> = HashSet::with_capacity(pf.symbols.len());
        for s in &pf.symbols {
            new_ids.insert(s.id());
            match before_symbols.get(&s.id()) {
                None => report.new_symbols += 1,
                Some(old) if *old != s.content_hash => report.changed_symbols += 1,
                Some(_) => {}
            }
        }
        if let Some(old_ids) = existing_ids_by_file.get(pf.file.as_str()) {
            report.deleted_symbols += old_ids.difference(&new_ids).count();
        }
        // Precomputed structural relations (structural_relations.rs): reuses
        // this loop's already-open `pf.src` and already-parsed `pf.symbols`
        // — one extra tree-sitter Query pass per reparsed file, no second
        // file read. Computed before `replace_file` so both land in the
        // same transaction (`replace_file`'s doc comment explains why a
        // separate follow-up call was a real interrupted-process bug: the
        // file's content_hash would already be updated, so a crash between
        // two separate calls would strand stale relations permanently,
        // since that file would never be reparsed again). Only for `parsed`
        // (reparsed) files, matching `extract_references` above: an
        // unchanged file keeps its existing `symbol_relations` rows
        // untouched, same incremental contract as everything else here.
        let relations = scanner::language_for_path(Path::new(&pf.file))
            .map(|lang| {
                crate::structural_relations::compute_file_relations(&pf.symbols, &pf.src, lang)
            })
            .unwrap_or_default();
        // Lexical postings, from the same already-open `pf.src` and
        // already-parsed `pf.symbols` as the relations above — no second
        // file read, and (unlike `LexicalIndex::build`) no file read at
        // query time at all. Written in `replace_file`'s transaction so a
        // file's symbols and its postings can never disagree. This is the
        // shared helper, so the watcher's fs-event-scoped path
        // (`update_base_for_files`) keeps postings current too; if it did
        // not, a watcher edit would leave a published lexical index
        // silently incomplete.
        let postings = crate::lexical::compute_file_postings(&pf.symbols, &pf.src);
        store.replace_file(&pf.file, pf.hash, &pf.symbols, &relations, &postings)?;
    }
    Ok(())
}

/// Embedding stage only: fingerprint/embedder staleness check, then
/// (re)embed. Must run after a base stage (`update_base`/`update_index`)
/// that reflects current file content — never call this against a store
/// whose symbols might be stale, or it will embed symbols against text
/// they no longer match. Adds this stage's elapsed time to
/// `report.duration_ms` rather than overwriting it, so a caller running
/// both stages back to back ends up with their sum.
pub fn update_embeddings(
    root: &Path,
    store: &mut dyn IndexBackend,
    embedder: &dyn crate::embeddings::EmbeddingProvider,
    opts: &IndexOptions,
    report: &mut IndexReport,
) -> Result<()> {
    let started = std::time::Instant::now();
    let workers = std::thread::available_parallelism()
        .map(|n| n.get())
        .unwrap_or(1)
        .clamp(1, 4);

    // Vectors from a different vector space are not comparable: wipe them
    // once so everything below re-embeds under the current model. Clearing
    // and marking the migration in flight is one transaction, so from here
    // on "marker present" implies "every surviving vector is the marker's"
    // — see `IndexBackend::begin_embedding_migration`.
    let current_fp = embedder.fingerprint();
    let fingerprint_json = serde_json::to_string(&current_fp)?;
    let resuming = store
        .get_meta(EMBEDDING_MIGRATION_KEY)?
        .is_some_and(|s| !s.is_empty());
    if let Some(reason) = incompatible_stored_space(store, embedder)? {
        eprintln!("oxide: {reason}");
        store.begin_embedding_migration(&fingerprint_json)?;
    } else if resuming {
        // Compatible *and* marked means an earlier run of this same provider
        // was interrupted: its rows are ours to finish. Rewrite the marker
        // with this run's canonical serialization so the guard below can
        // compare it as bytes rather than re-parsing it on every write.
        store.set_meta(EMBEDDING_MIGRATION_KEY, &fingerprint_json)?;
    }

    // The value every write below asserts the marker still holds. Empty for
    // an ordinary incremental run (no migration is in flight, and none may
    // start under us); this run's fingerprint while one is. See
    // `IndexBackend::put_embeddings_batch` for the concurrency this closes.
    // After the branch above this is either "" or this run's
    // `fingerprint_json`; read it back rather than reconstructing it.
    let expected_space = store.get_meta(EMBEDDING_MIGRATION_KEY)?.unwrap_or_default();

    // Embed only symbols whose embedding is missing or whose content
    // changed; everything else reuses its stored vector untouched — unless
    // `opts.force_embeddings` (`-e`/`--embeddings`) says recompute
    // everything regardless of the hash match. Vector computation is pure
    // CPU: fan out over the same bounded pool, write serially after.
    let embeddings = store.all_embeddings()?;
    let all = store.all_symbols()?;
    let to_embed: Vec<&Symbol> = all
        .iter()
        .filter(|s| match embeddings.get(&s.id()) {
            Some((old_hash, _)) if *old_hash == s.content_hash && !opts.force_embeddings => {
                report.reused_embeddings += 1;
                false
            }
            _ => true,
        })
        .collect();
    let chunk_size = to_embed.len().div_ceil(workers.max(1));
    // Batched path: providers with batch endpoints (HTTP) get one request per
    // chunk; the thread pool stays useful for per-text providers.
    if to_embed.len() < 8 || std::env::var("OXIDE_EMBED_URL").is_ok() {
        for chunk in to_embed.chunks(64) {
            let texts: Vec<String> = chunk.iter().map(|s| symbol_embed_text(s)).collect();
            let vectors = embedder.embed_documents(&texts);
            // One transaction per chunk instead of one autocommit per
            // symbol — see `IndexBackend::put_embeddings_batch`'s doc
            // comment for why this was worth doing and the batch/thread
            // chunking around it wasn't.
            let mut batch: Vec<(u64, Vec<f32>)> = Vec::with_capacity(chunk.len());
            for (s, vec) in chunk.iter().zip(vectors) {
                if vec.iter().all(|f| *f == 0.0) || vec.is_empty() {
                    report.embed_failures += 1;
                    continue;
                }
                batch.push((s.id(), vec));
            }
            report.embedded_symbols += batch.len();
            store.put_embeddings_batch(&expected_space, &batch)?;
        }
    } else {
        let computed: Vec<Vec<(u64, Vec<f32>)>> = std::thread::scope(|scope| {
            let mut handles = Vec::new();
            for chunk in to_embed.chunks(chunk_size.max(1)) {
                handles.push(scope.spawn(|| {
                    chunk
                        .iter()
                        .map(|s| (s.id(), embedder.embed_document(&symbol_embed_text(s))))
                        .collect::<Vec<_>>()
                }));
            }
            let mut out = Vec::new();
            for h in handles {
                out.push(
                    h.join()
                        .map_err(|_| anyhow::anyhow!("embed worker panicked"))?,
                );
            }
            Ok::<_, anyhow::Error>(out)
        })?;
        // Same rejection the batched path above applies: an empty or
        // all-zero vector is a provider failure, not an embedding. Counting
        // it as a success (and storing it) let a fully-failed run report
        // `embed_failures: 0`, which `index_staged` reads as "provider
        // healthy" — and left rows that satisfy the `embeddings == symbols`
        // completeness check while carrying no signal at all.
        for part in computed {
            let kept: Vec<(u64, Vec<f32>)> = part
                .into_iter()
                .filter(|(_, vec)| {
                    let failed = vec.is_empty() || vec.iter().all(|f| *f == 0.0);
                    if failed {
                        report.embed_failures += 1;
                    }
                    !failed
                })
                .collect();
            report.embedded_symbols += kept.len();
            store.put_embeddings_batch(&expected_space, &kept)?;
        }
    }

    let root_str = root.display().to_string();
    let dim_str = embedder.dim().to_string();
    let schema_str = SCHEMA_VERSION.to_string();
    let extraction_str = EXTRACTION_VERSION.to_string();
    // Publishing the completed identity and retiring the in-flight marker
    // must be the same transaction as everything else here: this is the one
    // instant at which the index stops being "mid-migration" and starts
    // being "built by this provider", and a torn version of it is exactly
    // the state `begin_embedding_migration` exists to make impossible.
    store.set_meta_all(
        &expected_space,
        &[
            ("root", root_str.as_str()),
            ("embedder", embedder.name()),
            ("dim", dim_str.as_str()),
            ("schema_version", schema_str.as_str()),
            ("extraction_version", extraction_str.as_str()),
            ("embedding_fingerprint", fingerprint_json.as_str()),
            (EMBEDDING_MIGRATION_KEY, ""),
        ],
    )?;
    // Additive, not an overwrite: a caller running this right after
    // `update_base` (the normal case) already has that stage's duration in
    // `report.duration_ms` and wants the combined total, not just this
    // stage's time.
    report.duration_ms += started.elapsed().as_millis();
    Ok(())
}

/// Read-only count of symbols whose embedding is missing, stale (content
/// changed since last embedded), or from a different embedding space than
/// `embedder` currently provides — exactly what `update_embeddings` would
/// (re)compute if called right now, without calling it. For the
/// auto-indexing watcher's "track base and semantic freshness independently"
/// / "stale/pending embeddings must never be presented as current"
/// requirements (`docs/auto-indexing-watcher-constraints/README.md` seam
/// #2): a caller can report "N symbols pending embedding" without
/// triggering the embedding work itself (which may be slow or
/// network-bound). Mirrors `update_embeddings`'s own staleness checks
/// exactly — the two must never diverge, or a watcher could report "0
/// pending" while a real `update_embeddings` run would still find work.
pub fn pending_embedding_count(
    store: &dyn IndexBackend,
    embedder: &dyn crate::embeddings::EmbeddingProvider,
) -> Result<usize> {
    if incompatible_stored_space(store, embedder)?.is_some() {
        return Ok(store.all_symbols()?.len());
    }
    content_stale_embedding_count(store)
}

/// Whether the index's stored vectors are usable under `embedder`, and why
/// not when they aren't: `Some(reason)` means "these vectors belong to a
/// different embedding space, clear them", `None` means "reuse is safe".
///
/// The single decision point for [`update_embeddings`] (which clears on
/// `Some`) and [`pending_embedding_count`] (which reports every symbol
/// pending on `Some`). They used to duplicate this comparison, with a
/// comment on each warning that they must never diverge; sharing it is how
/// that guarantee stops depending on the comment.
///
/// Precedence, strongest evidence first:
/// 1. [`EMBEDDING_MIGRATION_KEY`] — an unfinished migration. Only ever
///    written in the same transaction that empties the embeddings table, so
///    it, not the published metadata (which the interrupted run never
///    reached), describes the surviving rows.
/// 2. `embedding_fingerprint` — the real compatibility contract when
///    present (Phase 3.3 item 3).
/// 3. `embedder` + `dim` — the legacy fallback for indices written before
///    fingerprints existed, so upgrading OXIDE doesn't force a reindex.
///
/// A stored value at any tier that is present but unparseable is treated as
/// incompatible, never guessed at and never ignored: "unreadable" must not
/// fail open into the weaker tier below it, or a corrupt fingerprint would
/// be waved through by a matching legacy name.
fn incompatible_stored_space(
    store: &dyn IndexBackend,
    embedder: &dyn crate::embeddings::EmbeddingProvider,
) -> Result<Option<String>> {
    let current_fp = embedder.fingerprint();
    let parse =
        |raw: &str| serde_json::from_str::<crate::embeddings::EmbeddingSpaceFingerprint>(raw);

    if let Some(raw) = store
        .get_meta(EMBEDDING_MIGRATION_KEY)?
        .filter(|s| !s.is_empty())
    {
        return Ok(match parse(&raw) {
            Ok(prev) if prev == current_fp => None,
            Ok(prev) => Some(format!(
                "an interrupted migration to {} left this index's vectors mid-flight; re-embedding all symbols under {}",
                prev.model, current_fp.model
            )),
            Err(_) => Some(
                "an interrupted embedding migration left an unreadable fingerprint; re-embedding all symbols".to_string(),
            ),
        });
    }

    if let Some(raw) = store
        .get_meta("embedding_fingerprint")?
        .filter(|s| !s.is_empty())
    {
        return Ok(match parse(&raw) {
            Ok(prev) if prev == current_fp => None,
            Ok(prev) => Some(format!(
                "embedding space changed ({} -> {}); re-embedding all symbols",
                prev.model, current_fp.model
            )),
            Err(_) => Some(
                "stored embedding fingerprint is unreadable; re-embedding all symbols".to_string(),
            ),
        });
    }

    // Legacy index. The name check this codebase has always used, plus the
    // dimension: the name does not imply the width. `HashedEmbedder` reports
    // one fixed name at every `dim`, and a served model can change output
    // width without changing its label, so a name-only comparison happily
    // reuses rows of the wrong shape.
    let prev_name = store.get_meta("embedder")?.filter(|s| !s.is_empty());
    let prev_dim = store.get_meta("dim")?.filter(|s| !s.is_empty());
    if let Some(prev) = prev_name.filter(|p| p != embedder.name()) {
        return Ok(Some(format!(
            "embedder changed ({} -> {}); re-embedding all symbols",
            prev,
            embedder.name()
        )));
    }
    if let Some(prev) = prev_dim.filter(|d| *d != embedder.dim().to_string()) {
        return Ok(Some(format!(
            "embedding dimension changed ({} -> {}); re-embedding all symbols",
            prev,
            embedder.dim()
        )));
    }
    Ok(None)
}

/// Count of symbols whose stored embedding is missing or whose content
/// changed since it was computed — the embedding-space-agnostic half of
/// [`pending_embedding_count`]'s check, split out so a caller that has
/// already established embedder compatibility some other way (e.g.
/// `RepositoryService::status`'s existing name-based `embedder_current`
/// check, which is deliberately network-free) doesn't need a live
/// `EmbeddingProvider` just to ask "how many symbols are stale."
pub fn content_stale_embedding_count(store: &dyn IndexBackend) -> Result<usize> {
    let all = store.all_symbols()?;
    let embeddings = store.all_embeddings()?;
    Ok(all
        .iter()
        .filter(|s| match embeddings.get(&s.id()) {
            Some((old_hash, _)) => *old_hash != s.content_hash,
            None => true,
        })
        .count())
}

/// References = identifiers appearing in the symbol body that match a known
/// project definition name (excluding the symbol itself).
fn extract_references(s: &Symbol, src: &str, known: &HashSet<String>) -> Vec<String> {
    let body: String = src
        .lines()
        .skip(s.start_line.saturating_sub(1) as usize)
        .take(s.end_line.saturating_sub(s.start_line - 1) as usize + 1)
        .collect::<Vec<_>>()
        .join("\n");
    let mut refs: HashSet<String> = HashSet::new();
    for tok in crate::embeddings::tokenize(&body) {
        // tokenize splits camelCase; match on both raw and joined forms.
        if known.contains(&tok) && tok != s.name {
            refs.insert(tok);
        }
    }
    for raw in body.split(|c: char| !(c.is_alphanumeric() || c == '_')) {
        if known.contains(raw) && raw != s.name {
            refs.insert(raw.to_string());
        }
    }
    let mut out: Vec<String> = refs.into_iter().collect();
    out.sort();
    out
}

#[cfg(test)]
mod error_classification_tests {
    use crate::storage::is_locked_error;

    fn sqlite_error(result_code: std::ffi::c_int) -> anyhow::Error {
        let inner = rusqlite::ffi::Error::new(result_code);
        anyhow::Error::new(rusqlite::Error::SqliteFailure(inner, None))
    }

    #[test]
    fn classifies_busy_and_locked_as_transient() {
        assert!(is_locked_error(&sqlite_error(rusqlite::ffi::SQLITE_BUSY)));
        assert!(is_locked_error(&sqlite_error(rusqlite::ffi::SQLITE_LOCKED)));
    }

    #[test]
    fn does_not_classify_other_errors_as_transient() {
        assert!(!is_locked_error(&sqlite_error(
            rusqlite::ffi::SQLITE_CORRUPT
        )));
        assert!(!is_locked_error(&anyhow::anyhow!("unrelated io error")));
    }

    #[test]
    fn sees_through_context_wrapping() {
        // `open`/`open_read_only` wrap the underlying rusqlite::Error with
        // `.with_context(...)`; the classifier must still find it via the
        // error chain, not just the outermost layer.
        let wrapped = sqlite_error(rusqlite::ffi::SQLITE_BUSY).context("open index at /some/path");
        assert!(is_locked_error(&wrapped));
    }
}
