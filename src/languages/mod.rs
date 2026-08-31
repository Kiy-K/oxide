pub mod python;
pub mod tags;
pub mod typescript;

pub use crate::parser::LanguageExtractor;

use crate::symbols::Language;
use tags::LanguageProfile;

const PYTHON_TAGS: &str = include_str!("queries/python_tags.scm");
const TS_TAGS: &str = include_str!("queries/typescript_tags.scm");
const TS_LOCALS: &str = include_str!("queries/typescript_locals.scm");

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
