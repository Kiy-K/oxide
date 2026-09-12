//! Generic tree-sitter-tags-backed extraction, normalized into OXIDE's
//! `Symbol` IR. One `TagsExtractor` per `LanguageProfile` — no per-language
//! procedural AST walk for definitions/references.
//!
//! Upstream tags are flat: no parent/containment, and for Python, no
//! method-vs-function split. OXIDE reconstructs both here via a
//! byte-range-containment stack over the sorted definition list, which is
//! why definition dedup in `parser.rs` (keyed on `qualified_name`) stays
//! load-bearing — see `containment` below and the `same_named_methods_in_
//! different_classes_do_not_collide` test.
//!
//! Neither upstream `tags.scm` covers imports, `export` wrapping, or (for
//! TypeScript) `type_alias_declaration`/`enum_declaration` as *definitions*.
//! type_alias/enum are filled by two lines appended to the query itself
//! (see `queries/typescript_tags.scm`); imports and the exported flag need
//! actual tree structure (a `source` field, an `export_statement` ancestor)
//! that no tag capture exposes, so `collect_meta` walks the same parse tree
//! once, narrowly, for those — plus decorator ranges, which
//! `decorator_extended_start` uses to widen a decorated definition's span
//! back over its decorators. Still a fixed handful of node kinds, not a
//! general AST walker.

use super::LanguageExtractor;
use crate::symbols::{content_hash, Language, Symbol, SymbolKind};
use std::ops::Range;
use std::sync::OnceLock;
use tree_sitter::{Node, Parser};
use tree_sitter_tags::{TagsConfiguration, TagsContext};

pub struct LanguageProfile {
    pub language: Language,
    pub ts_language: fn() -> tree_sitter::Language,
    pub tags_query: &'static str,
    pub locals_query: &'static str,
}

/// Compiling `tags_query` (js+ts concatenated, several hundred lines) is
/// expensive — measured ~15x slower indexing than the handwritten extractor
/// when redone per file. `config` compiles it once per process, on first use.
pub struct TagsExtractor {
    pub profile: &'static LanguageProfile,
    config: OnceLock<Option<TagsConfiguration>>,
}

impl TagsExtractor {
    pub const fn new(profile: &'static LanguageProfile) -> Self {
        TagsExtractor {
            profile,
            config: OnceLock::new(),
        }
    }

    fn config(&self) -> Option<&TagsConfiguration> {
        self.config
            .get_or_init(|| {
                TagsConfiguration::new(
                    (self.profile.ts_language)(),
                    self.profile.tags_query,
                    self.profile.locals_query,
                )
                .ok()
            })
            .as_ref()
    }
}

struct RawDef {
    /// Byte range the symbol's span, hash and containment all use — the
    /// tag's own range, except where a language widens it (C functions, see
    /// [`FileMeta::c_function_spans`]).
    start: usize,
    end: usize,
    /// The tag's *unwidened* start byte. Every per-language map in
    /// [`FileMeta`] is keyed by it, so widening a span can never silently
    /// unhook a lookup.
    tag_start: usize,
    line_start: u32,
    line_end: u32,
    name: String,
    kind: SymbolKind,
}

/// 1-indexed row containing `offset`. `tag.span` from tree-sitter-tags is
/// only the *name* node's position (ctags "jump here" line) — the body's
/// actual extent is `tag.range` (byte range), so row numbers must be derived
/// from byte offsets directly rather than trusting `span`.
fn byte_to_line(bytes: &[u8], offset: usize) -> u32 {
    1 + bytes[..offset.min(bytes.len())]
        .iter()
        .filter(|&&b| b == b'\n')
        .count() as u32
}

/// 1-indexed row of a span's last *content* byte. A node's byte range can
/// end past the newline that terminates it — C's `preproc_def` does, since
/// the directive is newline-terminated — and taking that offset's row
/// verbatim puts the symbol's `end_line` on the *following* line, so
/// `#define A` would report a span covering the `#define B` under it.
fn span_end_line(bytes: &[u8], end: usize) -> u32 {
    let mut end = end.min(bytes.len());
    while end > 0 && (bytes[end - 1] as char).is_whitespace() {
        end -= 1;
    }
    byte_to_line(bytes, end)
}

/// Matches the old extractors' own body reconstruction (and `Symbol::
/// span_text()`) byte-for-byte: whole lines, including leading indentation.
fn span_lines(src: &str, start: u32, end: u32) -> String {
    src.lines()
        .skip(start.saturating_sub(1) as usize)
        .take(end.saturating_sub(start - 1) as usize)
        .collect::<Vec<_>>()
        .join("\n")
}

fn first_line(text: &str) -> String {
    text.lines()
        .next()
        .unwrap_or("")
        .trim()
        .chars()
        .take(200)
        .collect()
}

fn map_kind(name: &str) -> Option<SymbolKind> {
    match name {
        "class" => Some(SymbolKind::Class),
        "interface" => Some(SymbolKind::Interface),
        "function" => Some(SymbolKind::Function),
        "method" => Some(SymbolKind::Method),
        "constant" => Some(SymbolKind::Constant),
        "module" => Some(SymbolKind::Module),
        "type_alias" => Some(SymbolKind::TypeAlias),
        // Go's tags.scm gives every `type X ...` one syntax type; interfaces
        // are separated out from the parse tree in `extract`.
        "type" => Some(SymbolKind::Class),
        "enum" => Some(SymbolKind::Enum),
        _ => None,
    }
}

fn parse(profile: &LanguageProfile, src: &str) -> Option<tree_sitter::Tree> {
    let mut parser = Parser::new();
    parser.set_language(&(profile.ts_language)()).ok()?;
    parser.parse(src, None)
}

/// The handful of things `collect_meta`'s single walk gathers because no tag
/// capture exposes them. Bundled rather than passed as five out-params.
#[derive(Default)]
struct FileMeta {
    imports: Vec<String>,
    /// Byte ranges of `export_statement` wrappers (TypeScript/TSX).
    exports: Vec<Range<usize>>,
    /// Byte ranges of decorator nodes, for `decorator_extended_start`.
    decorators: Vec<Range<usize>>,
    /// Go only: `(method_declaration start byte, receiver type name)`. Go
    /// methods are top-level declarations, so the containment stack has
    /// nothing to qualify them with, and two types each with a `String()`
    /// method would collide on the bare name under parser.rs's dedup. The
    /// receiver is a field on the declaration itself, so the qualified name
    /// is derivable — just not from the flat tag list.
    go_receivers: Vec<(usize, String)>,
    /// Rust only: start bytes of `impl_item` nodes. An impl block is
    /// captured as a Class named after its type purely so the containment
    /// stack can nest the impl's methods; when the same file also *declares*
    /// that type, the impl's twin symbol must lose. `parser.rs`'s first-wins
    /// dedup alone cannot decide that — it keeps whichever comes first, and
    /// `impl Store {}` may legally precede `struct Store;`.
    rust_impls: Vec<usize>,
    /// Go only: start bytes of `type_spec` nodes whose type is an
    /// `interface_type`. Upstream tags.scm gives every `type X ...` the same
    /// syntax type, so struct and interface are indistinguishable from tags
    /// alone.
    go_interfaces: Vec<usize>,
    /// Java only: `(method/constructor declaration start byte, normalized
    /// parameter-type list)`, e.g. `(256, "String,String")`. See
    /// [`java_signature`].
    java_params: Vec<(usize, String)>,
    /// Ruby only: `(method declaration start byte, receiver prefix)` for
    /// every *singleton* (class-level) method — `def self.create` gives
    /// `(start, "self.")`, `def Foo.bar` gives `(start, "Foo.self.")`,
    /// and a `def` inside a `class << self` block gives `"self."` too.
    ///
    /// Ruby tags report a singleton method under its bare name, so
    /// `def get` and `def self.get` in one class both qualify as `Store.get`
    /// and `parser.rs::parse_file_with`'s first-wins dedup silently drops
    /// the second — the same failure Java overloads had. The prefix is
    /// The inserted `.self.` marks an explicit singleton receiver as
    /// class-level, so `def Foo.bar` cannot collide with `Foo#bar`.
    ruby_singletons: Vec<(usize, String)>,
    /// C only: `(function_declarator start byte, enclosing
    /// function_definition byte range)`.
    ///
    /// `c_tags.scm` tags the *declarator*, because a return type can carry
    /// any number of pointer levels and no finite set of query patterns can
    /// enumerate the wrappers those add above the declarator. The price is
    /// that the tag's range is `name(params)` with no body at all — and a
    /// symbol without its body has the wrong `content_hash`, the wrong
    /// span, and contributes none of the body tokens the lexical index
    /// weights. This restores the real range.
    c_function_spans: Vec<(usize, Range<usize>)>,
    /// C only: start bytes of `function_declarator`s that are bare
    /// *prototypes* (`static void helper(int);`) rather than definitions.
    ///
    /// A `.c` file that forward-declares a static and then defines it
    /// produces two tags with one qualified name, and `parse_file_with`'s
    /// first-wins dedup keeps whichever comes first — which is always the
    /// prototype, a one-line symbol with no body. Treated exactly like
    /// [`Self::rust_impls`]: a stand-in that loses to a real declaration of
    /// the same name in the same file.
    c_prototypes: Vec<usize>,
    /// C++ mirrors C's declaration-vs-definition and body-span rules, with
    /// signatures and qualified out-of-class methods layered on top.
    cpp_function_spans: Vec<(usize, Range<usize>)>,
    cpp_prototypes: Vec<usize>,
    cpp_params: Vec<(usize, String, String)>,
    cpp_casts: Vec<(usize, String)>,
    cpp_scopes: Vec<(usize, String)>,
}

/// The `function_declarator` at the bottom of a declarator chain, looking
/// through the pointer/array/attribute wrappers a return type can add
/// (`char **f(void)`, `__attribute__((pure)) int g(void)`). Recursive
/// rather than depth-enumerated because the chain has no bound.
fn function_declarator_of<'a>(node: Node<'a>) -> Option<Node<'a>> {
    let decl = node.child_by_field_name("declarator")?;
    if decl.kind() == "function_declarator" {
        return Some(decl);
    }
    function_declarator_of(decl)
}

/// One Java parameter type, normalized the way the JVM's own overload rules
/// are: generics erased (`List<String>` and `List<Integer>` cannot coexist
/// as overloads, so keeping the argument would only make two names for what
/// Java treats as one), package qualifiers dropped to the last segment
/// (`java.util.Set` and an imported `Set` are the same type written two
/// ways), array dimensions kept (`byte[]` really is a distinct overload).
/// Parameter *names*, `final`, and parameter annotations never appear: they
/// are separate grammar children, and renaming a parameter — or annotating
/// it — must not change a symbol's persisted identity.
fn java_type_name(text: &str) -> String {
    // Strip every balanced `<...>` region first, so dimensions belonging to
    // an array-of-generic (`List<String>[]`) survive the erasure.
    let mut base = String::new();
    let mut depth = 0usize;
    for ch in text.chars() {
        match ch {
            '<' => depth += 1,
            '>' => depth = depth.saturating_sub(1),
            _ if depth == 0 => base.push(ch),
            _ => {}
        }
    }
    let dims = base.matches('[').count();
    let head = base.split('[').next().unwrap_or(&base);
    let short = head.rsplit('.').next().unwrap_or(head).trim();
    // A *type* annotation (JSR-308: `java.util.@NonNull List`) lives inside
    // the type node, unlike a parameter annotation, which the grammar puts
    // in a sibling `modifiers`. Without dropping it here, adding or removing
    // `@NonNull` would rewrite the method's qualified name — and therefore
    // its persisted id — for a method that did not change, orphaning its
    // embedding and re-embedding it on the next index.
    let mut out: String = short
        .split_whitespace()
        .filter(|t| !t.starts_with('@'))
        .collect();
    for _ in 0..dims {
        out.push_str("[]");
    }
    out
}

/// Comma-joined normalized parameter types of a `formal_parameters` node, in
/// declaration order — the discriminator that keeps Java overloads apart.
///
/// Java overloading is idiomatic and pervasive, so without this every
/// `Store.get(...)` in a file collapses to the qualified name `Store.get`
/// and `parser.rs::parse_file_with`'s first-wins dedup silently drops the
/// rest (docs/java-feasibility/README.md's blocker). Putting the signature
/// in the *name* rather than changing `Symbol::id`'s composition is what
/// makes this safe: the id formula is still `FNV1a(file + \0 +
/// qualified_name)`, so every Python/TypeScript/TSX/Rust/Go id in every
/// existing index is bit-identical and nothing re-embeds
/// (`tests/language_conformance.rs`'s committed goldens are the proof).
///
/// A `spread_parameter` (`int... flags`) has no `type` field — its type is
/// its first named child — and erases to an array, exactly as the JVM does,
/// so `f(int...)` and `f(int[])` normalize alike and cannot both exist.
fn java_signature(params: Node<'_>, src: &str) -> String {
    let mut out: Vec<String> = Vec::new();
    let mut cur = params.walk();
    for child in params.named_children(&mut cur) {
        match child.kind() {
            "formal_parameter" => {
                if let Some(t) = child.child_by_field_name("type") {
                    if let Ok(text) = t.utf8_text(src.as_bytes()) {
                        out.push(java_type_name(text));
                    }
                }
            }
            "spread_parameter" => {
                // Unlike `formal_parameter`, a spread parameter exposes no
                // `type` field — its type is a positional child. It is not
                // reliably the *first* one, though: `final int... flags` and
                // `@NonNull String... xs` put a `modifiers` node ahead of it,
                // which produced `f(final[])` and `f([])` and, worse, moved
                // the method's persisted id whenever somebody added `final`
                // or an annotation (found by review).
                if let Some(t) = child
                    .named_children(&mut child.walk())
                    .find(|c| c.kind() != "modifiers")
                {
                    if let Ok(text) = t.utf8_text(src.as_bytes()) {
                        out.push(format!("{}[]", java_type_name(text)));
                    }
                }
            }
            // `receiver_parameter` (`Outer Outer.this`) is not an argument.
            _ => {}
        }
    }
    out.join(",")
}

/// C++ overload identity keeps template arguments (unlike Java's JVM-shaped
/// erasure), normalizes punctuation whitespace, and ignores parameter names
/// and defaults. `const`/`volatile` qualifiers on the type and `&`/`&&`
/// declarators remain because they can distinguish overloads.
fn cpp_type_name(text: &str) -> String {
    let mut out = String::new();
    let mut pending_space = false;
    for ch in text.trim().chars() {
        if ch.is_whitespace() {
            pending_space = !out.is_empty();
            continue;
        }
        let punctuation = matches!(ch, ':' | '<' | '>' | ',' | '*' | '&' | '[' | ']');
        if pending_space && !punctuation && !out.ends_with([':', '<', ',', '*', '&', '[']) {
            out.push(' ');
        }
        if punctuation && out.ends_with(' ') {
            out.pop();
        }
        out.push(ch);
        pending_space = false;
    }
    out
}

fn cpp_declarator_suffix(node: Node<'_>, src: &str) -> String {
    match node.kind() {
        "identifier" | "field_identifier" => String::new(),
        "reference_declarator" | "pointer_declarator" => {
            let Some(inner) = node.named_child(0) else {
                return String::new();
            };
            let prefix = &src[node.start_byte()..inner.start_byte()];
            let mut suffix: String = prefix.chars().filter(|c| matches!(c, '*' | '&')).collect();
            suffix.push_str(&cpp_declarator_suffix(inner, src));
            suffix
        }
        "array_declarator" => {
            let Some(inner) = node.named_child(0) else {
                return String::new();
            };
            let mut suffix = cpp_declarator_suffix(inner, src);
            suffix
                .push_str(&src[inner.end_byte()..node.end_byte()].replace(char::is_whitespace, ""));
            suffix
        }
        "parenthesized_declarator" => node
            .named_child(0)
            .map(|inner| cpp_declarator_suffix(inner, src))
            .unwrap_or_default(),
        _ => String::new(),
    }
}

fn cpp_parameter_type(param: Node<'_>, src: &str) -> Option<String> {
    let ty = param.child_by_field_name("type")?;
    let mut prefix = param
        .named_children(&mut param.walk())
        .filter(|child| child.kind() == "type_qualifier")
        .filter_map(|child| child.utf8_text(src.as_bytes()).ok())
        .collect::<Vec<_>>();
    prefix.push(ty.utf8_text(src.as_bytes()).ok()?);
    let mut out = cpp_type_name(&prefix.join(" "));
    if let Some(declarator) = param.child_by_field_name("declarator") {
        out.push_str(&cpp_declarator_suffix(declarator, src));
    }
    Some(out)
}

fn cpp_signature(params: Node<'_>, src: &str) -> String {
    params
        .named_children(&mut params.walk())
        .filter(|child| {
            matches!(
                child.kind(),
                "parameter_declaration" | "optional_parameter_declaration"
            )
        })
        .filter_map(|child| cpp_parameter_type(child, src))
        .collect::<Vec<_>>()
        .join(",")
}

fn cpp_method_qualifier(declarator: Node<'_>, src: &str) -> String {
    let Some(params) = declarator.child_by_field_name("parameters") else {
        return String::new();
    };
    let mut is_const = false;
    let mut is_volatile = false;
    let mut reference = None;
    for child in declarator.named_children(&mut declarator.walk()) {
        if child.start_byte() < params.end_byte() {
            continue;
        }
        let Ok(text) = child.utf8_text(src.as_bytes()) else {
            continue;
        };
        match (child.kind(), text.trim()) {
            ("type_qualifier", "const") => is_const = true,
            ("type_qualifier", "volatile") => is_volatile = true,
            ("ref_qualifier", "&" | "&&") => reference = Some(text.trim()),
            _ => {}
        }
    }
    format!(
        "{}{}{}",
        if is_const { "const" } else { "" },
        if is_volatile { "volatile" } else { "" },
        reference.unwrap_or_default()
    )
}

/// One narrow walk collecting the things no tag capture exposes:
/// import module strings, `export` wrapper ranges, and (for languages with
/// no export concept) nothing. Not a general extractor — four node kinds.
fn collect_meta(node: Node<'_>, lang: Language, src: &str, meta: &mut FileMeta) {
    let imports = &mut meta.imports;
    match (lang, node.kind()) {
        (_, "decorator") => meta.decorators.push(node.byte_range()),
        (Language::Rust, "impl_item") => meta.rust_impls.push(node.byte_range().start),
        (Language::Go, "import_spec") => {
            if let Some(path) = node.child_by_field_name("path") {
                if let Ok(t) = path.utf8_text(src.as_bytes()) {
                    imports.push(t.trim_matches('"').to_string());
                }
            }
            return;
        }
        (Language::Go, "method_declaration") => {
            // `func (s *Store) Get(...)` -> receiver type `Store`; the
            // pointer/parens/parameter name are all stripped by taking the
            // last identifier-ish token, and a generic receiver's type
            // parameters (`func (s *Store[T]) Get()`) go too, or the method
            // would qualify as `Store[T].Get` and never match the declared
            // `Store` (found by review).
            if let Some(recv) = node.child_by_field_name("receiver") {
                if let Ok(t) = recv.utf8_text(src.as_bytes()) {
                    if let Some(name) = t
                        .trim_matches(|c| c == '(' || c == ')')
                        .split_whitespace()
                        .next_back()
                        .map(|t| t.trim_start_matches('*'))
                        .map(|t| t.split('[').next().unwrap_or(t))
                        .map(|t| t.rsplit('.').next().unwrap_or(t))
                        .filter(|t| !t.is_empty())
                    {
                        meta.go_receivers
                            .push((node.byte_range().start, name.to_string()));
                    }
                }
            }
        }
        (Language::Java, "import_declaration") => {
            // The dotted path as written, minus `static`/`*` decoration —
            // same "raw module string" contract Python, TypeScript and Rust
            // record. `relations::resolve_module` never resolves these to a
            // file (Java's package layout is a directory tree whose leaf is
            // the *type*, and most imports are JDK or third-party), so they
            // inform lexical text and nothing else — the same known gap Go
            // imports have.
            if let Ok(t) = node.utf8_text(src.as_bytes()) {
                let path = t
                    .trim_start_matches("import")
                    .trim()
                    .trim_start_matches("static")
                    .trim()
                    .trim_end_matches(';')
                    .trim()
                    .trim_end_matches(".*")
                    .trim();
                if !path.is_empty() {
                    imports.push(path.to_string());
                }
            }
            return;
        }
        (
            Language::Java,
            "method_declaration" | "constructor_declaration" | "compact_constructor_declaration",
        ) => {
            // A compact constructor (`record R { R { .. } }`) declares no
            // parameter list at all, so its signature is empty — which is
            // correct: a record has exactly one, and it can never be
            // overloaded against itself.
            let sig = node
                .child_by_field_name("parameters")
                .map(|p| java_signature(p, src))
                .unwrap_or_default();
            meta.java_params.push((node.byte_range().start, sig));
        }
        (Language::C, "function_definition") => {
            if let Some(fd) = function_declarator_of(node) {
                meta.c_function_spans
                    .push((fd.byte_range().start, node.byte_range()));
            }
        }
        (Language::C, "declaration") => {
            if let Some(fd) = function_declarator_of(node) {
                meta.c_prototypes.push(fd.byte_range().start);
            }
        }
        (Language::Cpp, "function_definition") => {
            if let Some(fd) = function_declarator_of(node) {
                meta.cpp_function_spans
                    .push((fd.byte_range().start, node.byte_range()));
            }
        }
        (Language::Cpp, "declaration") | (Language::Cpp, "field_declaration") => {
            if let Some(fd) = function_declarator_of(node) {
                meta.cpp_prototypes.push(fd.byte_range().start);
            }
        }
        (Language::Cpp, "function_declarator") => {
            let sig = node
                .child_by_field_name("parameters")
                .map(|params| cpp_signature(params, src))
                .unwrap_or_default();
            meta.cpp_params.push((
                node.byte_range().start,
                sig,
                cpp_method_qualifier(node, src),
            ));
            if let Some(declarator) = node.child_by_field_name("declarator") {
                if let Ok(name) = declarator.utf8_text(src.as_bytes()) {
                    if let Some((scope, _)) = name.rsplit_once("::") {
                        meta.cpp_scopes
                            .push((node.byte_range().start, scope.replace("::", ".")));
                    }
                }
            }
        }
        (Language::Cpp, "operator_cast") => {
            let Some(ty) = node.child_by_field_name("type") else {
                return;
            };
            let Some(declarator) = node.child_by_field_name("declarator") else {
                return;
            };
            let Some(params) = declarator.child_by_field_name("parameters") else {
                return;
            };
            let Ok(ty) = ty.utf8_text(src.as_bytes()) else {
                return;
            };
            meta.cpp_casts.push((
                node.byte_range().start,
                format!(
                    "operator {}({}){}",
                    cpp_type_name(ty),
                    cpp_signature(params, src),
                    cpp_method_qualifier(declarator, src)
                ),
            ));
        }
        (Language::C | Language::Cpp, "preproc_include") => {
            // `#include "util.h"` is resolved by the compiler relative to
            // the including file's own directory, so it is recorded as
            // `./util.h` — the same spelling TypeScript writes for the same
            // meaning, which is what lets `relations::resolve_module` treat
            // it without knowing any C. `#include <stdio.h>` is a search
            // path OXIDE has no view of and is recorded verbatim, where it
            // informs lexical text and resolves to nothing.
            if let Some(path) = node.child_by_field_name("path") {
                match path.kind() {
                    "string_literal" => {
                        if let Some(content) = path
                            .named_children(&mut path.walk())
                            .find(|c| c.kind() == "string_content")
                        {
                            if let Ok(t) = content.utf8_text(src.as_bytes()) {
                                if !t.is_empty() {
                                    let rel = if t.starts_with("./") || t.starts_with("../") {
                                        t.to_string()
                                    } else {
                                        format!("./{t}")
                                    };
                                    imports.push(rel);
                                }
                            }
                        }
                    }
                    "system_lib_string" => {
                        if let Ok(t) = path.utf8_text(src.as_bytes()) {
                            let t = t.trim_matches(|c| c == '<' || c == '>');
                            if !t.is_empty() {
                                imports.push(t.to_string());
                            }
                        }
                    }
                    _ => {}
                }
            }
        }
        (Language::Ruby, "singleton_method") => {
            if let Some(obj) = node.child_by_field_name("object") {
                if let Ok(t) = obj.utf8_text(src.as_bytes()) {
                    let prefix = if obj.kind() == "self" {
                        "self.".to_string()
                    } else {
                        format!("{t}.self.")
                    };
                    meta.ruby_singletons.push((node.byte_range().start, prefix));
                }
            }
        }
        (Language::Ruby, "singleton_class") => {
            // `class << receiver` writes singleton methods without a method
            // receiver. The block is not a definition tag, so restore the
            // same explicit singleton marker `def receiver.method` uses.
            if let Some(value) = node.child_by_field_name("value") {
                let prefix = if value.kind() == "self" {
                    "self.".to_string()
                } else if let Ok(value) = value.utf8_text(src.as_bytes()) {
                    format!("{value}.self.")
                } else {
                    return;
                };
                if let Some(body) = node.child_by_field_name("body") {
                    for m in body.named_children(&mut body.walk()) {
                        if m.kind() == "method" {
                            meta.ruby_singletons
                                .push((m.byte_range().start, prefix.clone()));
                        }
                    }
                }
            }
        }
        (Language::Ruby, "call") => {
            // `require 'json'`, `require_relative '../lib/base'`, `load`.
            // Ruby has no import statement — these are ordinary method calls,
            // which is also why only a literal single string argument counts:
            // `require File.join(dir, x)` names no module, exactly as
            // JavaScript's `require(dynamic)` doesn't.
            let is_require = node
                .child_by_field_name("method")
                .and_then(|f| f.utf8_text(src.as_bytes()).ok())
                .is_some_and(|m| matches!(m, "require" | "require_relative" | "load"));
            if is_require {
                if let Some(args) = node.child_by_field_name("arguments") {
                    if args.named_child_count() == 1 {
                        if let Some(arg) = args.named_child(0) {
                            if arg.kind() == "string" {
                                if let Some(content) = arg
                                    .named_children(&mut arg.walk())
                                    .find(|c| c.kind() == "string_content")
                                {
                                    if let Ok(t) = content.utf8_text(src.as_bytes()) {
                                        if !t.is_empty() {
                                            imports.push(t.to_string());
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
        (Language::Php, "namespace_use_declaration") => {
            // `use App\Contracts\Backend;` — the dotted (here
            // backslash-separated) path as written, minus any `as` alias,
            // the same raw-module-string contract Java's imports record.
            // `relations::resolve_module` never resolves one: PSR-4 maps a
            // namespace prefix onto a directory through `composer.json`,
            // which OXIDE does not read, so these inform lexical text only.
            for clause in node.named_children(&mut node.walk()) {
                if clause.kind() != "namespace_use_clause" {
                    continue;
                }
                if let Some(path) = clause
                    .named_children(&mut clause.walk())
                    .find(|c| matches!(c.kind(), "qualified_name" | "name"))
                {
                    if let Ok(t) = path.utf8_text(src.as_bytes()) {
                        let t = t.trim_start_matches('\\');
                        if !t.is_empty() {
                            imports.push(t.to_string());
                        }
                    }
                }
            }
            return;
        }
        (
            Language::Php,
            "include_expression"
            | "include_once_expression"
            | "require_expression"
            | "require_once_expression",
        ) => {
            // Only a bare literal string counts. The `__DIR__ . '/x.php'`
            // concatenation idiom is deliberately not read: its string is a
            // *fragment* of a path rooted at the including file's own
            // directory, which `relations::resolve_module` has no notion
            // of, and half a path recorded as a module string would resolve
            // to nothing while looking like it had been handled.
            if let Some(arg) = node.named_children(&mut node.walk()).next() {
                if arg.kind() == "string" {
                    if let Some(content) = arg
                        .named_children(&mut arg.walk())
                        .find(|c| c.kind() == "string_content")
                    {
                        if let Ok(t) = content.utf8_text(src.as_bytes()) {
                            if !t.is_empty() {
                                imports.push(t.to_string());
                            }
                        }
                    }
                }
            }
        }
        (Language::Go, "type_spec") => {
            if node
                .child_by_field_name("type")
                .is_some_and(|t| t.kind() == "interface_type")
            {
                meta.go_interfaces.push(node.byte_range().start);
            }
        }
        (Language::Python, "import_from_statement") => {
            if let Some(m) = node.child_by_field_name("module_name") {
                if let Ok(t) = m.utf8_text(src.as_bytes()) {
                    imports.push(t.to_string());
                }
            }
            return;
        }
        (Language::Python, "import_statement") => {
            let mut cur = node.walk();
            for child in node.children(&mut cur) {
                match child.kind() {
                    "dotted_name" => {
                        if let Ok(t) = child.utf8_text(src.as_bytes()) {
                            imports.push(t.to_string());
                        }
                    }
                    "aliased_import" => {
                        if let Some(n) = child.child_by_field_name("name") {
                            if let Ok(t) = n.utf8_text(src.as_bytes()) {
                                imports.push(t.to_string());
                            }
                        }
                    }
                    _ => {}
                }
            }
            return;
        }
        (Language::Rust, "use_declaration") => {
            // The whole use tree as written (`std::collections::HashMap`,
            // `crate::net::{get, post}`), matching how Python and
            // TypeScript both record the raw module string rather than a
            // resolved path. `relations::resolve_module` maps `::` the same
            // way it maps Python's dots.
            if let Some(arg) = node.child_by_field_name("argument") {
                if let Ok(t) = arg.utf8_text(src.as_bytes()) {
                    imports.push(t.to_string());
                }
            }
            return;
        }
        (Language::JavaScript, "call_expression") => {
            // CommonJS `require('./util')`. ESM is handled by the shared
            // arm below (JavaScript uses the TSX grammar, so `import_
            // statement`/`export_statement` are the same nodes), but a `.js`
            // or `.cjs` file is as likely to use `require`, and
            // `relations::resolve_module` resolves `./util` identically
            // either way. Only a literal single-argument call counts —
            // `require(dynamic)` names no module.
            let is_require = node
                .child_by_field_name("function")
                .and_then(|f| f.utf8_text(src.as_bytes()).ok())
                == Some("require");
            if is_require {
                if let Some(args) = node.child_by_field_name("arguments") {
                    if args.named_child_count() == 1 {
                        if let Some(arg) = args.named_child(0) {
                            if matches!(arg.kind(), "string") {
                                if let Ok(t) = arg.utf8_text(src.as_bytes()) {
                                    let m = t.trim_matches(|c| c == '\'' || c == '"');
                                    if !m.is_empty() {
                                        imports.push(m.to_string());
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
        (
            Language::TypeScript | Language::Tsx | Language::JavaScript,
            "import_statement" | "export_statement",
        ) => {
            if node.kind() == "export_statement" {
                meta.exports.push(node.byte_range());
            }
            if let Some(src_node) = node.child_by_field_name("source") {
                if let Ok(t) = src_node.utf8_text(src.as_bytes()) {
                    imports.push(t.trim_matches(|c| c == '\'' || c == '"').to_string());
                }
            }
        }
        _ => {}
    }
    let mut cur = node.walk();
    for child in node.children(&mut cur) {
        collect_meta(child, lang, src, meta);
    }
}

/// Walk `start` back over any decorators that sit immediately before it with
/// nothing but whitespace in between, so a decorated definition's span (and
/// therefore its `content_hash`, `signature`, and the body tokens the lexical
/// index weights) begins at its first decorator.
///
/// Done here, over decorator byte ranges collected from the tree, rather than
/// as extra `.scm` patterns: the two grammars disagree about where a
/// decorator *lives* — Python wraps the definition in `decorated_definition`,
/// while TypeScript hangs the decorator off `export_statement` for a
/// decorated exported class and off the class body for a decorated method —
/// so a query-level fix needs a pattern per grammar shape per language plus a
/// Rust rule to collapse the resulting outer/inner twin definitions. The
/// contiguity check is what keeps this honest: a decorator belonging to some
/// *earlier* definition always has that definition's source between it and
/// `start`, so it is never absorbed.
/// Start byte of the `export_statement` that directly wraps a definition
/// (same "ends at the same byte" test the `exported` flag uses), else the
/// definition's own start. On its own this changes nothing — `export` and
/// the declaration it wraps share a line — but it is what lets
/// `decorator_extended_start` see past the `export` keyword in
/// `@Injectable()\nexport class Svc`, where the decorator is a sibling of
/// the export statement rather than of the class.
fn export_anchor(export_ranges: &[Range<usize>], start: usize, end: usize) -> usize {
    export_ranges
        .iter()
        .find(|r| r.start <= start && r.end == end)
        .map_or(start, |r| r.start)
}

fn decorator_extended_start(src: &str, decorators: &[Range<usize>], start: usize) -> usize {
    let mut start = start;
    // Loops rather than taking one step, so a stack of decorators is
    // absorbed whole.
    while let Some(dec) = decorators
        .iter()
        .filter(|r| r.end <= start && src[r.end..start].trim().is_empty())
        .max_by_key(|r| r.end)
    {
        start = dec.start;
    }
    start
}

impl LanguageExtractor for TagsExtractor {
    fn language(&self) -> Language {
        self.profile.language
    }

    fn ts_language(&self) -> tree_sitter::Language {
        (self.profile.ts_language)()
    }

    fn collect_imports(&self, src: &str) -> Vec<String> {
        let Some(tree) = parse(self.profile, src) else {
            return Vec::new();
        };
        let mut meta = FileMeta::default();
        collect_meta(tree.root_node(), self.profile.language, src, &mut meta);
        meta.imports.sort();
        meta.imports.dedup();
        meta.imports
    }

    fn extract(&self, file: &str, src: &str, imports: &[String]) -> Vec<Symbol> {
        let profile = self.profile;
        let bytes = src.as_bytes();

        let mut meta = FileMeta::default();
        if let Some(tree) = parse(profile, src) {
            collect_meta(tree.root_node(), profile.language, src, &mut meta);
        }
        let export_ranges = &meta.exports;
        let decorators = &meta.decorators;

        let mut defs: Vec<RawDef> = Vec::new();
        if let Some(config) = self.config() {
            let mut ctx = TagsContext::new();
            let generated = ctx.generate_tags(config, bytes, None);
            if let Ok((tags_iter, _)) = generated {
                for tag in tags_iter.flatten() {
                    if !tag.is_definition {
                        continue;
                    }
                    let Some(kind) = map_kind(config.syntax_type_name(tag.syntax_type_id)) else {
                        continue;
                    };
                    let Ok(name) = std::str::from_utf8(&bytes[tag.name_range.clone()]) else {
                        continue;
                    };
                    let name = meta
                        .cpp_casts
                        .iter()
                        .find(|(start, _)| *start == tag.range.start)
                        .map(|(_, name)| name.as_str())
                        .unwrap_or(name);
                    // C tags the declarator, not the definition, so the
                    // body is outside the tag's range until this puts it
                    // back — see `FileMeta::c_function_spans`.
                    let span = meta
                        .c_function_spans
                        .iter()
                        .find(|(start, _)| *start == tag.range.start)
                        .map(|(_, r)| r.clone())
                        .or_else(|| {
                            meta.cpp_function_spans
                                .iter()
                                .find(|(start, _)| *start == tag.range.start)
                                .map(|(_, r)| r.clone())
                        })
                        .unwrap_or(tag.range.clone());
                    defs.push(RawDef {
                        start: span.start,
                        end: span.end,
                        tag_start: tag.range.start,
                        // Containment below still keys off the raw tag
                        // range, so absorbing a decorator can never
                        // re-parent anything — only the span widens.
                        line_start: byte_to_line(
                            bytes,
                            decorator_extended_start(
                                src,
                                decorators,
                                export_anchor(export_ranges, span.start, span.end),
                            ),
                        ),
                        line_end: span_end_line(bytes, span.end),
                        name: name.to_string(),
                        kind,
                    });
                }
            }
        }

        // Outer-before-inner at equal start (longer span first) so the
        // containment stack below sees enclosing classes/interfaces before
        // the members nested inside them.
        defs.sort_by(|a, b| a.start.cmp(&b.start).then(b.end.cmp(&a.end)));
        defs.dedup_by(|a, b| a.start == b.start && a.end == b.end && a.name == b.name);

        let mut out = Vec::with_capacity(defs.len());
        // Parallel to `out`: whether each symbol is a *stand-in* for a
        // declaration rather than the declaration itself — a Rust `impl`
        // block's twin symbol (`FileMeta::rust_impls`) or a C prototype
        // (`FileMeta::c_prototypes`). Both lose to a real declaration of the
        // same name in the same file, and neither can be decided by
        // `parser.rs`'s first-wins dedup, which keeps whichever comes first:
        // `impl Store {}` may legally precede `struct Store;`, and a C
        // forward declaration always precedes its definition.
        let mut stand_in: Vec<bool> = Vec::with_capacity(defs.len());
        // (qualified_name, start, end, is_container)
        let mut stack: Vec<(String, usize, usize, bool)> = Vec::new();
        for d in defs {
            while let Some(top) = stack.last() {
                if d.start >= top.1 && d.end <= top.2 {
                    break;
                }
                stack.pop();
            }
            let parent_container = stack.last();
            let parent = parent_container.map(|(n, ..)| n.clone());
            let qualified = match &parent {
                Some(p) => format!("{p}.{}", d.name),
                None => d.name.clone(),
            };
            let in_class = parent_container.map(|(.., c)| *c).unwrap_or(false);
            let kind = if profile.language == Language::Python
                && d.kind == SymbolKind::Function
                && in_class
            {
                SymbolKind::Method
            } else if meta.go_interfaces.contains(&d.tag_start) {
                // Every Go `type X ...` carries one syntax type upstream;
                // only the parse tree says which are interfaces.
                SymbolKind::Interface
            } else {
                d.kind
            };
            // Go methods are top-level declarations qualified by their
            // receiver type, not by nesting — see `FileMeta::go_receivers`.
            let (parent, qualified) = match meta
                .go_receivers
                .iter()
                .find(|(start, _)| *start == d.tag_start)
            {
                Some((_, recv)) => (Some(recv.clone()), format!("{recv}.{}", d.name)),
                None => (parent, qualified),
            };
            // Ruby singleton methods carry their receiver prefix, so a
            // class method never collides with a same-named instance method
            // — see [`FileMeta::ruby_singletons`].
            let (parent, qualified) = match meta
                .ruby_singletons
                .iter()
                .find(|(start, _)| *start == d.tag_start)
            {
                Some((_, prefix)) => match &parent {
                    Some(p) => (parent.clone(), format!("{p}.{prefix}{}", d.name)),
                    None => (parent.clone(), format!("{prefix}{}", d.name)),
                },
                None => (parent, qualified),
            };
            // Java methods and constructors carry their normalized parameter
            // types, so overloads stay distinct symbols instead of colliding
            // under `parse_file_with`'s dedup — see [`java_signature`].
            // Scoped to the declarations `collect_meta` recorded, so a Java
            // class/enum/record name is untouched and no other language sees
            // any change at all.
            let qualified = match meta
                .java_params
                .iter()
                .find(|(start, _)| *start == d.tag_start)
            {
                Some((_, sig)) => format!("{qualified}({sig})"),
                None => qualified,
            };
            // C++ out-of-class definitions carry a `Store::method`
            // declarator, while the class-body declaration is contained by
            // `Store`. Keep that source scope so both resolve to one
            // qualified name before the prototype stand-in loses.
            let (parent, qualified) = match meta
                .cpp_scopes
                .iter()
                .find(|(start, _)| *start == d.tag_start)
            {
                Some((_, scope)) => {
                    let scoped = match &parent {
                        Some(p) if scope.starts_with(&format!("{p}.")) => scope.clone(),
                        Some(p) => format!("{p}.{scope}"),
                        None => scope.clone(),
                    };
                    (Some(scoped.clone()), format!("{scoped}.{}", d.name))
                }
                None => (parent, qualified),
            };
            let qualified = match meta
                .cpp_params
                .iter()
                .find(|(start, _, _)| *start == d.tag_start)
            {
                Some((_, sig, suffix)) => format!("{qualified}({sig}){suffix}"),
                None => qualified,
            };
            // Module counts as a container so a Rust `mod` block and a
            // TypeScript `namespace` qualify their members (`mod net { fn
            // get }` -> `net.get`). Without it every `mod` in a file
            // contributes bare names that collide under parser.rs's
            // qualified-name dedup. The file-level `__module__` fallback is
            // added after extraction and never reaches this stack.
            let is_container = matches!(
                kind,
                SymbolKind::Class | SymbolKind::Interface | SymbolKind::Module
            );
            // An `export_statement` directly wraps exactly one declaration
            // (`export class Foo {}`), so its end byte coincides with the
            // wrapped definition's end byte. Containment alone (`r.contains
            // (d.start)`) would also match every member nested arbitrarily
            // deep inside an exported class/interface, which the old
            // extractor never treated as individually "exported".
            let exported = profile.language == Language::Python
                || export_ranges
                    .iter()
                    .any(|r| r.start <= d.start && r.end == d.end);
            // Hash/signature the *line*-reconstructed body, not the raw byte
            // slice: `d.start` points at the definition keyword, not the
            // line's leading indentation, so a byte slice and `Symbol::
            // span_text()` (which is line-based) would disagree for any
            // indented method — silently decoupling the stored content_hash
            // from what span_text() recomputes for the same symbol later.
            let body = span_lines(src, d.line_start, d.line_end);
            out.push(Symbol {
                qualified_name: qualified.clone(),
                name: d.name,
                kind,
                language: profile.language,
                file: file.to_string(),
                start_line: d.line_start,
                end_line: d.line_end,
                content_hash: content_hash(&body),
                signature: first_line(&body),
                imports: imports.to_vec(),
                exported,
                parent,
                references: Vec::new(),
                calls: Vec::new(),
                bases: Vec::new(),
            });
            stand_in.push(
                meta.rust_impls.contains(&d.tag_start)
                    || meta.c_prototypes.contains(&d.tag_start)
                    || meta.cpp_prototypes.contains(&d.tag_start),
            );
            stack.push((qualified, d.start, d.end, is_container));
        }
        // Drop a stand-in when the file also carries the real declaration
        // it names. Done after the containment pass, so an impl block's
        // methods keep the qualified names it gave them.
        if stand_in.iter().any(|&b| b) {
            let declared: std::collections::HashSet<String> = out
                .iter()
                .zip(&stand_in)
                .filter(|(_, &is_stand_in)| !is_stand_in)
                .map(|(s, _)| s.qualified_name.clone())
                .collect();
            let mut idx = 0;
            out.retain(|s| {
                let is_stand_in = stand_in[idx];
                idx += 1;
                !(is_stand_in && declared.contains(&s.qualified_name))
            });
        }
        out
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::parser::parse_file_with;

    #[test]
    fn same_named_methods_in_different_classes_do_not_collide() {
        // Flat tags have no parent, so two classes each with a `get` method
        // would produce the same qualified_name ("get") without containment
        // reconstruction — and parser.rs's dedup (keyed on qualified_name,
        // AGENTS.md-pinned) would silently drop the second one entirely.
        let src = "\
class A:
    def get(self):
        return 1

class B:
    def get(self):
        return 2
";
        let syms = parse_file_with(&PYTHON_TAGS, "x.py", src, Language::Python);
        let names: Vec<&str> = syms.iter().map(|s| s.qualified_name.as_str()).collect();
        assert!(names.contains(&"A.get"), "{names:?}");
        assert!(names.contains(&"B.get"), "{names:?}");
        assert_eq!(
            syms.iter().filter(|s| s.name == "get").count(),
            2,
            "{names:?}"
        );
    }

    #[test]
    fn ruby_singleton_methods_survive_alongside_instance_methods() {
        // Without the receiver prefix both spellings qualify as `Store.get`
        // and `parse_file_with`'s first-wins dedup drops one of them
        // outright — the same silent loss Java overloads had.
        let src = "\
class Store
  def get(k)
    1
  end

  def self.get(k)
    2
  end

  class << self
    def reset
      3
    end
  end
end

class << Store
  def from_receiver(k)
    4
  end
end
";
        let syms = parse_file_with(&RUBY_TAGS, "store.rb", src, Language::Ruby);
        let names: Vec<&str> = syms.iter().map(|s| s.qualified_name.as_str()).collect();
        assert!(names.contains(&"Store.get"), "{names:?}");
        assert!(names.contains(&"Store.self.get"), "{names:?}");
        assert!(names.contains(&"Store.self.reset"), "{names:?}");
        assert!(names.contains(&"Store.self.from_receiver"), "{names:?}");
    }

    #[test]
    fn cpp_type_definitions_do_not_lose_to_forward_declarations() {
        let src = "class Forward;\nclass Forward { void live(); };\n";
        let syms = parse_file_with(&CPP_TAGS, "forward.cpp", src, Language::Cpp);
        let ty = syms
            .iter()
            .find(|s| s.qualified_name == "Forward")
            .expect("missing definition");
        assert_eq!(ty.start_line, 2, "forward declaration won: {syms:?}");
        assert!(
            syms.iter().any(|s| s.qualified_name == "Forward.live()"),
            "member vanished with the forward declaration: {syms:?}"
        );
    }

    #[test]
    fn ruby_requires_are_imports_and_dynamic_ones_are_not() {
        let src = "\
require 'json'
require_relative '../lib/base'
load \"tasks.rb\"
require File.join(dir, 'x')
";
        let mut imports = RUBY_TAGS.collect_imports(src);
        imports.sort();
        assert_eq!(
            imports,
            vec!["../lib/base", "json", "tasks.rb"],
            "a computed require names no module"
        );
    }

    #[test]
    fn php_methods_are_methods_and_enum_cases_are_constants() {
        // Upstream tags `method_declaration` as `@definition.function`, and
        // `tags.rs` only reclassifies for Python — so without the retag in
        // `php_tags.scm` every PHP method would read as a free function.
        let src = "<?php\nenum Mode: string {\n    case Fast = 'fast';\n    public function label(): string { return 'x'; }\n}\n";
        let syms = parse_file_with(&PHP_TAGS, "Mode.php", src, Language::Php);
        let by = |n: &str| {
            syms.iter()
                .find(|s| s.qualified_name == n)
                .unwrap_or_else(|| panic!("missing {n}: {syms:?}"))
                .kind
        };
        assert_eq!(by("Mode"), SymbolKind::Enum);
        assert_eq!(by("Mode.Fast"), SymbolKind::Constant);
        assert_eq!(by("Mode.label"), SymbolKind::Method);
    }

    #[test]
    fn php_imports_take_use_statements_and_literal_includes_only() {
        let src = "<?php\nuse App\\Contracts\\Backend;\nuse App\\Support\\Loggable as Log;\ninclude 'legacy.php';\nrequire_once __DIR__ . '/helpers.php';\n";
        let mut imports = PHP_TAGS.collect_imports(src);
        imports.sort();
        assert_eq!(
            imports,
            vec![
                "App\\Contracts\\Backend",
                "App\\Support\\Loggable",
                "legacy.php"
            ],
            "an alias is not part of the module path, and a concatenated \
             `__DIR__ . '...'` is half a path, not a module"
        );
    }

    #[test]
    fn c_function_spans_cover_the_body_through_any_pointer_depth() {
        // The tag is on the `function_declarator`, so without
        // `FileMeta::c_function_spans` every C function would be a
        // one-line, body-free symbol — wrong span, wrong hash, and none of
        // the body tokens the lexical index weights. The pointer levels are
        // the reason the widening is done in Rust rather than by
        // enumerating query patterns.
        let src = "\
char **argv_of(int n)
{
    return 0;
}

static int plain(void)
{
    return 1;
}
";
        let syms = parse_file_with(&C_TAGS, "a.c", src, Language::C);
        for (name, start, end) in [("argv_of", 1, 4), ("plain", 6, 9)] {
            let f = syms.iter().find(|s| s.qualified_name == name).unwrap();
            assert_eq!((f.start_line, f.end_line), (start, end), "{name}");
            assert_eq!(f.content_hash, content_hash(f.span_text(src)));
        }
    }

    #[test]
    fn c_prototype_loses_to_a_definition_but_survives_alone() {
        let both = "static void helper(int x);\n\nstatic void helper(int x)\n{\n    (void)x;\n}\n";
        let syms = parse_file_with(&C_TAGS, "a.c", both, Language::C);
        let h: Vec<&Symbol> = syms.iter().filter(|s| s.name == "helper").collect();
        assert_eq!(h.len(), 1, "{h:?}");
        assert_eq!((h[0].start_line, h[0].end_line), (3, 6));

        let header = "int base_retain(struct base *b);\n";
        let syms = parse_file_with(&C_TAGS, "b.h", header, Language::C);
        assert!(
            syms.iter().any(|s| s.qualified_name == "base_retain"),
            "a header whose functions vanished would be worse than useless"
        );
    }

    #[test]
    fn c_includes_are_relative_when_quoted_and_verbatim_when_angled() {
        let src = "#include <stdio.h>\n#include \"util.h\"\n#include \"../lib/base.h\"\n";
        let mut imports = C_TAGS.collect_imports(src);
        imports.sort();
        assert_eq!(
            imports,
            vec!["../lib/base.h", "./util.h", "stdio.h"],
            "a quoted include resolves against the including file's own \
             directory, which `./` is how OXIDE already spells"
        );
    }

    #[test]
    fn cpp_spans_and_imports_follow_c_while_signatures_keep_templates() {
        let src = "#include \"store.hpp\"\nint Store::get(const std::vector< int >& ids)\n{\n    return ids.size();\n}\n";
        let syms = parse_file_with(&CPP_TAGS, "store.cpp", src, Language::Cpp);
        let get = syms
            .iter()
            .find(|s| s.qualified_name == "Store.get(const std::vector<int>&)")
            .expect("C++ out-of-class method keeps its scope and signature");
        assert_eq!((get.start_line, get.end_line), (2, 5));
        assert_eq!(get.content_hash, content_hash(get.span_text(src)));
        assert_eq!(CPP_TAGS.collect_imports(src), vec!["./store.hpp"]);
    }

    #[test]
    fn content_hash_matches_span_text_reconstruction() {
        // content_hash is computed once at extract time; span_text() is
        // recomputed on demand from start_line/end_line. If extract() ever
        // hashes something other than what span_text() would produce for
        // the same symbol, the two silently drift apart.
        let src = "class A:\n    def get(self, key):\n        return self.data[key]\n";
        let syms = parse_file_with(&PYTHON_TAGS, "x.py", src, Language::Python);
        let get = syms.iter().find(|s| s.qualified_name == "A.get").unwrap();
        assert_eq!(get.content_hash, content_hash(get.span_text(src)));
    }

    #[test]
    fn decorated_definitions_span_their_decorators() {
        // Upstream python tags.scm binds @definition.class to the
        // class_definition node itself, never the wrapping
        // decorated_definition, so the decorator line — often the single
        // most retrieval-relevant line on a symbol (`@app.route`,
        // `@pytest.fixture`), and one that feeds the lexical index at body
        // weight — used to fall outside the span entirely.
        // `decorator_extended_start` widens it back.
        let src = "\
@dataclass
@final
class VersionedStore:
    @property
    def get(self):
        return self.data
";
        let syms = parse_file_with(&PYTHON_TAGS, "x.py", src, Language::Python);
        let cls = syms
            .iter()
            .find(|s| s.qualified_name == "VersionedStore")
            .unwrap();
        assert_eq!(cls.start_line, 1, "a stack of decorators is absorbed whole");
        assert_eq!(cls.signature, "@dataclass");
        let get = syms
            .iter()
            .find(|s| s.qualified_name == "VersionedStore.get")
            .unwrap();
        assert_eq!(get.start_line, 4);
        // The widened span must still be exactly what span_text() recomputes.
        assert_eq!(get.content_hash, content_hash(get.span_text(src)));
    }

    #[test]
    fn a_decorator_on_a_previous_definition_is_not_absorbed() {
        // The contiguity check is the whole safety argument: only whitespace
        // may sit between a decorator and the definition it widens.
        let src = "@dec\ndef a():\n    pass\n\ndef b():\n    pass\n";
        let syms = parse_file_with(&PYTHON_TAGS, "x.py", src, Language::Python);
        assert_eq!(syms.iter().find(|s| s.name == "a").unwrap().start_line, 1);
        assert_eq!(syms.iter().find(|s| s.name == "b").unwrap().start_line, 5);
    }

    #[test]
    fn typescript_decorators_widen_class_and_method_spans() {
        // TypeScript hangs a decorated exported class's decorator off the
        // export_statement and a decorated method's off the class body —
        // two grammar shapes neither of which Python has, both handled by
        // the same byte-range rule.
        let src = "@Injectable()\nexport class Svc {\n  @Log()\n  run() {\n    return 1;\n  }\n}\n";
        let syms = parse_file_with(&TYPESCRIPT_TAGS, "x.ts", src, Language::TypeScript);
        assert_eq!(syms.iter().find(|s| s.name == "Svc").unwrap().start_line, 1);
        assert_eq!(syms.iter().find(|s| s.name == "run").unwrap().start_line, 3);
    }

    #[test]
    fn an_impl_block_never_displaces_the_type_it_names() {
        // Found by review: an inherent `impl Store` may legally precede
        // `struct Store;`, and parser.rs's first-wins dedup would then keep
        // the impl block and discard the declaration a reader is after.
        let src = "impl Store {\n    fn new() -> Self { Store }\n}\n\npub struct Store;\n";
        let syms = parse_file_with(&RUST_TAGS, "a.rs", src, Language::Rust);
        let store = syms
            .iter()
            .find(|s| s.qualified_name == "Store")
            .expect("Store survives");
        assert_eq!(store.start_line, 5, "the declaration wins, not the impl");
        // The impl's methods still keep the qualified name it gave them.
        assert!(
            syms.iter().any(|s| s.qualified_name == "Store.new"),
            "{:?}",
            syms.iter().map(|s| &s.qualified_name).collect::<Vec<_>>()
        );
    }

    #[test]
    fn a_scoped_impl_type_still_nests_and_relates() {
        let src = "impl crate::store::Store {\n    fn helper() {}\n}\n";
        let syms = parse_file_with(&RUST_TAGS, "c.rs", src, Language::Rust);
        assert!(
            syms.iter().any(|s| s.qualified_name == "Store.helper"),
            "{:?}",
            syms.iter().map(|s| &s.qualified_name).collect::<Vec<_>>()
        );
    }

    #[test]
    fn export_const_with_non_function_value_is_captured() {
        // JavaScript's own @definition.constant pattern only matches the
        // rare `export x = <value>` bare-assignment form, not `export const
        // X = <value>` (a lexical_declaration) — measured to cost a real
        // fixtures/benchmark.json task (`ts-default-policy-const`, recall@5
        // 1.000 -> 0.000). Closed by the OXIDE-owned pattern appended to
        // queries/typescript_tags.scm; this pins both the primitive case and
        // the constructor-call case that actually broke the benchmark task.
        let src = "export const DEFAULT_TIMEOUT = 30;\nexport const policy = new Backoff(3);\n";
        let syms = parse_file_with(&TYPESCRIPT_TAGS, "x.ts", src, Language::TypeScript);
        let names: Vec<&str> = syms.iter().map(|s| s.name.as_str()).collect();
        assert!(names.contains(&"DEFAULT_TIMEOUT"), "{names:?}");
        assert!(names.contains(&"policy"), "{names:?}");
        assert_eq!(
            syms.iter()
                .find(|s| s.name == "DEFAULT_TIMEOUT")
                .unwrap()
                .kind,
            SymbolKind::Constant
        );
    }

    #[test]
    fn java_overloads_survive_as_distinct_symbols() {
        // The blocker docs/java-feasibility/README.md named: all three
        // `get`s normalize to the qualified name `Store.get` without a
        // signature, and `parse_file_with`'s first-wins dedup then keeps one
        // and silently drops two — routine data loss for a language where
        // overloading is idiomatic.
        let src = "\
class Store {
  String get(String key) { return key; }
  String get(String key, String fallback) { return fallback; }
  String get(byte[] raw) { return new String(raw); }
}
";
        let syms = parse_file_with(&JAVA_TAGS, "Store.java", src, Language::Java);
        let names: Vec<&str> = syms.iter().map(|s| s.qualified_name.as_str()).collect();
        assert!(names.contains(&"Store.get(String)"), "{names:?}");
        assert!(names.contains(&"Store.get(String,String)"), "{names:?}");
        assert!(names.contains(&"Store.get(byte[])"), "{names:?}");
        let mut ids: Vec<u64> = syms.iter().map(|s| s.id()).collect();
        ids.sort();
        ids.dedup();
        assert_eq!(ids.len(), syms.len(), "overloads must not collide on id");
    }

    #[test]
    fn java_identity_ignores_parameter_names_annotations_and_formatting() {
        // Identity must be the *types*, or every parameter rename re-embeds
        // the symbol and every `final`/`@Nullable` edit looks like a new
        // declaration.
        let a = "class S { void f(final @Nullable java.util.List<String> items, int... n) {} }\n";
        let b = "class S { void f(java.util.List<Integer> other, int[] n) {} }\n";
        let pick = |src: &str| {
            parse_file_with(&JAVA_TAGS, "S.java", src, Language::Java)
                .into_iter()
                .find(|s| s.kind == SymbolKind::Method)
                .map(|s| (s.id(), s.qualified_name))
                .unwrap()
        };
        let (id_a, name_a) = pick(a);
        let (id_b, name_b) = pick(b);
        assert_eq!(
            name_a, "S.f(List,int[])",
            "generics erase, varargs array-ify"
        );
        assert_eq!(name_a, name_b);
        assert_eq!(id_a, id_b);
    }

    #[test]
    fn java_type_normalization_matches_the_jvms_own_overload_rules() {
        assert_eq!(java_type_name("java.util.Map<String, String>"), "Map");
        assert_eq!(java_type_name("byte[]"), "byte[]");
        assert_eq!(java_type_name("List<String>[]"), "List[]");
        assert_eq!(java_type_name("int[][]"), "int[][]");
        assert_eq!(java_type_name("String []"), "String[]");
        assert_eq!(java_type_name("int"), "int");
        assert_eq!(java_type_name("T"), "T");
        // JSR-308 type annotations sit *inside* the type node, unlike the
        // parameter annotations the grammar keeps in a sibling `modifiers`.
        // Letting one through would move the symbol's persisted id whenever
        // somebody adds or removes `@NonNull` — the exact churn this
        // normalization exists to prevent (found reviewing this change).
        assert_eq!(java_type_name("java.util.@NonNull List<String>"), "List");
        assert_eq!(java_type_name("@NonNull String"), "String");
        assert_eq!(
            java_type_name("@NonNull String"),
            java_type_name("String"),
            "annotating a parameter type must not re-identify the method"
        );
    }

    #[test]
    fn javascript_gets_the_tsx_grammar_esm_and_commonjs() {
        // JavaScript has no grammar or query files of its own: it runs on
        // TSX plus the TypeScript tags query. This pins the four things that
        // buys — JSX component usage parsing at all, class heritage, arrow
        // assignments, and `export` — plus CommonJS `require`, which is the
        // one import form the shared TypeScript arm cannot see.
        let src = "\
import Button from './Button';
const ns = require('./ns');
export class Panel extends React.Component {
  render() { return <Button label={this.props.l} />; }
}
export const scale = (x) => x * 2;
";
        let syms = parse_file_with(&JAVASCRIPT_TAGS, "a.jsx", src, Language::JavaScript);
        let names: Vec<&str> = syms.iter().map(|s| s.qualified_name.as_str()).collect();
        assert!(names.contains(&"Panel"), "{names:?}");
        assert!(names.contains(&"Panel.render"), "{names:?}");
        assert!(names.contains(&"scale"), "{names:?}");
        assert!(
            syms.iter().find(|s| s.name == "Panel").unwrap().exported,
            "export wrapping is read through the shared TypeScript arm"
        );
        let imports = &syms.first().unwrap().imports;
        assert!(imports.contains(&"./Button".to_string()), "{imports:?}");
        assert!(imports.contains(&"./ns".to_string()), "{imports:?}");
    }

    #[test]
    fn a_dynamic_require_names_no_module() {
        let src = "const a = require(name);\nconst b = require('x', 'y');\n";
        let syms = parse_file_with(&JAVASCRIPT_TAGS, "a.js", src, Language::JavaScript);
        assert!(syms.first().unwrap().imports.is_empty());
    }

    static JAVA_TAGS: TagsExtractor = TagsExtractor::new(&crate::languages::JAVA_PROFILE);
    static JAVASCRIPT_TAGS: TagsExtractor =
        TagsExtractor::new(&crate::languages::JAVASCRIPT_PROFILE);
    static PYTHON_TAGS: TagsExtractor = TagsExtractor::new(&crate::languages::PYTHON_PROFILE);
    static C_TAGS: TagsExtractor = TagsExtractor::new(&crate::languages::C_PROFILE);
    static CPP_TAGS: TagsExtractor = TagsExtractor::new(&crate::languages::CPP_PROFILE);
    static PHP_TAGS: TagsExtractor = TagsExtractor::new(&crate::languages::PHP_PROFILE);
    static RUBY_TAGS: TagsExtractor = TagsExtractor::new(&crate::languages::RUBY_PROFILE);
    static RUST_TAGS: TagsExtractor = TagsExtractor::new(&crate::languages::RUST_PROFILE);
    static TYPESCRIPT_TAGS: TagsExtractor =
        TagsExtractor::new(&crate::languages::TYPESCRIPT_PROFILE);
}
