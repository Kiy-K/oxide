//! OXIDE structural-relation traversal over indexed symbols.

use crate::symbols::{Symbol, SymbolKind};
use std::cell::OnceCell;
use std::collections::{HashMap, HashSet};

/// High-confidence structural relations used for expansion.
pub struct RelationGraph<'a> {
    symbols: &'a [Symbol],
    by_qualified: HashMap<&'a str, &'a Symbol>,
    children_of: HashMap<&'a str, Vec<&'a Symbol>>,
    defs_by_name: HashMap<&'a str, Vec<&'a Symbol>>,
    files: HashSet<&'a str>,
    /// Non-module symbols per file, in corpus order — `resolve_import`
    /// used to rescan every symbol per import, and a seed can have dozens.
    by_file: HashMap<&'a str, Vec<&'a Symbol>>,
    /// `is_test_symbol` filtered once, in corpus order — `related_tests`
    /// used to lowercase every symbol's file and name per seed.
    test_symbols: Vec<&'a Symbol>,
    /// Reverse indexes over `Symbol::calls`/`bases` (experimental,
    /// `structural_relations` — empty on every symbol unless that module's
    /// opt-in second pass ran). Built lazily via `OnceCell`, not in
    /// `build()`, so the frozen path (`neighbors()`, called on every
    /// `RelationGraph::build()` in `context.rs`/`retrieval.rs`/`review.rs`)
    /// pays nothing for these — they're only populated the first time
    /// `callers_of`/`implementors_of` is actually called, which no
    /// production code path does.
    callers_of_index: OnceCell<HashMap<&'a str, Vec<&'a Symbol>>>,
    implementors_of_index: OnceCell<HashMap<&'a str, Vec<&'a Symbol>>>,
}

fn is_test_symbol(s: &Symbol) -> bool {
    let f = s.file.to_lowercase();
    let n = s.name.to_lowercase();
    f.starts_with("test_")
        || f.contains("_test.")
        || f.contains(".test.")
        || f.contains(".spec.")
        || f.contains("/tests/")
        || f.contains("\\tests\\")
        || n.starts_with("test_")
        || n.ends_with("_test")
        || n.ends_with("test") && (matches!(s.kind, SymbolKind::Function | SymbolKind::Method))
}

impl<'a> RelationGraph<'a> {
    pub fn build(symbols: &'a [Symbol]) -> Self {
        let mut by_qualified = HashMap::new();
        let mut children_of: HashMap<&str, Vec<&Symbol>> = HashMap::new();
        let mut defs_by_name: HashMap<&str, Vec<&Symbol>> = HashMap::new();
        let mut files = HashSet::new();
        let mut by_file: HashMap<&str, Vec<&Symbol>> = HashMap::new();
        let mut test_symbols = Vec::new();
        for s in symbols {
            by_qualified.insert(s.qualified_name.as_str(), s);
            if let Some(p) = &s.parent {
                children_of.entry(p.as_str()).or_default().push(s);
            } else if s.kind != SymbolKind::Module {
                defs_by_name.entry(s.name.as_str()).or_default().push(s);
            }
            files.insert(s.file.as_str());
            if s.kind != SymbolKind::Module {
                by_file.entry(s.file.as_str()).or_default().push(s);
            }
            if is_test_symbol(s) {
                test_symbols.push(s);
            }
        }
        Self {
            symbols,
            by_qualified,
            children_of,
            defs_by_name,
            files,
            by_file,
            test_symbols,
            callers_of_index: OnceCell::new(),
            implementors_of_index: OnceCell::new(),
        }
    }

    /// AST-precise callers of `name` (experimental, see `structural_relations`):
    /// symbols whose precomputed `calls` contains `name`, sorted `(file,
    /// start_line)` for the same reason `tree_sitter_structural.rs::finish`
    /// sorts its hits — a caller like `context.rs` truncating to the first N
    /// must see a deterministic order, not `HashMap` iteration order
    /// (`tests/determinism_stress.rs` exists for exactly this class of bug).
    /// Repo-wide, unlike `find_callers`'s bounded-file-list query-time
    /// contract — see docs/precomputed-structural-relations/README.md for
    /// why that's the actual axis this experiment had to measure.
    pub fn callers_of(&self, name: &str) -> Vec<&'a Symbol> {
        let index = self.callers_of_index.get_or_init(|| {
            let mut idx: HashMap<&str, Vec<&Symbol>> = HashMap::new();
            for s in self.symbols {
                for callee in &s.calls {
                    idx.entry(callee.as_str()).or_default().push(s);
                }
            }
            idx
        });
        let mut out: Vec<&Symbol> = index.get(name).cloned().unwrap_or_default();
        out.sort_by(|a, b| (a.file.as_str(), a.start_line).cmp(&(b.file.as_str(), b.start_line)));
        out
    }

    /// AST-precise implementors of `base_name` (experimental) — symbols
    /// whose precomputed `bases` contains `base_name`. Same sort/repo-wide
    /// contract as `callers_of`.
    pub fn implementors_of(&self, base_name: &str) -> Vec<&'a Symbol> {
        let index = self.implementors_of_index.get_or_init(|| {
            let mut idx: HashMap<&str, Vec<&Symbol>> = HashMap::new();
            for s in self.symbols {
                for base in &s.bases {
                    idx.entry(base.as_str()).or_default().push(s);
                }
            }
            idx
        });
        let mut out: Vec<&Symbol> = index.get(base_name).cloned().unwrap_or_default();
        out.sort_by(|a, b| (a.file.as_str(), a.start_line).cmp(&(b.file.as_str(), b.start_line)));
        out
    }

    /// Resolve an import string from a file to concrete symbols, when the
    /// target file exists in the indexed set.
    pub fn resolve_import<'b>(&'b self, from_file: &str, module: &str) -> Vec<&'a Symbol> {
        let Some(target) = resolve_module(module, from_file, &self.files) else {
            return Vec::new();
        };
        self.by_file
            .get(target.as_str())
            .cloned()
            .unwrap_or_default()
    }

    /// Related tests: test-file symbols referencing the seed's bare name.
    pub fn related_tests(&self, seed: &Symbol) -> Vec<&'a Symbol> {
        self.test_symbols
            .iter()
            .copied()
            .filter(|t| t.references.iter().any(|r| r == &seed.name) || t.name.contains(&seed.name))
            .collect()
    }

    /// Provenance audit (Phase 1.1, deliberately not a type): every `reasons`
    /// tag this engine emits falls into one of three confidence tiers. This
    /// is documentation for a future structural-relationship phase to hook
    /// into, not a data model change — no `Provenance` enum exists because
    /// nothing here is currently ambiguous enough to need one.
    ///
    /// - **Direct** — the query matched this symbol itself: `lexical=`,
    ///   `semantic=` tags from [`RetrievalEngine::search`].
    /// - **Resolved** — a relation backed by parsed structure or a concrete
    ///   file match, with ambiguous cases dropped rather than guessed:
    ///   `parent←`/`child←`/`sibling←` (from the parser's own `Symbol.parent`
    ///   field) and `imported-definition←` (import string resolved to one
    ///   unambiguous indexed file via [`resolve_module`]; see its doc comment
    ///   and the README's "Import resolution" note).
    /// - **Heuristic** — identifier-name intersection with no scope analysis
    ///   (see `# ponytail` note in `index.rs::extract_references`), so two
    ///   unrelated symbols sharing a name can produce a false link:
    ///   `uses←` (this symbol references a same-named definition) and
    ///   `test←` (from [`RelationGraph::related_tests`]). `uses←` is
    ///   narrowed, not promoted: when some candidate sits in a file the
    ///   seed's file imports, non-imported same-named definitions are
    ///   dropped. That is still name matching — it just stops offering
    ///   candidates the file demonstrably never pulled in.
    pub fn neighbors(&self, seed: &Symbol) -> Vec<(String, &'a Symbol)> {
        let mut out: Vec<(String, &'a Symbol)> = Vec::new();
        if let Some(p) = &seed.parent {
            if let Some(parent_sym) = self.by_qualified.get(p.as_str()) {
                out.push(("parent".into(), *parent_sym));
            }
            for c in self.children_of.get(p.as_str()).into_iter().flatten() {
                out.push(("sibling".into(), *c));
            }
        }
        for c in self
            .children_of
            .get(seed.qualified_name.as_str())
            .into_iter()
            .flatten()
        {
            out.push(("child".into(), *c));
        }
        // Files this symbol's own file actually imports, resolved to indexed
        // paths — membership tests only, never iterated, so a HashSet here
        // cannot leak nondeterministic order into `out` (which is truncated
        // to 24 below; see tests/determinism_stress.rs).
        let imported_files: HashSet<String> = seed
            .imports
            .iter()
            .filter_map(|m| resolve_module(m, &seed.file, &self.files))
            .collect();
        // References from this symbol to known definitions. When any
        // candidate lives in a file this one imports, the ones that don't
        // are dropped: `uses` is the weakest tier (bare identifier-name
        // intersection, no scope analysis), and an import is real syntactic
        // evidence of which same-named definition was meant. With no
        // import-backed candidate at all — a repo-internal helper reached
        // without an import statement, or an unresolvable module string —
        // the old fan-out is kept rather than dropping the relation.
        for r in &seed.references {
            let Some(defs) = self.defs_by_name.get(r.as_str()) else {
                continue;
            };
            let cross_file = || defs.iter().filter(|d| d.file != seed.file);
            let any_backed = cross_file().any(|d| imported_files.contains(&d.file));
            for d in cross_file() {
                if any_backed && !imported_files.contains(&d.file) {
                    continue;
                }
                out.push(("uses".into(), *d));
            }
        }
        // Definitions imported by this file.
        for m in &seed.imports {
            for d in self.resolve_import(&seed.file, m) {
                out.push(("imported-definition".into(), d));
            }
        }
        // Related tests.
        for t in self.related_tests(seed) {
            out.push(("test".into(), t));
        }
        out.truncate(24);
        out
    }
}

/// Directory of `file` with `ups` extra levels stripped, as a slash-suffixed
/// prefix (empty string at the repo root). `None` when `ups` would climb
/// past the root, which means the import cannot be resolved at all.
fn dir_of(file: &str, ups: usize) -> Option<String> {
    let mut parts: Vec<&str> = file.split('/').collect();
    parts.pop()?; // drop the file name
    if ups > parts.len() {
        return None;
    }
    for _ in 0..ups {
        parts.pop();
    }
    let mut p = parts.join("/");
    if !p.is_empty() {
        p.push('/');
    }
    Some(p)
}

/// Map `./utils/token` (+ language extensions / `__init__` / `index` / Rust
/// `::` paths) to a file present in `files`. Returns None when ambiguous or
/// missing.
///
/// Go is deliberately unresolvable here: a Go import names a *package
/// directory* holding many files, not one file, and most imports are
/// module-qualified (`github.com/…`) or stdlib. Since this function's whole
/// contract is "exactly one unambiguous file", Go imports are recorded on
/// the symbol but never produce an `imported-definition` edge — a known gap
/// listed in `docs/language-support/README.md`, not an accident.
pub fn resolve_module(module: &str, from_file: &str, files: &HashSet<&str>) -> Option<String> {
    let norm = module.trim_start_matches("@/");
    let joined = if let Some(rest) = norm.strip_prefix("./").or_else(|| norm.strip_prefix("../")) {
        let ups = norm.matches("../").count();
        let mut parts: std::collections::VecDeque<&str> = from_file.split('/').collect();
        parts.pop_back(); // drop file name
        for _ in 0..ups.min(parts.len()) {
            parts.pop_back();
        }
        let mut p = parts.into_iter().collect::<Vec<_>>().join("/");
        if !p.is_empty() {
            p.push('/');
        }
        format!("{p}{rest}")
    } else if norm.starts_with('.') {
        return None;
    } else {
        // Absolute python-style import: try as path anywhere.
        norm.replace('.', "/")
    };

    let mut candidates = vec![
        format!("{joined}.py"),
        format!("{joined}.pyi"),
        format!("{joined}.ts"),
        format!("{joined}.tsx"),
        format!("{joined}/__init__.py"),
        format!("{joined}/index.ts"),
        format!("{joined}/index.tsx"),
    ];
    // Rust `use` trees are `::`-separated and normally end in the *item*
    // name, not the module: `crate::backend::Backend` names `backend`. Try
    // the path with and without its last segment, at the repo root and under
    // a `src/` layout, as both `X.rs` and `X/mod.rs`. Extra candidates are
    // safe: more than one match still resolves to None below, so a wrong
    // guess degrades to no edge rather than a false one.
    if module.contains("::") {
        let path = norm.replace("::", "/");
        // `self::x` is relative to the current file's own directory and
        // `super::x` climbs one level per `super`, exactly like `./` and
        // `../` — resolving either from the crate root probed a file that
        // has nothing to do with the import.
        // A relative path is already anchored to one directory; only a
        // crate-root path needs the `src/` layout guess. `None` means the
        // path is relative but climbs past the repo root — unresolvable, so
        // it contributes no candidates rather than falling back to a
        // crate-root reading of the same text.
        let (prefixes, rest) = if let Some(rest) = path.strip_prefix("self/") {
            (dir_of(from_file, 0).map(|d| vec![d]), rest.to_string())
        } else if path.starts_with("super/") {
            let mut ups = 0usize;
            let mut rest = path.as_str();
            while let Some(next) = rest.strip_prefix("super/") {
                ups += 1;
                rest = next;
            }
            (dir_of(from_file, ups).map(|d| vec![d]), rest.to_string())
        } else {
            (
                Some(vec![String::new(), "src/".to_string()]),
                path.trim_start_matches("crate/").to_string(),
            )
        };
        if let Some(prefixes) = prefixes {
            let mut stems = vec![rest.clone()];
            if let Some((head, _)) = rest.rsplit_once('/') {
                stems.push(head.to_string());
            }
            for stem in stems {
                for prefix in &prefixes {
                    for suffix in [".rs", "/mod.rs"] {
                        candidates.push(format!("{prefix}{stem}{suffix}"));
                    }
                }
            }
        }
    }
    let matches: Vec<String> = candidates
        .into_iter()
        .filter(|c| files.contains(c.as_str()))
        .collect();
    if matches.len() == 1 {
        Some(matches.into_iter().next().unwrap())
    } else {
        None
    }
}

#[cfg(test)]
mod uses_narrowing_tests {
    use super::*;
    use crate::symbols::{Language, SymbolKind};

    fn sym(file: &str, name: &str, imports: &[&str], references: &[&str]) -> Symbol {
        Symbol {
            qualified_name: name.into(),
            name: name.into(),
            kind: SymbolKind::Class,
            language: Language::TypeScript,
            file: file.into(),
            start_line: 1,
            end_line: 2,
            content_hash: 0,
            signature: String::new(),
            imports: imports.iter().map(|s| s.to_string()).collect(),
            exported: true,
            parent: None,
            references: references.iter().map(|s| s.to_string()).collect(),
            calls: Vec::new(),
            bases: Vec::new(),
        }
    }

    #[test]
    fn rust_use_paths_resolve_to_module_files() {
        let files: HashSet<&str> = ["src/backend.rs", "src/net/mod.rs", "src/main.rs"]
            .into_iter()
            .collect();
        // The trailing segment is the imported item, not a module.
        assert_eq!(
            resolve_module("crate::backend::Backend", "src/main.rs", &files),
            Some("src/backend.rs".to_string())
        );
        // `X/mod.rs` layout, and a path that is already the module.
        assert_eq!(
            resolve_module("crate::net", "src/main.rs", &files),
            Some("src/net/mod.rs".to_string())
        );
        // An external crate resolves to nothing rather than to something wrong.
        assert_eq!(
            resolve_module("std::collections::HashMap", "src/main.rs", &files),
            None
        );
    }

    #[test]
    fn rust_self_and_super_paths_resolve_relative_to_the_importing_file() {
        // Found by review: `self`/`super` were stripped and the remainder
        // probed from the crate root, so `super::baz::Thing` in
        // `src/foo/bar.rs` looked for `src/baz.rs` instead of `src/foo/../
        // baz.rs` — a different file entirely, or none.
        let files: HashSet<&str> = [
            "src/foo/bar.rs",
            "src/foo/sib.rs",
            "src/baz.rs",
            "src/foo/baz.rs",
        ]
        .into_iter()
        .collect();
        assert_eq!(
            resolve_module("super::baz::Thing", "src/foo/bar.rs", &files),
            Some("src/baz.rs".to_string())
        );
        assert_eq!(
            resolve_module("self::sib::Thing", "src/foo/bar.rs", &files),
            Some("src/foo/sib.rs".to_string())
        );
        // Climbing past the root resolves to nothing, not to a crate-root read.
        assert_eq!(
            resolve_module("super::super::super::baz", "src/foo/bar.rs", &files),
            None
        );
    }

    #[test]
    fn an_imported_definition_wins_over_a_same_named_one_elsewhere() {
        let symbols = vec![
            sym("src/a.ts", "Client", &[], &[]),
            sym("src/b.ts", "Client", &[], &[]),
            sym("src/c.ts", "useIt", &["./a"], &["Client"]),
        ];
        let graph = RelationGraph::build(&symbols);
        let uses: Vec<&str> = graph
            .neighbors(&symbols[2])
            .into_iter()
            .filter(|(tag, _)| tag == "uses")
            .map(|(_, s)| s.file.as_str())
            .collect();
        assert_eq!(uses, vec!["src/a.ts"], "b.ts is never imported by c.ts");
    }

    #[test]
    fn with_no_import_backing_the_old_fan_out_is_kept() {
        // Dropping the relation entirely would lose a real signal for repos
        // where the reference isn't reached through an import statement.
        let symbols = vec![
            sym("src/a.ts", "Client", &[], &[]),
            sym("src/b.ts", "Client", &[], &[]),
            sym("src/c.ts", "useIt", &[], &["Client"]),
        ];
        let graph = RelationGraph::build(&symbols);
        let uses = graph
            .neighbors(&symbols[2])
            .into_iter()
            .filter(|(tag, _)| tag == "uses")
            .count();
        assert_eq!(uses, 2);
    }
}
