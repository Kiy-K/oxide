//! Regression: deeply nested sources must not overflow the stack while
//! indexing. `oxide index` aborted with "thread has overflowed its stack" on
//! prettier (`tests/format/flow-repo/union/yuge.js`, one union type whose
//! AST is ~5,000 levels deep) because `tags::collect_meta` recursed once per
//! level; `scanner::error_nodes` (every `.h` file) had the same shape, as
//! did the C/C++ declarator-chain helpers `function_declarator_of` and
//! `cpp_declarator_suffix` (`int ****…p`). All walk iteratively now.
//!
//! Each case runs on a thread with a deliberately small stack and a source
//! nested ~20,000 levels, so a recursive walk fails here without needing the
//! prettier repository — it aborts the test process, as it aborted indexing.

use oxide::parser::parse_file;
use oxide::scanner::language_for_source;
use oxide::symbols::Language;
use std::path::Path;

const DEPTH: usize = 20_000;
const SMALL_STACK: usize = 256 * 1024;

fn on_small_stack<T: Send + 'static>(f: impl FnOnce() -> T + Send + 'static) -> T {
    std::thread::Builder::new()
        .stack_size(SMALL_STACK)
        .spawn(f)
        .unwrap()
        .join()
        .unwrap()
}

#[test]
fn a_deep_typescript_union_type_indexes_on_a_small_stack() {
    let members: Vec<String> = (0..DEPTH).map(|i| format!("'m{i}'")).collect();
    let src = format!(
        "type Yuge = {};\nexport function after() {{}}\n",
        members.join(" | ")
    );
    let symbols = on_small_stack(move || parse_file("yuge.ts", &src, Language::TypeScript));
    let names: Vec<&str> = symbols.iter().map(|s| s.qualified_name.as_str()).collect();
    assert!(names.contains(&"Yuge"), "{names:?}");
    assert!(names.contains(&"after"), "{names:?}");
}

#[test]
fn metadata_after_a_deep_subtree_is_still_collected() {
    // The CommonJS `require` sits after a ~20,000-level binary expression:
    // the iterative walk must still reach it, exactly as the recursive
    // pre-order walk did.
    let chain = vec!["1"; DEPTH].join(" + ");
    let src = format!("const big = {chain};\nconst util = require('./util');\nfunction use() {{ return util; }}\n");
    let symbols = on_small_stack(move || parse_file("deep.js", &src, Language::JavaScript));
    assert!(
        symbols
            .iter()
            .any(|s| s.imports.iter().any(|i| i == "./util")),
        "require('./util') after the deep expression was not collected"
    );
}

#[test]
fn a_deep_c_header_resolves_its_language_on_a_small_stack() {
    let chain = vec!["1"; DEPTH].join(" + ");
    let src = format!("static const int big = {chain};\nint after(void);\n");
    let lang = on_small_stack(move || language_for_source(Path::new("deep.h"), &src));
    assert!(
        matches!(lang, Some(Language::C | Language::Cpp)),
        "{lang:?}"
    );
}

#[test]
fn a_deep_c_pointer_declarator_indexes_on_a_small_stack() {
    // `int ****…p;` nests one `pointer_declarator` per `*`; the C
    // `declaration` arm follows that chain looking for a function
    // declarator (`tags::function_declarator_of`).
    let src = format!(
        "int {}p;\nint after(void) {{ return 0; }}\n",
        "*".repeat(DEPTH)
    );
    let symbols = on_small_stack(move || parse_file("deep.c", &src, Language::C));
    assert!(symbols
        .iter()
        .any(|s| s.qualified_name.starts_with("after")));
}

#[test]
fn a_deep_cpp_parameter_declarator_indexes_on_a_small_stack() {
    // A parameter's declarator chain is rendered into the overload
    // signature (`tags::cpp_declarator_suffix`).
    let src = format!(
        "void take(int {}p);\nvoid after() {{}}\n",
        "*".repeat(DEPTH)
    );
    let symbols = on_small_stack(move || parse_file("deep.cpp", &src, Language::Cpp));
    assert!(symbols
        .iter()
        .any(|s| s.qualified_name.starts_with("after")));
}

#[test]
fn cpp_declarator_signatures_are_unchanged_by_the_iterative_rewrite() {
    // `cpp_declarator_suffix` and `function_declarator_of` became loops;
    // these renderings are what the recursive versions produced (checked
    // differentially against the parent commit's binary).
    let src = "namespace acme {\nstruct S {\n  void f(int *p);\n  void f(int **p);\n  \
               void f(int (&a)[3]);\n  void f(int *b[2][3]);\n  void f(char *&c);\n  \
               void f(int m[4][ 5 ]);\n  void f(int (**pp)[7]);\n};\n\
               char **k(char *argv[]);\n}\n";
    let names: Vec<String> = parse_file("decls.cpp", src, Language::Cpp)
        .into_iter()
        .map(|s| s.qualified_name)
        .collect();
    for want in [
        "acme.S.f(int*)",
        "acme.S.f(int**)",
        "acme.S.f(int&[3])",
        "acme.S.f(int*[2][3])",
        "acme.S.f(char*&)",
        "acme.S.f(int[4][5])",
        "acme.S.f(int**[7])",
        "acme.k(char*[])",
    ] {
        assert!(names.iter().any(|n| n == want), "missing {want}: {names:?}");
    }
}
