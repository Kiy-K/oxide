//! Deterministic context construction (SPEC § Selection pipeline):
//! entry-point retrieval → TreeRouter → [`CandidateGraph`] → capsules →
//! DecisionProvider → [`select`] → [`pack`] → `ContextBundle`. Every stage
//! artifact is returned in a [`ContextRun`] so it can be inspected and
//! replayed: feeding a run's recorded judgments and failures back through
//! [`crate::decision::Replay`] rebuilds the same plan and bundle, fallback
//! reasons included, whatever the original provider would answer today.
//!
//! The router and selection share one judgment [`Allowance`]. With no
//! provider nothing is requested and every decision is the heuristic's.

use crate::capsule::{Capsule, Evidence, Question, Snippet, Subject};
use crate::decision::{
    Allowance, Decision, DecisionPolicy, DecisionProvider, Failure, Fallback, Judgment,
    ProviderError, ProviderIdentity, decide,
};
use crate::digest::digest;
use crate::id::{EntityId, SnapshotKey};
use crate::knowledge::{Coverage, Entity, Relation, RelationKind};
use crate::pack::{ContextBundle, Degradation, PAYLOAD_VERSION, Versions, pack};
use crate::query::{ContextBudget, Query, QueryContext};
use crate::retrieve::{CandidateSet, ChannelState, RETRIEVAL_VERSION, RetrievalConfig, retrieve};
use crate::route::{
    BASELINE_LIMITS, Candidate, ROUTER_VERSION, RouteLimits, RoutePolicy, RouteRequest,
    RouteResult, baseline_policy as route_baseline, route,
};
use crate::select::{SELECTOR_VERSION, SelectInput, SelectPolicy, SelectionPlan, select};
use crate::source::{SourceProvider, Sources};
use crate::store::{AdjacencyRequest, Direction, ReadView, StoreError};

/// Bounds on the query-local graph.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct GraphLimits {
    pub max_nodes: usize,
    /// Relations read per node, both directions, every kind.
    pub max_edges_per_node: usize,
}

pub const BASELINE_GRAPH: GraphLimits = GraphLimits {
    max_nodes: 64,
    max_edges_per_node: 32,
};

#[derive(Debug, Clone, PartialEq)]
pub struct GraphNode {
    pub entity: Entity,
    /// The routed candidate: depth, origins, entry evidence.
    pub candidate: Candidate,
    /// Typed relations in stable domain order, at most `max_edges_per_node`.
    pub edges: Vec<Relation>,
    pub edges_truncated: bool,
}

/// Bounded query-local graph of routed candidates and their relations.
/// Membership is evidence for judgment, never inclusion.
#[derive(Debug, Clone, PartialEq)]
pub struct CandidateGraph {
    pub snapshot: SnapshotKey,
    /// Entry points in CandidateSet order, then routed-only candidates by
    /// depth, then domain order.
    pub nodes: Vec<GraphNode>,
    /// Routed candidates left out by `max_nodes`.
    pub omitted: usize,
    pub limits: GraphLimits,
}

pub fn graph(
    view: &impl ReadView,
    set: &CandidateSet,
    routed: &RouteResult,
    limits: GraphLimits,
) -> Result<CandidateGraph, StoreError> {
    let position = |id: &EntityId| set.entries.iter().position(|e| e.entity == *id);
    let mut candidates: Vec<&Candidate> = routed.candidates.iter().collect();
    candidates.sort_by_key(|c| {
        (
            position(&c.entity).unwrap_or(usize::MAX),
            c.depth,
            &c.entity,
        )
    });
    let omitted = candidates.len().saturating_sub(limits.max_nodes);
    candidates.truncate(limits.max_nodes);
    let ids: Vec<EntityId> = candidates.iter().map(|c| c.entity.clone()).collect();
    let mut nodes = Vec::new();
    for (candidate, entity) in candidates.into_iter().zip(view.entities(&ids)?) {
        let entity = entity.ok_or_else(|| StoreError::MissingEntity(candidate.entity.clone()))?;
        let adjacency = view.adjacency(&AdjacencyRequest {
            entity: entity.id.clone(),
            direction: Direction::Both,
            kinds: RelationKind::ALL.to_vec(),
            limit: limits.max_edges_per_node,
        })?;
        nodes.push(GraphNode {
            entity,
            candidate: candidate.clone(),
            edges: adjacency.edges,
            edges_truncated: adjacency.truncated,
        });
    }
    Ok(CandidateGraph {
        snapshot: routed.snapshot.clone(),
        nodes,
        omitted,
        limits,
    })
}

/// The subject's source as a capsule snippet, hydrated by digest.
pub(crate) fn snippet(sources: &mut Sources<'_>, entity: &Entity) -> Result<Snippet, StoreError> {
    let Some(source) = &entity.source else {
        return Ok(Snippet::Absent);
    };
    let Ok(bytes) = sources.file(&source.file, &source.digest)? else {
        return Ok(Snippet::Unavailable);
    };
    let range = source.range.start as usize..source.range.end as usize;
    Ok(bytes
        .get(range)
        .and_then(|b| std::str::from_utf8(b).ok())
        .map_or(Snippet::Unavailable, |text| Snippet::Text {
            text: text.to_owned(),
            truncated: false,
        }))
}

/// Coverage of the subject's file.
pub(crate) fn coverage(view: &impl ReadView, id: &EntityId) -> Option<Coverage> {
    let file = match id {
        EntityId::File(p) => p,
        EntityId::Symbol(s) => s.file(),
        _ => return None,
    };
    view.snapshot().files.get(file).map(|f| f.coverage.clone())
}

/// Every Rust-owned knob of one context request.
#[derive(Debug, Clone, PartialEq)]
pub struct ContextConfig {
    pub retrieval: RetrievalConfig,
    pub route_limits: RouteLimits,
    pub route_policy: RoutePolicy,
    pub graph: GraphLimits,
    pub select: SelectPolicy,
    pub decision: DecisionPolicy,
    /// Judgments the whole request may request (routing and selection).
    pub judgments: usize,
}

/// The frozen Phase 4 baseline: Phase 3 retrieval and routing unchanged.
pub fn baseline() -> ContextConfig {
    ContextConfig {
        retrieval: crate::retrieve::BASELINE,
        route_limits: BASELINE_LIMITS,
        route_policy: route_baseline(),
        graph: BASELINE_GRAPH,
        select: crate::select::baseline_policy(),
        decision: DecisionPolicy::default(),
        judgments: 256,
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct ContextRequest {
    pub query: Query,
    pub context: QueryContext,
    pub budget: ContextBudget,
    /// The semantic channel's state as the runtime found it.
    pub semantic: ChannelState,
}

/// Every stage artifact of one request.
#[derive(Debug, Clone, PartialEq)]
pub struct ContextRun {
    pub candidates: CandidateSet,
    pub route: RouteResult,
    pub graph: CandidateGraph,
    /// One `Relevance` capsule per graph node, in graph order.
    pub capsules: Vec<Capsule>,
    pub decisions: Vec<Decision>,
    /// The provider used, every judgment it returned (routing, relevance
    /// and expansion) and every failed call, for replay.
    pub provider: Option<ProviderIdentity>,
    pub judgments: Vec<Judgment>,
    pub failures: Vec<Failure>,
    pub plan: SelectionPlan,
    pub bundle: ContextBundle,
}

/// Records what a provider answers, failures included.
struct Recorder<'a, 'b> {
    inner: &'a mut (dyn DecisionProvider + 'b),
    judgments: Vec<Judgment>,
    failures: Vec<Failure>,
}

impl DecisionProvider for Recorder<'_, '_> {
    fn identity(&self) -> ProviderIdentity {
        self.inner.identity()
    }

    fn supports(&self, question: Question) -> bool {
        self.inner.supports(question)
    }

    fn judge(&mut self, capsules: &[Capsule]) -> Result<Vec<Judgment>, ProviderError> {
        match self.inner.judge(capsules) {
            Ok(answer) => {
                self.judgments.extend(answer.iter().cloned());
                Ok(answer)
            }
            Err(error) => {
                self.failures.extend(capsules.iter().map(|c| Failure {
                    subject: c.subject.clone(),
                    question: c.question,
                    capsule_digest: c.digest(),
                    error,
                }));
                Err(error)
            }
        }
    }
}

pub fn build_context(
    view: &impl ReadView,
    source: &dyn SourceProvider,
    request: &ContextRequest,
    config: &ContextConfig,
    provider: Option<&mut (dyn DecisionProvider + '_)>,
) -> Result<ContextRun, StoreError> {
    let identity = provider.as_ref().map(|p| p.identity());
    let mut recorder = provider.map(|inner| Recorder {
        inner,
        judgments: Vec::new(),
        failures: Vec::new(),
    });
    let mut allowance = Allowance {
        remaining: config.judgments,
    };
    let mut sources = Sources::new(source);
    let query = &request.query;

    let candidates = retrieve(
        view,
        query,
        &request.context,
        request.semantic.clone(),
        config.retrieval,
    )?;
    let route_request = RouteRequest {
        snapshot: candidates.snapshot.clone(),
        query: query.clone(),
        entry_points: candidates.entries.clone(),
        limits: config.route_limits,
        policy: config.route_policy.clone(),
    };
    let routed = route(
        view,
        &route_request,
        recorder.as_mut().map(|r| r as &mut dyn DecisionProvider),
        &mut allowance,
        config.decision,
    )?;
    let graph = graph(view, &candidates, &routed, config.graph)?;

    let mut capsules = Vec::new();
    for node in &graph.nodes {
        let snippet = snippet(&mut sources, &node.entity)?;
        let coverage = coverage(view, &node.entity.id);
        capsules.push(Capsule::new(
            &graph.snapshot,
            query,
            Subject::Candidate(node.entity.id.clone()),
            Question::Relevance,
            Evidence {
                entity: &node.entity,
                coverage: coverage.as_ref(),
                snippet: &snippet,
                channels: &node.candidate.channels,
                seeds: &node.candidate.seeds,
                depth: Some(node.candidate.depth),
                origins: &node.candidate.origins,
                relations: &node.edges,
                relations_truncated: node.edges_truncated,
            },
        ));
    }
    let decisions = decide(
        recorder.as_mut().map(|r| r as &mut dyn DecisionProvider),
        &capsules,
        &mut allowance,
        config.decision,
    );
    let plan = select(
        SelectInput {
            view,
            query,
            graph: &graph,
            relevance: &decisions,
            budget: &request.budget,
            policy: &config.select,
            decision_policy: config.decision,
        },
        &mut sources,
        recorder.as_mut().map(|r| r as &mut dyn DecisionProvider),
        &mut allowance,
    )?;
    let versions = Versions {
        retrieval: RETRIEVAL_VERSION,
        router: ROUTER_VERSION,
        capsule: crate::capsule::CAPSULE_VERSION,
        selector: SELECTOR_VERSION,
        payload: PAYLOAD_VERSION,
    };
    let mut bundle = pack(
        &plan,
        &mut sources,
        digest(query.text.as_bytes()),
        versions,
        &request.budget,
    )?;
    bundle.degraded = degraded(&candidates, &routed, &graph, &decisions, &plan);
    Ok(ContextRun {
        candidates,
        route: routed,
        graph,
        capsules,
        decisions,
        provider: identity,
        judgments: recorder
            .as_ref()
            .map(|r| r.judgments.clone())
            .unwrap_or_default(),
        failures: recorder.map(|r| r.failures).unwrap_or_default(),
        plan,
        bundle,
    })
}

fn degraded(
    set: &CandidateSet,
    routed: &RouteResult,
    graph: &CandidateGraph,
    relevance: &[Decision],
    plan: &SelectionPlan,
) -> Vec<Degradation> {
    let mut out: Vec<Degradation> = set
        .channels
        .iter()
        .filter(|c| !matches!(c.state, ChannelState::Ran { .. }))
        .cloned()
        .map(Degradation::Channel)
        .collect();
    out.extend(routed.trace.stopped_by.map(Degradation::Route));
    if graph.omitted > 0 {
        out.push(Degradation::GraphTruncated {
            omitted: graph.omitted,
        });
    }
    let mut fallbacks: Vec<(Fallback, usize)> = Vec::new();
    let all = routed
        .trace
        .decisions
        .iter()
        .chain(relevance)
        .chain(&plan.decisions);
    for reason in all.filter_map(|d| d.fallback) {
        if reason == Fallback::NoProvider {
            continue;
        }
        match fallbacks.iter_mut().find(|(r, _)| *r == reason) {
            Some((_, n)) => *n += 1,
            None => fallbacks.push((reason, 1)),
        }
    }
    out.extend(
        fallbacks
            .into_iter()
            .map(|(reason, count)| Degradation::Fallback { reason, count }),
    );
    out.extend(plan.expansion.stopped_by.map(Degradation::Expansion));
    out
}
