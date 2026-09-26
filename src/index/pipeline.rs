//! The per-file path shared by `update_base` and `update_base_for_files`:
//! parse → references → embedding-input hash → structural relations and
//! lexical postings → one `replace_file` transaction per file.

use super::{count_summary, IndexReport, ProgressSink, Stage};
use crate::embeddings::symbol_embed_text;
use crate::scanner;
use crate::storage::{IndexBackend, ParsedFile};
use crate::symbols::Symbol;
use anyhow::Result;
use std::collections::{HashMap, HashSet};
use std::path::Path;

/// Shared parse → reference-resolve → structural-relations → persist
/// pipeline for a batch of changed files, extracted so `update_base`
/// (repo-wide) and `update_base_for_files` (fs-event-scoped, for the
/// auto-indexing watcher) share one implementation of the part that never
/// differs between them — only how `to_parse`/`current`/`existing` are
/// computed differs by caller. `existing` is always a repo-wide snapshot
/// (`store.all_symbols()`, taken before any write) even when `to_parse` is
/// scoped: reference resolution needs the whole project's known names
/// regardless of how many files changed this run.
pub(super) fn parse_and_persist_changed_files(
    to_parse: Vec<(&String, u64)>,
    current: &HashMap<String, String>,
    existing: &[Symbol],
    unreadable_files: usize,
    store: &mut dyn IndexBackend,
    report: &mut IndexReport,
    progress: &dyn ProgressSink,
) -> Result<()> {
    // Parsing is pure CPU over independent files: fan out across a small
    // bounded pool (laptop-friendly cap) and collect in order.
    let workers = std::thread::available_parallelism()
        .map(|n| n.get())
        .unwrap_or(1)
        .clamp(1, 4);
    let chunk_size = to_parse.len().div_ceil(workers.max(1));
    let parse_total = to_parse.len();
    let parse_done = std::sync::atomic::AtomicUsize::new(0);
    let mut parsed: Vec<ParsedFile> = Vec::with_capacity(to_parse.len());
    let mut results: Vec<(Vec<ParsedFile>, usize)> = Vec::with_capacity(workers);
    progress.begin(Stage::Parse, Some(parse_total));
    std::thread::scope(|scope| {
        let mut handles = Vec::new();
        for (w, chunk) in to_parse.chunks(chunk_size.max(1)).enumerate() {
            let current = &current;
            let parse_done = &parse_done;
            results.push((Vec::new(), 0));
            handles.push(scope.spawn(move || {
                let mut out = Vec::with_capacity(chunk.len());
                // Files reaching here were already language-filtered by the
                // scanner; `unresolved` should stay 0 in practice, but a
                // future scanner bug must be counted, never silently dropped.
                let mut unresolved = 0usize;
                for (rel, hash) in chunk {
                    let src = &current[*rel];
                    let lang = match scanner::language_for_source(Path::new(rel), src) {
                        Some(l) => l,
                        None => {
                            unresolved += 1;
                            continue;
                        }
                    };
                    let syms = crate::parser::parse_file(rel, src, lang);
                    out.push(ParsedFile {
                        file: (*rel).clone(),
                        hash: *hash,
                        src: src.clone(),
                        symbols: syms,
                    });
                    let done = parse_done.fetch_add(1, std::sync::atomic::Ordering::Relaxed) + 1;
                    progress.advance(Stage::Parse, done, parse_total);
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
    progress.end(Stage::Parse, &count_summary(parsed.len(), parse_total));

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
        for s in &mut pf.symbols {
            s.references = extract_references(s, &pf.src, &known_names);
            // `content_hash` is the embedding reuse key, so it must cover the
            // exact embedding input. The parser hash covers only the span
            // (or, for the module symbol, imports + first line), but
            // `symbol_embed_text` also carries the file-level `imports` and
            // the `references` resolved just above against project-wide
            // names: an import-only edit, or a new same-file definition an
            // untouched body already names, changes the input without
            // moving the span. Fold the literal input into the key now that
            // references are final (see AGENTS.md invariant). The parser
            // hash stays in it so a span edit still reprocesses the symbol,
            // and the empty-file module's full-source hash still sees
            // comment-only edits.
            s.content_hash = embedding_input_hash(s.content_hash, &symbol_embed_text(s));
        }
    }

    // `replace_file` deletes every existing symbol row for a changed file and
    // reinserts the freshly parsed set, so a symbol removed or renamed within
    // an otherwise-still-present file (not just a whole-file deletion) is a
    // real deletion too. Group the pre-edit snapshot by file so that delta is
    // counted, not just symbols new/changed_symbols above it.
    let before_symbols: HashMap<u64, u64> =
        existing.iter().map(|s| (s.id(), s.content_hash)).collect();
    let mut existing_ids_by_file: HashMap<&str, HashSet<u64>> = HashMap::new();
    for s in existing {
        existing_ids_by_file
            .entry(s.file.as_str())
            .or_default()
            .insert(s.id());
    }

    progress.begin(Stage::Store, Some(parsed.len()));
    for (i, pf) in parsed.iter().enumerate() {
        progress.advance(Stage::Store, i + 1, parsed.len());
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
        let relations = scanner::language_for_source(Path::new(&pf.file), &pf.src)
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
    progress.end(Stage::Store, &count_summary(parsed.len(), parsed.len()));
    Ok(())
}

/// A symbol's persisted `content_hash`: its parser hash combined with the
/// exact text the embedder is given, so reuse can never outlive a change to
/// that text.
fn embedding_input_hash(parser_hash: u64, embed_text: &str) -> u64 {
    crate::symbols::fnv1a64_iter([&parser_hash.to_le_bytes()[..], embed_text.as_bytes()])
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
