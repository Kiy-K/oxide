//! Optional structural, Git, and blast-radius evidence collection.

pub mod contract;
pub mod coordinator;
pub mod scope;

pub use contract::{DegradeReason, Degraded, EvidenceCandidate, EvidenceSource, Outcome};
pub use coordinator::EvidenceCoordinator;
