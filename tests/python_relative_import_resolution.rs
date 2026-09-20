//! End-to-end contract for `relations::resolve_module` resolving Python's
//! dot-only relative import syntax (`.store`, `..pkg.util`, bare `.`/`..`)
//! through the real indexing path — `src/relations.rs`'s own unit tests
//! pin the pure-function cases (valid, unresolved, ambiguous); this pins
//! that the fix survives a real `update_index` round trip and stays correct
//! across an incremental re-index, not just against hand-built `Symbol`s.

use oxide::embeddings::HashedEmbedder;
use oxide::index::update_index;
use oxide::relations::RelationGraph;
use oxide::storage::{IndexBackend, SqliteStore};
use oxide::symbols::Symbol;
use std::fs;
use std::path::Path;

fn write(path: &Path, content: &str) {
    fs::create_dir_all(path.parent().unwrap()).unwrap();
    fs::write(path, content).unwrap();
}

const STORE_V1: &str = "class TokenStore:\n    def refresh(self, token):\n        return token\n";
const HANDLER: &str = "from .store import TokenStore\n\n\ndef handle(request):\n    return TokenStore().refresh(request)\n";

/// `(name, file)` for every `imported-definition` neighbor of `seed`. Both
/// `TokenStore` and its `refresh` method legitimately show up here — the
/// handler's body references both bare names, and both live in the file
/// `.store` resolves to — so callers should assert on names/files, not a
/// bare count.
fn imported_definitions<'a>(graph: &RelationGraph<'a>, seed: &Symbol) -> Vec<(&'a str, &'a str)> {
    graph
        .neighbors(seed)
        .into_iter()
        .filter(|(tag, _)| tag == "imported-definition")
        .map(|(_, s)| (s.name.as_str(), s.file.as_str()))
        .collect()
}

#[test]
fn python_relative_import_resolves_cross_file_and_survives_incremental_reindex() {
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path();
    write(&root.join("pkg/store.py"), STORE_V1);
    write(&root.join("pkg/handler.py"), HANDLER);

    let mut store = SqliteStore::open(&root.join(".oxide/index.db")).unwrap();
    let embedder = HashedEmbedder::default();
    update_index(root, &mut store, &embedder).unwrap();

    let symbols = (&store as &dyn IndexBackend).all_symbols().unwrap();
    let graph = RelationGraph::build(&symbols);
    let handle = symbols
        .iter()
        .find(|s| s.qualified_name == "handle")
        .expect("handle symbol indexed");
    let imported = imported_definitions(&graph, handle);
    assert!(
        imported.contains(&("TokenStore", "pkg/store.py")),
        "`.store` must resolve to pkg/store.py across files, not stay unresolved: {imported:?}"
    );
    assert!(
        imported.iter().all(|(_, file)| *file == "pkg/store.py"),
        "every imported-definition neighbor must come from the resolved file: {imported:?}"
    );

    // Incremental re-index: touch only handler.py (an unrelated whitespace
    // edit), leaving store.py untouched. The relative import must still
    // resolve after symbols are reloaded post-update, proving the fix isn't
    // an artifact of the first full-corpus pass.
    write(
        &root.join("pkg/handler.py"),
        &format!("{HANDLER}\n# trailing comment\n"),
    );
    let r = update_index(root, &mut store, &embedder).unwrap();
    assert_eq!(r.reparsed_files, 1, "only handler.py changed");

    let symbols = (&store as &dyn IndexBackend).all_symbols().unwrap();
    let graph = RelationGraph::build(&symbols);
    let handle = symbols
        .iter()
        .find(|s| s.qualified_name == "handle")
        .expect("handle symbol still indexed after incremental update");
    let imported = imported_definitions(&graph, handle);
    assert!(
        imported.contains(&("TokenStore", "pkg/store.py")),
        "the relative import must still resolve after an incremental reindex: {imported:?}"
    );
}
