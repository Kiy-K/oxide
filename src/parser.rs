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

pub use crate::languages::{python, typescript};

static PYTHON: python::PythonExtractor = python::PythonExtractor;
static TYPESCRIPT: typescript::TsExtractor = typescript::TsExtractor { tsx: false };
static TSX: typescript::TsExtractor = typescript::TsExtractor { tsx: true };

pub fn extractor_for(lang: Language) -> &'static dyn LanguageExtractor {
    match lang {
        Language::Python => &PYTHON,
        Language::TypeScript => &TYPESCRIPT,
        Language::Tsx => &TSX,
    }
}

/// Parse `src` into symbols. Returns an empty vec (not an error) on parse
/// failure so indexing degrades gracefully instead of aborting a run.
pub fn parse_file(file: &str, src: &str, lang: Language) -> Vec<Symbol> {
    let ext = extractor_for(lang);
    let imports = ext.collect_imports(src);
    let mut syms = ext.extract(file, src, &imports);
    // Stable ids are (file, qualified_name); duplicate qualified names in one
    // file (overloads, conditional defs) would violate the primary key. Keep
    // the first declaration per name.
    let mut seen: HashSet<String> = HashSet::new();
    syms.retain(|s| seen.insert(s.qualified_name.clone()));
    // File-level module symbol spanning the whole file. Its hash deliberately
    // ignores body edits (imports + first line only): otherwise every file
    // edit would re-embed the module blob alongside the truly-changed symbol.
    // # ponytail: coarse module identity; per-region hashing if staleness hurts
    syms.push(Symbol {
        qualified_name: format!("{file}:__module__"),
        name: "__module__".into(),
        kind: crate::symbols::SymbolKind::Module,
        language: lang,
        file: file.to_string(),
        start_line: 1,
        end_line: src.lines().count().max(1) as u32,
        content_hash: crate::symbols::content_hash(&format!(
            "{}\n{}",
            syms.first()
                .map(|s| s.imports.join(","))
                .unwrap_or_default(),
            src.lines()
                .find(|l| !l.trim().is_empty())
                .unwrap_or("")
                .trim()
        )),
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
