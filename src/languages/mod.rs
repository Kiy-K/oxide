pub mod tags;

pub use crate::parser::LanguageExtractor;

use crate::symbols::Language;
use tags::LanguageProfile;

const PYTHON_TAGS: &str = include_str!("queries/python_tags.scm");
const TS_TAGS: &str = include_str!("queries/typescript_tags.scm");
const TS_LOCALS: &str = include_str!("queries/typescript_locals.scm");
const RUST_TAGS: &str = include_str!("queries/rust_tags.scm");
const GO_TAGS: &str = include_str!("queries/go_tags.scm");
const JAVA_TAGS: &str = include_str!("queries/java_tags.scm");
const RUBY_TAGS: &str = include_str!("queries/ruby_tags.scm");
const PHP_TAGS: &str = include_str!("queries/php_tags.scm");
const C_TAGS: &str = include_str!("queries/c_tags.scm");

pub static PYTHON_PROFILE: LanguageProfile = LanguageProfile {
    language: Language::Python,
    ts_language: || tree_sitter_python::LANGUAGE.into(),
    tags_query: PYTHON_TAGS,
    locals_query: "",
};

pub static TYPESCRIPT_PROFILE: LanguageProfile = LanguageProfile {
    language: Language::TypeScript,
    ts_language: || tree_sitter_typescript::LANGUAGE_TYPESCRIPT.into(),
    tags_query: TS_TAGS,
    locals_query: TS_LOCALS,
};

pub static TSX_PROFILE: LanguageProfile = LanguageProfile {
    language: Language::Tsx,
    ts_language: || tree_sitter_typescript::LANGUAGE_TSX.into(),
    tags_query: TS_TAGS,
    locals_query: TS_LOCALS,
};

/// JavaScript and JSX, parsed with the **TSX** grammar and the TypeScript
/// tags/locals queries — not a separate `tree-sitter-javascript` grammar and
/// not duplicated `.scm` files.
///
/// TSX is `tree-sitter-javascript` plus TypeScript's additions, so it is a
/// syntactic superset of JavaScript; the one place the two grammars
/// genuinely disagree is `<`, and TSX resolves it the way a `.jsx` file
/// does (JSX element, not a type assertion — that reading is `.ts`-only).
/// Measured before adopting: 261/261 real `.js` files across tailwindcss and
/// openlibrary parse with zero ERROR/MISSING nodes, as do hand-written
/// probes of the ambiguity traps (`const lt = 1 < 2 > 0`, `/a<b>c/g`),
/// private fields, static blocks, generators, optional chaining and CJS.
///
/// Sharing the queries rather than forking them is the same argument
/// `TSX_CALLERS_SRC` already makes for its concatenation: two copies of the
/// same call/definition patterns drift, and every TypeScript pattern that
/// isn't JavaScript (`interface_declaration`, `type_alias_declaration`)
/// simply never matches in a `.js` file.
pub static JAVASCRIPT_PROFILE: LanguageProfile = LanguageProfile {
    language: Language::JavaScript,
    ts_language: || tree_sitter_typescript::LANGUAGE_TSX.into(),
    tags_query: TS_TAGS,
    locals_query: TS_LOCALS,
};

pub static RUST_PROFILE: LanguageProfile = LanguageProfile {
    language: Language::Rust,
    ts_language: || tree_sitter_rust::LANGUAGE.into(),
    tags_query: RUST_TAGS,
    locals_query: "",
};

pub static GO_PROFILE: LanguageProfile = LanguageProfile {
    language: Language::Go,
    ts_language: || tree_sitter_go::LANGUAGE.into(),
    tags_query: GO_TAGS,
    locals_query: "",
};

/// Java. Upstream `tree-sitter-java`'s `tags.scm` covers only classes,
/// interfaces and methods (see `docs/java-feasibility/README.md`), so the
/// query is OXIDE-owned: constructors, enums, records and annotation types
/// are appended. Method and constructor *qualified names* carry a
/// normalized parameter-type list (`Store.get(String,String)`) — see
/// `tags.rs::java_signature` for why that is required and why it is safe.
pub static JAVA_PROFILE: LanguageProfile = LanguageProfile {
    language: Language::Java,
    ts_language: || tree_sitter_java::LANGUAGE.into(),
    tags_query: JAVA_TAGS,
    locals_query: "",
};

/// Ruby. Upstream `tree-sitter-ruby`'s `tags.scm` covers methods, classes
/// and modules; constants are appended and its two `@reference.call`
/// patterns are dropped (see `queries/ruby_tags.scm`). `locals_query` stays
/// empty like every other profile but Java-adjacent TypeScript's — the only
/// upstream pattern that needed it was one of the dropped ones.
pub static RUBY_PROFILE: LanguageProfile = LanguageProfile {
    language: Language::Ruby,
    ts_language: || tree_sitter_ruby::LANGUAGE.into(),
    tags_query: RUBY_TAGS,
    locals_query: "",
};

/// PHP, on the `LANGUAGE_PHP` grammar rather than `LANGUAGE_PHP_ONLY`: a
/// real `.php` file is a *template* that opens with `<?php`, and the
/// PHP-only grammar cannot parse the surrounding text at all. Node kinds
/// are shared between the two, so the queries read identically either way.
pub static PHP_PROFILE: LanguageProfile = LanguageProfile {
    language: Language::Php,
    ts_language: || tree_sitter_php::LANGUAGE_PHP.into(),
    tags_query: PHP_TAGS,
    locals_query: "",
};

/// C.
pub static C_PROFILE: LanguageProfile = LanguageProfile {
    language: Language::C,
    ts_language: || tree_sitter_c::LANGUAGE.into(),
    tags_query: C_TAGS,
    locals_query: "",
};
