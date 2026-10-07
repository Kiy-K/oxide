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
//! ([`decision`]).
#![forbid(unsafe_code)]

pub mod decision;
pub mod id;
pub mod knowledge;
pub mod query;
pub mod route;
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
