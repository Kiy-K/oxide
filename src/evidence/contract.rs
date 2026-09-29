//! Shared shape and execution controls for evidence sources the
//! `EvidenceCoordinator` collects — standardizes failure reporting, not
//! scoring: each source keeps its own reason-tag vocabulary and score
//! fractions (`config.rs`).

use crate::symbols::Symbol;
use std::time::Duration;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum EvidenceSource {
    Structural,
    Git,
    BlastRadius,
}

impl EvidenceSource {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Structural => "structural",
            Self::Git => "git",
            Self::BlastRadius => "blast_radius",
        }
    }
}

/// One piece of coordinator evidence as the public
/// [`EvidenceCoordinator::collect`](super::EvidenceCoordinator::collect)
/// returns it, `reasons` rendered. Inside the crate the coordinator works on
/// the typed internal candidate (#34 S3); this pre-S3 public shape is kept
/// as its adapter until #34 S6 decides the public surface.
#[derive(Debug, Clone)]
pub struct EvidenceCandidate {
    pub symbol: Symbol,
    pub score: f32,
    pub reasons: Vec<String>,
}

#[derive(Debug)]
pub enum DegradeReason {
    Timeout {
        elapsed: Duration,
        deadline: Duration,
    },
    Unavailable {
        detail: String,
    },
    ProtocolError {
        detail: String,
    },
    SubprocessError {
        exit_code: Option<i32>,
        stderr_tail: Option<String>,
    },
    Cancelled,
}

#[derive(Debug)]
pub struct Degraded {
    pub source: EvidenceSource,
    pub reason: DegradeReason,
    pub elapsed: Duration,
}

/// An evidence source either produced candidates, or degraded — never a hard
/// error the coordinator propagates. `review --diff` is a separate,
/// non-`Outcome` path (Task 1).
pub enum Outcome<T> {
    Ready(T),
    Degraded(Degraded),
}
