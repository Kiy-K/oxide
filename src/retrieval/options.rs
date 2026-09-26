//! Request and response contracts of a retrieval call: which channels run
//! ([`SearchMode`]), how much bounded structural evidence a request may
//! collect ([`RetrievalMode`]), the per-call [`SearchOptions`], and the
//! [`SearchHit`] a search returns.

use crate::symbols::Symbol;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SearchMode {
    LexicalOnly,
    VectorOnly,
    Hybrid,
}

/// Relevance/latency tradeoff for a request. Controls how much *expensive*
/// evidence (bounded ast-grep expansion, in `context.rs`) gets collected on
/// top of the always-on lexical+semantic stage — it does not gate lexical or
/// semantic scoring themselves, which run unconditionally and concurrently.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum RetrievalMode {
    Fast,
    #[default]
    Balanced,
    Quality,
}

impl RetrievalMode {
    pub fn parse(s: &str) -> Option<Self> {
        match s.trim().to_ascii_lowercase().as_str() {
            "fast" => Some(Self::Fast),
            "balanced" => Some(Self::Balanced),
            "quality" => Some(Self::Quality),
            _ => None,
        }
    }

    /// `explicit` (a `--mode`/tool-argument flag) wins; then `$OXIDE_RETRIEVAL_MODE`;
    /// an unconfigured agent always lands on `Balanced` (the `Default` impl).
    /// Mirrors the existing embedder-selection precedence in `cli.rs`.
    pub fn resolve(explicit: Option<&str>) -> Self {
        explicit
            .and_then(Self::parse)
            .or_else(|| {
                std::env::var("OXIDE_RETRIEVAL_MODE")
                    .ok()
                    .and_then(|v| Self::parse(&v))
            })
            .unwrap_or_default()
    }

    /// Bounded ast-grep expansion budget: `(max anchored seeds, max files per
    /// seed)`. `None` means skip the stage entirely (`Fast`) — never a
    /// whole-repo scan regardless of mode.
    pub fn structural_budget(self) -> Option<(usize, usize)> {
        match self {
            Self::Fast => None,
            Self::Balanced => Some((2, 3)),
            Self::Quality => Some((3, 6)),
        }
    }

    /// Whether the (currently no-op) downstream reranker stage runs.
    pub fn rerank(self) -> bool {
        matches!(self, Self::Quality)
    }
}

pub struct SearchOptions {
    pub limit: usize,
    pub mode: SearchMode,
    /// Include structural expansion around strong initial hits.
    pub expand: bool,
    pub retrieval_mode: RetrievalMode,
}

impl Default for SearchOptions {
    fn default() -> Self {
        Self {
            limit: 10,
            mode: SearchMode::Hybrid,
            expand: true,
            retrieval_mode: RetrievalMode::default(),
        }
    }
}

#[derive(Debug, Clone, serde::Serialize)]
pub struct SearchHit {
    #[serde(flatten)]
    pub symbol: Symbol,
    pub score: f32,
    pub reasons: Vec<String>,
    pub snippet: String,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn retrieval_mode_parses_case_insensitively_and_rejects_garbage() {
        assert_eq!(RetrievalMode::parse("Fast"), Some(RetrievalMode::Fast));
        assert_eq!(
            RetrievalMode::parse("QUALITY"),
            Some(RetrievalMode::Quality)
        );
        assert_eq!(RetrievalMode::parse("turbo"), None);
    }

    #[test]
    fn retrieval_mode_resolve_prefers_explicit_then_defaults_to_balanced() {
        assert_eq!(RetrievalMode::resolve(Some("fast")), RetrievalMode::Fast);
        // No explicit value and (in a clean test process) no
        // $OXIDE_RETRIEVAL_MODE set: an unconfigured agent must land on
        // Balanced, never silently on Fast or Quality.
        assert_eq!(RetrievalMode::resolve(None), RetrievalMode::Balanced);
    }
}
