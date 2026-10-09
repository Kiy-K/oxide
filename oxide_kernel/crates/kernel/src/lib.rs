//! OXIDE v2 kernel: repository intelligence and deterministic policy.
//!
//! A pure library. It links no crates and performs no I/O, so database,
//! transport, protocol and integration types cannot reach its API
//! (`tests/boundary.rs` enforces both). The runtime calls it and maps its
//! types onto the service contract.
//!
//! Phase 1 (docs/BOOTSTRAP.md) adds the domain contracts: identity
//! ([`id`]), the knowledge model ([`knowledge`]), task inputs ([`query`]), the
//! KnowledgeStore port and in-memory store ([`store`]), TreeIndex navigation
//! ([`tree`]), TreeRouter contracts ([`route`]) and the DecisionProvider seam
//! ([`decision`]). Phase 2A adds the captured-source input ([`source`]);
//! Phase 2B adds ingestion ([`ingest`]) and its one language slice ([`python`]).
//! Phase 3 adds lexical terms ([`lexical`]), entry-point retrieval
//! ([`retrieve`]) and the TreeRouter baseline in [`route`]. Phase 4 adds
//! capsule v2 ([`capsule`], with [`digest`]), deterministic selection and
//! expansion ([`select`]), the ContextPacker ([`pack`]) and the end-to-end
//! pipeline with its CandidateGraph ([`context`]).
#![forbid(unsafe_code)]

pub mod capsule;
pub mod context;
pub mod decision;
pub mod digest;
pub mod id;
pub mod ingest;
pub mod knowledge;
pub mod lexical;
pub mod pack;
pub mod python;
pub mod query;
pub mod retrieve;
pub mod route;
pub mod select;
pub mod source;
pub mod store;
pub mod tree;

/// Kernel version, reported through the runtime's `status` operation.
pub const VERSION: &str = env!("CARGO_PKG_VERSION");

/// Result of the Phase 0 stub domain operation.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Status {
    pub kernel_version: &'static str,
}

pub fn status() -> Status {
    Status {
        kernel_version: VERSION,
    }
}

#[cfg(test)]
mod tests {
    #[test]
    fn status_reports_the_crate_version() {
        assert_eq!(super::status().kernel_version, env!("CARGO_PKG_VERSION"));
    }
}
