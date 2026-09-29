//! The one owner of corpus snapshot assembly. Everything that decides
//! what a whole-corpus load looks like lives here: lean (partial) rows when
//! BM25 is served from persisted postings, complete rows when it must fall
//! back to the in-memory index ([`lexical_persisted`], the persisted
//! lexical-generation rule), whether stored `calls`/`bases` are merged in,
//! and [`complete_symbols`], which every partial symbol goes through before
//! it becomes a seed or leaves as output. Storage only supplies the typed
//! reads ([`IndexRead`]); this policy stays out of it.

use crate::relations::RelationState;
use crate::storage::IndexRead;
use crate::symbols::{Completeness, Symbol};
use std::collections::HashMap;

/// Every indexed symbol, with `calls`/`bases` merged in from the relations
/// side table, plus an id index. Lean
/// ([`IndexRead::all_symbols_lean`]): every symbol is
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
    /// `context.rs` (`callers_of`) needs them. Inside the crate only
    /// [`SymbolSnapshot`]'s own assembly sets it, in the same step as the
    /// merge; it stays a public field for callers that merge relations
    /// themselves (kept by #34 S6). Read it as
    /// [`Self::relation_state`], which graphs over this snapshot inherit.
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
    pub fn load(store: &dyn IndexRead) -> anyhow::Result<Self> {
        Self::assemble(store, true)
    }

    pub(super) fn load_without_relations(store: &dyn IndexRead) -> anyhow::Result<Self> {
        Self::assemble(store, false)
    }

    /// The single corpus-load path: row shape from [`lexical_persisted`],
    /// then the relations merge when asked for.
    fn assemble(store: &dyn IndexRead, with_relations: bool) -> anyhow::Result<Self> {
        let mut symbols = if lexical_persisted(store) {
            store.all_symbols_lean()?
        } else {
            store.all_symbols()?
        };
        if with_relations {
            merge_relations(store, &mut symbols)?;
        }
        let mut snapshot = Self::from_symbols(symbols);
        snapshot.with_relations = with_relations;
        Ok(snapshot)
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

    /// Whether this snapshot's `calls`/`bases` are authoritative (#34 S5).
    pub(crate) fn relation_state(&self) -> RelationState {
        if self.with_relations {
            RelationState::Loaded
        } else {
            RelationState::NotLoaded
        }
    }

    pub fn get(&self, id: u64) -> Option<&Symbol> {
        self.by_id.get(&id).map(|&i| &self.symbols[i])
    }
}

/// Complete symbols with `calls`/`bases` merged in from `symbol_relations`,
/// regardless of the lexical state — the read-side counterpart of
/// `structural_relations::compute_file_relations`/`update_index`, for
/// tests and research harnesses that want the whole stored corpus. The
/// request path uses [`SymbolSnapshot::load`] instead.
pub fn load_symbols_with_relations(store: &dyn IndexRead) -> anyhow::Result<Vec<Symbol>> {
    let mut symbols = store.all_symbols()?;
    merge_relations(store, &mut symbols)?;
    Ok(symbols)
}

/// [`load_symbols_with_relations`] over [`IndexRead::all_symbols_lean`]
/// rows, regardless of the lexical state. No production caller: the
/// request path goes through [`SymbolSnapshot::load`], which picks lean
/// rows itself.
pub fn load_lean_symbols_with_relations(store: &dyn IndexRead) -> anyhow::Result<Vec<Symbol>> {
    let mut symbols = store.all_symbols_lean()?;
    merge_relations(store, &mut symbols)?;
    Ok(symbols)
}

fn merge_relations(store: &dyn IndexRead, symbols: &mut [Symbol]) -> anyhow::Result<()> {
    let mut relations = store.all_symbol_relations()?;
    for s in symbols {
        if let Some((calls, bases)) = relations.remove(&s.id()) {
            s.calls = calls;
            s.bases = bases;
        }
    }
    Ok(())
}

/// Whether BM25 can be served from the persisted postings: only on an exact
/// `meta.lexical_index_version` match (see [`RetrievalEngine`](super::RetrievalEngine)'s
/// construction). Otherwise retrieval rebuilds BM25 in memory from complete
/// symbols, which also decides [`SymbolSnapshot::load`]'s shape.
pub fn lexical_persisted(store: &dyn IndexRead) -> bool {
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
    store: &dyn IndexRead,
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
    use crate::storage::{IndexWrite, SqliteStore};
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

    /// Pins how a corpus snapshot is assembled, written against the code
    /// before snapshot assembly had one owner (#34 S2) and never
    /// re-baselined: for each persisted-lexical state, the lean/complete
    /// choice, symbol order, which symbols carry merged `calls`/`bases`,
    /// the engine's lazy with/without-relations loads, and completion.
    #[test]
    fn snapshot_assembly_matches_the_pre_s2_behavior() {
        fn digest(snap: &SymbolSnapshot) -> String {
            let order =
                crate::symbols::fnv1a64_iter(snap.symbols.iter().map(|s| s.id().to_le_bytes()));
            let body = crate::symbols::fnv1a64_iter(snap.symbols.iter().map(|s| {
                format!(
                    "{}|{}|{}|{}|{}|{}|{}|{}",
                    s.id(),
                    s.is_complete(),
                    s.file,
                    s.qualified_name,
                    s.imports.join(","),
                    s.references.join(","),
                    s.calls.join(","),
                    s.bases.join(",")
                )
            }));
            let count = |f: fn(&Symbol) -> bool| snap.symbols.iter().filter(|s| f(s)).count();
            format!(
                "n={} partial={} rel={} calls={} bases={} refs={} imports={} order={order:016x} body={body:016x}",
                snap.symbols.len(),
                count(|s| !s.is_complete()),
                snap.with_relations,
                count(|s| !s.calls.is_empty()),
                count(|s| !s.bases.is_empty()),
                count(|s| !s.references.is_empty()),
                count(|s| !s.imports.is_empty()),
            )
        }
        let emb = HashedEmbedder::default();
        let mut out = Vec::new();
        for (fixture, changed) in [
            ("py_repo", "oxidepy/retry.py"),
            ("ts_repo", "src/net/retry.ts"),
        ] {
            let repo = fixture_repo(fixture, changed);
            let mut store = SqliteStore::open(&repo.path().join(".oxide/index.db")).unwrap();
            crate::index::update_index(repo.path(), &mut store, &emb).unwrap();
            for lexical in [None, Some(""), Some("stale")] {
                if let Some(v) = lexical {
                    store
                        .set_meta(crate::storage::LEXICAL_INDEX_KEY, v)
                        .unwrap();
                }
                let tag = format!(
                    "{fixture} lexical={lexical:?} persisted={}",
                    lexical_persisted(&store)
                );
                let load = SymbolSnapshot::load(&store).unwrap();
                out.push(format!("{tag} load: {}", digest(&load)));
                let bare = SymbolSnapshot::load_without_relations(&store).unwrap();
                out.push(format!("{tag} without: {}", digest(&bare)));

                let engine = crate::retrieval::RetrievalEngine::new(&store, &emb);
                out.push(format!(
                    "{tag} engine.snapshot: {}",
                    digest(engine.snapshot())
                ));
                let with = engine.snapshot_with_relations().unwrap();
                out.push(format!("{tag} engine.then_relations: {}", digest(with)));
                let engine = crate::retrieval::RetrievalEngine::new(&store, &emb);
                let with = engine.snapshot_with_relations().unwrap();
                out.push(format!("{tag} engine.relations_first: {}", digest(with)));
                out.push(format!(
                    "{tag} engine.then_snapshot: {}",
                    digest(engine.snapshot())
                ));

                let mut completed = load.symbols.clone();
                complete_symbols(&store, completed.iter_mut()).unwrap();
                out.push(format!(
                    "{tag} completed: {}",
                    digest(&SymbolSnapshot::from_symbols(completed))
                ));
            }
        }
        let mut ghost = sym(
            "src/ghost.py",
            "ghost",
            SymbolKind::Function,
            "def ghost():",
            &[],
        );
        ghost.completeness = Completeness::Partial;
        let store = SqliteStore::open(std::path::Path::new(":memory:")).unwrap();
        let err = complete_symbols(&store, std::iter::once(&mut ghost)).unwrap_err();
        out.push(format!("missing row: {err}"));
        let actual = out.join("\n");
        let expected = "py_repo lexical=None persisted=true load: n=55 partial=55 rel=true calls=24 bases=5 refs=6 imports=0 order=7a66c11726c15b25 body=bcd1ec9490c0a31f\n\
            py_repo lexical=None persisted=true without: n=55 partial=55 rel=false calls=0 bases=0 refs=6 imports=0 order=7a66c11726c15b25 body=5dd9bb1de8f5cd04\n\
            py_repo lexical=None persisted=true engine.snapshot: n=55 partial=55 rel=false calls=0 bases=0 refs=6 imports=0 order=7a66c11726c15b25 body=5dd9bb1de8f5cd04\n\
            py_repo lexical=None persisted=true engine.then_relations: n=55 partial=55 rel=true calls=24 bases=5 refs=6 imports=0 order=7a66c11726c15b25 body=bcd1ec9490c0a31f\n\
            py_repo lexical=None persisted=true engine.relations_first: n=55 partial=55 rel=true calls=24 bases=5 refs=6 imports=0 order=7a66c11726c15b25 body=bcd1ec9490c0a31f\n\
            py_repo lexical=None persisted=true engine.then_snapshot: n=55 partial=55 rel=true calls=24 bases=5 refs=6 imports=0 order=7a66c11726c15b25 body=bcd1ec9490c0a31f\n\
            py_repo lexical=None persisted=true completed: n=55 partial=0 rel=false calls=24 bases=5 refs=33 imports=54 order=7a66c11726c15b25 body=d52c5f4b80b0ee51\n\
            py_repo lexical=Some(\"\") persisted=false load: n=55 partial=0 rel=true calls=24 bases=5 refs=33 imports=54 order=7a66c11726c15b25 body=d52c5f4b80b0ee51\n\
            py_repo lexical=Some(\"\") persisted=false without: n=55 partial=0 rel=false calls=0 bases=0 refs=33 imports=54 order=7a66c11726c15b25 body=2c087a0df5104d18\n\
            py_repo lexical=Some(\"\") persisted=false engine.snapshot: n=55 partial=0 rel=false calls=0 bases=0 refs=33 imports=54 order=7a66c11726c15b25 body=2c087a0df5104d18\n\
            py_repo lexical=Some(\"\") persisted=false engine.then_relations: n=55 partial=0 rel=true calls=24 bases=5 refs=33 imports=54 order=7a66c11726c15b25 body=d52c5f4b80b0ee51\n\
            py_repo lexical=Some(\"\") persisted=false engine.relations_first: n=55 partial=0 rel=true calls=24 bases=5 refs=33 imports=54 order=7a66c11726c15b25 body=d52c5f4b80b0ee51\n\
            py_repo lexical=Some(\"\") persisted=false engine.then_snapshot: n=55 partial=0 rel=false calls=0 bases=0 refs=33 imports=54 order=7a66c11726c15b25 body=2c087a0df5104d18\n\
            py_repo lexical=Some(\"\") persisted=false completed: n=55 partial=0 rel=false calls=24 bases=5 refs=33 imports=54 order=7a66c11726c15b25 body=d52c5f4b80b0ee51\n\
            py_repo lexical=Some(\"stale\") persisted=false load: n=55 partial=0 rel=true calls=24 bases=5 refs=33 imports=54 order=7a66c11726c15b25 body=d52c5f4b80b0ee51\n\
            py_repo lexical=Some(\"stale\") persisted=false without: n=55 partial=0 rel=false calls=0 bases=0 refs=33 imports=54 order=7a66c11726c15b25 body=2c087a0df5104d18\n\
            py_repo lexical=Some(\"stale\") persisted=false engine.snapshot: n=55 partial=0 rel=false calls=0 bases=0 refs=33 imports=54 order=7a66c11726c15b25 body=2c087a0df5104d18\n\
            py_repo lexical=Some(\"stale\") persisted=false engine.then_relations: n=55 partial=0 rel=true calls=24 bases=5 refs=33 imports=54 order=7a66c11726c15b25 body=d52c5f4b80b0ee51\n\
            py_repo lexical=Some(\"stale\") persisted=false engine.relations_first: n=55 partial=0 rel=true calls=24 bases=5 refs=33 imports=54 order=7a66c11726c15b25 body=d52c5f4b80b0ee51\n\
            py_repo lexical=Some(\"stale\") persisted=false engine.then_snapshot: n=55 partial=0 rel=false calls=0 bases=0 refs=33 imports=54 order=7a66c11726c15b25 body=2c087a0df5104d18\n\
            py_repo lexical=Some(\"stale\") persisted=false completed: n=55 partial=0 rel=false calls=24 bases=5 refs=33 imports=54 order=7a66c11726c15b25 body=d52c5f4b80b0ee51\n\
            ts_repo lexical=None persisted=true load: n=41 partial=41 rel=true calls=9 bases=3 refs=1 imports=0 order=2b76e800cd0fe354 body=4fa2b52c83294591\n\
            ts_repo lexical=None persisted=true without: n=41 partial=41 rel=false calls=0 bases=0 refs=1 imports=0 order=2b76e800cd0fe354 body=8c5d4341eed80f77\n\
            ts_repo lexical=None persisted=true engine.snapshot: n=41 partial=41 rel=false calls=0 bases=0 refs=1 imports=0 order=2b76e800cd0fe354 body=8c5d4341eed80f77\n\
            ts_repo lexical=None persisted=true engine.then_relations: n=41 partial=41 rel=true calls=9 bases=3 refs=1 imports=0 order=2b76e800cd0fe354 body=4fa2b52c83294591\n\
            ts_repo lexical=None persisted=true engine.relations_first: n=41 partial=41 rel=true calls=9 bases=3 refs=1 imports=0 order=2b76e800cd0fe354 body=4fa2b52c83294591\n\
            ts_repo lexical=None persisted=true engine.then_snapshot: n=41 partial=41 rel=true calls=9 bases=3 refs=1 imports=0 order=2b76e800cd0fe354 body=4fa2b52c83294591\n\
            ts_repo lexical=None persisted=true completed: n=41 partial=0 rel=false calls=9 bases=3 refs=29 imports=23 order=2b76e800cd0fe354 body=bac26b2795d77ad2\n\
            ts_repo lexical=Some(\"\") persisted=false load: n=41 partial=0 rel=true calls=9 bases=3 refs=29 imports=23 order=2b76e800cd0fe354 body=bac26b2795d77ad2\n\
            ts_repo lexical=Some(\"\") persisted=false without: n=41 partial=0 rel=false calls=0 bases=0 refs=29 imports=23 order=2b76e800cd0fe354 body=8da57174b02e2486\n\
            ts_repo lexical=Some(\"\") persisted=false engine.snapshot: n=41 partial=0 rel=false calls=0 bases=0 refs=29 imports=23 order=2b76e800cd0fe354 body=8da57174b02e2486\n\
            ts_repo lexical=Some(\"\") persisted=false engine.then_relations: n=41 partial=0 rel=true calls=9 bases=3 refs=29 imports=23 order=2b76e800cd0fe354 body=bac26b2795d77ad2\n\
            ts_repo lexical=Some(\"\") persisted=false engine.relations_first: n=41 partial=0 rel=true calls=9 bases=3 refs=29 imports=23 order=2b76e800cd0fe354 body=bac26b2795d77ad2\n\
            ts_repo lexical=Some(\"\") persisted=false engine.then_snapshot: n=41 partial=0 rel=false calls=0 bases=0 refs=29 imports=23 order=2b76e800cd0fe354 body=8da57174b02e2486\n\
            ts_repo lexical=Some(\"\") persisted=false completed: n=41 partial=0 rel=false calls=9 bases=3 refs=29 imports=23 order=2b76e800cd0fe354 body=bac26b2795d77ad2\n\
            ts_repo lexical=Some(\"stale\") persisted=false load: n=41 partial=0 rel=true calls=9 bases=3 refs=29 imports=23 order=2b76e800cd0fe354 body=bac26b2795d77ad2\n\
            ts_repo lexical=Some(\"stale\") persisted=false without: n=41 partial=0 rel=false calls=0 bases=0 refs=29 imports=23 order=2b76e800cd0fe354 body=8da57174b02e2486\n\
            ts_repo lexical=Some(\"stale\") persisted=false engine.snapshot: n=41 partial=0 rel=false calls=0 bases=0 refs=29 imports=23 order=2b76e800cd0fe354 body=8da57174b02e2486\n\
            ts_repo lexical=Some(\"stale\") persisted=false engine.then_relations: n=41 partial=0 rel=true calls=9 bases=3 refs=29 imports=23 order=2b76e800cd0fe354 body=bac26b2795d77ad2\n\
            ts_repo lexical=Some(\"stale\") persisted=false engine.relations_first: n=41 partial=0 rel=true calls=9 bases=3 refs=29 imports=23 order=2b76e800cd0fe354 body=bac26b2795d77ad2\n\
            ts_repo lexical=Some(\"stale\") persisted=false engine.then_snapshot: n=41 partial=0 rel=false calls=0 bases=0 refs=29 imports=23 order=2b76e800cd0fe354 body=8da57174b02e2486\n\
            ts_repo lexical=Some(\"stale\") persisted=false completed: n=41 partial=0 rel=false calls=9 bases=3 refs=29 imports=23 order=2b76e800cd0fe354 body=bac26b2795d77ad2\n\
            missing row: cannot complete src/ghost.py#ghost: no such row in the index";
        assert_eq!(actual, expected, "\n{actual}\n");
    }

    /// The relation-loading axis, pinned before #34 S5 made it explicit:
    /// a snapshot assembled with the relations merge says so, and a symbol
    /// with no stored relations is then "loaded, none found"; one assembled
    /// without it has no `calls`/`bases` anywhere, whatever the lexical
    /// state (so complete rows are not relation-loaded by themselves);
    /// completion touches only `imports`/`references`, never relation data
    /// or state; and the engine's relation graph answers from merged data
    /// even after it first loaded a bare snapshot.
    #[test]
    fn relation_loading_state_is_independent_of_completeness() {
        use crate::relations::RelationGraph;
        let emb = HashedEmbedder::default();
        let repo = fixture_repo("py_repo", "oxidepy/retry.py");
        let mut store = SqliteStore::open(&repo.path().join(".oxide/index.db")).unwrap();
        crate::index::update_index(repo.path(), &mut store, &emb).unwrap();
        let stored = store.all_symbol_relations().unwrap();
        for lexical in [None, Some("stale")] {
            if let Some(v) = lexical {
                store
                    .set_meta(crate::storage::LEXICAL_INDEX_KEY, v)
                    .unwrap();
            }
            let lean_rows = lexical.is_none();
            let with = SymbolSnapshot::load(&store).unwrap();
            let bare = SymbolSnapshot::load_without_relations(&store).unwrap();
            assert!(with.with_relations && !bare.with_relations);
            for (w, b) in with.symbols.iter().zip(&bare.symbols) {
                assert_eq!(w.is_complete(), !lean_rows);
                assert_eq!(b.is_complete(), !lean_rows);
                assert!(b.calls.is_empty() && b.bases.is_empty());
                let (calls, bases) = stored.get(&w.id()).cloned().unwrap_or_default();
                assert_eq!((&w.calls, &w.bases), (&calls, &bases));
            }
            assert!(with
                .symbols
                .iter()
                .any(|s| s.calls.is_empty() && s.bases.is_empty()));
            assert!(with.symbols.iter().any(|s| !s.calls.is_empty()));

            for mut snap in [with, bare] {
                let before: Vec<_> = snap
                    .symbols
                    .iter()
                    .map(|s| (s.calls.clone(), s.bases.clone()))
                    .collect();
                let state = snap.with_relations;
                complete_symbols(&store, snap.symbols.iter_mut()).unwrap();
                assert!(snap.symbols.iter().all(Symbol::is_complete));
                let after: Vec<_> = snap
                    .symbols
                    .iter()
                    .map(|s| (s.calls.clone(), s.bases.clone()))
                    .collect();
                assert_eq!(before, after);
                assert_eq!(snap.with_relations, state);
            }

            // Graph answers over merged relations, pinned per queried name.
            let engine = crate::retrieval::RetrievalEngine::new(&store, &emb);
            let _ = engine.snapshot();
            let graph = engine.relation_graph().unwrap();
            let oracle_symbols = super::load_symbols_with_relations(&store).unwrap();
            let oracle = RelationGraph::build(&oracle_symbols);
            let mut names: Vec<&String> = oracle_symbols
                .iter()
                .flat_map(|s| s.calls.iter().chain(&s.bases))
                .collect();
            names.sort();
            names.dedup();
            assert!(names.len() > 5);
            let ids = |v: Vec<&Symbol>| v.into_iter().map(Symbol::id).collect::<Vec<_>>();
            for n in names {
                assert_eq!(
                    ids(graph.callers_of(n)),
                    ids(oracle.callers_of(n)),
                    "callers_of({n})"
                );
                assert_eq!(
                    ids(graph.implementors_of(n)),
                    ids(oracle.implementors_of(n)),
                    "implementors_of({n})"
                );
            }
        }
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
