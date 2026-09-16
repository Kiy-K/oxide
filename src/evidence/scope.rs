//! One shared "which files does this seed pool touch" helper, replacing the
//! three independent implementations that used to live inline in
//! `context.rs` for structural/Git/LSP evidence (audit MINOR-1: they had
//! silently diverging bounds despite a comment claiming parity). A source
//! that needs a different bound passes a different `max_files` and
//! documents why at its call site.

use crate::retrieval::SearchHit;

pub fn scope_files_from_seeds(seeds: &[SearchHit], max_files: usize) -> Vec<String> {
    let mut files = Vec::new();
    for h in seeds {
        if files.len() >= max_files {
            break;
        }
        if !files.contains(&h.symbol.file) {
            files.push(h.symbol.file.clone());
        }
    }
    files
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::symbols::{Language, Symbol, SymbolKind};

    fn hit(file: &str, score: f32) -> SearchHit {
        SearchHit {
            symbol: Symbol {
                file: file.to_string(),
                name: "x".into(),
                qualified_name: "x".into(),
                kind: SymbolKind::Function,
                language: Language::Python,
                start_line: 1,
                end_line: 2,
                content_hash: 0,
                signature: String::new(),
                imports: vec![],
                exported: false,
                parent: None,
                references: vec![],
                calls: vec![],
                bases: vec![],
            },
            score,
            reasons: vec![],
            snippet: String::new(),
        }
    }

    #[test]
    fn caps_to_max_files_preserving_seed_order_and_dedups() {
        let seeds = vec![
            hit("a.py", 3.0),
            hit("b.py", 2.0),
            hit("a.py", 1.9),
            hit("c.py", 1.0),
        ];
        let scoped = scope_files_from_seeds(&seeds, 2);
        assert_eq!(scoped, vec!["a.py".to_string(), "b.py".to_string()]);
    }

    #[test]
    fn empty_seeds_yields_empty_scope() {
        assert!(scope_files_from_seeds(&[], 5).is_empty());
    }
}
