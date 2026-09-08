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
        for s in symbols {
            by_qualified.insert(s.qualified_name.as_str(), s);
            if let Some(p) = &s.parent {
                children_of.entry(p.as_str()).or_default().push(s);
            } else if s.kind != SymbolKind::Module {
                defs_by_name.entry(s.name.as_str()).or_default().push(s);
            }
            files.insert(s.file.as_str());
        }
        Self {
            symbols,
            by_qualified,
            children_of,
            defs_by_name,
            files,
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
        self.symbols
            .iter()
            .filter(move |s| s.file == target && s.kind != SymbolKind::Module)
            .collect()
    }

    /// Related tests: test-file symbols referencing the seed's bare name.
    pub fn related_tests(&self, seed: &Symbol) -> Vec<&'a Symbol> {
        self.symbols
            .iter()
            .filter(|s| is_test_symbol(s))
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
    ///   `test←` (from [`RelationGraph::related_tests`]).
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
        // References from this symbol to known definitions.
        for r in &seed.references {
            if let Some(defs) = self.defs_by_name.get(r.as_str()) {
                for d in defs {
                    if d.file != seed.file {
                        out.push(("uses".into(), *d));
                    }
                }
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

/// Map `./utils/token` (+ language extensions / __init__ / index) to a file
/// present in `files`. Returns None when ambiguous or missing.
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

    let candidates = [
        format!("{joined}.py"),
        format!("{joined}.pyi"),
        format!("{joined}.ts"),
        format!("{joined}.tsx"),
        format!("{joined}/__init__.py"),
        format!("{joined}/index.ts"),
        format!("{joined}/index.tsx"),
    ];
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
