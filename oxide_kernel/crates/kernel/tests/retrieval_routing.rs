//! Phase 3 entry-point retrieval and the TreeRouter baseline against the
//! in-memory store: channel and seed evidence, unavailable capabilities,
//! cycles, every bound, root fallback, and judgments that may prune but
//! never steer the route when missing, invalid, uncertain or over allowance.

mod common;

use common::*;
use oxide_kernel::decision::*;
use oxide_kernel::id::{EntityId, SnapshotKey};
use oxide_kernel::knowledge::{ByteRange, Entity, RepositorySnapshot};
use oxide_kernel::lexical::{LexicalRequest, LexicalResult};
use oxide_kernel::query::{Query, QueryContext};
use oxide_kernel::retrieve::*;
use oxide_kernel::route::*;
use oxide_kernel::store::*;
use oxide_kernel::tree::RegionId;

fn view() -> MemoryView {
    let mut store = MemoryStore::default();
    let k = key("s1", "d1");
    publish(&mut store, &k, batch());
    store.open(&k).unwrap()
}

fn query(text: &str) -> Query {
    Query { text: text.into() }
}

fn ids(set: &CandidateSet) -> Vec<EntityId> {
    set.entries.iter().map(|e| e.entity.clone()).collect()
}

fn get(set: &CandidateSet, id: &EntityId) -> EntryPoint {
    set.entries
        .iter()
        .find(|e| e.entity == *id)
        .unwrap()
        .clone()
}

#[test]
fn lexical_entry_points_keep_channel_evidence_and_semantic_status() {
    let view = view();
    let set = retrieve(
        &view,
        &query("test_h"),
        &QueryContext::default(),
        ChannelState::Unconfigured,
        BASELINE,
    )
    .unwrap();
    assert_eq!(set.snapshot, key("s1", "d1"));
    assert_eq!(set.entries[0].entity, test_h());
    let lexical = &set.entries[0].channels[0];
    assert_eq!((lexical.channel, lexical.rank), (Channel::Lexical, 1));
    assert_eq!(lexical.scorer, "memory-term-overlap-v1");
    assert!(set.entries.iter().all(|e| e.seeds.is_empty()));
    assert!(matches!(
        set.channels[0].state,
        ChannelState::Ran { returned, .. } if returned == set.entries.len()
    ));
    assert_eq!(set.channels[1].state, ChannelState::Unconfigured);
}

#[test]
fn structural_seeds_resolve_every_hint_kind_with_visible_reasons() {
    let view = view();
    let context = QueryContext {
        paths: vec![path("SRC/B.RS"), path("missing.rs")],
        symbols: vec!["f".into(), "C.f".into(), "app".into(), "nothing".into()],
        selection: Some((path("src/a.rs"), ByteRange { start: 22, end: 25 })),
        changed: vec![path("tests/t.rs")],
    };
    let config = RetrievalConfig {
        lexical: false,
        ..BASELINE
    };
    let set = retrieve(
        &view,
        &query("g"),
        &context,
        ChannelState::Unconfigured,
        config,
    )
    .unwrap();
    assert_eq!(set.channels[0].state, ChannelState::Disabled);
    // Seeds in hint order: selection, paths, symbols, changed.
    assert_eq!(
        ids(&set),
        [f1(), file("src/b.rs"), f0(), module(), file("tests/t.rs")]
    );
    let selection = &get(&set, &f1()).seeds;
    assert_eq!(selection[0].kind, HintKind::Selection);
    assert_eq!(selection[0].hint, "src/a.rs@22..25");
    // f#1 is seeded by the selection and both symbol hints, each kept.
    assert_eq!(selection.len(), 3);
    let folded = &get(&set, &file("src/b.rs")).seeds[0];
    assert!(folded.case_insensitive && folded.kind == HintKind::Path);
    assert_eq!(
        get(&set, &file("tests/t.rs")).seeds[0].kind,
        HintKind::Changed
    );
    assert!(set.entries.iter().all(|e| e.channels.is_empty()));
    assert_eq!(
        set.hints,
        [
            HintDiagnostic {
                kind: HintKind::Path,
                hint: "missing.rs".into(),
                issue: HintIssue::NotFound,
            },
            HintDiagnostic {
                kind: HintKind::Symbol,
                hint: "nothing".into(),
                issue: HintIssue::NotFound,
            },
        ]
    );
    let one = RetrievalConfig {
        symbol_limit: 1,
        ..config
    };
    let context = QueryContext {
        symbols: vec!["f".into()],
        ..Default::default()
    };
    let set = retrieve(&view, &query(""), &context, ChannelState::Unconfigured, one).unwrap();
    assert_eq!(ids(&set), [f0()]);
    assert_eq!(set.hints[0].issue, HintIssue::Truncated { limit: 1 });
}

#[test]
fn seeds_precede_lexical_hits_and_duplicates_merge_their_evidence() {
    let view = view();
    let context = QueryContext {
        symbols: vec!["g".into(), "test_h".into()],
        ..Default::default()
    };
    let set = retrieve(
        &view,
        &query("test_h"),
        &context,
        ChannelState::Unconfigured,
        BASELINE,
    )
    .unwrap();
    assert_eq!(set.entries[0].entity, g());
    assert_eq!(set.entries[1].entity, test_h());
    let merged = get(&set, &test_h());
    assert_eq!((merged.seeds.len(), merged.channels.len()), (1, 1));
    let mut unique = ids(&set);
    unique.sort();
    unique.dedup();
    assert_eq!(unique.len(), set.entries.len());
}

/// A view whose store has no lexical capability.
struct NoLexical(MemoryView);

impl ReadView for NoLexical {
    fn snapshot(&self) -> &RepositorySnapshot {
        self.0.snapshot()
    }
    fn entities(&self, ids: &[EntityId]) -> Result<Vec<Option<Entity>>, StoreError> {
        self.0.entities(ids)
    }
    fn adjacency(&self, request: &AdjacencyRequest) -> Result<Adjacency, StoreError> {
        self.0.adjacency(request)
    }
    fn lexical(&self, _: &LexicalRequest) -> Result<LexicalResult, StoreError> {
        Err(StoreError::Unsupported("no lexical accelerator".into()))
    }
    fn named(&self, name: &str, limit: usize) -> Result<Named, StoreError> {
        self.0.named(name, limit)
    }
}

#[test]
fn unavailable_capabilities_are_reported_and_structure_still_works() {
    let view = NoLexical(view());
    let context = QueryContext {
        symbols: vec!["g".into()],
        ..Default::default()
    };
    let semantic = ChannelState::Unavailable("configured runner unreachable".into());
    let set = retrieve(&view, &query("g"), &context, semantic.clone(), BASELINE).unwrap();
    assert_eq!(
        set.channels[0].state,
        ChannelState::Unavailable("no lexical accelerator".into())
    );
    assert_eq!(set.channels[1].state, semantic);
    assert_eq!(ids(&set), [g()]);
    let routed = route_all(&view, &set, None);
    assert!(routed.candidates.len() > 1);
}

fn request(set: &CandidateSet, limits: RouteLimits) -> RouteRequest {
    RouteRequest {
        snapshot: set.snapshot.clone(),
        query: query("q"),
        entry_points: set.entries.clone(),
        limits,
        policy: baseline_policy(),
    }
}

fn seeded(view: &MemoryView, entity: &str) -> CandidateSet {
    let context = QueryContext {
        symbols: vec![entity.into()],
        ..Default::default()
    };
    let config = RetrievalConfig {
        lexical: false,
        ..BASELINE
    };
    retrieve(
        view,
        &query(""),
        &context,
        ChannelState::Unconfigured,
        config,
    )
    .unwrap()
}

fn route_all(
    view: &impl ReadView,
    set: &CandidateSet,
    provider: Option<&mut dyn DecisionProvider>,
) -> RouteResult {
    let mut allowance = Allowance { remaining: 100 };
    let request = request(set, BASELINE_LIMITS);
    route(
        view,
        &request,
        provider,
        &mut allowance,
        DecisionPolicy::default(),
    )
    .unwrap()
}

fn routed(result: &RouteResult) -> Vec<EntityId> {
    result.candidates.iter().map(|c| c.entity.clone()).collect()
}

#[test]
fn router_explores_structure_and_cycles_within_depth() {
    let view = view();
    let result = route_all(&view, &seeded(&view, "g"), None);
    assert_eq!(result.snapshot, key("s1", "d1"));
    // g → scope a.rs, callee h, ambiguous callees f#0/f#1 → C, module
    // (logical), b.rs, test_h (tested-by). tests/t.rs is one layer too deep.
    let mut expected = vec![
        g(),
        file("src/a.rs"),
        h(),
        f0(),
        f1(),
        c(),
        module(),
        file("src/b.rs"),
        test_h(),
    ];
    expected.sort();
    assert_eq!(routed(&result), expected);
    let trace = &result.trace;
    assert!(!trace.root_fallback);
    assert_eq!(trace.stopped_by, Some(Bound::Depth));
    let deferred: Vec<_> = trace
        .steps
        .iter()
        .filter(|s| s.visit == Visit::Deferred(Bound::Depth))
        .map(|s| s.region.anchor.clone())
        .collect();
    assert_eq!(deferred, [file("tests/t.rs")]);
    assert!(
        trace.work.revisits > 0,
        "the g <-> h cycle is revisited, not re-explored"
    );
    assert_eq!(trace.work.regions_loaded, 9);
    assert_eq!(trace.work.max_depth_reached, 2);
    assert!(trace.decisions.is_empty());

    let entry = result.candidates.iter().find(|c| c.entity == g()).unwrap();
    assert_eq!(
        (entry.origins[0].clone(), entry.depth),
        (Origin::EntryPoint, 0)
    );
    assert_eq!(entry.seeds[0].kind, HintKind::Symbol);
    let callee = result.candidates.iter().find(|c| c.entity == h()).unwrap();
    assert!(callee.channels.is_empty() && callee.seeds.is_empty());
    assert_eq!(
        callee.origins[0],
        Origin::Neighbor {
            via: g(),
            relation: oxide_kernel::knowledge::RelationKind::Calls,
            direction: Direction::Outgoing,
        }
    );
    // Deterministic and replayable.
    assert_eq!(route_all(&view, &seeded(&view, "g"), None), result);
}

#[test]
fn every_bound_stops_or_cuts_exploration_visibly() {
    let view = view();
    let set = seeded(&view, "g");
    let run = |limits| {
        let mut allowance = Allowance { remaining: 0 };
        route(
            &view,
            &request(&set, limits),
            None,
            &mut allowance,
            DecisionPolicy::default(),
        )
        .unwrap()
    };
    let regions = run(RouteLimits {
        max_regions: 2,
        ..BASELINE_LIMITS
    });
    assert_eq!(regions.candidates.len(), 2);
    assert_eq!(regions.trace.stopped_by, Some(Bound::Regions));
    assert!(
        regions
            .trace
            .steps
            .iter()
            .any(|s| s.visit == Visit::Deferred(Bound::Regions))
    );

    let depth = run(RouteLimits {
        max_depth: 0,
        ..BASELINE_LIMITS
    });
    assert_eq!(routed(&depth), [g()]);
    assert_eq!(depth.trace.stopped_by, Some(Bound::Depth));

    let fanout = run(RouteLimits {
        max_fanout: 1,
        ..BASELINE_LIMITS
    });
    assert!(
        fanout
            .trace
            .truncations
            .contains(&(RegionId::of(g()), Bound::Fanout))
    );
    assert!(fanout.candidates.len() < 9);

    let edges = run(RouteLimits {
        max_edges: 1,
        ..BASELINE_LIMITS
    });
    assert_eq!(edges.trace.stopped_by, Some(Bound::Edges));
    assert_eq!(routed(&edges), [g()]);
}

#[test]
fn no_entry_points_route_from_the_root() {
    let view = view();
    let empty = CandidateSet {
        entries: Vec::new(),
        ..seeded(&view, "g")
    };
    let result = route_all(&view, &empty, None);
    assert!(result.trace.root_fallback);
    assert_eq!(result.trace.steps[0].region, RegionId::root());
    let root = result
        .candidates
        .iter()
        .find(|c| c.entity == EntityId::Repository)
        .unwrap();
    assert_eq!(root.origins, [Origin::RootFallback]);
    assert!(routed(&result).contains(&file("src/a.rs")));
}

/// Answers every branch question with one verdict.
struct Fixed(Verdict);

impl DecisionProvider for Fixed {
    fn identity(&self) -> ProviderIdentity {
        ProviderIdentity {
            name: "fixed".into(),
            version: "1".into(),
        }
    }
    fn supports(&self, _: Question) -> bool {
        true
    }
    fn judge(&mut self, capsules: &[Capsule]) -> Result<Vec<Judgment>, ProviderError> {
        Ok(capsules
            .iter()
            .map(|c| Judgment {
                subject: c.subject.clone(),
                question: c.question,
                capsule_version: c.version,
                verdict: self.0,
            })
            .collect())
    }
}

#[test]
fn judgments_prune_branches_but_fallbacks_never_steer_the_route() {
    let view = view();
    let set = seeded(&view, "g");
    let baseline = route_all(&view, &set, None);

    // The heuristic is asked and recorded, and changes nothing.
    let heuristic = route_all(&view, &set, Some(&mut Heuristic));
    assert_eq!(heuristic.candidates, baseline.candidates);
    assert_eq!(heuristic.trace.steps, baseline.trace.steps);
    assert!(!heuristic.trace.decisions.is_empty());
    assert!(heuristic.trace.decisions.iter().all(|d| d.value == 0.5));

    // Abstention, invalid values: per-subject heuristic fallback, same route.
    for verdict in [
        Verdict::Abstain,
        Verdict::Unsupported,
        Verdict::Value {
            value: f64::NAN,
            confidence: None,
        },
    ] {
        let result = route_all(&view, &set, Some(&mut Fixed(verdict)));
        assert_eq!(result.candidates, baseline.candidates, "{verdict:?}");
        assert!(result.trace.decisions.iter().all(|d| d.fallback.is_some()));
    }

    // Low confidence under a confidence floor: fallback, same route.
    let low = Verdict::Value {
        value: 0.0,
        confidence: Some(0.1),
    };
    let mut allowance = Allowance { remaining: 100 };
    let floor = DecisionPolicy {
        min_confidence: Some(0.5),
    };
    let request = request(&set, BASELINE_LIMITS);
    let result = route(
        &view,
        &request,
        Some(&mut Fixed(low)),
        &mut allowance,
        floor,
    )
    .unwrap();
    assert_eq!(result.candidates, baseline.candidates);

    // A validated low value prunes every non-entry branch; the entry point
    // itself is never judged or pruned.
    let result = route_all(&view, &set, Some(&mut Fixed(low)));
    assert_eq!(routed(&result), [g()]);
    assert!(result.trace.steps.iter().any(|s| s.visit == Visit::Pruned));

    // An exhausted allowance falls back too, and is shared.
    let mut allowance = Allowance { remaining: 0 };
    let none_left = route(
        &view,
        &request,
        Some(&mut Fixed(low)),
        &mut allowance,
        floor,
    )
    .unwrap();
    assert_eq!(none_left.candidates, baseline.candidates);
    let mut allowance = Allowance { remaining: 2 };
    let some = route(
        &view,
        &request,
        Some(&mut Fixed(low)),
        &mut allowance,
        DecisionPolicy::default(),
    )
    .unwrap();
    assert_eq!(
        (allowance.remaining, some.trace.work.judgments_requested),
        (0, 2)
    );
    assert!(
        some.trace
            .decisions
            .iter()
            .any(|d| d.fallback == Some(Fallback::AllowanceExhausted))
    );
}

#[test]
fn a_request_for_another_generation_is_refused() {
    let view = view();
    let mut set = seeded(&view, "g");
    set.snapshot = SnapshotKey {
        derivation: oxide_kernel::id::DerivationId::new("other").unwrap(),
        ..set.snapshot
    };
    let mut allowance = Allowance { remaining: 0 };
    let refused = route(
        &view,
        &request(&set, BASELINE_LIMITS),
        None,
        &mut allowance,
        DecisionPolicy::default(),
    );
    assert!(matches!(refused, Err(StoreError::Unsupported(_))));
}

#[test]
fn origins_are_complete_whatever_the_entry_order() {
    let view = view();
    let both = |order: [&str; 2]| {
        let context = QueryContext {
            symbols: order.iter().map(|s| (*s).to_owned()).collect(),
            ..Default::default()
        };
        let config = RetrievalConfig {
            lexical: false,
            ..BASELINE
        };
        let set = retrieve(
            &view,
            &query(""),
            &context,
            ChannelState::Unconfigured,
            config,
        )
        .unwrap();
        route_all(&view, &set, None)
    };
    // One origin per neighbor region and expansion: the first cross-edge in
    // domain order names it (here the g -> h call, seen from either end).
    let via = |origins: &[Origin], from: EntityId| {
        origins
            .iter()
            .any(|o| matches!(o, Origin::Neighbor { via, .. } if *via == from))
    };
    for order in [["g", "h"], ["h", "g"]] {
        let result = both(order);
        let origins = |id: EntityId| {
            let c = result.candidates.iter().find(|c| c.entity == id).unwrap();
            c.origins.clone()
        };
        // Each entry point is also reached from the other, in either order.
        assert!(origins(h()).contains(&Origin::EntryPoint));
        assert!(via(&origins(h()), g()), "{order:?}");
        assert!(origins(g()).contains(&Origin::EntryPoint));
        assert!(via(&origins(g()), h()), "{order:?}");
    }
}

#[test]
fn the_route_judgment_cap_binds_below_the_shared_allowance() {
    let view = view();
    let set = seeded(&view, "g");
    let limits = RouteLimits {
        max_judgments: 2,
        ..BASELINE_LIMITS
    };
    let mut allowance = Allowance { remaining: 100 };
    let low = Verdict::Value {
        value: 0.0,
        confidence: None,
    };
    let result = route(
        &view,
        &request(&set, limits),
        Some(&mut Fixed(low)),
        &mut allowance,
        DecisionPolicy::default(),
    )
    .unwrap();
    assert_eq!(result.trace.work.judgments_requested, 2);
    assert_eq!(allowance.remaining, 98);
    let exhausted = result.trace.decisions.iter();
    assert!(
        exhausted
            .filter(|d| d.fallback == Some(Fallback::AllowanceExhausted))
            .count()
            > 0
    );
}
