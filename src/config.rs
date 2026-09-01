//! Fixed production retrieval and context-allocation settings.
//!
//! This is the sole source for values that affect ranking or packed context.
//! Change one only with a fresh canonical benchmark and an intentional
//! re-baseline.

pub(crate) const FUSION_RRF_K: f32 = 60.0;
pub(crate) const FUSION_LEXICAL_WEIGHT: f32 = 0.6;
pub(crate) const FUSION_SEMANTIC_WEIGHT: f32 = 0.4;
pub(crate) const FUSION_CANDIDATE_LIMIT: usize = 200;
pub(crate) const EXPANSION_STRONG_SEED_FRACTION: f32 = 0.55;

pub(crate) const CONTEXT_DEFAULT_BUDGET_TOKENS: usize = 4096;
pub(crate) const CONTEXT_MAX_CANDIDATES: usize = 16;
pub(crate) const CONTEXT_CHARS_PER_TOKEN: f32 = 4.0;
pub(crate) const CONTEXT_ITEM_OVERHEAD_TOKENS: usize = 12;
pub(crate) const CONTEXT_PER_ITEM_TOKEN_CAP: usize = 350;
pub(crate) const CONTEXT_RELEVANCE_FLOOR_FRACTION: f32 = 0.15;
pub(crate) const CONTEXT_EXPANSION_PER_SEED: usize = 2;
pub(crate) const CONTEXT_EXPANSION_TOTAL: usize = 2;
pub(crate) const CONTEXT_MAX_ITEMS_PER_FILE: usize = 2;
pub(crate) const CONTEXT_MAX_PRIMARIES: usize = 5;
pub(crate) const CONTEXT_MAX_TESTS: usize = 1;

/// Term-coverage corroboration boost (experiment, see
/// docs/term-coverage-eval/README.md). `0.0` is a no-op — byte-identical to
/// pre-experiment fusion scoring — and is overridden only via
/// `$OXIDE_TERM_COVERAGE_ALPHA` for the experiment itself. Promoting a
/// nonzero value to the shipped default here requires the same fresh
/// canonical-benchmark re-baseline as any other constant in this file.
pub(crate) const TERM_COVERAGE_ALPHA_DEFAULT: f32 = 0.0;
