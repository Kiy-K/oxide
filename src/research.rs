//! The one owner of the research and debug environment overrides (#34 S4).
//! Each is resolved once per request, at the request boundary
//! (`RetrievalEngine::search`, `context::build_context_with`,
//! `review::build_review_context`), and handed to the code that uses it, so
//! the frozen fusion and allocator bodies never read the process
//! environment. Per request, not per process: a long-lived caller (and the
//! tests that pin these) may change a variable between two requests, and the
//! next request sees it. Unset, every override is the shipped behavior.
//!
//! Production configuration (provider selection, retrieval mode, session
//! pools, telemetry) is not here.

use crate::config::{CONTEXT_MAX_PRIMARIES, TERM_COVERAGE_ALPHA_DEFAULT};

pub(crate) struct ResearchOverrides {
    /// `$OXIDE_TERM_COVERAGE_ALPHA`: the term-coverage corroboration bonus
    /// (docs/term-coverage-eval/, verdict REJECT). Any parse failure,
    /// including unset, is the frozen `0.0` default, and fusion applies the
    /// bonus only when this is `> 0.0`, so a non-positive or NaN value is the
    /// same no-op.
    pub(crate) term_coverage_alpha: f32,
    /// `$OXIDE_CONTEXT_MAX_PRIMARIES`: the context allocator's primary cap,
    /// for the primary-cap sensitivity experiment
    /// (docs/primary-cap-sensitivity/). Any `usize`, including 0; anything
    /// else is the shipped [`CONTEXT_MAX_PRIMARIES`].
    pub(crate) context_max_primaries: usize,
    /// `$OXIDE_DEBUG_DUMP_KEPT`: when set (to valid UTF-8), the path the
    /// context allocator writes its pre-allocation `kept` pool to as JSON.
    pub(crate) debug_dump_kept: Option<String>,
}

impl ResearchOverrides {
    pub(crate) fn from_env() -> Self {
        Self {
            term_coverage_alpha: std::env::var("OXIDE_TERM_COVERAGE_ALPHA")
                .ok()
                .and_then(|v| v.parse::<f32>().ok())
                .unwrap_or(TERM_COVERAGE_ALPHA_DEFAULT),
            context_max_primaries: std::env::var("OXIDE_CONTEXT_MAX_PRIMARIES")
                .ok()
                .and_then(|v| v.parse::<usize>().ok())
                .unwrap_or(CONTEXT_MAX_PRIMARIES),
            debug_dump_kept: std::env::var("OXIDE_DEBUG_DUMP_KEPT").ok(),
        }
    }
}
