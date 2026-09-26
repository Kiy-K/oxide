//! Conversion of retrieval output into wire `Evidence`, and the
//! deterministic order context items are returned in. Scores and ranking
//! are decided upstream (`crate::retrieval`, `crate::context`); nothing
//! here re-scores or re-ranks search hits.

use super::types::{ContextEvidence, Evidence};
use crate::context::Role;
use crate::symbols::Symbol;
use std::cmp::Ordering;

impl Evidence {
    pub(super) fn from_symbol(
        symbol: &Symbol,
        score: f32,
        reasons: Vec<String>,
        snippet: String,
    ) -> Self {
        Self {
            id: format!("{}#{}", symbol.file, symbol.qualified_name),
            file: symbol.file.clone(),
            qualified_name: symbol.qualified_name.clone(),
            name: symbol.name.clone(),
            kind: symbol.kind,
            language: symbol.language,
            start_line: symbol.start_line,
            end_line: symbol.end_line,
            score,
            reasons,
            snippet,
            blast_radius: Vec::new(),
        }
    }
}

fn compare_evidence(a: &Evidence, b: &Evidence) -> Ordering {
    b.score
        .partial_cmp(&a.score)
        .unwrap_or(Ordering::Equal)
        .then_with(|| a.id.cmp(&b.id))
}

fn role_rank(role: Role) -> u8 {
    match role {
        Role::Primary => 0,
        Role::Dependency => 1,
        Role::Test => 2,
    }
}

pub(super) fn compare_context_evidence(a: &ContextEvidence, b: &ContextEvidence) -> Ordering {
    role_rank(a.role)
        .cmp(&role_rank(b.role))
        .then_with(|| compare_evidence(&a.evidence, &b.evidence))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::symbols::{Language, SymbolKind};

    #[test]
    fn evidence_id_is_repository_relative_symbol_identity() {
        let symbol = Symbol {
            qualified_name: "Auth.refresh".into(),
            name: "refresh".into(),
            kind: SymbolKind::Method,
            language: Language::Python,
            file: "src/auth.py".into(),
            start_line: 2,
            end_line: 4,
            content_hash: 1,
            signature: "def refresh".into(),
            imports: Vec::new(),
            exported: false,
            parent: Some("Auth".into()),
            references: Vec::new(),
            calls: Vec::new(),
            bases: Vec::new(),
            completeness: Default::default(),
        };
        let evidence = Evidence::from_symbol(&symbol, 1.0, Vec::new(), "return token".into());
        assert_eq!(evidence.id, "src/auth.py#Auth.refresh");
        assert_eq!(evidence.file, "src/auth.py");
    }
}
