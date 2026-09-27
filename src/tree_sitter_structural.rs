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
//! `implementors_of`, `relations/mod.rs`).
//!
//! Query source stays declarative (`.scm` files under
//! `src/languages/queries/`): each `.scm` captures shape only (`@name`,
//! `@base`, `@call`, `@class`), and callers filter/attribute in Rust after
//! matching.

use crate::languages::{profile_for, tags::LanguageProfile};
use crate::symbols::Language;
use std::sync::OnceLock;
use tree_sitter::{Parser, Query, QueryCursor, StreamingIterator};

/// Compiled once per process, mirroring `tags.rs::TagsExtractor::config`'s
/// `OnceLock` precedent — that pass measured ~15x slower indexing from
/// recompiling a query per file, and `Query::new` does real work (parsing
/// the query source, resolving node-kind/field ids against the grammar).
struct LangQueries {
    callers: OnceLock<Query>,
    implementors: OnceLock<Query>,
}

/// One slot per `Language` (its discriminant; `Language::ALL` lists every
/// variant), so each language keeps its own compiled pair — JavaScript and
/// TSX share a grammar and query source but not a cache, as before.
static QUERIES: [LangQueries; Language::ALL.len()] = [const {
    LangQueries {
        callers: OnceLock::new(),
        implementors: OnceLock::new(),
    }
}; Language::ALL.len()];

fn profile(lang: Language) -> &'static LanguageProfile {
    profile_for(lang).expect(
        "markdown has no grammar; compute_file_relations skips it via has_structural_queries",
    )
}

fn ts_language(lang: Language) -> tree_sitter::Language {
    (profile(lang).ts_language)()
}

/// `Query::new` fails only for a query source that references a node kind
/// or field the grammar doesn't have — a static, per-language `.scm` file
/// mismatching its own grammar is a programming error, not a runtime
/// condition callers should handle. `tests::all_language_queries_compile`
/// exercises all six combinations so a grammar-divergent query source
/// (TS vs TSX diverging on a node kind) surfaces as a test failure, not a
/// first-caller panic.
fn compiled_callers(lang: Language) -> &'static Query {
    QUERIES[lang as usize].callers.get_or_init(|| {
        Query::new(&ts_language(lang), profile(lang).callers_query)
            .expect("static callers query compiles")
    })
}

fn compiled_implementors(lang: Language) -> &'static Query {
    QUERIES[lang as usize].implementors.get_or_init(|| {
        Query::new(&ts_language(lang), profile(lang).implementors_query)
            .expect("static implementors query compiles")
    })
}

/// Last segment of a possibly-qualified, possibly-generic name: `abc.ABC`
/// and `React.Component` become `ABC` and `Component`, `Iface<T>` becomes
/// `Iface`, and PHP's `App\\Contracts\\Backend` / `\\JsonSerializable`
/// become `Backend` / `JsonSerializable`. `calls`/`bases` are a bare-name tier by construction (see
/// AGENTS.md) — the queries capture the qualified node so the relation
/// exists at all, and this is where it is brought back onto that tier. It
/// is not resolution: `ns.Base` and a local `Base` are indistinguishable
/// afterwards, exactly as two same-named definitions already are.
fn last_segment(name: &str) -> &str {
    name.split(['<', '(', '!'])
        .next()
        .unwrap_or(name)
        .trim()
        .rsplit(['.', ':', '\\'])
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
    parse(lang, src).map_or_else(Vec::new, |tree| calls_in_tree(lang, &tree, src))
}

/// One [`all_calls_in_file`] entry: `(start_line, callee_name)`.
pub type CallSite = (u32, String);
/// One [`all_bases_in_file`] entry: `(class_start_line, class_name, base_name)`.
pub type BaseClause = (u32, String, String);
/// A file's call sites and base clauses, as [`all_calls_and_bases_in_file`]
/// returns them.
pub type StructuralSites = (Vec<CallSite>, Vec<BaseClause>);

/// [`all_calls_in_file`] and [`all_bases_in_file`] from a single parse —
/// what `structural_relations::compute_file_relations` uses, so a reparsed
/// file pays for one tree here instead of two. Each half is exactly what
/// its standalone function returns.
pub fn all_calls_and_bases_in_file(lang: Language, src: &str) -> StructuralSites {
    parse(lang, src).map_or_else(Default::default, |tree| {
        calls_and_bases_in_tree(lang, &tree, src)
    })
}

/// [`all_calls_and_bases_in_file`] on a tree the caller already parsed from
/// `src` with `lang`'s grammar (`tags.rs` shares the one it builds for
/// extraction metadata). A tree from any other grammar or source would
/// silently answer for that one instead.
pub(crate) fn calls_and_bases_in_tree(
    lang: Language,
    tree: &tree_sitter::Tree,
    src: &str,
) -> StructuralSites {
    (
        calls_in_tree(lang, tree, src),
        bases_in_tree(lang, tree, src),
    )
}

fn calls_in_tree(lang: Language, tree: &tree_sitter::Tree, src: &str) -> Vec<(u32, String)> {
    let query = compiled_callers(lang);
    let name_idx = query
        .capture_index_for_name("name")
        .expect("callers query defines @name");
    let call_idx = query
        .capture_index_for_name("call")
        .expect("callers query defines @call");
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
    parse(lang, src).map_or_else(Vec::new, |tree| bases_in_tree(lang, &tree, src))
}

fn bases_in_tree(
    lang: Language,
    tree: &tree_sitter::Tree,
    src: &str,
) -> Vec<(u32, String, String)> {
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
    fn shared_parse_matches_separate_calls_and_bases() {
        for (rel, src, lang) in crate::languages::conformance_sources() {
            if !lang.has_structural_queries() {
                continue;
            }
            assert_eq!(
                all_calls_and_bases_in_file(lang, &src),
                (all_calls_in_file(lang, &src), all_bases_in_file(lang, &src)),
                "{rel}"
            );
        }
    }

    #[test]
    fn all_language_queries_compile() {
        // Markdown is deliberately excluded: it has no grammar, and
        // `structural_relations::compute_file_relations` never reaches this
        // module for it (`Language::has_structural_queries`).
        for &lang in Language::ALL.iter().filter(|l| l.has_structural_queries()) {
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
    fn rust_qualified_calls_and_macros_are_found() {
        let src = "fn f() {\n    std::println!(\"x\");\n    crate::metrics::emit!(1);\n    log_it!(2);\n    crate::net::get();\n}\n";
        let calls = all_calls_in_file(Language::Rust, src);
        let mut names: Vec<&str> = calls.iter().map(|(_, n)| n.as_str()).collect();
        names.sort();
        assert_eq!(names, vec!["emit", "get", "log_it", "println"], "{calls:?}");
    }

    #[test]
    fn rust_scoped_implementor_types_are_matched() {
        let src = "pub struct Store;\n\nimpl Backend for crate::store::Store {\n    fn get(&self) {}\n}\n";
        let bases = all_bases_in_file(Language::Rust, src);
        assert_eq!(
            bases,
            vec![(3, "Store".to_string(), "Backend".to_string())],
            "{bases:?}"
        );
    }

    #[test]
    fn qualified_generic_traits_and_pointer_embeddings_are_matched() {
        // Both found by review: the alternations covered the qualified and
        // the generic shape, but not the two composed.
        let rust = "impl external::Trait<T> for Local {}\n";
        assert_eq!(
            all_bases_in_file(Language::Rust, rust),
            vec![(1, "Local".to_string(), "Trait".to_string())]
        );
        let go = "type Store struct {\n\t*pkg.Base\n}\n";
        assert_eq!(
            all_bases_in_file(Language::Go, go),
            vec![(1, "Store".to_string(), "Base".to_string())]
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
    fn java_extends_and_implements_each_produce_their_own_base() {
        // Upstream tags reports `type_list` once per name but at the
        // *clause's* range, so `Backend` and `Cloneable` come out sharing a
        // byte range and nothing can tell them apart — the reason Java gets
        // an OXIDE-owned implementors query (docs/java-feasibility).
        let src = "@Service\nclass Store extends Base implements Backend, Cloneable {\n}\n";
        let mut bases = all_bases_in_file(Language::Java, src);
        bases.sort();
        assert_eq!(
            bases,
            vec![
                // Line 1, not 2: Java's declaration node includes its
                // annotations, so the class's own `start_line` is the
                // annotation's — and the two must agree or
                // `structural_relations` cannot attribute the clause.
                (1, "Store".to_string(), "Backend".to_string()),
                (1, "Store".to_string(), "Base".to_string()),
                (1, "Store".to_string(), "Cloneable".to_string()),
            ],
            "{bases:?}"
        );
    }

    #[test]
    fn java_interface_enum_and_record_heritage_is_matched() {
        for (src, want) in [
            (
                "interface A extends B, C {}\n",
                vec![
                    (1, "A".to_string(), "B".to_string()),
                    (1, "A".to_string(), "C".to_string()),
                ],
            ),
            (
                "enum Mode implements Named {\n  FAST\n}\n",
                vec![(1, "Mode".to_string(), "Named".to_string())],
            ),
            (
                "record Point(int x) implements Comparable<Point> {}\n",
                vec![(1, "Point".to_string(), "Comparable".to_string())],
            ),
        ] {
            let mut got = all_bases_in_file(Language::Java, src);
            got.sort();
            assert_eq!(got, want, "{src}");
        }
    }

    #[test]
    fn java_calls_cover_invocations_and_constructions_but_not_method_references() {
        let src = "\
class S {
  void run() {
    helper();
    other.compute();
    new java.util.ArrayList<String>();
    list.forEach(S::consume);
  }
}
";
        let mut calls: Vec<String> = all_calls_in_file(Language::Java, src)
            .into_iter()
            .map(|(_, n)| n)
            .collect();
        calls.sort();
        assert_eq!(
            calls,
            vec!["ArrayList", "compute", "forEach", "helper"],
            "a method reference names no unambiguous callee and is skipped"
        );
    }

    #[test]
    fn javascript_reuses_the_tsx_call_and_heritage_patterns_jsx_included() {
        let src = "\
class Panel extends React.Component {
  render() {
    return <Button label={fmt(this.props.l)} />;
  }
}
function App() { return <div><Panel /></div>; }
";
        assert_eq!(
            all_bases_in_file(Language::JavaScript, src),
            vec![(1, "Panel".to_string(), "Component".to_string())]
        );
        let mut calls: Vec<String> = all_calls_in_file(Language::JavaScript, src)
            .into_iter()
            .map(|(_, n)| n)
            .collect();
        calls.sort();
        assert_eq!(
            calls,
            vec!["Button", "Panel", "fmt"],
            "JSX components count as calls; the `div` intrinsic does not"
        );
    }

    #[test]
    fn ruby_mixins_count_as_bases_alongside_real_inheritance() {
        // `include`/`extend`/`prepend` are how Ruby actually composes
        // behavior; `<` is the only true inheritance it has. Both land in
        // `bases`, and a qualified mixin reduces to its last segment.
        let src = "\
module Acme
  class Store < Base
    include Loggable
    extend ActiveSupport::Concern
  end
end
";
        let mut bases = all_bases_in_file(Language::Ruby, src);
        bases.sort();
        assert_eq!(
            bases,
            vec![
                (2, "Store".to_string(), "Base".to_string()),
                (2, "Store".to_string(), "Concern".to_string()),
                (2, "Store".to_string(), "Loggable".to_string()),
            ],
            "{bases:?}"
        );
    }

    #[test]
    fn ruby_conditional_mixins_are_not_declared_bases() {
        // Only a *direct* child of the class body counts. An `include`
        // behind a conditional is conditional behavior, and walking deeper
        // would also hand a nested class's mixins to its enclosing one.
        let src = "\
class Store
  if RUBY_VERSION > \"3\"
    include Modern
  end
  class Inner
    include Nested
  end
end
";
        let bases = all_bases_in_file(Language::Ruby, src);
        let names: Vec<&str> = bases.iter().map(|(_, _, b)| b.as_str()).collect();
        assert_eq!(names, vec!["Nested"], "{bases:?}");
    }

    #[test]
    fn ruby_construction_attributes_to_the_class_not_to_new() {
        let src = "\
def build
  Acme::Store.new(\"x\").get(1)
  helper(2)
  include Thing
end
";
        let mut calls: Vec<String> = all_calls_in_file(Language::Ruby, src)
            .into_iter()
            .map(|(_, n)| n)
            .collect();
        calls.sort();
        assert_eq!(
            calls,
            vec!["Store", "get", "helper"],
            "`new` resolves to the constructed class, and a declaration \
             macro already recorded as a base is not also a call"
        );
    }

    #[test]
    fn php_traits_extends_and_implements_all_land_in_bases() {
        // A namespaced or leading-backslash base reduces to its last
        // segment — the reason `last_segment` splits on `\\` too — and
        // `use Trait;` inside the body is PHP's mixin, the same relation
        // Ruby's `include` carries.
        let src = "<?php\nfinal class Store extends Base implements Readable, \\JsonSerializable\n{\n    use App\\Support\\Cacheable;\n}\n";
        let mut bases = all_bases_in_file(Language::Php, src);
        bases.sort();
        assert_eq!(
            bases,
            vec![
                (2, "Store".to_string(), "Base".to_string()),
                (2, "Store".to_string(), "Cacheable".to_string()),
                (2, "Store".to_string(), "JsonSerializable".to_string()),
                (2, "Store".to_string(), "Readable".to_string()),
            ],
            "{bases:?}"
        );
    }

    #[test]
    fn php_calls_cover_free_member_static_and_construction() {
        let src = "<?php\nfunction build() {\n    helper(1);\n    $obj->run();\n    Store::create('x');\n    new Store();\n    new self();\n    $fn();\n}\n";
        let mut calls: Vec<String> = all_calls_in_file(Language::Php, src)
            .into_iter()
            .map(|(_, n)| n)
            .collect();
        calls.sort();
        assert_eq!(
            calls,
            vec!["Store", "create", "helper", "run"],
            "`new self()` names the enclosing class under another spelling, \
             and a variable call names a runtime value"
        );
    }

    #[test]
    fn c_first_member_struct_embedding_is_the_only_base_relation() {
        // First member only: any-position would report ordinary
        // composition — a struct that merely holds another — as
        // inheritance.
        let embed = "struct derived {\n    struct base base;\n    int extra;\n};\n";
        assert_eq!(
            all_bases_in_file(Language::C, embed),
            vec![(1, "derived".to_string(), "base".to_string())]
        );
        let compose = "struct holder {\n    int tag;\n    struct base b;\n};\n";
        assert!(
            all_bases_in_file(Language::C, compose).is_empty(),
            "composition is not inheritance"
        );
    }

    #[test]
    fn c_calls_include_dispatch_through_a_struct_field() {
        // `s->ops->read(buf)` is how C does virtual dispatch; a query that
        // only matched bare identifiers would miss every one.
        let src =
            "int run(struct store *s) {\n    helper(1);\n    return s->ops->read(s->buf);\n}\n";
        let mut calls: Vec<String> = all_calls_in_file(Language::C, src)
            .into_iter()
            .map(|(_, n)| n)
            .collect();
        calls.sort();
        assert_eq!(calls, vec!["helper", "read"]);
    }

    #[test]
    fn c_embedding_requires_a_direct_first_member() {
        let src = "\
struct base { int x; };
struct value_holder { struct base value; };
struct pointer_holder { struct base *value; };
struct array_holder { struct base value[2]; };
";
        assert_eq!(
            all_bases_in_file(Language::C, src),
            vec![(2, "value_holder".to_string(), "base".to_string())]
        );
    }

    #[test]
    fn cpp_bases_ignore_access_specifiers() {
        let src = "class Store {};\nclass MemoryStore : public Store {};\n";
        assert_eq!(
            all_bases_in_file(Language::Cpp, src),
            vec![(2, "MemoryStore".to_string(), "Store".to_string())]
        );
    }

    #[test]
    fn cpp_calls_include_qualified_and_member_dispatch() {
        let src = "void h() { f(); ns::g(); A::make(); obj.run(); }\n";
        let mut calls: Vec<String> = all_calls_in_file(Language::Cpp, src)
            .into_iter()
            .map(|(_, name)| name)
            .collect();
        calls.sort();
        assert_eq!(calls, vec!["f", "g", "make", "run"]);
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
