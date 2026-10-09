//! Deterministic inclusion and bounded structural expansion (SPEC §
//! Selection): routed candidates plus their judgments become a
//! [`SelectionPlan`]. Judgments set utility; Rust owns the floor, the
//! budget, prerequisites, expansion bounds, tie-breaks and fallback.
//!
//! Baseline `oxide-select-greedy-v1`, one greedy loop over one pool:
//!
//! 1. Every graph node is a primary candidate. Repositories, modules and
//!    files are containers (their members compete on their own), entities
//!    without source cannot be shown, and a value below `include_floor` is
//!    excluded; each gets a recorded reason.
//! 2. Repeatedly take the best pending candidate: highest value, then
//!    primaries before expansions, then pool order (graph order for
//!    primaries, discovery order for expansions). Its group is the item
//!    plus a header view of every enclosing symbol not already shown (the
//!    declared prerequisite). Its cost is the exact `oxide-units-v1` count
//!    of the group rendered alone. It is selected in full if the group fits
//!    the remaining budget, else reduced to its header view if that fits,
//!    else omitted (`TooLarge` when even the smallest form exceeds the whole
//!    budget). Selection stops adding at `max_items`.
//! 3. A selected item below `max_depth` expansion steps is expanded: its
//!    followed relations (kind and direction) are read in stable order, up
//!    to `max_fanout` new symbol neighbors per seed and `max_nodes`
//!    overall. Each neighbor is judged (`NeighborValue`, heuristic by
//!    default) and, at or above `expand_floor`, joins the same pool, so
//!    expansions compete for the same budget. A neighbor already selected,
//!    pending or considered is a duplicate (cycles end here); ambiguous and
//!    unresolved targets are counted, never guessed. A routed candidate
//!    excluded below the include floor may be re-reached this way; it is
//!    then judged as a neighbor and keeps only the later outcome.
//!
//! A view inside an already planned range (a method after its class body, a
//! class header already shown as a prerequisite) costs only its name in the
//! containing item's label, where the packer lists it. Other parts are charged
//! as rendered alone; the packer merges overlaps and recounts exactly, so
//! it can only use fewer units than the plan estimates.

use std::collections::{BTreeMap, BTreeSet};

use crate::capsule::{Capsule, Evidence, Question, Subject};
use crate::context::{CandidateGraph, coverage, snippet};
use crate::decision::{
    Allowance, Decision, DecisionPolicy, DecisionProvider, Fallback, ProviderIdentity, decide,
};
use crate::id::{EntityId, RepoPath, SnapshotKey, SymbolId};
use crate::knowledge::{ByteRange, Entity, RelationKind, SourceRef, Target};
use crate::pack::{View, count, declared_name, display, render, view_range};
use crate::query::{ContextBudget, Query};
use crate::route::Origin;
use crate::source::{SourceIssue, Sources};
use crate::store::{AdjacencyRequest, Direction, ReadView, StoreError};

pub const SELECTOR_VERSION: &str = "oxide-select-greedy-v1";

#[derive(Debug, Clone, PartialEq)]
pub struct ExpansionLimits {
    /// Expansion steps from a primary; 0 disables expansion.
    pub max_depth: u32,
    /// New neighbors one seed may add.
    pub max_fanout: usize,
    /// Neighbors considered in total.
    pub max_nodes: usize,
    /// Edges read per seed (store work bound).
    pub max_edges_per_seed: usize,
    /// Relations followed, as seen from the seed.
    pub follow: Vec<(RelationKind, Direction)>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct SelectPolicy {
    pub include_floor: f64,
    pub expand_floor: f64,
    pub max_items: usize,
    pub expansion: ExpansionLimits,
}

/// The frozen Phase 4 baseline policy.
pub fn baseline_policy() -> SelectPolicy {
    SelectPolicy {
        include_floor: 0.4,
        expand_floor: 0.4,
        max_items: 24,
        expansion: ExpansionLimits {
            max_depth: 1,
            max_fanout: 4,
            max_nodes: 16,
            max_edges_per_seed: 64,
            follow: vec![
                (RelationKind::Calls, Direction::Outgoing),
                (RelationKind::Calls, Direction::Incoming),
                (RelationKind::References, Direction::Outgoing),
                (RelationKind::Implements, Direction::Outgoing),
                (RelationKind::Implements, Direction::Incoming),
                (RelationKind::TestedBy, Direction::Outgoing),
            ],
        },
    }
}

/// Why an entity is in the plan.
#[derive(Debug, Clone, PartialEq)]
pub enum Reason {
    /// A routed candidate chosen on its relevance judgment.
    Primary {
        value: f64,
        provider: ProviderIdentity,
        fallback: Option<Fallback>,
        /// It was a retrieval entry point (channel or seed evidence).
        entry: bool,
        /// Routing depth.
        depth: u32,
    },
    /// Header of an enclosing declaration required by `of`.
    Prerequisite { of: EntityId },
    /// Reached from a selected `seed` over `relation`, judged on its own.
    Expanded {
        seed: EntityId,
        relation: RelationKind,
        direction: Direction,
        depth: u32,
        value: f64,
        provider: ProviderIdentity,
        fallback: Option<Fallback>,
    },
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum Stage {
    Selection,
    Expansion,
    Packing,
}

#[derive(Debug, Clone, PartialEq)]
pub enum OmitReason {
    /// Repository, module or file: its members compete instead.
    Container,
    /// The entity has no source range.
    NoSource,
    BelowFloor {
        value: f64,
    },
    /// `max_items` was reached.
    MaxItems,
    BudgetExceeded {
        needed: u32,
        remaining: u32,
    },
    /// Even its smallest view exceeds the whole budget.
    TooLarge {
        needed: u32,
    },
    SourceUnavailable,
    SourceMismatch,
    NotText,
    /// An enclosing declaration's header could not be shown.
    PrerequisiteUnavailable {
        prerequisite: EntityId,
    },
}

#[derive(Debug, Clone, PartialEq)]
pub struct Omission {
    pub entity: EntityId,
    pub stage: Stage,
    pub reason: OmitReason,
}

/// An enclosing declaration shown as a header before its member.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Prerequisite {
    pub entity: EntityId,
    pub source: SourceRef,
}

#[derive(Debug, Clone, PartialEq)]
pub struct PlannedItem {
    pub entity: EntityId,
    pub source: SourceRef,
    pub view: View,
    /// The budget forced a header view.
    pub reduced: bool,
    pub reason: Reason,
    /// Headers to show first; already-shown ones are left out.
    pub prerequisites: Vec<Prerequisite>,
    /// Standalone cost of the group, prerequisites included.
    pub estimated_tokens: u32,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum ExpansionBound {
    /// A seed had more new neighbors than `max_fanout`, or more edges than
    /// `max_edges_per_seed`.
    Fanout,
    /// `max_nodes` neighbors were considered.
    Nodes,
}

#[derive(Debug, Clone, Default, PartialEq)]
pub struct ExpansionTrace {
    pub seeds: usize,
    pub edges_examined: usize,
    pub considered: usize,
    /// Neighbors already selected, pending or considered.
    pub duplicates: usize,
    pub ambiguous_skipped: usize,
    pub unresolved_skipped: usize,
    pub truncations: Vec<(EntityId, ExpansionBound)>,
    pub stopped_by: Option<ExpansionBound>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct SelectionPlan {
    pub snapshot: SnapshotKey,
    pub selector: &'static str,
    pub budget: ContextBudget,
    /// In selection order.
    pub items: Vec<PlannedItem>,
    pub excluded: Vec<Omission>,
    /// Expansion neighbors' capsules and decisions, for replay.
    pub capsules: Vec<Capsule>,
    pub decisions: Vec<Decision>,
    pub expansion: ExpansionTrace,
    pub estimated_tokens: u32,
}

struct Pending {
    entity: Entity,
    value: f64,
    /// 0 primary, 1 expansion: primaries win ties.
    class: u8,
    seq: usize,
    reason: Reason,
    expansion_depth: u32,
}

/// Inputs selection reads but does not own.
pub struct SelectInput<'a, V: ReadView> {
    pub view: &'a V,
    pub query: &'a Query,
    pub graph: &'a CandidateGraph,
    /// One `Relevance` decision per graph node, in graph order.
    pub relevance: &'a [Decision],
    pub budget: &'a ContextBudget,
    pub policy: &'a SelectPolicy,
    pub decision_policy: DecisionPolicy,
}

pub fn select<V: ReadView>(
    input: SelectInput<'_, V>,
    sources: &mut Sources<'_>,
    mut provider: Option<&mut (dyn DecisionProvider + '_)>,
    allowance: &mut Allowance,
) -> Result<SelectionPlan, StoreError> {
    let SelectInput {
        view,
        query,
        graph,
        relevance,
        budget,
        policy,
        decision_policy,
    } = input;
    assert_eq!(graph.nodes.len(), relevance.len(), "one decision per node");
    let mut plan = SelectionPlan {
        snapshot: graph.snapshot.clone(),
        selector: SELECTOR_VERSION,
        budget: budget.clone(),
        items: Vec::new(),
        excluded: Vec::new(),
        capsules: Vec::new(),
        decisions: Vec::new(),
        expansion: ExpansionTrace::default(),
        estimated_tokens: 0,
    };
    let exclude = |plan: &mut SelectionPlan, entity: &EntityId, stage, reason| {
        plan.excluded.push(Omission {
            entity: entity.clone(),
            stage,
            reason,
        })
    };

    let mut pool: Vec<Pending> = Vec::new();
    // Entities pending, selected or already considered by expansion.
    let mut known: BTreeSet<EntityId> = BTreeSet::new();
    for (seq, (node, decision)) in graph.nodes.iter().zip(relevance).enumerate() {
        let id = &node.entity.id;
        let reason = if !matches!(id, EntityId::Symbol(_)) {
            Some(OmitReason::Container)
        } else if node.entity.source.is_none() {
            Some(OmitReason::NoSource)
        } else if decision.value < policy.include_floor {
            Some(OmitReason::BelowFloor {
                value: decision.value,
            })
        } else {
            None
        };
        if !matches!(reason, Some(OmitReason::BelowFloor { .. })) {
            known.insert(id.clone());
        }
        if let Some(reason) = reason {
            exclude(&mut plan, id, Stage::Selection, reason);
            continue;
        }
        let entry = !node.candidate.channels.is_empty() || !node.candidate.seeds.is_empty();
        pool.push(Pending {
            entity: node.entity.clone(),
            value: decision.value,
            class: 0,
            seq,
            reason: Reason::Primary {
                value: decision.value,
                provider: decision.provider.clone(),
                fallback: decision.fallback,
                entry,
                depth: node.candidate.depth,
            },
            expansion_depth: 0,
        });
    }

    let mut shown: BTreeSet<EntityId> = BTreeSet::new();
    // Byte ranges already planned, per file: a view inside one costs nothing.
    let mut planned: Vec<(RepoPath, ByteRange)> = Vec::new();
    let mut ancestors: BTreeMap<EntityId, Option<Entity>> = BTreeMap::new();
    let mut seq = graph.nodes.len();
    let mut used = 0u32;

    while !pool.is_empty() {
        let best = (0..pool.len())
            .max_by(|&a, &b| {
                let (a, b) = (&pool[a], &pool[b]);
                a.value
                    .total_cmp(&b.value)
                    .then(b.class.cmp(&a.class))
                    .then(b.seq.cmp(&a.seq))
            })
            .expect("non-empty");
        let candidate = pool.remove(best);
        let id = candidate.entity.id.clone();
        let stage = match candidate.class {
            0 => Stage::Selection,
            _ => Stage::Expansion,
        };
        if plan.items.len() >= policy.max_items {
            exclude(&mut plan, &id, stage, OmitReason::MaxItems);
            continue;
        }
        let source = candidate.entity.source.clone().expect("checked on entry");

        // Prerequisites: enclosing symbols not yet shown, outermost first.
        let mut prerequisites = Vec::new();
        let mut missing = None;
        for ancestor in enclosing(&id) {
            if shown.contains(&ancestor) {
                continue;
            }
            if !ancestors.contains_key(&ancestor) {
                let found = view
                    .entities(std::slice::from_ref(&ancestor))?
                    .pop()
                    .flatten();
                ancestors.insert(ancestor.clone(), found);
            }
            match ancestors[&ancestor].as_ref().and_then(|e| e.source.clone()) {
                Some(source) => prerequisites.push(Prerequisite {
                    entity: ancestor,
                    source,
                }),
                None => {
                    missing = Some(ancestor);
                    break;
                }
            }
        }
        if let Some(prerequisite) = missing {
            let reason = OmitReason::PrerequisiteUnavailable { prerequisite };
            exclude(&mut plan, &id, stage, reason);
            continue;
        }

        let remaining = budget.tokens - used;
        let mut chosen = None;
        let mut smallest = None;
        let mut failure = None;
        for view_kind in [View::Full, View::Header] {
            match cost(sources, &id, &source, view_kind, &prerequisites, &planned)? {
                Ok(Some((c, ranges))) => {
                    smallest = Some(c);
                    if c <= remaining {
                        chosen = Some((view_kind, c, ranges));
                        break;
                    }
                }
                Ok(None) => {}
                Err(reason) => {
                    failure = Some(reason);
                    break;
                }
            }
        }
        let Some((view_kind, cost, ranges)) = chosen else {
            let reason = failure.unwrap_or_else(|| {
                let needed = smallest.unwrap_or(0);
                if needed > budget.tokens {
                    OmitReason::TooLarge { needed }
                } else {
                    OmitReason::BudgetExceeded { needed, remaining }
                }
            });
            exclude(&mut plan, &id, stage, reason);
            continue;
        };
        used += cost;
        planned.extend(ranges);
        shown.extend(prerequisites.iter().map(|p| p.entity.clone()));
        shown.insert(id.clone());
        plan.items.push(PlannedItem {
            entity: id.clone(),
            source,
            view: view_kind,
            reduced: view_kind != View::Full,
            reason: candidate.reason,
            prerequisites,
            estimated_tokens: cost,
        });

        if candidate.expansion_depth >= policy.expansion.max_depth {
            continue;
        }
        let found = expand(
            view,
            &id,
            &policy.expansion,
            &mut known,
            &mut plan.expansion,
        )?;
        if found.is_empty() {
            continue;
        }
        let ids: Vec<EntityId> = found.iter().map(|(e, _, _)| e.clone()).collect();
        let mut neighbors = Vec::new();
        let mut capsules = Vec::new();
        for ((entity, relation, direction), found) in found.iter().zip(view.entities(&ids)?) {
            let Some(found) = found else {
                return Err(StoreError::MissingEntity(entity.clone()));
            };
            let origin = Origin::Neighbor {
                via: id.clone(),
                relation: *relation,
                direction: *direction,
            };
            let snippet = snippet(sources, &found)?;
            let coverage = coverage(view, &found.id);
            capsules.push(Capsule::new(
                &plan.snapshot,
                query,
                Subject::Candidate(found.id.clone()),
                Question::NeighborValue,
                Evidence {
                    entity: &found,
                    coverage: coverage.as_ref(),
                    snippet: &snippet,
                    channels: &[],
                    seeds: &[],
                    depth: None,
                    origins: std::slice::from_ref(&origin),
                    relations: &[],
                    relations_truncated: true,
                },
            ));
            neighbors.push((found, *relation, *direction));
        }
        let decisions = decide(
            provider.as_deref_mut(),
            &capsules,
            allowance,
            decision_policy,
        );
        for ((entity, relation, direction), decision) in neighbors.into_iter().zip(&decisions) {
            if entity.source.is_none() {
                exclude(
                    &mut plan,
                    &entity.id,
                    Stage::Expansion,
                    OmitReason::NoSource,
                );
            } else if decision.value < policy.expand_floor {
                let reason = OmitReason::BelowFloor {
                    value: decision.value,
                };
                exclude(&mut plan, &entity.id, Stage::Expansion, reason);
            } else {
                pool.push(Pending {
                    reason: Reason::Expanded {
                        seed: id.clone(),
                        relation,
                        direction,
                        depth: candidate.expansion_depth + 1,
                        value: decision.value,
                        provider: decision.provider.clone(),
                        fallback: decision.fallback,
                    },
                    entity,
                    value: decision.value,
                    class: 1,
                    seq,
                    expansion_depth: candidate.expansion_depth + 1,
                });
                seq += 1;
            }
        }
        plan.capsules.extend(capsules);
        plan.decisions.extend(decisions);
    }
    // A routed candidate excluded below the floor and later selected as an
    // expansion keeps only its inclusion.
    let selected: BTreeSet<EntityId> = plan.items.iter().map(|i| i.entity.clone()).collect();
    plan.excluded.retain(|o| !selected.contains(&o.entity));
    plan.estimated_tokens = used;
    Ok(plan)
}

/// Enclosing symbols of `id`, outermost first.
fn enclosing(id: &EntityId) -> Vec<EntityId> {
    let EntityId::Symbol(symbol) = id else {
        return Vec::new();
    };
    let path = symbol.path();
    (1..path.len())
        .map(|n| {
            let prefix = path[..n].to_vec();
            EntityId::Symbol(SymbolId::new(symbol.file().clone(), prefix).expect("non-empty"))
        })
        .collect()
}

/// Group cost and the ranges it shows, `Ok(None)` when `view` is a header
/// that is no reduction. A part inside an already planned range of the same
/// file is evidence already paid for and costs nothing; any other part is
/// charged as if rendered alone.
#[allow(clippy::type_complexity)]
fn cost(
    sources: &mut Sources<'_>,
    id: &EntityId,
    source: &SourceRef,
    view: View,
    prerequisites: &[Prerequisite],
    planned: &[(RepoPath, ByteRange)],
) -> Result<Result<Option<(u32, Vec<(RepoPath, ByteRange)>)>, OmitReason>, StoreError> {
    let mut total = 0;
    let mut ranges = Vec::new();
    let parts = prerequisites
        .iter()
        .map(|p| (&p.entity, &p.source, View::Header))
        .chain([(id, source, view)]);
    for (entity, source, view) in parts {
        let bytes = match sources.file(&source.file, &source.digest)? {
            Ok(bytes) => bytes,
            Err(SourceIssue::Unavailable) => return Ok(Err(OmitReason::SourceUnavailable)),
            Err(SourceIssue::Mismatch) => return Ok(Err(OmitReason::SourceMismatch)),
        };
        if source.range.end as usize > bytes.len() || source.range.start > source.range.end {
            return Ok(Err(OmitReason::SourceMismatch));
        }
        let range = match view_range(&bytes, source.range, view, declared_name(entity)) {
            Some(range) => range,
            None if entity == id => return Ok(Ok(None)),
            None => source.range,
        };
        ranges.push((source.file.clone(), range));
        let inside = |(file, r): &(RepoPath, ByteRange)| {
            *file == source.file && r.start <= range.start && range.end <= r.end
        };
        let label = if range == source.range {
            display(entity)
        } else {
            format!("{} (header)", display(entity))
        };
        if planned.iter().any(inside) {
            // Already paid for, except its name in the merged item's label.
            total += count(&format!(", {label}"));
            continue;
        }
        let Some(text) = render(&source.file, &bytes, range, &[label]) else {
            return Ok(Err(OmitReason::NotText));
        };
        total += count(&text);
    }
    Ok(Ok(Some((total, ranges))))
}

/// New neighbors of a selected seed under the expansion limits, in stable
/// edge order: (entity, relation, direction from the seed).
fn expand(
    view: &impl ReadView,
    seed: &EntityId,
    limits: &ExpansionLimits,
    known: &mut BTreeSet<EntityId>,
    trace: &mut ExpansionTrace,
) -> Result<Vec<(EntityId, RelationKind, Direction)>, StoreError> {
    if trace.considered >= limits.max_nodes {
        trace.stopped_by.get_or_insert(ExpansionBound::Nodes);
        return Ok(Vec::new());
    }
    trace.seeds += 1;
    let mut kinds: Vec<RelationKind> = limits.follow.iter().map(|(k, _)| *k).collect();
    kinds.sort();
    kinds.dedup();
    let adjacency = view.adjacency(&AdjacencyRequest {
        entity: seed.clone(),
        direction: Direction::Both,
        kinds,
        limit: limits.max_edges_per_seed,
    })?;
    trace.edges_examined += adjacency.edges.len();
    let mut cut = adjacency.truncated;
    let mut out = Vec::new();
    'edges: for edge in &adjacency.edges {
        let direction = if edge.from == *seed {
            Direction::Outgoing
        } else {
            Direction::Incoming
        };
        if !limits.follow.contains(&(edge.kind, direction)) {
            continue;
        }
        let end = match (direction, &edge.to) {
            (Direction::Incoming, _) => edge.from.clone(),
            (_, Target::Resolved(id)) => id.clone(),
            (_, Target::Ambiguous(_)) => {
                trace.ambiguous_skipped += 1;
                continue;
            }
            (_, Target::Unresolved { .. }) => {
                trace.unresolved_skipped += 1;
                continue;
            }
        };
        if end == *seed || !matches!(end, EntityId::Symbol(_)) {
            continue;
        }
        if known.contains(&end) {
            trace.duplicates += 1;
            continue;
        }
        if out.len() >= limits.max_fanout {
            cut = true;
            break 'edges;
        }
        if trace.considered >= limits.max_nodes {
            trace.stopped_by.get_or_insert(ExpansionBound::Nodes);
            break 'edges;
        }
        known.insert(end.clone());
        trace.considered += 1;
        out.push((end, edge.kind, direction));
    }
    if cut {
        trace
            .truncations
            .push((seed.clone(), ExpansionBound::Fanout));
        trace.stopped_by.get_or_insert(ExpansionBound::Fanout);
    }
    Ok(out)
}
