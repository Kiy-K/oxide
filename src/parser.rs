//! Tree-sitter plumbing and the per-language extraction interface.

use crate::symbols::{Language, Symbol};
use std::collections::HashSet;

/// A grammar-backed extractor producing declaration symbols for one language.
/// Adding a language = implementing this trait + registering below.
pub trait LanguageExtractor: Sync {
    fn language(&self) -> Language;
    fn ts_language(&self) -> tree_sitter::Language;
    /// Extract declaration symbols (the module symbol is added by the caller).
    fn extract(&self, file: &str, src: &str, file_imports: &[String]) -> Vec<Symbol>;
    /// Raw import module strings declared anywhere in the file.
    fn collect_imports(&self, src: &str) -> Vec<String>;
}

pub use crate::languages::tags;

static PYTHON_TAGS: tags::TagsExtractor =
    tags::TagsExtractor::new(&crate::languages::PYTHON_PROFILE);
static TYPESCRIPT_TAGS: tags::TagsExtractor =
    tags::TagsExtractor::new(&crate::languages::TYPESCRIPT_PROFILE);
static TSX_TAGS: tags::TagsExtractor = tags::TagsExtractor::new(&crate::languages::TSX_PROFILE);
static RUST_TAGS: tags::TagsExtractor = tags::TagsExtractor::new(&crate::languages::RUST_PROFILE);

/// The only extraction path: grammar + declarative tags.scm +
/// normalization, not a bespoke per-language walker. The handwritten
/// procedural extractors this replaced were deleted once
/// `tags.rs::decorator_extended_start` closed the one capability
/// (decorator-inclusive spans) they were still retained for.
pub fn extractor_for(lang: Language) -> &'static dyn LanguageExtractor {
    match lang {
        Language::Python => &PYTHON_TAGS,
        Language::TypeScript => &TYPESCRIPT_TAGS,
        Language::Tsx => &TSX_TAGS,
        Language::Rust => &RUST_TAGS,
    }
}

/// Parse `src` into symbols. Returns an empty vec (not an error) on parse
/// failure so indexing degrades gracefully instead of aborting a run.
pub fn parse_file(file: &str, src: &str, lang: Language) -> Vec<Symbol> {
    parse_file_with(extractor_for(lang), file, src, lang)
}

/// Same orchestration as `parse_file` (dedup + module fallback), against an
/// arbitrary extractor — lets tests pin behavior against a specific
/// extractor regardless of which one `extractor_for` currently defaults to.
pub fn parse_file_with(
    ext: &dyn LanguageExtractor,
    file: &str,
    src: &str,
    lang: Language,
) -> Vec<Symbol> {
    let imports = ext.collect_imports(src);
    let mut syms = ext.extract(file, src, &imports);
    // Stable ids are (file, qualified_name); duplicate qualified names in one
    // file (overloads, conditional defs) would violate the primary key. Keep
    // the first declaration per name.
    let mut seen: HashSet<String> = HashSet::new();
    syms.retain(|s| seen.insert(s.qualified_name.clone()));
    // Structure-preserving fallback: when normal extraction yields nothing
    // (e.g., test file with only imports/comments or parse failure), keep a
    // file-level module symbol so the file remains discoverable. Otherwise
    // the file would be invisible to lexical/semantic search despite being
    // a valid gold target. Hash for empty-extraction files uses full source
    // to avoid collisions across empty files.
    let empty_before_module = syms.is_empty();
    // File-level module symbol spanning the whole file. Its hash deliberately
    // ignores body edits when concrete symbols exist (imports + first line
    // only): otherwise every file edit would re-embed the module blob
    // alongside the truly-changed symbol.
    // # ponytail: coarse module identity; per-region hashing if staleness hurts
    syms.push(Symbol {
        qualified_name: format!("{file}:__module__"),
        name: "__module__".into(),
        kind: crate::symbols::SymbolKind::Module,
        language: lang,
        file: file.to_string(),
        start_line: 1,
        end_line: src.lines().count().max(1) as u32,
        content_hash: if empty_before_module {
            crate::symbols::content_hash(src)
        } else {
            crate::symbols::content_hash(&format!(
                "{}\n{}",
                syms.first()
                    .map(|s| s.imports.join(","))
                    .unwrap_or_default(),
                src.lines()
                    .find(|l| !l.trim().is_empty())
                    .unwrap_or("")
                    .trim()
            ))
        },
        signature: src
            .lines()
            .find(|l| !l.trim().is_empty())
            .unwrap_or("")
            .trim()
            .chars()
            .take(160)
            .collect(),
        imports,
        exported: false,
        parent: None,
        references: Vec::new(),
        calls: Vec::new(),
        bases: Vec::new(),
    });
    syms
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::symbols::Language;

    #[test]
    fn duplicate_qualified_names_are_deduped() {
        // Conditional defs with the same name appear twice in the tree.
        let src = "def f():\n    return 1\n\nif x:\n    def f():\n        return 2\n";
        let syms = parse_file("a.py", src, Language::Python);
        let count = syms.iter().filter(|s| s.name == "f").count();
        assert_eq!(
            count,
            1,
            "{:?}",
            syms.iter()
                .map(|s| s.qualified_name.clone())
                .collect::<Vec<_>>()
        );
    }

    #[test]
    fn empty_file_yields_module_fallback() {
        // Source/test file with no declarations must still be indexed via
        // structure-preserving module fallback so gold files remain discoverable.
        let syms = parse_file(
            "tests/test_empty.py",
            "# just a comment\n",
            Language::Python,
        );
        assert_eq!(syms.len(), 1, "empty file must yield fallback module");
        assert_eq!(syms[0].file, "tests/test_empty.py");
        assert!(syms[0].qualified_name.ends_with(":__module__"));
        // fallback hash uses full source, not coarse imports+first-line
        let syms2 = parse_file(
            "tests/test_empty2.py",
            "# different comment\n",
            Language::Python,
        );
        assert_ne!(
            syms[0].content_hash, syms2[0].content_hash,
            "empty files must hash distinctly"
        );
    }

    #[test]
    fn ts_overloads_do_not_crash_ids() {
        let src = "interface Foo { bar(x: string): void; bar(x: number): void; }\n\
                   function helper(): void {}\n";
        let syms = parse_file("b.ts", src, Language::TypeScript);
        let mut ids: Vec<u64> = syms.iter().map(|s| s.id()).collect();
        ids.sort();
        ids.dedup();
        assert_eq!(ids.len(), syms.len());
    }
}
