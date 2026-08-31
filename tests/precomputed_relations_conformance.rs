//! Callers/implementors conformance suite, production path: write files to
//! a temp repo, run `update_index` (which now populates `symbol_relations`
//! directly — no separate pass), build a `RelationGraph` from
//! `load_symbols_with_relations`, and query `callers_of`/`implementors_of`.
//! Originally translated from the now-deleted query-time
//! `tests/structural_conformance.rs` (ast-grep vs. Tree-sitter query-time
//! providers — see docs/treesitter-structural-eval/README.md and
//! docs/precomputed-structural-relations/README.md for that history); this
//! is now the sole conformance suite for callers/implementors, per
//! docs/precomputed-relations-migration/README.md.
//!
//! 10 of the original 12 query-time cases translate directly. 2 do not, and
//! are recorded here rather than silently dropped:
//!
//! - `empty_file_list_returns_empty_instead_of_panicking` has no analog: a
//!   graph lookup takes a name, not a file list — there is nothing to make
//!   empty.
//! - `malformed_source_returns_empty_instead_of_panicking` is vacuous here:
//!   malformed source that fails to parse into any symbols has nothing to
//!   attach relations to, so `callers_of`/`implementors_of` trivially return
//!   empty for the *wrong* reason (no symbols exist at all, not "input was
//!   rejected by a match"). Not asserted as a meaningful pass.
//!
//! One deliberate cardinality difference, not hidden: `StructuralHit`s are
//! per call-site; graph edges are per (caller-symbol, callee-name) pair. Two
//! calls to the same name from the *same* enclosing symbol collapse to one
//! symbol in `callers_of`'s result. `tsx_finds_bare_and_method_calls_inside_jsx_expressions`
//! below still asserts count 2 only because its two calls happen to sit in
//! two different enclosing symbols (a function and the module fallback) —
//! verified explicitly, not assumed.

use oxide::embeddings::HashedEmbedder;
use oxide::index::{update_index, IndexBackend, SqliteStore};
use oxide::retrieval::RelationGraph;
use oxide::structural_relations::load_symbols_with_relations;
use oxide::symbols::Symbol;
use std::fs;

/// Writes `files` (repo-relative path -> contents) to a fresh temp repo,
/// indexes it, and returns the merged symbol list a `RelationGraph` can be
/// built from. `update_index` alone populates `symbol_relations` — no
/// separate pass.
fn indexed_with_relations(files: &[(&str, &str)]) -> Vec<Symbol> {
    let tmp = tempfile::tempdir().unwrap();
    for (rel, contents) in files {
        let p = tmp.path().join(rel);
        fs::create_dir_all(p.parent().unwrap()).unwrap();
        fs::write(p, contents).unwrap();
    }
    let mut store = SqliteStore::open(&tmp.path().join(".oxide/index.db")).unwrap();
    let embedder = HashedEmbedder::default();
    update_index(tmp.path(), &mut store, &embedder).unwrap();
    load_symbols_with_relations(&store as &dyn IndexBackend).unwrap()
}

fn names<'a>(symbols: &'a [&'a Symbol]) -> Vec<&'a str> {
    symbols.iter().map(|s| s.qualified_name.as_str()).collect()
}

#[test]
fn finds_cross_file_implementors_and_excludes_non_implementors() {
    let symbols = indexed_with_relations(&[
        (
            "shapes.ts",
            "interface Shape { area(): number }\nclass Other { area() { return 0 } }\n",
        ),
        (
            "impls.ts",
            "class Circle implements Shape { area() { return 1 } }\nclass Square implements Shape { area() { return 2 } }\n",
        ),
    ]);
    let graph = RelationGraph::build(&symbols);
    let hits = graph.implementors_of("Shape");
    let n = names(&hits);
    assert_eq!(hits.len(), 2, "{n:?}");
    assert!(n.contains(&"Circle") && n.contains(&"Square"), "{n:?}");
}

#[test]
fn call_matching_is_ast_precise_not_lexical() {
    let symbols = indexed_with_relations(&[(
        "x.ts",
        "// call fetch(x) in a comment\nconst s = \"fetch(y)\";\nfetch(real());\n",
    )]);
    let graph = RelationGraph::build(&symbols);
    let hits = graph.callers_of("fetch");
    assert_eq!(hits.len(), 1, "{:?}", names(&hits));
    assert!(hits[0].qualified_name.ends_with(":__module__"));
}

#[test]
fn python_subclass_implementors_across_files() {
    let symbols = indexed_with_relations(&[
        (
            "notifiers.py",
            "class Notifier:\n    def notify(self, m): raise NotImplementedError\n",
        ),
        (
            "email.py",
            "class EmailNotifier(Notifier):\n    def notify(self, m): print(m)\n\nclass Standalone:\n    pass\n",
        ),
    ]);
    let graph = RelationGraph::build(&symbols);
    let hits = graph.implementors_of("Notifier");
    assert_eq!(hits.len(), 1, "{:?}", names(&hits));
    assert_eq!(hits[0].qualified_name, "EmailNotifier");
}

#[test]
fn finds_method_style_calls_not_just_bare_calls() {
    let symbols = indexed_with_relations(&[(
        "x.py",
        "def f(policy, attempt, error):\n    if not policy.should_retry(attempt, error):\n        return\n",
    )]);
    let graph = RelationGraph::build(&symbols);
    let hits = graph.callers_of("should_retry");
    assert_eq!(hits.len(), 1, "{:?}", names(&hits));
    assert_eq!(hits[0].qualified_name, "f");
}

#[test]
fn typescript_implements_list_matches_every_interface() {
    let symbols = indexed_with_relations(&[(
        "x.ts",
        "class Widget implements A, B {\n  a() {}\n  b() {}\n}\n",
    )]);
    let graph = RelationGraph::build(&symbols);
    assert_eq!(graph.implementors_of("A").len(), 1);
    assert_eq!(graph.implementors_of("B").len(), 1);
}

#[test]
fn python_multiple_inheritance_matches_every_base() {
    let symbols = indexed_with_relations(&[("x.py", "class X(A, B):\n    pass\n")]);
    let graph = RelationGraph::build(&symbols);
    assert_eq!(graph.implementors_of("A").len(), 1);
    assert_eq!(graph.implementors_of("B").len(), 1);
}

#[test]
fn typescript_extends_plus_implements_matches_both_sides() {
    let symbols = indexed_with_relations(&[(
        "x.ts",
        "class Widget extends Base implements Iface {\n  render() {}\n}\n",
    )]);
    let graph = RelationGraph::build(&symbols);
    assert_eq!(graph.implementors_of("Base").len(), 1);
    assert_eq!(graph.implementors_of("Iface").len(), 1);
}

#[test]
fn typescript_finds_method_style_calls_not_just_bare_calls() {
    let symbols = indexed_with_relations(&[(
        "x.ts",
        "function handle(client: Client) {\n  if (!client.shouldRetry(1)) return;\n}\n",
    )]);
    let graph = RelationGraph::build(&symbols);
    let hits = graph.callers_of("shouldRetry");
    assert_eq!(hits.len(), 1, "{:?}", names(&hits));
    assert_eq!(hits[0].qualified_name, "handle");
}

#[test]
fn tsx_finds_implementors_across_component_files() {
    let symbols = indexed_with_relations(&[
        ("props.tsx", "interface ClickHandler { onClick(): void }\n"),
        (
            "widget.tsx",
            "class Widget implements ClickHandler {\n  onClick() {}\n  render() { return <div/> }\n}\n",
        ),
        (
            "other.tsx",
            "class Unrelated {\n  render() { return <span/> }\n}\n",
        ),
    ]);
    let graph = RelationGraph::build(&symbols);
    let hits = graph.implementors_of("ClickHandler");
    assert_eq!(hits.len(), 1, "{:?}", names(&hits));
    assert_eq!(hits[0].qualified_name, "Widget");
}

#[test]
fn tsx_finds_bare_and_method_calls_inside_jsx_expressions() {
    let symbols = indexed_with_relations(&[(
        "app.tsx",
        "function App() {\n  return <div onClick={() => api.fetchData(1)} />;\n}\napi.fetchData(2);\n",
    )]);
    let graph = RelationGraph::build(&symbols);
    let hits = graph.callers_of("fetchData");
    let n = names(&hits);
    assert_eq!(hits.len(), 2, "{n:?}");
    assert!(n.contains(&"App"), "{n:?}");
    assert!(n.iter().any(|q| q.ends_with(":__module__")), "{n:?}");
}
