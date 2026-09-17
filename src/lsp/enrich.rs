//! Query-time integration: turns raw LSP responses into bounded, scoped
//! evidence `context.rs` folds into its candidate pool.
//!
//! **Scope-then-cap, not cap-then-scope.** `textDocument/references`,
//! `callHierarchy/incomingCalls`, and `textDocument/implementation` are all
//! repo-wide by construction — the same shape `RelationGraph::callers_of`
//! already has (AGENTS.md). The wire response is intersected with
//! `scope_files` *before* the per-seed item cap is applied. Capping first
//! would let a same-named symbol in an unrelated, high-ranked file crowd out
//! the in-scope hit that actually answers the query — exactly the case the
//! probe's `should_retry` fixture (a comment mentioning `should_retry(x, y)`
//! that a bare-name heuristic could false-positive on) is built to catch;
//! see `tests/lsp_scope_regression.rs`.
//!
//! Attribution — mapping a raw LSP location back to the OXIDE `Symbol` that
//! contains it — reuses `structural_relations::enclosing`, not a
//! reimplementation: its three tie-breaks were each found empirically
//! (AGENTS.md LANG-002).

use crate::lsp::client::{symbol_position, uri_to_repo_relative, LspClient};
use crate::structural_relations::enclosing;
use crate::symbols::Symbol;
use lsp_types::DiagnosticSeverity;
use std::collections::{HashMap, HashSet};
use std::path::Path;

/// One piece of LSP-sourced evidence: an OXIDE symbol plus the already
/// formatted reason tag (`lsp-caller←...`, `lsp-diagnostic(...): ...`, ...).
/// `context.rs` assigns the score fraction and calls `order_note` with this,
/// the same shape `blast_radius::compute`'s `(BlastItem, &Symbol)` pairs are
/// consumed in.
pub struct LspEvidence<'a> {
    pub symbol: &'a Symbol,
    pub reason: String,
    /// The seed this evidence was found from, when there is one (`None` for
    /// diagnostics, which are a property of a file rather than of a
    /// specific seed) — lets `context.rs` look up the seed's own score
    /// without re-parsing `reason`.
    pub via: Option<&'a str>,
}

fn severity_label(sev: Option<DiagnosticSeverity>) -> &'static str {
    match sev {
        Some(DiagnosticSeverity::ERROR) => "error",
        Some(DiagnosticSeverity::WARNING) => "warning",
        Some(DiagnosticSeverity::INFORMATION) => "info",
        Some(DiagnosticSeverity::HINT) => "hint",
        _ => "diagnostic",
    }
}

/// Scope-then-cap, not cap-then-scope: filters `raw` (repo-relative file,
/// 1-based line) pairs to those whose file is in `scope_files`, in the
/// order the LSP response listed them, THEN takes the first `cap`.
/// Capping before scoping can silently drop the in-scope hit an unbounded,
/// repo-wide LSP response (`textDocument/references`,
/// `callHierarchy/incomingCalls`, `textDocument/implementation`) buried
/// behind out-of-scope ones earlier in the list — see the module doc and
/// `tests/lsp_scope_regression.rs`.
fn scoped_and_capped(
    raw: &[(String, u32)],
    scope_files: &[String],
    cap: usize,
) -> Vec<(String, u32)> {
    raw.iter()
        .filter(|(rel, _)| scope_files.contains(rel))
        .take(cap)
        .cloned()
        .collect()
}

/// Truncate `s` to at most `max` chars, so a long diagnostic message can't
/// blow the reason-tag budget. Char-counted, not byte-sliced, so this never
/// panics on a multibyte boundary.
fn truncate_chars(s: &str, max: usize) -> String {
    if s.chars().count() <= max {
        return s.to_string();
    }
    let mut out: String = s.chars().take(max).collect();
    out.push('…');
    out
}

/// Enrich `seeds` (already ranked, highest first) with LSP evidence scoped
/// to `scope_files` and bounded by `max_seeds`/`per_seed`. `root` is the
/// repo root the client's file URIs are relative to; `symbols` is the full
/// corpus (needed for `enclosing()` attribution, same as
/// `RelationGraph::build`'s input).
///
/// Every LSP call here is best-effort: a timeout, an unsupported method, or
/// a malformed response for one seed drops that seed's evidence and moves
/// on — it never aborts the whole enrichment pass or propagates an error to
/// `context.rs`, matching "safe to fall back from."
pub fn enrich_seeds<'a>(
    client: &mut LspClient,
    root: &Path,
    symbols: &'a [Symbol],
    seeds: &[&'a Symbol],
    scope_files: &[String],
    max_seeds: usize,
    per_seed: usize,
) -> Vec<LspEvidence<'a>> {
    let mut by_file: HashMap<&str, Vec<&'a Symbol>> = HashMap::new();
    for s in symbols {
        by_file.entry(s.file.as_str()).or_default().push(s);
    }

    // Freshen the server's view of every file this session already knows
    // about (issue found in the hardening pass: didChange only covered the
    // current seed's own file, so a caller/reference target opened for an
    // earlier seed could go stale after an on-disk edit with nothing to
    // ever tell the server). Also open every file in this query's own
    // bounded scope — the caller/reference files most likely to matter
    // here — so a first-time cross-file reference isn't served from a
    // cold, never-opened view either. Both calls are `ensure_open`'s
    // existing content-hash no-op for anything already fresh; bounded by
    // `LSP_MAX_OPEN_DOCUMENTS` either way, never a repo-wide scan.
    client.resync_open_documents();
    for file in scope_files {
        let _ = client.ensure_open(file);
    }

    let mut out: Vec<LspEvidence<'a>> = Vec::new();
    let mut diagnosed_files: HashSet<&str> = HashSet::new();

    for seed in seeds.iter().take(max_seeds) {
        let Ok(text) = std::fs::read_to_string(root.join(&seed.file)) else {
            continue;
        };
        let Some(pos) = symbol_position(&text, seed.start_line, &seed.name) else {
            continue;
        };
        let Ok(uri) = client.ensure_open(&seed.file) else {
            continue;
        };

        // ---- callers: precise call sites via call hierarchy ---------------
        if let Ok(calls) = client.incoming_calls(&uri, pos) {
            let raw: Vec<(String, u32)> = calls
                .into_iter()
                .filter_map(|call| {
                    let rel = uri_to_repo_relative(root, &call.from.uri)?;
                    // LSP is 0-based, OXIDE is 1-based.
                    Some((rel, call.from.selection_range.start.line + 1))
                })
                .collect();
            for (rel, line) in scoped_and_capped(&raw, scope_files, per_seed) {
                let Some(file_syms) = by_file.get(rel.as_str()) else {
                    continue;
                };
                let Some(sym) = enclosing(file_syms, line) else {
                    continue;
                };
                if sym.id() == seed.id() {
                    continue;
                }
                out.push(LspEvidence {
                    symbol: sym,
                    reason: format!("lsp-caller←{}", seed.qualified_name),
                    via: Some(&seed.qualified_name),
                });
            }
        }

        // ---- references: every use, not just calls -------------------------
        if let Ok(refs) = client.references(&uri, pos) {
            let raw: Vec<(String, u32)> = refs
                .into_iter()
                .filter_map(|loc| {
                    let rel = uri_to_repo_relative(root, &loc.uri)?;
                    Some((rel, loc.range.start.line + 1))
                })
                .collect();
            for (rel, line) in scoped_and_capped(&raw, scope_files, per_seed) {
                let Some(file_syms) = by_file.get(rel.as_str()) else {
                    continue;
                };
                let Some(sym) = enclosing(file_syms, line) else {
                    continue;
                };
                if sym.id() == seed.id() {
                    continue;
                }
                out.push(LspEvidence {
                    symbol: sym,
                    reason: format!("lsp-reference←{}", seed.qualified_name),
                    via: Some(&seed.qualified_name),
                });
            }
        }

        // ---- implementations/subtypes of this type -------------------------
        if let Ok(impls) = client.implementations(&uri, pos) {
            let raw: Vec<(String, u32)> = impls
                .into_iter()
                .filter_map(|loc| {
                    let rel = uri_to_repo_relative(root, &loc.uri)?;
                    Some((rel, loc.range.start.line + 1))
                })
                .collect();
            for (rel, line) in scoped_and_capped(&raw, scope_files, per_seed) {
                let Some(file_syms) = by_file.get(rel.as_str()) else {
                    continue;
                };
                let Some(sym) = enclosing(file_syms, line) else {
                    continue;
                };
                if sym.id() == seed.id() {
                    continue;
                }
                out.push(LspEvidence {
                    symbol: sym,
                    reason: format!("lsp-implementation←{}", seed.qualified_name),
                    via: Some(&seed.qualified_name),
                });
            }
        }

        // ---- diagnostics on the seed's own file, once per file -------------
        if diagnosed_files.insert(seed.file.as_str()) {
            if let Ok(diags) = client.diagnostics(&uri) {
                for d in diags.into_iter().take(per_seed) {
                    let line = d.range.start.line + 1;
                    let Some(file_syms) = by_file.get(seed.file.as_str()) else {
                        continue;
                    };
                    let Some(sym) = enclosing(file_syms, line) else {
                        continue;
                    };
                    out.push(LspEvidence {
                        symbol: sym,
                        reason: format!(
                            "lsp-diagnostic({}): {}",
                            severity_label(d.severity),
                            truncate_chars(&d.message, 80)
                        ),
                        via: None,
                    });
                }
            }
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::symbols::{Language, SymbolKind};

    fn sym(file: &str, qname: &str, start: u32, end: u32) -> Symbol {
        Symbol {
            qualified_name: qname.into(),
            name: qname.rsplit('.').next().unwrap().to_string(),
            kind: SymbolKind::Function,
            language: Language::Python,
            file: file.into(),
            start_line: start,
            end_line: end,
            content_hash: 0,
            signature: String::new(),
            imports: vec![],
            exported: true,
            parent: None,
            references: vec![],
            calls: vec![],
            bases: vec![],
        }
    }

    /// The exact bug this function exists to prevent: many out-of-scope
    /// hits arrive before the one in-scope hit. A cap-then-scope
    /// implementation (`raw.take(cap).filter(in_scope)`) would see zero
    /// survivors here because the in-scope hit sits past the cap; scope-
    /// then-cap must still find it.
    #[test]
    fn scoped_and_capped_finds_an_in_scope_hit_buried_past_the_cap() {
        let mut raw: Vec<(String, u32)> =
            (0..10).map(|i| (format!("unrelated_{i}.py"), 1)).collect();
        raw.push(("seed_pool_file.py".to_string(), 5));
        let scope = vec!["seed_pool_file.py".to_string()];
        let result = scoped_and_capped(&raw, &scope, 3);
        assert_eq!(
            result,
            vec![("seed_pool_file.py".to_string(), 5)],
            "cap-then-scope would have dropped this: {result:?}"
        );
    }

    #[test]
    fn scoped_and_capped_still_bounds_in_scope_results() {
        let raw: Vec<(String, u32)> = (0..10).map(|i| ("hot.py".to_string(), i)).collect();
        let scope = vec!["hot.py".to_string()];
        let result = scoped_and_capped(&raw, &scope, 3);
        assert_eq!(result.len(), 3, "must still cap once scoped: {result:?}");
    }

    #[test]
    fn severity_label_maps_known_levels() {
        assert_eq!(severity_label(Some(DiagnosticSeverity::ERROR)), "error");
        assert_eq!(severity_label(Some(DiagnosticSeverity::HINT)), "hint");
        assert_eq!(severity_label(None), "diagnostic");
    }

    #[test]
    fn truncate_chars_never_panics_on_multibyte_boundaries() {
        let s = "café ".repeat(50);
        let t = truncate_chars(&s, 10);
        assert!(t.chars().count() <= 11); // 10 + the trailing ellipsis char
    }

    /// Attribution smoke test: two symbols in the same file, a location
    /// inside the second one's line range must resolve to it via
    /// `enclosing`, exercising the same by-file grouping `enrich_seeds`
    /// builds.
    #[test]
    fn enclosing_attribution_picks_the_containing_symbol() {
        let a = sym("src/a.py", "outer", 1, 5);
        let b = sym("src/a.py", "outer.inner", 6, 10);
        let refs = vec![&a, &b];
        let found = enclosing(&refs, 8).expect("line 8 is inside `inner`");
        assert_eq!(found.qualified_name, "outer.inner");
    }
}
