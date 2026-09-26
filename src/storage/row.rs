//! Turning `symbols` rows into [`Symbol`]s: the column decoders, the corpus
//! statements and the corpus loader built on them. Query-plan pinned:
//! `tests/query_plans.rs` checks [`CORPUS_SQL`] and [`LEAN_CORPUS_SQL`].

use super::sqlite::SqliteStore;
use crate::symbols::{is_test_symbol, Completeness, Language, Symbol};
use anyhow::Result;

pub(super) fn row_to_symbol(r: &rusqlite::Row<'_>) -> rusqlite::Result<Symbol> {
    let mut s = row_to_symbol_without_imports(r, None)?;
    s.imports = serde_json::from_str(r.get_ref(9)?.as_str()?).unwrap_or_default();
    Ok(s)
}

/// [`row_to_symbol`] with `imports` left empty, for a caller that fills it
/// from its own per-file cache (`all_symbols`). Text columns that are only
/// parsed, never kept (`kind`, `language`, `references_json`), are read by
/// reference straight off the row buffer instead of through an owned
/// `String` first.
fn row_to_symbol_without_imports(
    r: &rusqlite::Row<'_>,
    lean: Option<&mut (String, String)>,
) -> rusqlite::Result<Symbol> {
    let mut s = Symbol {
        file: r.get(0)?,
        qualified_name: r.get(1)?,
        name: r.get(2)?,
        kind: r
            .get_ref(3)?
            .as_str()?
            .parse()
            .unwrap_or(crate::symbols::SymbolKind::Function),
        // Parsed via `Language::ALL`, not a second hand-written match —
        // see `Language::from_str`. The fallback only covers a value this
        // build has no variant for at all.
        language: r
            .get_ref(4)?
            .as_str()?
            .parse()
            .unwrap_or(Language::TypeScript),
        start_line: r.get(5)?,
        end_line: r.get(6)?,
        content_hash: r.get::<_, i64>(7)? as u64,
        signature: r.get(8)?,
        imports: Vec::new(),
        exported: r.get::<_, i64>(10)? != 0,
        parent: r.get(11)?,
        references: Vec::new(),
        // Not columns on `symbols` — populated separately by
        // `structural_relations::load_symbols_with_relations` from the
        // side table `symbol_relations`, never by this loader.
        calls: Vec::new(),
        bases: Vec::new(),
        completeness: Completeness::Complete,
    };
    // Lean (`all_symbols_lean`): `references` only for test symbols, the
    // one non-seed read `RelationGraph` makes (`related_tests`).
    let keep_refs = match lean {
        None => true,
        Some(buf) => {
            s.completeness = Completeness::Partial;
            is_test_symbol(&s.file, &s.name, s.kind, buf)
        }
    };
    if keep_refs {
        s.references = serde_json::from_str(r.get_ref(12)?.as_str()?).unwrap_or_default();
    }
    Ok(s)
}

/// The corpus load's statement ([`IndexBackend::all_symbols`]).
///
/// [`IndexBackend::all_symbols`]: crate::storage::IndexBackend::all_symbols
pub const CORPUS_SQL: &str =
    "SELECT file, qualified_name, name, kind, language, start_line, end_line,
            content_hash, signature, imports_json, exported, parent, references_json
     FROM symbols ORDER BY file, start_line";
/// [`CORPUS_SQL`] without `imports_json` ([`IndexBackend::all_symbols_lean`]).
///
/// [`IndexBackend::all_symbols_lean`]: crate::storage::IndexBackend::all_symbols_lean
pub const LEAN_CORPUS_SQL: &str =
    "SELECT file, qualified_name, name, kind, language, start_line, end_line,
            content_hash, signature, NULL, exported, parent, references_json
     FROM symbols ORDER BY file, start_line";

impl SqliteStore {
    /// [`IndexBackend::all_symbols`] (`lean = false`) and
    /// [`IndexBackend::all_symbols_lean`]: one statement shape, one decoder.
    ///
    /// [`IndexBackend::all_symbols`]: crate::storage::IndexBackend::all_symbols
    /// [`IndexBackend::all_symbols_lean`]: crate::storage::IndexBackend::all_symbols_lean
    pub(super) fn load_corpus(&self, lean: bool) -> Result<Vec<Symbol>> {
        // The `ORDER BY` stays in SQL. Measured on a 7.8k-symbol index
        // (docs/retrieval-profile/README.md): the ordered scan is no slower
        // than a rowid scan plus a Rust sort — the time that *looks* like
        // sorting in a step-only probe is SQLite reading each ~1 KB row's
        // overflow pages, which a plain scan merely defers to column
        // access — and the ties in `(file, start_line)` come out in index
        // order `(file, rowid)`, which the sort would have had to
        // reproduce.
        //
        // `imports_json` is file-level and identical for every symbol in a
        // file, and the rows of one file are adjacent in this order, so the
        // parsed list is reused while the raw text repeats instead of being
        // parsed once per symbol.
        //
        // The lean statement reads `NULL` in `imports_json`'s place (same
        // column positions, same decoder) and never parses it; its plan is
        // pinned next to the full one in `tests/query_plans.rs`.
        let mut stmt = self
            .conn
            .prepare(if lean { LEAN_CORPUS_SQL } else { CORPUS_SQL })?;
        let mut rows = stmt.query([])?;
        let mut out: Vec<Symbol> = Vec::new();
        let mut last_imports: (String, Vec<String>) = (String::new(), Vec::new());
        let mut buf = (String::new(), String::new());
        while let Some(r) = rows.next()? {
            if lean {
                out.push(row_to_symbol_without_imports(r, Some(&mut buf))?);
                continue;
            }
            let imports_json = r.get_ref(9)?.as_str()?;
            if imports_json != last_imports.0 {
                last_imports = (
                    imports_json.to_string(),
                    serde_json::from_str(imports_json).unwrap_or_default(),
                );
            }
            let mut s = row_to_symbol_without_imports(r, None)?;
            s.imports = last_imports.1.clone();
            out.push(s);
        }
        Ok(out)
    }
}
