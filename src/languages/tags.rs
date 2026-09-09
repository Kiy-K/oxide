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
    start: usize,
    end: usize,
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
    /// Go only: start bytes of `type_spec` nodes whose type is an
    /// `interface_type`. Upstream tags.scm gives every `type X ...` the same
    /// syntax type, so struct and interface are indistinguishable from tags
    /// alone.
    go_interfaces: Vec<usize>,
}

/// One narrow walk collecting the things no tag capture exposes:
/// import module strings, `export` wrapper ranges, and (for languages with
/// no export concept) nothing. Not a general extractor — four node kinds.
fn collect_meta(node: Node<'_>, lang: Language, src: &str, meta: &mut FileMeta) {
    let imports = &mut meta.imports;
    match (lang, node.kind()) {
        (_, "decorator") => meta.decorators.push(node.byte_range()),
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
            // last identifier-ish token.
            if let Some(recv) = node.child_by_field_name("receiver") {
                if let Ok(t) = recv.utf8_text(src.as_bytes()) {
                    if let Some(name) = t
                        .trim_matches(|c| c == '(' || c == ')')
                        .split_whitespace()
                        .next_back()
                        .map(|t| t.trim_start_matches('*'))
                        .map(|t| t.rsplit('.').next().unwrap_or(t))
                        .filter(|t| !t.is_empty())
                    {
                        meta.go_receivers
                            .push((node.byte_range().start, name.to_string()));
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
        (Language::TypeScript | Language::Tsx, "import_statement" | "export_statement") => {
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
                    defs.push(RawDef {
                        start: tag.range.start,
                        end: tag.range.end,
                        // Containment below still keys off the raw tag
                        // range, so absorbing a decorator can never
                        // re-parent anything — only the span widens.
                        line_start: byte_to_line(
                            bytes,
                            decorator_extended_start(
                                src,
                                decorators,
                                export_anchor(export_ranges, tag.range.start, tag.range.end),
                            ),
                        ),
                        line_end: byte_to_line(bytes, tag.range.end),
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
            } else if meta.go_interfaces.contains(&d.start) {
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
                .find(|(start, _)| *start == d.start)
            {
                Some((_, recv)) => (Some(recv.clone()), format!("{recv}.{}", d.name)),
                None => (parent, qualified),
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
            stack.push((qualified, d.start, d.end, is_container));
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

    static PYTHON_TAGS: TagsExtractor = TagsExtractor::new(&crate::languages::PYTHON_PROFILE);
    static TYPESCRIPT_TAGS: TagsExtractor =
        TagsExtractor::new(&crate::languages::TYPESCRIPT_PROFILE);
}
