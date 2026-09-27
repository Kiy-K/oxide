//! Research-only (issue #23) shim: CodeGraph Kernel as a plain Rust library.
//!
//! Every module below is the kernel's own source file, unmodified, pulled in
//! by `#[path]`. Only `lib.rs` is replaced: the napi `extract_file` becomes a
//! Rust function returning the same `EmitOut` buffers, and `decode` turns
//! those flat buffers back into structs (the job `src/extraction/kernel/
//! layout.ts` does on the TS side).
#![allow(dead_code, unused_imports, clippy::all)]

macro_rules! stack_guard {
    () => {
        if $crate::stack::exhausted() {
            return ::core::default::Default::default();
        }
    };
}

#[path = "../vendor/codegraph/codegraph-kernel/src/buffers.rs"]
mod buffers;
#[path = "../vendor/codegraph/codegraph-kernel/src/cfnptr.rs"]
mod cfnptr;
#[path = "../vendor/codegraph/codegraph-kernel/src/csharp.rs"]
mod csharp;
#[path = "../vendor/codegraph/codegraph-kernel/src/dart.rs"]
mod dart;
#[path = "../vendor/codegraph/codegraph-kernel/src/docstring.rs"]
mod docstring;
#[path = "../vendor/codegraph/codegraph-kernel/src/ids.rs"]
mod ids;
#[path = "../vendor/codegraph/codegraph-kernel/src/go.rs"]
mod go;
#[path = "../vendor/codegraph/codegraph-kernel/src/java.rs"]
mod java;
#[path = "../vendor/codegraph/codegraph-kernel/src/kotlin.rs"]
mod kotlin;
#[path = "../vendor/codegraph/codegraph-kernel/src/langs.rs"]
mod langs;
#[path = "../vendor/codegraph/codegraph-kernel/src/lua.rs"]
mod lua;
#[path = "../vendor/codegraph/codegraph-kernel/src/php.rs"]
mod php;
#[path = "../vendor/codegraph/codegraph-kernel/src/rlang.rs"]
mod rlang;
#[path = "../vendor/codegraph/codegraph-kernel/src/ruby.rs"]
mod ruby;
#[path = "../vendor/codegraph/codegraph-kernel/src/rustlang.rs"]
mod rustlang;
#[path = "../vendor/codegraph/codegraph-kernel/src/scala.rs"]
mod scala;
#[path = "../vendor/codegraph/codegraph-kernel/src/stack.rs"]
mod stack;
#[path = "../vendor/codegraph/codegraph-kernel/src/swift.rs"]
mod swift;
#[path = "../vendor/codegraph/codegraph-kernel/src/textutil.rs"]
mod textutil;
#[path = "../vendor/codegraph/codegraph-kernel/src/python.rs"]
mod python;
#[path = "../vendor/codegraph/codegraph-kernel/src/ccpp/mod.rs"]
mod ccpp;
#[path = "../vendor/codegraph/codegraph-kernel/src/tsjs/mod.rs"]
mod tsjs;

pub mod decode;

pub use buffers::EmitOut;

/// Languages the kernel binary reports (`contractInfo().languages`).
pub fn languages() -> &'static [&'static str] {
    &langs::LANGUAGES
}

/// Mirror of the kernel's napi `extract_file` dispatch, verbatim.
pub fn extract_file(file_path: &str, content: &str, language: &str) -> Result<EmitOut, String> {
    stack::run_guarded(|| match language {
        "java" => java::extract(file_path, content),
        "python" => python::extract(file_path, content),
        "go" => go::extract(file_path, content),
        "c" | "cpp" => ccpp::extract(file_path, content, language),
        "rust" => rustlang::extract(file_path, content),
        "csharp" => csharp::extract(file_path, content),
        "ruby" => ruby::extract(file_path, content),
        "php" => php::extract(file_path, content),
        "swift" => swift::extract(file_path, content),
        "kotlin" => kotlin::extract(file_path, content),
        "r" => rlang::extract(file_path, content),
        "lua" | "luau" => lua::extract(file_path, content, language),
        "scala" => scala::extract(file_path, content),
        "dart" => dart::extract(file_path, content),
        _ => tsjs::extract(file_path, content, language),
    })
}

/// One bare tree-sitter parse with the kernel's own grammar for `language`
/// (harness-only; the kernel itself parses exactly once per extract).
pub fn parse_only(language: &str, content: &str) -> usize {
    let Some(g) = langs::grammar_for(language) else { return 0 };
    let mut p = tree_sitter::Parser::new();
    p.set_language(&g).unwrap();
    p.parse(content, None).map_or(0, |t| t.root_node().child_count())
}

pub(crate) fn node_kind_name(i: u8) -> &'static str {
    buffers::NODE_KINDS.get(i as usize).copied().unwrap_or("?")
}

pub(crate) fn edge_kind_name(i: u8) -> &'static str {
    if i == buffers::FUNCTION_REF_CODE {
        return "function_ref";
    }
    buffers::EDGE_KINDS.get(i as usize).copied().unwrap_or("?")
}
