//! The whole-corpus [`SymbolSnapshot`] and its completeness policy: lean
//! (partial) loads when BM25 is served from persisted postings, complete
//! loads when it must fall back to the in-memory index
//! ([`lexical_persisted`]), and [`complete_symbols`], which every partial
//! symbol goes through before it becomes a seed or leaves as output.

use crate::storage::IndexBackend;
use crate::symbols::{Completeness, Symbol};
use std::collections::HashMap;

/// Every indexed symbol, with `calls`/`bases` merged in from the relations
/// side table, plus an id index. Lean
/// ([`IndexBackend::all_symbols_lean`]): every symbol is
/// [`Completeness::Partial`] — `imports` empty, `references` only on test
/// symbols — which is all `RelationGraph` reads of a non-seed symbol. A
/// symbol that becomes a seed or leaves as output goes through
/// [`complete_symbols`] first; `neighbors()` and serialization refuse a
/// partial one. The one O(N) load retrieval still has,
/// and it is only paid when something genuinely needs the whole corpus:
/// structural expansion (`RelationGraph` answers `related_tests` by
/// scanning every symbol, so it cannot be built from a candidate subset)
/// or the in-memory lexical fallback. A long-lived process (`oxide mcp`)
/// loads one per `(index_id, index_generation)` and hands it to every
/// request's engine via
/// [`RetrievalEngine::with_snapshot`](super::RetrievalEngine::with_snapshot).
#[derive(Clone)]
pub struct SymbolSnapshot {
    pub symbols: Vec<Symbol>,
    by_id: rustc_hash::FxHashMap<u64, usize>,
    /// Whether `calls`/`bases` were merged in. Search's own expansion
    /// (`RelationGraph::neighbors`) never reads them, so it loads without;
    /// `context.rs` (`callers_of`) needs them.
    pub with_relations: bool,
}

impl SymbolSnapshot {
    /// Every symbol with relations merged in — what a long-lived cache
    /// should hold, since it serves both search and context.
    ///
    /// Lean whenever BM25 is served from the persisted postings; complete
    /// when it must fall back to
    /// [`LexicalIndex::build`](crate::lexical::LexicalIndex::build), which indexes
    /// `references`/`imports` — so one load serves both, as before, and the
    /// `oxide mcp` cache (keyed on the lexical version too) holds whichever
    /// its requests need.
    pub fn load(store: &dyn IndexBackend) -> anyhow::Result<Self> {
        let symbols = if lexical_persisted(store) {
            crate::structural_relations::load_lean_symbols_with_relations(store)?
        } else {
            crate::structural_relations::load_symbols_with_relations(store)?
        };
        let mut snapshot = Self::from_symbols(symbols);
        snapshot.with_relations = true;
        Ok(snapshot)
    }

    pub(super) fn load_without_relations(store: &dyn IndexBackend) -> anyhow::Result<Self> {
        Ok(Self::from_symbols(if lexical_persisted(store) {
            store.all_symbols_lean()?
        } else {
            store.all_symbols()?
        }))
    }

    pub fn from_symbols(symbols: Vec<Symbol>) -> Self {
        let by_id = symbols
            .iter()
            .enumerate()
            .map(|(i, s)| (s.id(), i))
            .collect();
        Self {
            symbols,
            by_id,
            with_relations: false,
        }
    }

    pub fn get(&self, id: u64) -> Option<&Symbol> {
        self.by_id.get(&id).map(|&i| &self.symbols[i])
    }
}

/// Whether BM25 can be served from the persisted postings: only on an exact
/// `meta.lexical_index_version` match (see [`RetrievalEngine`](super::RetrievalEngine)'s
/// construction). Otherwise retrieval rebuilds BM25 in memory from complete
/// symbols, which also decides [`SymbolSnapshot::load`]'s shape.
pub fn lexical_persisted(store: &dyn IndexBackend) -> bool {
    matches!(
        store.get_meta(crate::storage::LEXICAL_INDEX_KEY),
        Ok(Some(v)) if v == crate::storage::LEXICAL_INDEX_VERSION.to_string()
    )
}

/// Fill in what a lean snapshot left out ([`Completeness::Partial`]) for
/// every partial symbol in `symbols`, from one bounded `symbols_by_ids`
/// read (the candidate hydration statement). Complete symbols are left
/// alone and, when there is no partial one, nothing is read. `calls`/
/// `bases` — merged from the relations side table, which `symbols_by_ids`
/// leaves empty — are kept. A row missing from the store is an error: a
/// partial symbol is never passed off as complete.
pub fn complete_symbols<'s>(
    store: &dyn IndexBackend,
    symbols: impl IntoIterator<Item = &'s mut Symbol>,
) -> anyhow::Result<()> {
    let partial: Vec<&mut Symbol> = symbols.into_iter().filter(|s| !s.is_complete()).collect();
    if partial.is_empty() {
        return Ok(());
    }
    let ids: Vec<u64> = partial.iter().map(|s| s.id()).collect();
    let full: HashMap<u64, Symbol> = store
        .symbols_by_ids(&ids)?
        .into_iter()
        .map(|s| (s.id(), s))
        .collect();
    for s in partial {
        let f = full.get(&s.id()).ok_or_else(|| {
            anyhow::anyhow!(
                "cannot complete {}#{}: no such row in the index",
                s.file,
                s.qualified_name
            )
        })?;
        s.imports.clone_from(&f.imports);
        s.references.clone_from(&f.references);
        s.completeness = Completeness::Complete;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::embeddings::HashedEmbedder;
    use crate::relations::RelationGraph;
    use crate::retrieval::test_support::{fixture_repo, json, sym};
    use crate::retrieval::SearchHit;
    use crate::storage::SqliteStore;
    use crate::symbols::SymbolKind;

    /// The lean load is `all_symbols`' rows in the same order, partial
    /// everywhere, with `references` exactly on test symbols; completing it
    /// reproduces `all_symbols` byte for byte.
    #[test]
    fn completed_lean_corpus_equals_the_full_load() {
        let emb = HashedEmbedder::default();
        let repo = fixture_repo("py_repo", "oxidepy/retry.py");
        let mut store = SqliteStore::open(&repo.path().join(".oxide/index.db")).unwrap();
        crate::index::update_index(repo.path(), &mut store, &emb).unwrap();
        let full = store.all_symbols().unwrap();
        let mut lean = store.all_symbols_lean().unwrap();
        let mut buf = Default::default();
        assert_eq!(full.len(), lean.len());
        let mut tests = 0;
        for (f, l) in full.iter().zip(&lean) {
            assert_eq!(f.id(), l.id());
            assert!(!l.is_complete() && l.imports.is_empty());
            if crate::symbols::is_test_symbol(&f.file, &f.name, f.kind, &mut buf) {
                tests += 1;
                assert_eq!(f.references, l.references);
            } else {
                assert!(l.references.is_empty());
            }
        }
        assert!(tests > 0, "fixture has test symbols");
        complete_symbols(&store, lean.iter_mut()).unwrap();
        assert_eq!(json(&full), json(&lean));
    }

    #[test]
    #[should_panic(expected = "partial seed")]
    fn neighbors_refuses_a_partial_seed() {
        let mut s = sym("src/a.py", "f", SymbolKind::Function, "def f():", &[]);
        s.completeness = Completeness::Partial;
        let corpus = vec![s.clone()];
        RelationGraph::build(&corpus).neighbors(&s);
    }

    /// `skip_serializing_if` under `#[serde(flatten)]`: a complete symbol
    /// emits no `completeness` key at all, a partial one fails to
    /// serialize instead of emitting empty `imports`/`references`.
    #[test]
    fn a_partial_symbol_never_serializes() {
        let s = sym("src/a.py", "f", SymbolKind::Function, "def f():", &["g"]);
        let hit = |symbol: Symbol| SearchHit {
            symbol,
            score: 1.0,
            reasons: vec![],
            snippet: String::new(),
        };
        let out = serde_json::to_string(&hit(s.clone())).unwrap();
        assert!(!out.contains("completeness"), "{out}");
        let mut p = s;
        p.completeness = Completeness::Partial;
        assert!(serde_json::to_string(&hit(p.clone())).is_err());
        assert!(serde_json::to_string(&p).is_err());
    }
}
