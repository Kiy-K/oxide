//! TreeRouter (SPEC § TreeRouter): from entry points over TreeIndex to a
//! bounded structural candidate universe, with a trace of every visit,
//! prune, deferral and limit. Routing is not inclusion: a routed candidate
//! is evidence for later selection, never context by itself.
//!
//! Baseline algorithm (`ROUTER_VERSION`): level-synchronous breadth-first
//! exploration. Layer 0 is the entry points in CandidateSet order, or the
//! root region when there are none (root fallback). Each explored region
//! expands to its physical children, its containing scope (never the
//! repository root) and the ends of the policy's cross-edge kinds, in that
//! order, each in stable domain order. A visited set makes cycles
//! terminate. Before exploring a non-entry region the router may ask a
//! DecisionProvider for its branch value; Rust prunes only on a validated
//! judgment below the policy threshold, so missing, invalid, uncertain or
//! over-allowance judgments (heuristic fallback) never change the route.

use std::collections::{BTreeMap, BTreeSet};

use crate::decision::{
    Allowance, Capsule, Decision, DecisionPolicy, DecisionProvider, Subject, decide,
};
use crate::id::{EntityId, SnapshotKey};
use crate::knowledge::{Containment, RelationKind};
use crate::query::Query;
use crate::retrieve::{ChannelEvidence, EntryPoint, Seed};
use crate::store::{Direction, ReadView, StoreError};
use crate::tree::{NavLimits, Region, RegionId, region};

pub const ROUTER_VERSION: &str = "oxide-router-bfs-v1";

/// Declared traversal limits. Rust checks every step against them; a judge
/// cannot raise them.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct RouteLimits {
    /// Regions loaded (explored or pruned).
    pub max_regions: usize,
    pub max_depth: usize,
    /// Neighbors one region may hand to the next layer.
    pub max_fanout: usize,
    /// Hierarchy and cross-edges examined after each region's fanout cut,
    /// summed over loaded regions. Checked before each load, so the last
    /// region may overshoot by up to two fanouts; store work per region is
    /// bounded separately by `MAX_REQUEST_ITEMS`.
    pub max_edges: usize,
    /// Branch judgments this route may request; it also draws on the
    /// request's shared [`Allowance`].
    pub max_judgments: usize,
}

/// Rust-owned expansion rules.
#[derive(Debug, Clone, PartialEq)]
pub struct RoutePolicy {
    /// Cross-edge kinds followed, in both directions.
    pub follow: Vec<RelationKind>,
    /// Also expand to the containing scope (symbol → class/file).
    pub ascend: bool,
    /// A validated branch value below this prunes the region.
    pub prune_below: f64,
}

/// The frozen v2 baseline limits (docs/phase-3.md).
pub const BASELINE_LIMITS: RouteLimits = RouteLimits {
    max_regions: 64,
    max_depth: 2,
    max_fanout: 16,
    max_edges: 2048,
    max_judgments: 64,
};

/// The frozen v2 baseline policy. Logical containment is followed so an
/// import of a module reaches its file.
pub fn baseline_policy() -> RoutePolicy {
    RoutePolicy {
        follow: vec![
            RelationKind::Calls,
            RelationKind::References,
            RelationKind::Imports,
            RelationKind::Implements,
            RelationKind::TestedBy,
            RelationKind::Contains(Containment::Logical),
        ],
        ascend: true,
        prune_below: 0.25,
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct RouteRequest {
    /// The generation every ID in the request and its result is scoped to.
    pub snapshot: SnapshotKey,
    pub query: Query,
    /// Empty means routing starts from the root region (root fallback).
    pub entry_points: Vec<EntryPoint>,
    pub limits: RouteLimits,
    pub policy: RoutePolicy,
}

/// The limit that stopped or cut exploration.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum Bound {
    Regions,
    Depth,
    Fanout,
    Edges,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Visit {
    Explored,
    /// Rust policy judged the branch not worth exploring.
    Pruned,
    /// Reached but not explored because a bound fired first.
    Deferred(Bound),
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RouteStep {
    pub region: RegionId,
    pub depth: u32,
    pub visit: Visit,
}

/// How a region was reached. Routed-only candidates carry no invented
/// channel score.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Origin {
    EntryPoint,
    RootFallback,
    /// A physical child of this explored region.
    RegionMember(RegionId),
    /// The containing scope of this explored region.
    Scope(RegionId),
    /// A cross-edge of `via`; `direction` is the edge's direction seen from
    /// `via` (outgoing: `via` calls/imports/... this one).
    Neighbor {
        via: EntityId,
        relation: RelationKind,
        direction: Direction,
    },
}

#[derive(Debug, Clone, PartialEq)]
pub struct Candidate {
    pub entity: EntityId,
    /// Layer it was explored in.
    pub depth: u32,
    /// Every way it was reached, first one first.
    pub origins: Vec<Origin>,
    /// Entry-point evidence only; empty for routed-only candidates.
    pub channels: Vec<ChannelEvidence>,
    pub seeds: Vec<Seed>,
}

/// What routing cost.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct RouteWork {
    pub regions_loaded: usize,
    pub edges_examined: usize,
    /// Neighbors handed to a next layer.
    pub edges_followed: usize,
    pub judgments_requested: usize,
    /// Neighbors already visited (cycles, shared targets).
    pub revisits: usize,
    pub max_depth_reached: u32,
    /// Largest neighbor list one region produced, before the fanout cut.
    pub max_fanout_seen: usize,
}

#[derive(Debug, Clone, PartialEq)]
pub struct RoutingTrace {
    pub router: &'static str,
    pub root_fallback: bool,
    /// Explored, pruned and deferred regions in processing order.
    pub steps: Vec<RouteStep>,
    pub decisions: Vec<Decision>,
    /// Regions whose neighbor lists a bound cut.
    pub truncations: Vec<(RegionId, Bound)>,
    /// The bound that ended exploration; `None` when the frontier ran out.
    pub stopped_by: Option<Bound>,
    pub work: RouteWork,
}

/// Router output: the routed candidate universe in domain order, scoped to
/// `snapshot`.
#[derive(Debug, Clone, PartialEq)]
pub struct RouteResult {
    pub snapshot: SnapshotKey,
    pub candidates: Vec<Candidate>,
    pub trace: RoutingTrace,
}

/// Routes one request over `view`. With no provider no branch judgment is
/// requested (the heuristic would only return its neutral prior).
pub fn route(
    view: &impl ReadView,
    request: &RouteRequest,
    mut provider: Option<&mut (dyn DecisionProvider + '_)>,
    allowance: &mut Allowance,
    decision_policy: DecisionPolicy,
) -> Result<RouteResult, StoreError> {
    if view.snapshot().key != request.snapshot {
        return Err(StoreError::Unsupported(
            "route request and view pin different generations".into(),
        ));
    }
    let limits = request.limits;
    let nav = NavLimits {
        children: limits.max_fanout,
        cross_edges: limits.max_fanout,
    };
    let mut trace = RoutingTrace {
        router: ROUTER_VERSION,
        root_fallback: request.entry_points.is_empty(),
        steps: Vec::new(),
        decisions: Vec::new(),
        truncations: Vec::new(),
        stopped_by: None,
        work: RouteWork::default(),
    };
    // First occurrence wins for duplicate entry points.
    let mut entries: BTreeMap<&EntityId, &EntryPoint> = BTreeMap::new();
    for entry in &request.entry_points {
        entries.entry(&entry.entity).or_insert(entry);
    }
    let mut seen: BTreeSet<RegionId> = BTreeSet::new();
    let mut layer: Vec<(RegionId, Origin)> = if trace.root_fallback {
        vec![(RegionId::root(), Origin::RootFallback)]
    } else {
        let ids = request.entry_points.iter().map(|e| e.entity.clone());
        ids.map(|id| (RegionId::of(id), Origin::EntryPoint))
            .collect()
    };
    layer.retain(|(id, _)| seen.insert(id.clone()));
    // Every way each seen region was reached, attached to the candidates
    // once routing ends, so the record does not depend on visit order.
    let mut reached: BTreeMap<RegionId, Vec<Origin>> = layer
        .iter()
        .map(|(id, origin)| (id.clone(), vec![origin.clone()]))
        .collect();

    let mut candidates: BTreeMap<EntityId, Candidate> = BTreeMap::new();
    let mut depth = 0u32;
    while !layer.is_empty() {
        let mut loaded: Vec<(Region, Origin)> = Vec::new();
        for (id, origin) in layer {
            let bound = if trace.work.regions_loaded >= limits.max_regions {
                Some(Bound::Regions)
            } else if trace.work.edges_examined >= limits.max_edges {
                Some(Bound::Edges)
            } else {
                None
            };
            if let Some(bound) = bound {
                trace.stopped_by.get_or_insert(bound);
                trace.steps.push(RouteStep {
                    region: id,
                    depth,
                    visit: Visit::Deferred(bound),
                });
                continue;
            }
            let region = region(view, &id, nav)?;
            trace.work.regions_loaded += 1;
            trace.work.edges_examined += region.children.len() + region.cross_edges.len();
            loaded.push((region, origin));
        }

        let cap = limits.max_judgments - trace.work.judgments_requested;
        let (decisions, sent) = judge(
            &request.snapshot,
            &request.query,
            &loaded,
            provider.as_deref_mut(),
            allowance,
            cap,
            decision_policy,
        );
        trace.work.judgments_requested += sent;
        let mut values: BTreeMap<RegionId, f64> = BTreeMap::new();
        for decision in decisions {
            if let (Subject::Region(id), None) = (&decision.subject, decision.fallback) {
                values.insert(id.clone(), decision.value);
            }
            trace.decisions.push(decision);
        }

        let mut next: Vec<(RegionId, Origin)> = Vec::new();
        for (region, _) in loaded {
            let pruned = values
                .get(&region.id)
                .is_some_and(|v| *v < request.policy.prune_below);
            trace.steps.push(RouteStep {
                region: region.id.clone(),
                depth,
                visit: if pruned {
                    Visit::Pruned
                } else {
                    Visit::Explored
                },
            });
            if pruned {
                continue;
            }
            trace.work.max_depth_reached = depth;
            let anchor = &region.id.anchor;
            let entry = entries.get(anchor);
            candidates.insert(
                anchor.clone(),
                Candidate {
                    entity: anchor.clone(),
                    depth,
                    origins: Vec::new(),
                    channels: entry.map_or_else(Vec::new, |e| e.channels.clone()),
                    seeds: entry.map_or_else(Vec::new, |e| e.seeds.clone()),
                },
            );

            let mut neighbors = expand(&region, &request.policy);
            trace.work.max_fanout_seen = trace.work.max_fanout_seen.max(neighbors.len());
            if neighbors.len() > limits.max_fanout
                || region.children_truncated
                || region.cross_edges_truncated
            {
                neighbors.truncate(limits.max_fanout);
                trace.truncations.push((region.id.clone(), Bound::Fanout));
            }
            for (id, origin) in neighbors {
                let origins = reached.entry(id.clone()).or_default();
                if !origins.contains(&origin) {
                    origins.push(origin.clone());
                }
                if seen.contains(&id) {
                    trace.work.revisits += 1;
                    continue;
                }
                seen.insert(id.clone());
                if depth as usize >= limits.max_depth {
                    trace.stopped_by.get_or_insert(Bound::Depth);
                    trace.steps.push(RouteStep {
                        region: id,
                        depth: depth + 1,
                        visit: Visit::Deferred(Bound::Depth),
                    });
                    continue;
                }
                trace.work.edges_followed += 1;
                next.push((id, origin));
            }
        }
        layer = next;
        depth += 1;
    }

    for candidate in candidates.values_mut() {
        let id = RegionId::of(candidate.entity.clone());
        candidate.origins = reached.get(&id).cloned().unwrap_or_default();
    }
    Ok(RouteResult {
        snapshot: request.snapshot.clone(),
        candidates: candidates.into_values().collect(),
        trace,
    })
}

/// Neighbors of one explored region in expansion order, deduplicated.
fn expand(region: &Region, policy: &RoutePolicy) -> Vec<(RegionId, Origin)> {
    let anchor = &region.id.anchor;
    let mut out: Vec<(RegionId, Origin)> = region
        .children
        .iter()
        .map(|child| (child.clone(), Origin::RegionMember(region.id.clone())))
        .collect();
    if policy.ascend
        && let Some(parent) = &region.parent
        && parent.anchor != EntityId::Repository
    {
        out.push((parent.clone(), Origin::Scope(region.id.clone())));
    }
    for edge in &region.cross_edges {
        if !policy.follow.contains(&edge.kind) {
            continue;
        }
        let (direction, ends): (Direction, Vec<EntityId>) = if edge.from == *anchor {
            (Direction::Outgoing, edge.to.entities().to_vec())
        } else {
            (Direction::Incoming, vec![edge.from.clone()])
        };
        for end in ends {
            let origin = Origin::Neighbor {
                via: anchor.clone(),
                relation: edge.kind,
                direction,
            };
            out.push((RegionId::of(end), origin));
        }
    }
    let mut unique = BTreeSet::new();
    out.retain(|(id, _)| id.anchor != *anchor && unique.insert(id.clone()));
    out
}

/// Branch judgments for the non-entry regions of one layer, in one batch,
/// within `cap` and the shared allowance. Returns the decisions and how
/// many capsules were sent.
fn judge(
    snapshot: &SnapshotKey,
    query: &Query,
    loaded: &[(Region, Origin)],
    provider: Option<&mut (dyn DecisionProvider + '_)>,
    allowance: &mut Allowance,
    cap: usize,
    policy: DecisionPolicy,
) -> (Vec<Decision>, usize) {
    let Some(provider) = provider else {
        return (Vec::new(), 0);
    };
    let capsules: Vec<Capsule> = loaded
        .iter()
        .filter(|(_, origin)| !matches!(origin, Origin::EntryPoint | Origin::RootFallback))
        .map(|(region, _)| Capsule::branch(snapshot, query, region))
        .collect();
    if capsules.is_empty() {
        return (Vec::new(), 0);
    }
    let mut local = Allowance {
        remaining: allowance.remaining.min(cap),
    };
    let before = local.remaining;
    let decisions = decide(Some(provider), &capsules, &mut local, policy);
    let sent = before - local.remaining;
    allowance.remaining -= sent;
    (decisions, sent)
}
