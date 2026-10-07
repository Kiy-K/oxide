//! TreeRouter contracts: what routing takes in, the candidates it hands to
//! CandidateGraph, and the trace that explains every visit, prune and
//! stopping bound. The exploration algorithm is Phase 3; these types fix only
//! what any algorithm must report.

use crate::decision::Decision;
use crate::id::{EntityId, SnapshotKey};
use crate::knowledge::RelationKind;
use crate::query::{Query, QueryContext};
use crate::tree::RegionId;

/// Retrieval channel that proposed an entry point.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum Channel {
    Lexical,
    Semantic,
}

/// One channel's evidence, on that channel's own scale. A missing channel is
/// absent from the list, never a score of zero.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct ChannelEvidence {
    pub channel: Channel,
    pub rank: u32,
    pub score: f64,
}

/// A retrieval or hint-derived starting point. Entry points start routing;
/// they are never final context.
#[derive(Debug, Clone, PartialEq)]
pub struct EntryPoint {
    pub entity: EntityId,
    pub channels: Vec<ChannelEvidence>,
    /// Matched an explicit path/symbol/selection hint from the query context.
    pub hinted: bool,
}

/// Declared traversal limits. Rust checks every step against them; a judge
/// cannot raise them.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct RouteLimits {
    pub max_regions: usize,
    pub max_depth: usize,
    pub max_fanout: usize,
    /// Branch judgments this route may request; shared with candidate
    /// judging through [`crate::decision::Allowance`].
    pub max_judgments: usize,
}

#[derive(Debug, Clone, PartialEq)]
pub struct RouteRequest {
    /// The generation every ID in the request and its result is scoped to.
    pub snapshot: SnapshotKey,
    pub query: Query,
    pub context: QueryContext,
    /// Empty means routing starts from the root region (root fallback).
    pub entry_points: Vec<EntryPoint>,
    pub limits: RouteLimits,
}

/// The limit that stopped or cut exploration.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Bound {
    Regions,
    Depth,
    Fanout,
    Judgments,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Visit {
    Explored,
    /// Rust policy judged the branch not worth exploring.
    Pruned,
    /// Not explored because a bound fired first.
    Deferred(Bound),
    /// Already visited through another path (cycle or shared cross-edge).
    Revisit,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RouteStep {
    pub region: RegionId,
    pub depth: u32,
    pub visit: Visit,
}

/// Where a routed candidate came from. Routed-only candidates carry no
/// invented channel score.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Origin {
    EntryPoint,
    RegionMember(RegionId),
    Neighbor {
        seed: EntityId,
        relation: RelationKind,
    },
}

#[derive(Debug, Clone, PartialEq)]
pub struct Candidate {
    pub entity: EntityId,
    pub origins: Vec<Origin>,
    pub channels: Vec<ChannelEvidence>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct RoutingTrace {
    pub root_fallback: bool,
    pub steps: Vec<RouteStep>,
    pub decisions: Vec<Decision>,
    pub stopped_by: Option<Bound>,
}

/// Router output: deduplicated routed candidates in stable domain order.
/// Candidate, region and decision IDs are scoped to `snapshot`.
#[derive(Debug, Clone, PartialEq)]
pub struct RouteResult {
    pub snapshot: SnapshotKey,
    pub candidates: Vec<Candidate>,
    pub trace: RoutingTrace,
}
