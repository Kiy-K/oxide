use crate::symbols::Symbol;

/// Convenience: embedding text for a symbol (kept next to the provider).
pub fn symbol_embed_text(s: &Symbol) -> String {
    format!(
        "{} {} {} {} {} {}",
        s.file,
        s.kind,
        s.qualified_name,
        s.signature,
        s.imports.join(" "),
        s.references.join(" ")
    )
}

/// Qwen3's instruction-prefixed query protocol (model-card guidance: improves
/// NL→PL retrieval 1-5%). Pure and independently testable — this is the
/// literal text `HttpEmbedder::embed_query` sends; pin expectations against
/// this function's output, not against the call sites that use it.
///
/// Relocated from `context::instructed_query` (Phase 3.3 embedding-boundary
/// refactor): query formatting is a property of the model behind the
/// provider, not of the caller building a query.
pub(crate) fn qwen3_query_text(task: &str) -> String {
    format!(
        "Instruct: Given a coding task, retrieve repository symbols that are \
         relevant to understand or change to complete it\nQuery: {task}"
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    /// Regression pin (Phase 3.3 embedding-boundary refactor): the exact
    /// bytes previously produced by the now-removed `context::instructed_query`.
    /// The expected string is written out literally, not derived by calling
    /// the function under test, so a future reword of the prompt fails this
    /// test instead of silently vanishing.
    #[test]
    fn qwen3_query_text_matches_legacy_instructed_query_format() {
        assert_eq!(
            qwen3_query_text("fix backoff"),
            "Instruct: Given a coding task, retrieve repository symbols that are \
             relevant to understand or change to complete it\nQuery: fix backoff"
        );
    }
}
