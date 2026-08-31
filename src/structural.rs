//! Structural search adapter (Phase 3.4b spike): isolates `ast-grep-core`
//! behind OXIDE's own types. No `ast_grep_core` type appears in this
//! module's public API — callers only ever see `StructuralHit` and the
//! `StructuralSearchProvider` trait.
//!
//! Deliberately additive, not wired into `retrieval.rs`/`context.rs`: those
//! stay frozen per the Phase 3.4b brief. This module answers two
//! symbol-anchored questions ast-grep is well-suited for and OXIDE's
//! existing lexical/vector pipeline cannot answer at all:
//!
//! - "what implements/extends this type?" — a relationship OXIDE has zero
//!   representation of today (no inheritance graph).
//! - "what are the AST-precise call sites of this function?" — distinct
//!   from `index.rs::extract_references`'s lexical token-matching, which
//!   would count `fetch` inside a comment or a string literal as a
//!   reference. This module's callers-intent is evidence-only for this
//!   commit: it does not replace `extract_references` (that's frozen, and
//!   doing so would ripple into `embed_text`/`content_hash` re-embedding
//!   exactly as the tags migration's constant-capture fix did).
//!
//! Only two languages implement `ast_grep_core::Language` here (Python,
//! TypeScript — TSX reuses TypeScript's grammar family and syntax for the
//! patterns this module uses), reusing the exact same `tree_sitter::Language`
//! instances `languages::PYTHON_PROFILE`/`TYPESCRIPT_PROFILE`/`TSX_PROFILE`
//! already wire up — no second source of truth for which grammar a language
//! uses.

use crate::symbols::Language;
use ast_grep_core::language::Language as AgLanguage;
use ast_grep_core::matcher::{Pattern, PatternBuilder, PatternError};
use ast_grep_core::tree_sitter::{LanguageExt, StrDoc, TSLanguage};

// The two macros below are copied, not vendored as a dependency, from
// ast-grep-language 0.45.3's src/lib.rs (MIT license,
// https://github.com/ast-grep/ast-grep) — depending on that crate directly
// would pull in ~23 grammar crates OXIDE doesn't use (rust, go, java, php,
// ruby, html, yaml, kotlin, ...) for zero benefit; `ast-grep-core` alone
// with `default-features = false, features = ["tree-sitter"]` needs none of
// them. Each macro wires a raw `tree_sitter::Language` into ast-grep-core's
// `Language`/`LanguageExt` traits in ~15 mechanical lines — this is exactly
// the same "grammar + tiny glue, not a bespoke implementation" shape as the
// tags.scm migration.
macro_rules! impl_ag_lang {
    ($lang:ident, $func:expr) => {
        #[derive(Clone, Copy, Debug)]
        struct $lang;
        impl AgLanguage for $lang {
            fn kind_to_id(&self, kind: &str) -> u16 {
                self.get_ts_language().id_for_node_kind(kind, true)
            }
            fn field_to_id(&self, field: &str) -> Option<u16> {
                self.get_ts_language()
                    .field_id_for_name(field)
                    .map(|f| f.get())
            }
            fn build_pattern(&self, builder: &PatternBuilder) -> Result<Pattern, PatternError> {
                builder.build(|src| StrDoc::try_new(src, self.clone()))
            }
        }
        impl LanguageExt for $lang {
            fn get_ts_language(&self) -> TSLanguage {
                ($func)().into()
            }
        }
    };
    ($lang:ident, $func:expr, expando = $char:expr) => {
        #[derive(Clone, Copy, Debug)]
        struct $lang;
        impl AgLanguage for $lang {
            fn kind_to_id(&self, kind: &str) -> u16 {
                self.get_ts_language().id_for_node_kind(kind, true)
            }
            fn field_to_id(&self, field: &str) -> Option<u16> {
                self.get_ts_language()
                    .field_id_for_name(field)
                    .map(|f| f.get())
            }
            fn expando_char(&self) -> char {
                $char
            }
            fn pre_process_pattern<'q>(&self, query: &'q str) -> std::borrow::Cow<'q, str> {
                ast_grep_expando::pre_process_pattern(self.expando_char(), query)
            }
            fn build_pattern(&self, builder: &PatternBuilder) -> Result<Pattern, PatternError> {
                builder.build(|src| StrDoc::try_new(src, self.clone()))
            }
        }
        impl LanguageExt for $lang {
            fn get_ts_language(&self) -> TSLanguage {
                ($func)().into()
            }
        }
    };
}

/// `$` is not a valid mid-identifier character in Python, so ast-grep needs
/// a runtime substitute (expando) character for meta-variables. Copied
/// verbatim from ast-grep-language 0.45.3's `pre_process_pattern`.
mod ast_grep_expando {
    pub fn pre_process_pattern(expando: char, query: &str) -> std::borrow::Cow<'_, str> {
        let mut ret = Vec::with_capacity(query.len());
        let mut dollar_count = 0;
        for c in query.chars() {
            if c == '$' {
                dollar_count += 1;
                continue;
            }
            let need_replace = matches!(c, 'A'..='Z' | '_') || dollar_count == 3;
            let sigil = if need_replace { expando } else { '$' };
            ret.extend(std::iter::repeat_n(sigil, dollar_count));
            dollar_count = 0;
            ret.push(c);
        }
        let sigil = if dollar_count == 3 { expando } else { '$' };
        ret.extend(std::iter::repeat_n(sigil, dollar_count));
        std::borrow::Cow::Owned(ret.into_iter().collect())
    }
}

impl_ag_lang!(AgPython, || tree_sitter_python::LANGUAGE, expando = 'µ');
impl_ag_lang!(AgTypeScript, || tree_sitter_typescript::LANGUAGE_TYPESCRIPT);
impl_ag_lang!(AgTsx, || tree_sitter_typescript::LANGUAGE_TSX);

#[derive(Debug, Clone, PartialEq)]
pub struct StructuralHit {
    pub file: String,
    pub start_line: u32,
    pub end_line: u32,
    pub text: String,
}

/// One file's source, keyed by its repo-relative path — the bounded
/// candidate set a caller passes in. Structural queries here are always
/// scoped to an explicit file list (the files of already-retrieved
/// symbols), never a whole-repo scan: ast-grep re-parses every file it's
/// given, on top of the parse OXIDE's own indexer already did for the same
/// file, so an unbounded scan multiplies real cost across the repo.
pub struct FileSource<'a> {
    pub file: &'a str,
    pub src: &'a str,
}

pub trait StructuralSearchProvider {
    /// Definitions in `files` that implement/extend `type_name` — Python:
    /// `class X(type_name):`; TypeScript/TSX: `class X implements type_name`
    /// or `class X extends type_name`.
    fn find_implementors(
        &self,
        lang: Language,
        files: &[FileSource],
        type_name: &str,
    ) -> Vec<StructuralHit>;

    /// AST-precise call sites of `function_name` in `files` — a call
    /// expression, never text inside a comment or string literal.
    fn find_callers(
        &self,
        lang: Language,
        files: &[FileSource],
        function_name: &str,
    ) -> Vec<StructuralHit>;
}

pub struct AstGrepProvider;

fn hits_for<L: LanguageExt + Clone>(
    lang: L,
    files: &[FileSource],
    patterns: &[String],
) -> Vec<StructuralHit> {
    let mut out = Vec::new();
    for f in files {
        let doc = lang.clone().ast_grep(f.src);
        for pattern in patterns {
            for n in doc.root().find_all(pattern.as_str()) {
                let range = n.range();
                out.push(StructuralHit {
                    file: f.file.to_string(),
                    start_line: f.src[..range.start].bytes().filter(|&b| b == b'\n').count() as u32
                        + 1,
                    end_line: f.src[..range.end.min(f.src.len())]
                        .bytes()
                        .filter(|&b| b == b'\n')
                        .count() as u32
                        + 1,
                    text: n.text().to_string(),
                });
            }
        }
    }
    out.sort_by(|a, b| (a.file.as_str(), a.start_line).cmp(&(b.file.as_str(), b.start_line)));
    out.dedup_by(|a, b| a.file == b.file && a.start_line == b.start_line && a.text == b.text);
    out
}

impl StructuralSearchProvider for AstGrepProvider {
    fn find_implementors(
        &self,
        lang: Language,
        files: &[FileSource],
        type_name: &str,
    ) -> Vec<StructuralHit> {
        match lang {
            Language::Python => hits_for(
                AgPython,
                files,
                &[format!("class $NAME({type_name}): $$$BODY")],
            ),
            Language::TypeScript | Language::Tsx => {
                let patterns = vec![
                    format!("class $NAME implements {type_name} {{ $$$BODY }}"),
                    format!("class $NAME extends {type_name} {{ $$$BODY }}"),
                ];
                if lang == Language::Tsx {
                    hits_for(AgTsx, files, &patterns)
                } else {
                    hits_for(AgTypeScript, files, &patterns)
                }
            }
        }
    }

    fn find_callers(
        &self,
        lang: Language,
        files: &[FileSource],
        function_name: &str,
    ) -> Vec<StructuralHit> {
        // Both bare (`fn(...)`) and method/attribute (`obj.fn(...)`) forms —
        // a bare-only pattern misses the common case, confirmed empirically:
        // `policy.should_retry(attempt, error)` in fixtures/py_repo would
        // not match `should_retry($$$ARGS)` alone.
        let patterns = vec![
            format!("{function_name}($$$ARGS)"),
            format!("$OBJ.{function_name}($$$ARGS)"),
        ];
        match lang {
            Language::Python => hits_for(AgPython, files, &patterns),
            Language::TypeScript => hits_for(AgTypeScript, files, &patterns),
            Language::Tsx => hits_for(AgTsx, files, &patterns),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn finds_cross_file_implementors_and_excludes_non_implementors() {
        let base = FileSource {
            file: "shapes.ts",
            src: "interface Shape { area(): number }\nclass Other { area() { return 0 } }\n",
        };
        let impls = FileSource {
            file: "impls.ts",
            src: "class Circle implements Shape { area() { return 1 } }\nclass Square implements Shape { area() { return 2 } }\n",
        };
        let hits = AstGrepProvider.find_implementors(Language::TypeScript, &[base, impls], "Shape");
        let names: Vec<&str> = hits
            .iter()
            .map(|h| h.text.lines().next().unwrap())
            .collect();
        assert_eq!(hits.len(), 2, "{names:?}");
        assert!(names
            .iter()
            .all(|n| n.starts_with("class Circle") || n.starts_with("class Square")));
    }

    #[test]
    fn call_matching_is_ast_precise_not_lexical() {
        let f = FileSource {
            file: "x.ts",
            src: "// call fetch(x) in a comment\nconst s = \"fetch(y)\";\nfetch(real());\n",
        };
        let hits = AstGrepProvider.find_callers(Language::TypeScript, &[f], "fetch");
        assert_eq!(hits.len(), 1, "{hits:?}");
        assert_eq!(hits[0].text, "fetch(real())");
    }

    #[test]
    fn python_subclass_implementors_across_files() {
        let base = FileSource {
            file: "notifiers.py",
            src: "class Notifier:\n    def notify(self, m): raise NotImplementedError\n",
        };
        let sub = FileSource {
            file: "email.py",
            src: "class EmailNotifier(Notifier):\n    def notify(self, m): print(m)\n\nclass Standalone:\n    pass\n",
        };
        let hits = AstGrepProvider.find_implementors(Language::Python, &[base, sub], "Notifier");
        assert_eq!(hits.len(), 1, "{hits:?}");
        assert!(hits[0].text.starts_with("class EmailNotifier(Notifier)"));
    }

    #[test]
    fn finds_method_style_calls_not_just_bare_calls() {
        // Caught empirically via fixtures/structural_benchmark.json's
        // py-callers-should-retry task: a bare-only pattern missed
        // `policy.should_retry(attempt, error)` entirely.
        let f = FileSource {
            file: "x.py",
            src: "def f(policy, attempt, error):\n    if not policy.should_retry(attempt, error):\n        return\n",
        };
        let hits = AstGrepProvider.find_callers(Language::Python, &[f], "should_retry");
        assert_eq!(hits.len(), 1, "{hits:?}");
        assert_eq!(hits[0].text, "policy.should_retry(attempt, error)");
    }
}
