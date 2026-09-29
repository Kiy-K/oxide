//! The candidate/evidence boundary between retrieval and allocation (#34
//! S3): a symbol, its unchanged scalar `score`, and typed [`Reason`]s
//! recording why it is a candidate. Every stage that produces or merges
//! candidates — fusion and expansion in `RetrievalEngine`, the evidence
//! coordinator, `context.rs`, `review.rs` — works on this type; the public
//! `reasons: Vec<String>` of `SearchHit`/`ContextItem` is rendered from it
//! at the output edge, byte-identical to the strings those stages used to
//! format directly.
//!
//! Observational only: nothing reads a [`Reason`] to rank, cap, admit,
//! assign a role or break a tie. The channel ranks it carries are there so
//! research can see per-channel provenance (e.g. through
//! `RetrievalEngine::search_candidates`, from in-crate code) without
//! patching production code. Crate-internal: the public Rust API and the
//! wire output are unchanged by it.

use crate::retrieval::SearchHit;
use crate::symbols::Symbol;
use std::fmt;

#[derive(Debug, Clone)]
pub(crate) struct Candidate {
    pub(crate) symbol: Symbol,
    pub(crate) score: f32,
    pub(crate) reasons: Vec<Reason>,
}

impl Candidate {
    /// The public hit shape, with `reasons` rendered.
    pub(crate) fn into_hit(self) -> SearchHit {
        SearchHit {
            symbol: self.symbol,
            score: self.score,
            reasons: render(&self.reasons),
            snippet: String::new(),
        }
    }
}

/// A fused retrieval channel.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum Channel {
    Lexical,
    Semantic,
}

/// One piece of evidence. Each variant holds exactly what its rendered
/// reason string shows, plus (for channels) the rank the string never
/// carried; equality therefore matches the string equality the stages used
/// to deduplicate on.
#[derive(Debug, Clone, PartialEq)]
pub(crate) enum Reason {
    /// Ranked by a fusion channel: `rank` is the 0-based position in that
    /// channel's top-K, `score` its raw channel score (BM25 or dot
    /// product). Renders `lexical=0.123` / `semantic=0.456`.
    Channel {
        channel: Channel,
        rank: usize,
        score: f32,
    },
    /// Reached from `seed` (its qualified name) over a `RelationGraph`
    /// neighbor relation (`uses`, `test`, `child`, ...). Renders
    /// `{relation}←{seed}`.
    Related { relation: String, seed: String },
    /// A scoped caller of `seed` found by the evidence coordinator.
    /// Renders `ast-grep-caller←{seed}` (the tag predates the move off
    /// ast-grep and is wire-visible).
    Caller { seed: String },
    /// In the blast radius of the seed `via`. Renders
    /// `blast-radius:{relation}←{via}`.
    BlastRadius { relation: &'static str, via: String },
    /// A symbol the current diff touches. Renders
    /// `git-changed(+{added_lines})←{file}`.
    GitChanged { added_lines: u32, file: String },
    /// A caller or test of the changed symbol `changed`. Renders
    /// `git-caller-of-changed←{changed}`.
    GitCallerOfChanged { changed: String },
    /// Declared in a file that co-changed with `file`. Renders
    /// `git-cochange({commits} commits)←{file}`.
    GitCoChange { commits: usize, file: String },
    /// `review`'s semantic top-up. Renders `semantic-neighbor`.
    SemanticNeighbor,
}

impl fmt::Display for Reason {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Channel {
                channel: Channel::Lexical,
                score,
                ..
            } => write!(f, "lexical={score:.3}"),
            Self::Channel {
                channel: Channel::Semantic,
                score,
                ..
            } => write!(f, "semantic={score:.3}"),
            Self::Related { relation, seed } => write!(f, "{relation}←{seed}"),
            Self::Caller { seed } => write!(f, "ast-grep-caller←{seed}"),
            Self::BlastRadius { relation, via } => write!(f, "blast-radius:{relation}←{via}"),
            Self::GitChanged { added_lines, file } => {
                write!(f, "git-changed(+{added_lines})←{file}")
            }
            Self::GitCallerOfChanged { changed } => write!(f, "git-caller-of-changed←{changed}"),
            Self::GitCoChange { commits, file } => {
                write!(f, "git-cochange({commits} commits)←{file}")
            }
            Self::SemanticNeighbor => f.write_str("semantic-neighbor"),
        }
    }
}

/// The public `reasons` strings, in order.
pub(crate) fn render(reasons: &[Reason]) -> Vec<String> {
    reasons.iter().map(Reason::to_string).collect()
}
