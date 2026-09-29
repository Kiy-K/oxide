//! Optional structural, Git, and blast-radius evidence collection.

pub(crate) mod candidate;
pub mod contract;
pub mod coordinator;
pub mod scope;

pub(crate) use candidate::{Candidate, Channel, Reason};
pub use contract::{DegradeReason, Degraded, EvidenceCandidate, EvidenceSource, Outcome};
pub use coordinator::EvidenceCoordinator;
