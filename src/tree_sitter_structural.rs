//! Direct `tree_sitter::Query` extraction for AST-precise call sites and
//! extends/implements clauses — the substrate `structural_relations.rs`
//! uses to precompute callers/implementors at index time. Originally built
//! (and evaluated against an `ast-grep-core` query-time backend) as
//! `docs/treesitter-structural-eval/README.md`'s experiment; now the only
//! structural-search implementation in the codebase, folded into
//! `index::update_index`'s indexing pipeline per
//! `docs/precomputed-relations-migration/README.md`. The old query-time
//! `StructuralSearchProvider` trait and its `ast-grep-core`-backed
//! implementation (`structural.rs`) are gone — nothing in this crate
//! answers a structural query live against arbitrary source anymore, only
//! against what's actually been indexed (`RelationGraph::callers_of`/
//! `implementors_of`, `retrieval.rs`).
//!
//! Query source stays declarative (`.scm` files under
//! `src/languages/queries/`): each `.scm` captures shape only (`@name`,
//! `@base`, `@call`, `@class`), and callers filter/attribute in Rust after
//! matching.

use crate::languages::{PYTHON_PROFILE, RUST_PROFILE, TSX_PROFILE, TYPESCRIPT_PROFILE};
use crate::symbols::Language;
use std::sync::OnceLock;
use tree_sitter::{Parser, Query, QueryCursor, StreamingIterator};

const PYTHON_CALLERS_SRC: &str = include_str!("languages/queries/python_callers.scm");
const PYTHON_IMPLEMENTORS_SRC: &str = include_str!("languages/queries/python_implementors.scm");
const TS_CALLERS_SRC: &str = include_str!("languages/queries/typescript_callers.scm");
/// TSX gets the shared TypeScript patterns plus JSX element usage. Kept as a
/// concatenation rather than a duplicated file so the two grammars can never
/// drift apart on the call-site patterns they do share; the JSX patterns
/// cannot go in the shared file because `jsx_opening_element` does not exist
/// in the TypeScript grammar and `Query::new` would reject the whole source.
const TSX_CALLERS_SRC: &str = concat!(
    include_str!("languages/queries/typescript_callers.scm"),
    include_str!("languages/queries/tsx_callers.scm")
);
const TS_IMPLEMENTORS_SRC: &str = include_str!("languages/queries/typescript_implementors.scm");
const RUST_CALLERS_SRC: &str = include_str!("languages/queries/rust_callers.scm");
const RUST_IMPLEMENTORS_SRC: &str = include_str!("languages/queries/rust_implementors.scm");

/// Compiled once per process, mirroring `tags.rs::TagsExtractor::config`'s
/// `OnceLock` precedent — that pass measured ~15x slower indexing from
/// recompiling a query per file, and `Query::new` does real work (parsing
/// the query source, resolving node-kind/field ids against the grammar).
struct LangQueries {
    callers: OnceLock<Query>,
    implementors: OnceLock<Query>,
}

static PYTHON_QUERIES: LangQueries = LangQueries {
    callers: OnceLock::new(),
    implementors: OnceLock::new(),
};
static TYPESCRIPT_QUERIES: LangQueries = LangQueries {
    callers: OnceLock::new(),
    implementors: OnceLock::new(),
};
static TSX_QUERIES: LangQueries = LangQueries {
    callers: OnceLock::new(),
    implementors: OnceLock::new(),
};
static RUST_QUERIES: LangQueries = LangQueries {
    callers: OnceLock::new(),
    implementors: OnceLock::new(),
};

fn ts_language(lang: Language) -> tree_sitter::Language {
    match lang {
        Language::Python => (PYTHON_PROFILE.ts_language)(),
        Language::TypeScript => (TYPESCRIPT_PROFILE.ts_language)(),
        Language::Tsx => (TSX_PROFILE.ts_language)(),
        Language::Rust => (RUST_PROFILE.ts_language)(),
    }
}

fn queries_for(lang: Language) -> &'static LangQueries {
    match lang {
        Language::Python => &PYTHON_QUERIES,
        Language::TypeScript => &TYPESCRIPT_QUERIES,
        Language::Tsx => &TSX_QUERIES,
        Language::Rust => &RUST_QUERIES,
    }
}

fn callers_src(lang: Language) -> &'static str {
    match lang {
        Language::Python => PYTHON_CALLERS_SRC,
        Language::TypeScript => TS_CALLERS_SRC,
        Language::Tsx => TSX_CALLERS_SRC,
        Language::Rust => RUST_CALLERS_SRC,
    }
}

fn implementors_src(lang: Language) -> &'static str {
    match lang {
        Language::Python => PYTHON_IMPLEMENTORS_SRC,
        Language::TypeScript | Language::Tsx => TS_IMPLEMENTORS_SRC,
        Language::Rust => RUST_IMPLEMENTORS_SRC,
    }
}

/// `Query::new` fails only for a query source that references a node kind
/// or field the grammar doesn't have — a static, per-language `.scm` file
/// mismatching its own grammar is a programming error, not a runtime
/// condition callers should handle. `tests::all_language_queries_compile`
/// exercises all six combinations so a grammar-divergent query source
/// (TS vs TSX diverging on a node kind) surfaces as a test failure, not a
/// first-caller panic.
fn compiled_callers(lang: Language) -> &'static Query {
    queries_for(lang).callers.get_or_init(|| {
        Query::new(&ts_language(lang), callers_src(lang)).expect("static callers query compiles")
    })
}

fn compiled_implementors(lang: Language) -> &'static Query {
    queries_for(lang).implementors.get_or_init(|| {
        Query::new(&ts_language(lang), implementors_src(lang))
            .expect("static implementors query compiles")
    })
}

/// Last segment of a possibly-qualified, possibly-generic name: `abc.ABC`
/// and `React.Component` become `ABC` and `Component`, `Iface<T>` becomes
/// `Iface`. `calls`/`bases` are a bare-name tier by construction (see
/// AGENTS.md) — the queries capture the qualified node so the relation
/// exists at all, and this is where it is brought back onto that tier. It
/// is not resolution: `ns.Base` and a local `Base` are indistinguishable
/// afterwards, exactly as two same-named definitions already are.
fn last_segment(name: &str) -> &str {
    name.split(['<', '(', '!'])
        .next()
        .unwrap_or(name)
        .trim()
        .rsplit(['.', ':'])
        .next()
        .unwrap_or(name)
}

fn line_of(src: &str, byte: usize) -> u32 {
    1 + src[..byte.min(src.len())]
        .bytes()
        .filter(|&b| b == b'\n')
        .count() as u32
}

fn parse(lang: Language, src: &str) -> Option<tree_sitter::Tree> {
    let mut parser = Parser::new();
    parser.set_language(&ts_language(lang)).ok()?;
    parser.parse(src, None)
}

/// One `(start_line, callee_name)` per call site in `src`, used by
/// `structural_relations::compute_file_relations` to attribute each call to
/// its enclosing symbol.
///
/// One filter: a JSX element whose name starts lowercase is an intrinsic
/// (`<div>`, `<span>`), not a component. That is JSX's own rule for the
/// distinction, not a heuristic invented here, and without it every
/// component in a repo becomes a "caller" of `div` — and any symbol
/// unlucky enough to be named `div` inherits them all.
pub fn all_calls_in_file(lang: Language, src: &str) -> Vec<(u32, String)> {
    let query = compiled_callers(lang);
    let name_idx = query
        .capture_index_for_name("name")
        .expect("callers query defines @name");
    let call_idx = query
        .capture_index_for_name("call")
        .expect("callers query defines @call");
    let Some(tree) = parse(lang, src) else {
        return Vec::new();
    };
    let mut out = Vec::new();
    let mut cursor = QueryCursor::new();
    let mut matches = cursor.matches(query, tree.root_node(), src.as_bytes());
    while let Some(m) = matches.next() {
        let name = m
            .captures()
            .iter()
            .find(|c| c.index == name_idx)
            .and_then(|c| c.node.utf8_text(src.as_bytes()).ok());
        let call_node = m.captures().iter().find(|c| c.index == call_idx);
        let call_line = call_node.map(|c| line_of(src, c.node.byte_range().start));
        let intrinsic = call_node.is_some_and(|c| {
            c.node.kind().starts_with("jsx_")
                && name.is_some_and(|n| last_segment(n).starts_with(|ch: char| ch.is_lowercase()))
        });
        if let (Some(name), Some(line), false) = (name, call_line, intrinsic) {
            out.push((line, last_segment(name).to_string()));
        }
    }
    out
}

/// One `(class_start_line, class_name, base_name)` per extends/implements
/// clause entry in `src`, unfiltered. Keyed by the class declaration's own
/// start line, with the name as a fallback join key for languages where the
/// declaration and the heritage clause live in different places (Rust's
/// `impl Trait for Type` is nowhere near `struct Type`) — see
/// `structural_relations::compute_file_relations`. Primarily keyed by line —
/// not name, not line-containment — deliberately: name alone
/// over-attributes when two differently-nested classes in one file share a
/// bare name (`Outer1.Config`/`Outer2.Config` both named `Config`), and
/// containment is ambiguous whenever a class and one of its own members
/// share a start line (a single-line class body, e.g. `class Square
/// implements Shape { area() { return 2 } }` — the class node and its first
/// member node can have byte-identical line spans). A class declaration's
/// own start line is unique within a file and doesn't collide with a
/// member's start line unless the member is quite literally on the class's
/// declaration line — which is exactly the case the caller (`structural_relations.rs`)
/// resolves by filtering the exact-line match to `Class`/`Interface`-kind
/// symbols only, so the member never qualifies. Matches with no captured
/// class name (the anonymous class-expression pattern's optional `@name`,
/// e.g. `const w = class implements Runnable {}`) are skipped — nothing to
/// key them by, since anonymous classes have no declared symbol to attach to
/// either.
pub fn all_bases_in_file(lang: Language, src: &str) -> Vec<(u32, String, String)> {
    let query = compiled_implementors(lang);
    let base_idx = query
        .capture_index_for_name("base")
        .expect("implementors query defines @base");
    let name_idx = query
        .capture_index_for_name("name")
        .expect("implementors query defines @name");
    let class_idx = query
        .capture_index_for_name("class")
        .expect("implementors query defines @class");
    let Some(tree) = parse(lang, src) else {
        return Vec::new();
    };
    let mut out = Vec::new();
    let mut cursor = QueryCursor::new();
    let mut matches = cursor.matches(query, tree.root_node(), src.as_bytes());
    while let Some(m) = matches.next() {
        let base = m
            .captures()
            .iter()
            .find(|c| c.index == base_idx)
            .and_then(|c| c.node.utf8_text(src.as_bytes()).ok());
        let class_name = m
            .captures()
            .iter()
            .find(|c| c.index == name_idx)
            .and_then(|c| c.node.utf8_text(src.as_bytes()).ok());
        let class_line = m
            .captures()
            .iter()
            .find(|c| c.index == class_idx)
            .map(|c| line_of(src, c.node.byte_range().start));
        if let (Some(base), Some(class_name), Some(line)) = (base, class_name, class_line) {
            out.push((
                line,
                last_segment(class_name).to_string(),
                last_segment(base).to_string(),
            ));
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn all_language_queries_compile() {
        for lang in [
            Language::Python,
            Language::TypeScript,
            Language::Tsx,
            Language::Rust,
        ] {
            compiled_callers(lang);
            compiled_implementors(lang);
        }
    }

    #[test]
    fn abstract_class_bases_are_found() {
        let src = "abstract class Worker implements Runnable {\n  run() {}\n}\n";
        let bases = all_bases_in_file(Language::TypeScript, src);
        assert_eq!(
            bases,
            vec![(1, "Worker".to_string(), "Runnable".to_string())],
            "{bases:?}"
        );
    }

    #[test]
    fn rust_impl_blocks_report_the_type_as_the_implementor() {
        let src = "struct Store;\nimpl Backend for Store {\n    fn get(&self) {}\n}\n";
        let bases = all_bases_in_file(Language::Rust, src);
        assert_eq!(
            bases,
            vec![(2, "Store".to_string(), "Backend".to_string())],
            "{bases:?}"
        );
    }

    #[test]
    fn anonymous_class_expression_bases_are_skipped_not_misattributed() {
        // No declared name to key by — `structural_relations.rs`'s
        // attribution can't attach this to any symbol either, so skipping
        // here (rather than emitting a line with no real owner) is correct,
        // not a coverage gap.
        let src = "const w = class implements Runnable {\n  run() {}\n};\n";
        let bases = all_bases_in_file(Language::TypeScript, src);
        assert!(bases.is_empty(), "{bases:?}");
    }

    #[test]
    fn jsx_components_are_calls_but_intrinsics_are_not() {
        let src = "const a = <div><Button onClick={go} /><ns.Panel /></div>;\n";
        let calls = all_calls_in_file(Language::Tsx, src);
        let names: Vec<&str> = calls.iter().map(|(_, n)| n.as_str()).collect();
        assert!(names.contains(&"Button"), "{names:?}");
        assert!(
            names.contains(&"Panel"),
            "qualified element name: {names:?}"
        );
        assert!(!names.contains(&"div"), "intrinsic leaked in: {names:?}");
    }

    #[test]
    fn qualified_and_generic_bases_are_reduced_to_their_last_segment() {
        let ts = "class Panel extends React.Component<Props> implements ns.Iface, Other<T> {}\n";
        let bases = all_bases_in_file(Language::TypeScript, ts);
        let mut names: Vec<&str> = bases.iter().map(|(_, _, n)| n.as_str()).collect();
        names.sort();
        assert_eq!(names, vec!["Component", "Iface", "Other"], "{bases:?}");

        let py = "class Repo(abc.ABC, Base, metaclass=Meta):\n    pass\n";
        let mut py_names: Vec<String> = all_bases_in_file(Language::Python, py)
            .into_iter()
            .map(|(_, _, n)| n)
            .collect();
        py_names.sort();
        assert_eq!(py_names, vec!["ABC", "Base"], "metaclass= must stay out");
    }

    #[test]
    fn bare_and_method_calls_are_both_found() {
        let src = "fetch(real());\nclient.shouldRetry(1);\n";
        let calls = all_calls_in_file(Language::TypeScript, src);
        let names: Vec<&str> = calls.iter().map(|(_, n)| n.as_str()).collect();
        assert!(names.contains(&"fetch"), "{names:?}");
        assert!(names.contains(&"shouldRetry"), "{names:?}");
    }
}
