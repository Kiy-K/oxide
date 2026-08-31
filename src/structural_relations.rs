//! Precomputes AST-precise call/implementor relations as part of the
//! normal indexing pipeline. [`compute_file_relations`] is called from
//! `index::update_index`'s existing per-file loop (the same place
//! `extract_references` already runs) — it reuses that loop's already-open
//! `pf.src` and already-parsed `pf.symbols`, adding one extra tree-sitter
//! `Query` pass per file (`tree_sitter_structural::all_calls_in_file`/
//! `all_bases_in_file`) instead of a second file read. Results are written
//! atomically alongside that file's symbols via
//! `IndexBackend::replace_file`'s `relations` parameter — not a separate
//! call, so an interrupted process can't strand `symbol_relations` out of
//! sync with a `content_hash` that already moved on (see `replace_file`'s
//! doc comment). `update_index` also runs a one-time backfill
//! (`IndexBackend::put_symbol_relations_batch`, the standalone form) for
//! files that predate this feature — see `update_index`'s own comment for
//! why an index with symbols but an empty `symbol_relations` table needs
//! one.
//!
//! Migrated from an experimental, unwired second pass — see
//! `docs/precomputed-structural-relations/README.md` for that evidence and
//! `docs/precomputed-relations-migration/README.md` for this migration's.
//! `context.rs`'s bounded expansion now reads these via
//! `RelationGraph::callers_of`/`implementors_of` (`retrieval.rs`) instead of
//! a live AST scan; the old query-time `structural.rs`/ast-grep backend is
//! gone.

use crate::index::IndexBackend;
use crate::symbols::{Language, Symbol, SymbolKind};
use crate::tree_sitter_structural::{all_bases_in_file, all_calls_in_file};
use anyhow::Result;
use std::collections::HashMap;

/// Smallest non-Module symbol (by span, then by deepest nesting) in
/// `file_symbols` whose `[start_line, end_line]` contains `line`, falling
/// back to the file's Module symbol only when nothing more specific
/// contains it. Two ties found empirically, both fixed by a stronger
/// tie-break rather than accepting order-dependent results:
///
/// - A file containing a single top-level function has a Module symbol
///   whose span is numerically identical to that function's
///   (`parser.rs::parse_file_with` spans Module `1..=line_count`) —
///   `finds_method_style_calls_not_just_bare_calls` in
///   `tests/precomputed_relations_conformance.rs`. Fixed by excluding
///   Module from the numeric competition entirely: it's a pure fallback,
///   not a competitor.
/// - A function nested inside another function, both collapsed onto one
///   source line (`function outer() { function inner() { target(); } }`),
///   gives `outer` and `outer.inner` byte-identical spans — smallest-span
///   alone ties, and whichever the caller happened to list first in
///   `file_symbols` would win regardless of actual nesting. Fixed by a
///   secondary tie-break on qualified-name length: a more deeply nested
///   symbol's qualified name is strictly longer (`outer.inner` vs
///   `outer`), so preferring the longest name among span-tied candidates
///   always prefers the innermost enclosing scope.
///
/// Every file always has the Module fallback (`parse_file_with`'s module
/// symbol), so a top-level call still resolves to *some* symbol, never
/// `None`.
fn enclosing<'a>(file_symbols: &[&'a Symbol], line: u32) -> Option<&'a Symbol> {
    file_symbols
        .iter()
        .filter(|s| s.kind != SymbolKind::Module && s.start_line <= line && line <= s.end_line)
        .min_by_key(|s| {
            (
                s.end_line - s.start_line,
                std::cmp::Reverse(s.qualified_name.len()),
            )
        })
        .copied()
        .or_else(|| {
            file_symbols
                .iter()
                .find(|s| s.kind == SymbolKind::Module)
                .copied()
        })
}

/// One `(symbol_id, calls, bases)` triple per symbol in `file_symbols` —
/// **every** symbol, including ones with no relations at all (empty
/// `Vec`s), not just ones with something to report. This matters for
/// incremental reindexing: `put_symbol_relations_batch`'s DELETE-then-INSERT
/// per entry is what clears a symbol's *stale* relations after an edit
/// removes a call/base — a symbol silently skipped here because it
/// currently has zero relations would keep whatever relations it had from
/// a *previous* index run forever, since its `symbols` row (and therefore
/// its stable id, `Symbol::id()`) never changes across an edit that only
/// touches its body. `src`/`file_symbols` are assumed already read/parsed
/// by the caller (`index::update_index`) — this function does no I/O.
pub fn compute_file_relations(
    file_symbols: &[Symbol],
    src: &str,
    lang: Language,
) -> Vec<(u64, Vec<String>, Vec<String>)> {
    let refs: Vec<&Symbol> = file_symbols.iter().collect();

    let mut calls_by_symbol: HashMap<u64, Vec<String>> = HashMap::new();
    for (line, name) in all_calls_in_file(lang, src) {
        if let Some(sym) = enclosing(&refs, line) {
            calls_by_symbol.entry(sym.id()).or_default().push(name);
        }
    }
    // Keyed by the class declaration's own start line, filtered to
    // Class/Interface-kind symbols — not name (two differently-nested
    // classes can share a bare name, e.g. `Outer1.Config`/`Outer2.Config`,
    // and matching by name alone would fan a base list onto every
    // same-named class in the file — a real bug found by review, fixed by
    // this exact-line approach: a class's own start line doesn't collide
    // with a same-line member's start line for anything but that member
    // itself, and the kind filter excludes it). See `all_bases_in_file`'s
    // doc comment.
    let mut bases_by_symbol: HashMap<u64, Vec<String>> = HashMap::new();
    for (class_line, base_name) in all_bases_in_file(lang, src) {
        for sym in refs.iter().filter(|s| {
            s.start_line == class_line
                && matches!(s.kind, SymbolKind::Class | SymbolKind::Interface)
        }) {
            bases_by_symbol
                .entry(sym.id())
                .or_default()
                .push(base_name.clone());
        }
    }

    let mut out = Vec::with_capacity(file_symbols.len());
    for sym in file_symbols {
        let mut calls = calls_by_symbol.remove(&sym.id()).unwrap_or_default();
        let mut bases = bases_by_symbol.remove(&sym.id()).unwrap_or_default();
        calls.sort();
        calls.dedup();
        bases.sort();
        bases.dedup();
        out.push((sym.id(), calls, bases));
    }
    out
}

/// Loads symbols with `calls`/`bases` merged in from `symbol_relations` —
/// the read-side counterpart of [`compute_file_relations`]/`update_index`.
/// `context.rs`'s `build_context` is the production caller; nothing calls
/// `store.all_symbols()` directly anymore when relations are needed.
pub fn load_symbols_with_relations(store: &dyn IndexBackend) -> Result<Vec<Symbol>> {
    let mut symbols = store.all_symbols()?;
    let mut relations = store.all_symbol_relations()?;
    for s in &mut symbols {
        if let Some((calls, bases)) = relations.remove(&s.id()) {
            s.calls = calls;
            s.bases = bases;
        }
    }
    Ok(symbols)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::embeddings::HashedEmbedder;
    use crate::index::{update_index, SqliteStore};
    use std::fs;
    use std::path::Path;

    fn write(dir: &Path, rel: &str, contents: &str) {
        let p = dir.join(rel);
        fs::create_dir_all(p.parent().unwrap()).unwrap();
        fs::write(p, contents).unwrap();
    }

    fn indexed(dir: &Path) -> SqliteStore {
        let mut store = SqliteStore::open(&dir.join(".oxide/index.db")).unwrap();
        let embedder = HashedEmbedder::default();
        update_index(dir, &mut store, &embedder).unwrap();
        store
    }

    #[test]
    fn update_index_populates_relations_directly_no_second_pass_needed() {
        let tmp = tempfile::tempdir().unwrap();
        write(
            tmp.path(),
            "notifiers.py",
            "class Notifier:\n    def notify(self, m): raise NotImplementedError\n\nclass EmailNotifier(Notifier):\n    def notify(self, m):\n        print(m)\n\ndef notify_after_final_attempt(n, m):\n    n.notify(m)\n",
        );
        let store = indexed(tmp.path());

        let symbols = load_symbols_with_relations(&store).unwrap();
        let email_notifier = symbols
            .iter()
            .find(|s| s.qualified_name == "EmailNotifier")
            .unwrap();
        assert_eq!(email_notifier.bases, vec!["Notifier".to_string()]);

        let caller = symbols
            .iter()
            .find(|s| s.qualified_name == "notify_after_final_attempt")
            .unwrap();
        assert_eq!(caller.calls, vec!["notify".to_string()]);
    }

    #[test]
    fn top_level_calls_attach_to_the_module_fallback_symbol() {
        let tmp = tempfile::tempdir().unwrap();
        write(
            tmp.path(),
            "x.ts",
            "// call fetch(x) in a comment\nconst s = \"fetch(y)\";\nfetch(real());\n",
        );
        let store = indexed(tmp.path());

        let symbols = load_symbols_with_relations(&store).unwrap();
        let module = symbols
            .iter()
            .find(|s| s.qualified_name.ends_with(":__module__"))
            .unwrap();
        assert!(
            module.calls.contains(&"fetch".to_string()),
            "{:?}",
            module.calls
        );
    }

    #[test]
    fn call_inside_a_one_line_nested_function_attaches_to_the_inner_function() {
        // Found by review: `outer` and `outer.inner` have byte-identical
        // line spans when collapsed onto one line, so smallest-span alone
        // ties — `enclosing()`'s qualified-name-length tie-break must
        // prefer the more deeply nested `outer.inner`, not whichever the
        // caller happened to list first.
        let tmp = tempfile::tempdir().unwrap();
        write(
            tmp.path(),
            "x.ts",
            "function outer() { function inner() { target(); } }\n",
        );
        let store = indexed(tmp.path());

        let symbols = load_symbols_with_relations(&store).unwrap();
        let outer = symbols
            .iter()
            .find(|s| s.qualified_name == "outer")
            .unwrap();
        let inner = symbols
            .iter()
            .find(|s| s.qualified_name == "outer.inner")
            .unwrap();
        assert!(outer.calls.is_empty(), "{:?}", outer.calls);
        assert_eq!(inner.calls, vec!["target".to_string()]);
    }

    #[test]
    fn same_bare_name_classes_in_different_scopes_do_not_cross_attribute_bases() {
        // Found by review: matching bases by bare class name alone fanned a
        // base list onto every same-named class in the file. Two `C`
        // classes nested in different outer classes must each get only
        // their own base.
        let tmp = tempfile::tempdir().unwrap();
        write(
            tmp.path(),
            "y.ts",
            "class Outer1 {\n  field = class C extends A {}\n}\nclass Outer2 {\n  field2 = class C extends B {}\n}\n",
        );
        let store = indexed(tmp.path());

        let symbols = load_symbols_with_relations(&store).unwrap();
        let c1 = symbols
            .iter()
            .find(|s| s.qualified_name == "Outer1.C")
            .unwrap();
        let c2 = symbols
            .iter()
            .find(|s| s.qualified_name == "Outer2.C")
            .unwrap();
        assert_eq!(c1.bases, vec!["A".to_string()], "{:?}", c1.bases);
        assert_eq!(c2.bases, vec!["B".to_string()], "{:?}", c2.bases);
    }

    #[test]
    fn editing_away_a_call_clears_the_stale_relation_on_reindex() {
        // A symbol whose calls become empty after an edit must have its
        // symbol_relations row cleared, not left stale — compute_file_relations
        // always emits an entry (even empty) for every symbol so
        // put_symbol_relations_batch's DELETE-then-INSERT runs regardless.
        let tmp = tempfile::tempdir().unwrap();
        write(
            tmp.path(),
            "x.py",
            "def helper():\n    pass\n\ndef f():\n    helper()\n",
        );
        let mut store = indexed(tmp.path());
        let before = load_symbols_with_relations(&store).unwrap();
        let f_before = before.iter().find(|s| s.qualified_name == "f").unwrap();
        assert_eq!(f_before.calls, vec!["helper".to_string()]);

        write(
            tmp.path(),
            "x.py",
            "def helper():\n    pass\n\ndef f():\n    pass\n",
        );
        let embedder = HashedEmbedder::default();
        update_index(tmp.path(), &mut store, &embedder).unwrap();

        let after = load_symbols_with_relations(&store).unwrap();
        let f_after = after.iter().find(|s| s.qualified_name == "f").unwrap();
        assert!(f_after.calls.is_empty(), "{:?}", f_after.calls);
    }

    #[test]
    fn a_preexisting_index_with_no_relations_gets_backfilled_without_any_file_edit() {
        // Simulates upgrading from before this feature existed: symbols are
        // present (via `replace_file` with an empty relations slice, same
        // shape a pre-migration `replace_file` call always had) but
        // `symbol_relations` is completely empty. Found by review: without
        // a backfill, a file that never changes again would never get
        // relations, since `update_index`'s incremental logic only computes
        // them for reparsed (`to_parse`) files.
        let tmp = tempfile::tempdir().unwrap();
        let src = "def helper():\n    pass\n\ndef f():\n    helper()\n";
        write(tmp.path(), "x.py", src);

        let mut store = SqliteStore::open(&tmp.path().join(".oxide/index.db")).unwrap();
        let symbols = crate::parser::parse_file("x.py", src, crate::symbols::Language::Python);
        let hash = crate::symbols::content_hash(src);
        store.replace_file("x.py", hash, &symbols, &[]).unwrap();
        assert!(
            store.all_symbol_relations().unwrap().is_empty(),
            "precondition: simulated pre-migration index has symbols but no relations"
        );

        // No file edit — the backfill must run even though `x.py` is
        // reported unchanged by content_hash comparison.
        let embedder = HashedEmbedder::default();
        let report = update_index(tmp.path(), &mut store, &embedder).unwrap();
        assert_eq!(report.reparsed_files, 0, "x.py must not have been reparsed");

        let after = load_symbols_with_relations(&store).unwrap();
        let f = after.iter().find(|s| s.qualified_name == "f").unwrap();
        assert_eq!(f.calls, vec!["helper".to_string()], "{:?}", f.calls);
    }
}
