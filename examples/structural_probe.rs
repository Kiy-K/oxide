//! Structural-relation spot check against a real repository: index it, then
//! print `implementors_of`/`callers_of` for the names given on the command
//! line. Used to produce the per-language structural evidence in
//! `docs/language-support/README.md`.
//!
//! `cargo run --release --example structural_probe <repo> <name>...`
//! Writes a `.oxide/` index into <repo>, so point it at a copy.

use oxide::embeddings::HashedEmbedder;
use oxide::index::update_index;
use oxide::relations::RelationGraph;
use oxide::storage::SqliteStore;
use oxide::structural_relations::load_symbols_with_relations;
use std::path::Path;

fn main() {
    let root = Path::new(&std::env::args().nth(1).unwrap()).to_path_buf();
    let mut store = SqliteStore::open(&root.join(".oxide/index.db")).unwrap();
    let t = std::time::Instant::now();
    let report = update_index(&root, &mut store, &HashedEmbedder::default()).unwrap();
    let symbols = load_symbols_with_relations(&store).unwrap();
    println!(
        "cold index {:?}, {} symbols, {} files",
        t.elapsed(),
        symbols.len(),
        report.scanned_files
    );
    let g = RelationGraph::build(&symbols);
    for name in std::env::args().skip(2) {
        let imps: Vec<String> = g
            .implementors_of(&name)
            .iter()
            .map(|s| format!("{}#{}", s.file, s.qualified_name))
            .collect();
        let calls: Vec<String> = g
            .callers_of(&name)
            .iter()
            .take(6)
            .map(|s| format!("{}#{}", s.file, s.qualified_name))
            .collect();
        println!("implementors_of({name}) = {imps:?}");
        println!("callers_of({name})[..6] = {calls:?}");
    }
}
