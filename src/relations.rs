//! OXIDE structural-relation traversal over indexed symbols.

use crate::symbols::{Symbol, SymbolKind};
use rustc_hash::FxHashMap;
use std::borrow::Cow;
use std::collections::HashSet;
use std::sync::OnceLock;

/// Lifetime-free index over a symbol corpus: everything [`RelationGraph`]
/// needs to answer a query, keyed by the FNV-1a hash of the string it is
/// looked up by and holding `u32` positions into the corpus in corpus
/// order. Owning no borrows is what lets `oxide mcp` keep one per
/// `(index_id, index_generation)` next to the [`crate::retrieval::
/// SymbolSnapshot`] it was built from (`service/cache.rs::CachedSnapshot`) instead
/// of rebuilding it on every request, and it costs the one-shot path
/// nothing: building it is the same hashing work the borrowed maps did,
/// with no per-key `&str` and no owned `String` keys.
///
/// Hash keys are exact, not probabilistic: every read verifies the
/// candidate's actual field against the query string (`verified`), so a
/// collision can only cost a comparison, never a wrong neighbor. Positions
/// are pushed in corpus order and read back in that order, which is the
/// order `neighbors()` truncates on (`tests/determinism_stress.rs`).
#[derive(Clone)]
pub struct RelationIndex {
    /// The *last* symbol with that qualified name in corpus order — the
    /// value the old `HashMap::insert` kept — plus, on a hash collision
    /// only, the earlier positions the read must fall back to (a collision
    /// is a 64-bit event; the `Vec` is there so it costs a comparison, not
    /// a wrong answer).
    by_qualified: FxHashMap<u64, u32>,
    by_qualified_collisions: FxHashMap<u64, Vec<u32>>,
    children_of: FxHashMap<u64, Vec<u32>>,
    defs_by_name: FxHashMap<u64, Vec<u32>>,
    /// One symbol per indexed file (any kind), for `resolve_module`'s
    /// existence check — one position per *distinct path* under a key, so
    /// two paths colliding on the hash are both still found (Greptile
    /// review: keeping only the first would have denied the second).
    files: FxHashMap<u64, Vec<u32>>,
    /// Non-module symbols per file, in corpus order — `resolve_import`
    /// used to rescan every symbol per import, and a seed can have dozens.
    by_file: FxHashMap<u64, Vec<u32>>,
    /// `is_test_symbol` filtered once, in corpus order — `related_tests`
    /// used to lowercase every symbol's file and name per seed.
    test_symbols: Vec<u32>,
    /// Reverse indexes over `Symbol::calls`/`bases` (`structural_relations`
    /// — empty on every symbol unless that pass ran). Built lazily, not in
    /// `build()`, so the frozen path (`neighbors()`) pays nothing for
    /// these; `OnceLock` rather than `OnceCell` so a cached index can be
    /// shared across `oxide mcp`'s request threads.
    callers_of_index: OnceLock<FxHashMap<u64, Vec<u32>>>,
    implementors_of_index: OnceLock<FxHashMap<u64, Vec<u32>>>,
}

/// Hash key for a string: `FxHasher` over the bytes (word-at-a-time, the
/// same hasher the maps use; deterministic, unseeded). Every lookup
/// verifies the string, so the key only has to be fast, not collision-free.
fn key(s: &str) -> u64 {
    use std::hash::Hasher;
    let mut h = rustc_hash::FxHasher::default();
    h.write(s.as_bytes());
    h.finish()
}

/// High-confidence structural relations used for expansion: a corpus plus
/// the [`RelationIndex`] over it, either built here ([`Self::build`]) or
/// borrowed from a cache ([`Self::with_index`]).
pub struct RelationGraph<'a> {
    symbols: &'a [Symbol],
    index: Cow<'a, RelationIndex>,
}

/// `symbols::is_test_symbol` over a symbol — the one classification the
/// lean corpus loader also uses to decide whose `references` it keeps.
fn is_test_symbol(s: &Symbol, buf: &mut (String, String)) -> bool {
    crate::symbols::is_test_symbol(&s.file, &s.name, s.kind, buf)
}

impl RelationIndex {
    pub fn build(symbols: &[Symbol]) -> Self {
        let mut by_qualified: FxHashMap<u64, u32> = FxHashMap::default();
        let mut by_qualified_collisions: FxHashMap<u64, Vec<u32>> = FxHashMap::default();
        let mut children_of: FxHashMap<u64, Vec<u32>> = FxHashMap::default();
        let mut defs_by_name: FxHashMap<u64, Vec<u32>> = FxHashMap::default();
        let mut files: FxHashMap<u64, Vec<u32>> = FxHashMap::default();
        let mut by_file: FxHashMap<u64, Vec<u32>> = FxHashMap::default();
        let mut test_symbols = Vec::new();
        let mut lower = (String::new(), String::new());
        for (i, s) in symbols.iter().enumerate() {
            let i = i as u32;
            let k = key(&s.qualified_name);
            if let Some(prev) = by_qualified.insert(k, i) {
                if symbols[prev as usize].qualified_name != s.qualified_name {
                    by_qualified_collisions.entry(k).or_default().push(prev);
                }
            }
            if let Some(p) = &s.parent {
                children_of.entry(key(p)).or_default().push(i);
            } else if s.kind != SymbolKind::Module {
                defs_by_name.entry(key(&s.name)).or_default().push(i);
            }
            let seen = files.entry(key(&s.file)).or_default();
            if !seen.iter().any(|&j| symbols[j as usize].file == s.file) {
                seen.push(i);
            }
            if s.kind != SymbolKind::Module {
                by_file.entry(key(&s.file)).or_default().push(i);
            }
            if is_test_symbol(s, &mut lower) {
                test_symbols.push(i);
            }
        }
        Self {
            by_qualified,
            by_qualified_collisions,
            children_of,
            defs_by_name,
            files,
            by_file,
            test_symbols,
            callers_of_index: OnceLock::new(),
            implementors_of_index: OnceLock::new(),
        }
    }
}

impl<'a> RelationGraph<'a> {
    pub fn build(symbols: &'a [Symbol]) -> Self {
        Self {
            symbols,
            index: Cow::Owned(RelationIndex::build(symbols)),
        }
    }

    /// A graph over `symbols` using an index built earlier from **that same
    /// corpus** — the cached path. The caller owns the pairing: `service/cache.rs`
    /// builds the index inside the cache entry that holds the snapshot, so
    /// the two can only ever be read together at one generation.
    pub fn with_index(symbols: &'a [Symbol], index: &'a RelationIndex) -> Self {
        Self {
            symbols,
            index: Cow::Borrowed(index),
        }
    }

    /// The symbols behind `positions` whose `field` equals `want` — the
    /// verify-on-read step that makes a hash-keyed lookup exact.
    fn verified(
        &self,
        positions: Option<&Vec<u32>>,
        want: &str,
        field: impl Fn(&Symbol) -> &str,
    ) -> Vec<&'a Symbol> {
        positions
            .into_iter()
            .flatten()
            .map(|&i| &self.symbols[i as usize])
            .filter(|s| field(s) == want)
            .collect()
    }

    /// The last symbol in corpus order with this qualified name, exactly
    /// (verified), falling back to earlier positions only on a hash
    /// collision.
    fn by_qualified(&self, qualified_name: &str) -> Option<&'a Symbol> {
        let k = key(qualified_name);
        let last = self.index.by_qualified.get(&k)?;
        let s = &self.symbols[*last as usize];
        if s.qualified_name == qualified_name {
            return Some(s);
        }
        self.index
            .by_qualified_collisions
            .get(&k)
            .into_iter()
            .flatten()
            .rev()
            .map(|&i| &self.symbols[i as usize])
            .find(|s| s.qualified_name == qualified_name)
    }

    fn file_exists(&self, path: &str) -> bool {
        !self
            .verified(self.index.files.get(&key(path)), path, |s| &s.file)
            .is_empty()
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
        let index = self.index.callers_of_index.get_or_init(|| {
            let mut idx: FxHashMap<u64, Vec<u32>> = FxHashMap::default();
            for (i, s) in self.symbols.iter().enumerate() {
                for callee in &s.calls {
                    idx.entry(key(callee)).or_default().push(i as u32);
                }
            }
            idx
        });
        let mut out: Vec<&Symbol> = index
            .get(&key(name))
            .into_iter()
            .flatten()
            .map(|&i| &self.symbols[i as usize])
            .filter(|s| s.calls.iter().any(|c| c == name))
            .collect();
        out.sort_by(|a, b| (a.file.as_str(), a.start_line).cmp(&(b.file.as_str(), b.start_line)));
        out
    }

    /// AST-precise implementors of `base_name` (experimental) — symbols
    /// whose precomputed `bases` contains `base_name`. Same sort/repo-wide
    /// contract as `callers_of`.
    pub fn implementors_of(&self, base_name: &str) -> Vec<&'a Symbol> {
        let index = self.index.implementors_of_index.get_or_init(|| {
            let mut idx: FxHashMap<u64, Vec<u32>> = FxHashMap::default();
            for (i, s) in self.symbols.iter().enumerate() {
                for base in &s.bases {
                    idx.entry(key(base)).or_default().push(i as u32);
                }
            }
            idx
        });
        let mut out: Vec<&Symbol> = index
            .get(&key(base_name))
            .into_iter()
            .flatten()
            .map(|&i| &self.symbols[i as usize])
            .filter(|s| s.bases.iter().any(|b| b == base_name))
            .collect();
        out.sort_by(|a, b| (a.file.as_str(), a.start_line).cmp(&(b.file.as_str(), b.start_line)));
        out
    }

    /// Resolve an import string from a file to concrete symbols, when the
    /// target file exists in the indexed set.
    pub fn resolve_import<'b>(&'b self, from_file: &str, module: &str) -> Vec<&'a Symbol> {
        let Some(target) = resolve_module_with(module, from_file, &|p| self.file_exists(p)) else {
            return Vec::new();
        };
        self.verified(self.index.by_file.get(&key(&target)), &target, |s| &s.file)
    }

    /// Related tests: test-file symbols referencing the seed's bare name.
    pub fn related_tests(&self, seed: &Symbol) -> Vec<&'a Symbol> {
        self.index
            .test_symbols
            .iter()
            .map(|&i| &self.symbols[i as usize])
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
        // A lean-snapshot seed has no `imports` and (unless it is a test) no
        // `references`: it would silently lose its `uses` and
        // `imported-definition` neighbors. Callers complete seeds first
        // (`retrieval::complete_symbols`); this is the backstop.
        assert!(
            seed.is_complete(),
            "neighbors() called with a partial seed: {}#{}",
            seed.file,
            seed.qualified_name
        );
        let mut out: Vec<(String, &'a Symbol)> = Vec::new();
        if let Some(p) = &seed.parent {
            if let Some(parent_sym) = self.by_qualified(p) {
                out.push(("parent".into(), parent_sym));
            }
            for c in self.verified(self.index.children_of.get(&key(p)), p, |s| {
                s.parent.as_deref().unwrap_or("")
            }) {
                out.push(("sibling".into(), c));
            }
        }
        for c in self.verified(
            self.index.children_of.get(&key(&seed.qualified_name)),
            &seed.qualified_name,
            |s| s.parent.as_deref().unwrap_or(""),
        ) {
            out.push(("child".into(), c));
        }
        // Files this symbol's own file actually imports, resolved to indexed
        // paths — membership tests only, never iterated, so a HashSet here
        // cannot leak nondeterministic order into `out` (which is truncated
        // to 24 below; see tests/determinism_stress.rs).
        let imported_files: HashSet<String> = seed
            .imports
            .iter()
            .filter_map(|m| resolve_module_with(m, &seed.file, &|p| self.file_exists(p)))
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
            let defs = self.verified(self.index.defs_by_name.get(&key(r)), r, |s| &s.name);
            if defs.is_empty() {
                continue;
            }
            let cross_file = || defs.iter().filter(|d| d.file != seed.file);
            let any_backed = cross_file().any(|d| imported_files.contains(&d.file));
            for d in cross_file() {
                if any_backed && !imported_files.contains(&d.file) {
                    continue;
                }
                out.push(("uses".into(), *d));
            }
        }
        // Definitions imported by this file, narrowed to ones the seed
        // actually references by name -- otherwise importing a single name
        // from a file pulled in that file's entire unrelated symbol list
        // (EXPERIMENT: docs/evals/phase-4.2-typesafe).
        for m in &seed.imports {
            for d in self.resolve_import(&seed.file, m) {
                if seed.references.contains(&d.name) {
                    out.push(("imported-definition".into(), d));
                }
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
    resolve_module_with(module, from_file, &|p| files.contains(p))
}

/// [`resolve_module`] over any existence predicate — what [`RelationGraph`]
/// uses so the indexed file set never has to be materialized as a set of
/// borrowed strings.
pub fn resolve_module_with(
    module: &str,
    from_file: &str,
    exists: &dyn Fn(&str) -> bool,
) -> Option<String> {
    let norm = module.trim_start_matches("@/");
    // `.name`/`..pkg.name` (dots followed by a module path, no slash) is
    // Python's syntax and nobody else's, so its candidates are Python's
    // alone: a `store.ts`/`store.rb` next to a Python package must neither
    // satisfy `.store` when `store.py` is absent nor make it ambiguous when
    // present (Greptile review). A bare `.`/`..` stays language-agnostic —
    // TypeScript's `import x from '.'` names `index.ts` the same way.
    let mut python_only = false;
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
        // Python relative import with no path separator: `.store`,
        // `..pkg.util`, or a bare `.`/`..` (`from . import x`). A single
        // leading dot names the current package (0 levels up); each
        // additional dot climbs one level — the same contract `dir_of`
        // already gives Rust's `self::`/`super::` below, reused here rather
        // than duplicated. `from . import submodule` records only `.` as
        // the import string (the name after `import` is never captured by
        // `collect_meta`'s `import_from_statement` arm — see tags.rs), so
        // an empty `rest` resolves to the package's own `__init__.py`
        // rather than to the submodule. That's a pre-existing limitation of
        // what gets recorded, not something this fix introduces or is
        // responsible for closing.
        let dots = norm.chars().take_while(|&c| c == '.').count();
        let rest = &norm[dots..];
        let dir = dir_of(from_file, dots - 1)?;
        if rest.is_empty() {
            dir.trim_end_matches('/').to_string()
        } else {
            python_only = true;
            format!("{dir}{}", rest.replace('.', "/"))
        }
    } else {
        // Absolute python-style import: try as path anywhere.
        norm.replace('.', "/")
    };

    // Package/directory forms. A bare `.`/`..` that lands on the repo root
    // itself leaves `joined` empty, and `"/__init__.py"` would never match
    // an indexed path (none carries a leading slash) — so the separator is
    // only added when there is a directory to separate from.
    let dir_prefix = if joined.is_empty() {
        String::new()
    } else {
        format!("{joined}/")
    };
    let mut candidates = vec![
        format!("{joined}.py"),
        format!("{joined}.pyi"),
        format!("{dir_prefix}__init__.py"),
    ];
    if !python_only {
        candidates.extend([
            format!("{joined}.ts"),
            format!("{joined}.tsx"),
            // Ruby `require_relative './base'` is a real path, minus the
            // extension — the same shape TypeScript's `./base` already has.
            format!("{joined}.rb"),
            // The path as written, extension included — C's `#include
            // "util.h"` (recorded as `./util.h`) already names a file, so
            // appending a language extension to it could only miss.
            joined.clone(),
            format!("{dir_prefix}index.ts"),
            format!("{dir_prefix}index.tsx"),
        ]);
    }
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
    let matches: Vec<String> = candidates.into_iter().filter(|c| exists(c)).collect();
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
            completeness: Default::default(),
        }
    }

    #[test]
    fn cached_index_answers_exactly_like_a_freshly_built_graph() {
        // `oxide mcp` reuses one `RelationIndex` per index generation; every
        // relation method must answer identically through it, in the same
        // order, including the collision-verified hash lookups, duplicate
        // qualified names (last one wins for `parent`), children/siblings,
        // import-backed `uses` narrowing, and the lazy reverse indexes.
        let mut symbols = vec![
            sym("pkg/store.py", "TokenStore", &[], &[]),
            sym("pkg/other.py", "TokenStore", &[], &[]),
            sym(
                "pkg/handler.py",
                "handle",
                &[".store"],
                &["TokenStore", "refresh"],
            ),
            sym("tests/test_handler.py", "test_handle", &[], &["handle"]),
            sym("pkg/store.py", "TokenStore.refresh", &[], &[]),
            sym("pkg/store.py", "TokenStore.expire", &[], &[]),
        ];
        symbols[4].name = "refresh".into();
        symbols[4].parent = Some("TokenStore".into());
        symbols[4].kind = SymbolKind::Method;
        symbols[5].name = "expire".into();
        symbols[5].parent = Some("TokenStore".into());
        symbols[5].kind = SymbolKind::Method;
        symbols[5].calls = vec!["refresh".into()];
        symbols[2].calls = vec!["refresh".into()];
        symbols[1].bases = vec!["TokenStore".into()];
        let built = RelationGraph::build(&symbols);
        let index = RelationIndex::build(&symbols);
        let cached = RelationGraph::with_index(&symbols, &index);
        let ids = |v: Vec<&Symbol>| v.iter().map(|s| s.id()).collect::<Vec<_>>();
        for seed in &symbols {
            let a: Vec<(String, u64)> = built
                .neighbors(seed)
                .into_iter()
                .map(|(r, s)| (r, s.id()))
                .collect();
            let b: Vec<(String, u64)> = cached
                .neighbors(seed)
                .into_iter()
                .map(|(r, s)| (r, s.id()))
                .collect();
            assert_eq!(a, b, "neighbors of {}", seed.qualified_name);
            assert_eq!(
                ids(built.related_tests(seed)),
                ids(cached.related_tests(seed))
            );
            assert_eq!(
                ids(built.callers_of(&seed.name)),
                ids(cached.callers_of(&seed.name))
            );
            assert_eq!(
                ids(built.implementors_of(&seed.name)),
                ids(cached.implementors_of(&seed.name))
            );
            for m in &seed.imports {
                assert_eq!(
                    ids(built.resolve_import(&seed.file, m)),
                    ids(cached.resolve_import(&seed.file, m))
                );
            }
        }
        // The graph is not trivially empty: the handler sees its import-
        // backed definition and its test, and the reverse call index works.
        let n = cached.neighbors(&symbols[2]);
        assert!(n.iter().any(|(r, _)| r == "imported-definition"), "{n:?}");
        assert!(n.iter().any(|(r, _)| r == "test"), "{n:?}");
        assert_eq!(cached.callers_of("refresh").len(), 2);
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
    fn python_dotted_relative_imports_resolve_without_a_path_separator() {
        // Found in review: `resolve_module` only recognized TypeScript/Ruby-
        // style `./`/`../` relative paths. Python's own relative-import
        // syntax has no slash at all (`.store`, `..pkg.util`), so it fell
        // into the catch-all `starts_with('.') => None` arm and never
        // resolved — every `imported-definition` edge for a Python relative
        // import silently degraded to the weaker `uses` name-heuristic.
        let files: HashSet<&str> = [
            "pkg/sub/mod.py",
            "pkg/sub/sibling.py",
            "pkg/util.py",
            "pkg/__init__.py",
            "pkg/sub/__init__.py",
        ]
        .into_iter()
        .collect();
        // `from .sibling import X` — one dot is the current package.
        assert_eq!(
            resolve_module(".sibling", "pkg/sub/mod.py", &files),
            Some("pkg/sub/sibling.py".to_string())
        );
        // `from ..util import X` — each extra dot climbs one directory.
        assert_eq!(
            resolve_module("..util", "pkg/sub/mod.py", &files),
            Some("pkg/util.py".to_string())
        );
        // `from . import sibling` — bare dot names the package itself.
        assert_eq!(
            resolve_module(".", "pkg/sub/mod.py", &files),
            Some("pkg/sub/__init__.py".to_string())
        );
        // `from .. import x` — bare double dot names the parent package.
        assert_eq!(
            resolve_module("..", "pkg/sub/mod.py", &files),
            Some("pkg/__init__.py".to_string())
        );
        // A module that doesn't exist in the indexed set resolves to nothing.
        assert_eq!(resolve_module(".missing", "pkg/sub/mod.py", &files), None);
        // Climbing past the repo root resolves to nothing, matching Rust's
        // `super::super::super::` contract above.
        assert_eq!(resolve_module("....deep", "pkg/sub/mod.py", &files), None);
    }

    #[test]
    fn python_dotted_relative_import_is_ambiguous_when_both_forms_exist() {
        // A module file and a same-named package directory both matching is
        // the pre-existing "more than one candidate => None" contract every
        // other language already relies on (see `matches.len() == 1` at the
        // bottom of `resolve_module`) — this just proves the new Python arm
        // doesn't bypass it.
        let files: HashSet<&str> = ["pkg/sub/mod.py", "pkg/store.py", "pkg/store/__init__.py"]
            .into_iter()
            .collect();
        assert_eq!(resolve_module("..store", "pkg/sub/mod.py", &files), None);
    }

    #[test]
    fn bare_dot_import_resolves_at_the_repo_root() {
        // A bare `.`/`..` whose target directory is the repo root itself
        // (`from . import x` in a top-level `mod.py`, `from .. import x` one
        // level down, or TypeScript's `import x from '.'`) has an empty
        // directory prefix. Building the package candidate as
        // `format!("{joined}/__init__.py")` from that empty prefix produced
        // `/__init__.py` — a leading slash no indexed path ever carries — so
        // a root-level package could never be resolved. Non-root packages
        // were unaffected.
        let py: HashSet<&str> = ["mod.py", "__init__.py", "pkg/sub.py"]
            .into_iter()
            .collect();
        assert_eq!(
            resolve_module(".", "mod.py", &py),
            Some("__init__.py".to_string())
        );
        assert_eq!(
            resolve_module("..", "pkg/sub.py", &py),
            Some("__init__.py".to_string())
        );
        // Separate file set: a root holding both `__init__.py` and `index.ts`
        // is the pre-existing "more than one candidate => None" case, which
        // the language-agnostic candidate list has always had for `./x`
        // when both `x.py` and `x.ts` exist.
        let ts: HashSet<&str> = ["index.ts", "app.ts"].into_iter().collect();
        assert_eq!(
            resolve_module(".", "app.ts", &ts),
            Some("index.ts".to_string())
        );
    }

    #[test]
    fn python_dotted_relative_import_only_considers_python_candidates() {
        // Greptile review of the dot-relative branch: `.store` is Python
        // syntax, so a same-stem TypeScript/Ruby file next to the package
        // must neither satisfy it when `store.py` is absent (a false
        // `imported-definition` edge into the wrong language) nor make it
        // ambiguous when `store.py` is present (a false negative).
        let no_py: HashSet<&str> = ["pkg/mod.py", "pkg/store.ts", "pkg/store.rb"]
            .into_iter()
            .collect();
        assert_eq!(resolve_module(".store", "pkg/mod.py", &no_py), None);
        let both: HashSet<&str> = ["pkg/mod.py", "pkg/store.py", "pkg/store.ts"]
            .into_iter()
            .collect();
        assert_eq!(
            resolve_module(".store", "pkg/mod.py", &both),
            Some("pkg/store.py".to_string())
        );
        // A bare dot is shared with TypeScript and keeps both forms.
        let ts: HashSet<&str> = ["pkg/app.ts", "pkg/index.ts"].into_iter().collect();
        assert_eq!(
            resolve_module(".", "pkg/app.ts", &ts),
            Some("pkg/index.ts".to_string())
        );
    }

    #[test]
    fn python_relative_import_now_produces_imported_definition_and_narrows_uses() {
        // The fix has a two-sided consequence: `.store` didn't just start
        // resolving to a file, it also started feeding `neighbors()`'s
        // `imported_files` set for Python — so the `uses` narrowing that
        // `an_imported_definition_wins_over_a_same_named_one_elsewhere`
        // already pins for TypeScript's `./a` now applies to Python's `.store`
        // too, where before this fix it always fell into the "no import
        // backing" branch below and kept every same-named candidate.
        let symbols = vec![
            sym("pkg/store.py", "TokenStore", &[], &[]),
            sym("pkg/other.py", "TokenStore", &[], &[]),
            sym("pkg/handler.py", "handle", &[".store"], &["TokenStore"]),
        ];
        let graph = RelationGraph::build(&symbols);
        let neighbors = graph.neighbors(&symbols[2]);
        let uses: Vec<&str> = neighbors
            .iter()
            .filter(|(tag, _)| tag == "uses")
            .map(|(_, s)| s.file.as_str())
            .collect();
        assert_eq!(
            uses,
            vec!["pkg/store.py"],
            "pkg/other.py must be dropped now the relative import is import-backed"
        );
        let imported: Vec<&str> = neighbors
            .iter()
            .filter(|(tag, _)| tag == "imported-definition")
            .map(|(_, s)| s.file.as_str())
            .collect();
        assert_eq!(imported, vec!["pkg/store.py"]);
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

    #[test]
    fn imported_definition_is_narrowed_to_symbols_the_seed_actually_references() {
        // Importing one name from a file must not pull in that file's whole
        // unrelated symbol list -- only definitions the seed actually names
        // in its own `references` count as `imported-definition`.
        let symbols = vec![
            sym("src/a.ts", "Foo", &[], &[]),
            sym("src/a.ts", "Bar", &[], &[]),
            sym("src/c.ts", "useIt", &["./a"], &["Foo"]),
        ];
        let graph = RelationGraph::build(&symbols);
        let imported: Vec<&str> = graph
            .neighbors(&symbols[2])
            .into_iter()
            .filter(|(tag, _)| tag == "imported-definition")
            .map(|(_, s)| s.name.as_str())
            .collect();
        assert_eq!(
            imported,
            vec!["Foo"],
            "Bar lives in the same imported file but is never referenced by \
             useIt, and must not be pulled in"
        );
    }

    #[test]
    fn imported_definition_is_empty_when_the_seed_references_nothing_from_it() {
        let symbols = vec![
            sym("src/a.ts", "Foo", &[], &[]),
            sym("src/c.ts", "useIt", &["./a"], &[]),
        ];
        let graph = RelationGraph::build(&symbols);
        let imported = graph
            .neighbors(&symbols[1])
            .into_iter()
            .filter(|(tag, _)| tag == "imported-definition")
            .count();
        assert_eq!(imported, 0);
    }

    #[test]
    fn an_aliased_import_is_a_known_gap_shared_with_uses_not_new_here() {
        // Greptile review finding on this fix: `import { Foo as Bar } from
        // './a'` -- the importing file's body only ever mentions "Bar", but
        // `d.name` is the symbol's own declared name "Foo", so the reference
        // check can't match. Confirmed here as a *pre-existing* limitation
        // of bare-name matching (no `Symbol` field records an alias
        // mapping), not a regression this fix introduces: `uses<-`, built on
        // the exact same `defs_by_name`-keyed-by-declared-name lookup, has
        // never resolved this either. Neither relation fires for "Bar".
        let symbols = vec![
            sym("src/a.ts", "Foo", &[], &[]),
            sym("src/c.ts", "useIt", &["./a"], &["Bar"]),
        ];
        let graph = RelationGraph::build(&symbols);
        let neighbors = graph.neighbors(&symbols[1]);
        assert!(
            neighbors
                .iter()
                .all(|(tag, _)| tag != "imported-definition" && tag != "uses"),
            "{neighbors:?}"
        );
    }
}
