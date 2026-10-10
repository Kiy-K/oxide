//! TreeIndex navigation and the TreeRouter ↔ DecisionProvider boundary, run
//! offline against the in-memory store: cycles and cross-edges, partial
//! coverage, navigation bounds, and missing/invalid/uncertain/over-allowance
//! judgments falling back to the heuristic.

mod common;

use std::collections::BTreeSet;

use common::*;
use oxide_kernel::decision::*;
use oxide_kernel::id::EntityId;
use oxide_kernel::knowledge::{Containment, Coverage, Entity, Relation, RelationKind, Target};
use oxide_kernel::query::Query;
use oxide_kernel::store::{KnowledgeStore, MemoryStore, MemoryView, ReadView, StoreError};
use oxide_kernel::tree::{NavLimits, ProjectionId, RegionId, region};

const WIDE: NavLimits = NavLimits {
    children: 16,
    cross_edges: 16,
};

fn view() -> MemoryView {
    let mut store = MemoryStore::default();
    let k = key("s1", "d1");
    publish(&mut store, &k, batch());
    store.open(&k).unwrap()
}

#[test]
fn hierarchy_follows_physical_containment_in_stable_order() {
    let view = view();
    let root = region(&view, &RegionId::root(), WIDE).unwrap();
    assert_eq!(root.parent, None);
    let top = [
        module(),
        file("src/a.rs"),
        file("src/b.rs"),
        file("tests/t.rs"),
    ];
    assert_eq!(root.children, top.map(RegionId::of));

    let a = region(&view, &RegionId::of(file("src/a.rs")), WIDE).unwrap();
    assert_eq!(a.parent, Some(RegionId::root()));
    assert_eq!(a.children, [c(), g()].map(RegionId::of));
    assert_eq!(a.coverage, Some(Coverage::Complete));
    // Logical containment is a cross-edge, not a second parent.
    assert_eq!(a.cross_edges.len(), 1);
    let logical = RelationKind::Contains(Containment::Logical);
    assert_eq!(a.cross_edges[0].kind, logical);

    let nested = region(&view, &RegionId::of(f1()), WIDE).unwrap();
    assert_eq!(nested.parent, Some(RegionId::of(c())));
    assert!(nested.children.is_empty());
}

#[test]
fn cross_edges_keep_cycles_and_unresolved_evidence() {
    let view = view();
    let g_region = region(&view, &RegionId::of(g()), WIDE).unwrap();
    let targets: Vec<&Target> = g_region.cross_edges.iter().map(|r| &r.to).collect();
    assert!(targets.contains(&&Target::Resolved(h())));
    assert!(targets.contains(&&Target::Ambiguous(vec![f0(), f1()])));
    let missing = Target::Unresolved {
        name: "missing".into(),
    };
    assert!(targets.contains(&&missing));
    let called_back = |r: &Relation| r.from == h() && r.kind == RelationKind::Calls;
    assert!(g_region.cross_edges.iter().any(called_back));

    // Following cross-edges around the g <-> h cycle terminates with a visited set.
    let mut seen = BTreeSet::new();
    let mut frontier = vec![g()];
    while let Some(id) = frontier.pop() {
        if !seen.insert(id.clone()) {
            continue;
        }
        let r = region(&view, &RegionId::of(id), WIDE).unwrap();
        for edge in r
            .cross_edges
            .iter()
            .filter(|e| e.kind == RelationKind::Calls)
        {
            frontier.extend(edge.to.entities().iter().cloned());
            frontier.push(edge.from.clone());
        }
    }
    assert_eq!(seen, BTreeSet::from([f0(), f1(), g(), h()]));
}

#[test]
fn coverage_and_bounds_are_explicit() {
    let view = view();
    let h_region = region(&view, &RegionId::of(h()), WIDE).unwrap();
    assert!(matches!(h_region.coverage, Some(Coverage::Partial { .. })));
    let module_region = region(&view, &RegionId::of(module()), WIDE).unwrap();
    assert_eq!(module_region.coverage, None);

    let tight = NavLimits {
        children: 1,
        cross_edges: 1,
    };
    let root = region(&view, &RegionId::root(), tight).unwrap();
    assert_eq!((root.children.len(), root.children_truncated), (1, true));
    let g_region = region(&view, &RegionId::of(g()), tight).unwrap();
    let cross = (g_region.cross_edges.len(), g_region.cross_edges_truncated);
    assert_eq!(cross, (1, true));

    let other = RegionId {
        projection: ProjectionId(2),
        anchor: EntityId::Repository,
    };
    assert!(matches!(
        region(&view, &other, WIDE),
        Err(StoreError::Unsupported(_))
    ));
    let absent = sym("src/a.rs", &[("absent", 0)]);
    assert_eq!(
        region(&view, &RegionId::of(absent.clone()), WIDE),
        Err(StoreError::MissingEntity(absent))
    );
}

/// Scripted provider: answers through a function and counts calls.
struct Fake {
    questions: Vec<Question>,
    answer: fn(&[Capsule]) -> Result<Vec<Judgment>, ProviderError>,
    calls: usize,
}

impl DecisionProvider for Fake {
    fn identity(&self) -> ProviderIdentity {
        ProviderIdentity {
            name: "fake".into(),
            version: "1".into(),
        }
    }

    fn supports(&self, question: Question) -> bool {
        self.questions.contains(&question)
    }

    fn judge(&mut self, capsules: &[Capsule]) -> Result<Vec<Judgment>, ProviderError> {
        self.calls += 1;
        (self.answer)(capsules)
    }
}

fn fake(answer: fn(&[Capsule]) -> Result<Vec<Judgment>, ProviderError>) -> Fake {
    Fake {
        questions: vec![Question::BranchValue],
        answer,
        calls: 0,
    }
}

fn value(capsule: &Capsule, value: f64, confidence: Option<f64>) -> Judgment {
    Judgment::of(capsule, Verdict::Value { value, confidence })
}

fn verdict(capsule: &Capsule, verdict: Verdict) -> Judgment {
    Judgment {
        verdict,
        ..value(capsule, 0.0, None)
    }
}

/// Branch capsules for the root's children: module, a.rs, b.rs, t.rs.
fn branch_capsules() -> Vec<Capsule> {
    let view = view();
    let query = Query {
        text: "where is h called".into(),
    };
    let root = region(&view, &RegionId::root(), WIDE).unwrap();
    let capsule = |id| {
        let r = region(&view, id, WIDE).unwrap();
        Capsule::branch(&key("s1", "d1"), &query, &r)
    };
    root.children.iter().map(capsule).collect()
}

fn fallbacks(decisions: &[Decision]) -> Vec<Option<Fallback>> {
    decisions.iter().map(|d| d.fallback).collect()
}

const ANY: DecisionPolicy = DecisionPolicy {
    min_confidence: None,
    confidence_calibrated: false,
};
const CONFIDENT: DecisionPolicy = DecisionPolicy {
    min_confidence: Some(0.5),
    confidence_calibrated: true,
};

#[test]
fn malformed_and_uncertain_judgments_fall_back_per_subject() {
    let capsules = branch_capsules();
    let mut provider = fake(|c| {
        let stray = Judgment {
            subject: Subject::Candidate(EntityId::Repository),
            ..value(&c[0], 1.0, None)
        };
        Ok(vec![
            value(&c[0], 0.9, Some(0.8)),
            value(&c[1], f64::NAN, None),
            value(&c[2], 0.4, Some(0.2)),
            value(&c[2], 0.4, Some(0.9)),
            stray,
            // nothing for c[3]
        ])
    });
    let mut allowance = Allowance { remaining: 10 };
    let decisions = decide(Some(&mut provider), &capsules, &mut allowance, CONFIDENT);
    assert_eq!(
        fallbacks(&decisions),
        [
            None,
            Some(Fallback::Invalid),
            Some(Fallback::Duplicate),
            Some(Fallback::Missing)
        ]
    );
    assert_eq!(decisions[0].value, 0.9);
    assert_eq!(decisions[0].provider.name, "fake");
    let heuristic = |d: &Decision| d.provider == Heuristic.identity() && d.confidence.is_none();
    assert!(decisions[1..].iter().all(heuristic));
    assert_eq!((provider.calls, allowance.remaining), (1, 6));
    let subjects: Vec<_> = decisions.iter().map(|d| &d.subject).collect();
    let expected: Vec<_> = capsules.iter().map(|c| &c.subject).collect();
    assert_eq!(subjects, expected);
}

#[test]
fn abstention_wrong_version_and_low_or_unreported_confidence_fall_back() {
    let capsules = branch_capsules();
    let mut provider = fake(|c| {
        let stale = Judgment {
            capsule_version: CAPSULE_VERSION + 1,
            ..value(&c[1], 0.7, Some(0.9))
        };
        Ok(vec![
            verdict(&c[0], Verdict::Abstain),
            stale,
            value(&c[2], 0.7, None),
            verdict(&c[3], Verdict::Unsupported),
        ])
    });
    // An answer for other capsule content (same subject, question and
    // version) is not an answer for this capsule.
    let mut edited = fake(|c| {
        let mut other = c[0].clone();
        other.query.push('!');
        Ok(vec![Judgment {
            capsule_digest: other.digest(),
            ..value(&c[0], 0.7, Some(0.9))
        }])
    });
    let mut allowance = Allowance { remaining: 10 };
    let decisions = decide(Some(&mut edited), &capsules[..1], &mut allowance, ANY);
    assert_eq!(fallbacks(&decisions), [Some(Fallback::Invalid)]);
    let mut allowance = Allowance { remaining: 10 };
    let decisions = decide(Some(&mut provider), &capsules, &mut allowance, CONFIDENT);
    assert_eq!(
        fallbacks(&decisions),
        [
            Some(Fallback::Abstained),
            Some(Fallback::Invalid),
            Some(Fallback::LowConfidence),
            Some(Fallback::Unsupported)
        ]
    );

    // A floor over confidence nobody has calibrated accepts nothing: a
    // provider's own confidence is not evidence.
    let uncalibrated = DecisionPolicy {
        confidence_calibrated: false,
        ..CONFIDENT
    };
    let mut sure = fake(|c| Ok(vec![value(&c[0], 0.7, Some(0.99))]));
    let decisions = decide(
        Some(&mut sure),
        &capsules[..1],
        &mut allowance,
        uncalibrated,
    );
    assert_eq!(fallbacks(&decisions), [Some(Fallback::Uncalibrated)]);
    let decisions = decide(Some(&mut sure), &capsules[..1], &mut allowance, CONFIDENT);
    assert_eq!(fallbacks(&decisions), [None]);

    // Without a confidence floor an unreported confidence is accepted, and it
    // stays unreported rather than becoming certainty.
    let mut unreported = fake(|c| Ok(vec![value(&c[0], 0.7, None)]));
    let decisions = decide(Some(&mut unreported), &capsules[2..3], &mut allowance, ANY);
    assert_eq!(
        (decisions[0].fallback, decisions[0].confidence),
        (None, None)
    );
}

#[test]
fn allowance_is_shared_and_failures_consume_it() {
    let capsules = branch_capsules();
    let mut provider = fake(|c| Ok(c.iter().map(|c| value(c, 1.0, Some(1.0))).collect()));
    let mut allowance = Allowance { remaining: 3 };
    let first = decide(Some(&mut provider), &capsules[..2], &mut allowance, ANY);
    let second = decide(Some(&mut provider), &capsules[2..], &mut allowance, ANY);
    assert_eq!(fallbacks(&first), [None, None]);
    assert_eq!(
        fallbacks(&second),
        [None, Some(Fallback::AllowanceExhausted)]
    );
    assert_eq!(allowance.remaining, 0);

    // Nothing left: the provider is not called at all.
    let calls = provider.calls;
    decide(Some(&mut provider), &capsules, &mut allowance, ANY);
    assert_eq!(provider.calls, calls);

    // A timeout spends what it was given and falls back for each subject.
    let mut down = fake(|_| Err(ProviderError::Timeout));
    let mut allowance = Allowance { remaining: 2 };
    let decisions = decide(Some(&mut down), &capsules[..2], &mut allowance, ANY);
    let timed_out = Some(Fallback::ProviderFailed(ProviderError::Timeout));
    assert_eq!(fallbacks(&decisions), [timed_out; 2]);
    assert_eq!(allowance.remaining, 0);
}

#[test]
fn unsupported_questions_and_no_provider_use_the_heuristic() {
    let capsules = branch_capsules();
    let mut candidates_only = Fake {
        questions: vec![Question::Relevance, Question::NeighborValue],
        answer: |_| panic!("must not be asked a branch question"),
        calls: 0,
    };
    let mut allowance = Allowance { remaining: 10 };
    let decisions = decide(Some(&mut candidates_only), &capsules, &mut allowance, ANY);
    let unsupported = |d: &Decision| d.fallback == Some(Fallback::Unsupported);
    assert!(decisions.iter().all(unsupported));
    assert_eq!(allowance.remaining, 10);

    let offline = decide(None, &capsules, &mut allowance, ANY);
    let no_provider = |d: &Decision| d.fallback == Some(Fallback::NoProvider);
    assert!(offline.iter().all(no_provider));

    // The heuristic is itself a provider behind the same boundary.
    let direct = decide(Some(&mut Heuristic), &capsules, &mut allowance, ANY);
    assert!(direct.iter().all(|d| d.fallback.is_none()));
    let values = |ds: &[Decision]| ds.iter().map(|d| d.value).collect::<Vec<_>>();
    assert_eq!(values(&direct), values(&offline));
}

#[test]
fn capsules_are_bounded_and_correlated() {
    let view = view();
    let g_entity: Entity = view.entities(&[g()]).unwrap().pop().flatten().unwrap();
    let long = Query {
        text: "é".repeat(MAX_CAPSULE_QUERY_BYTES), // two bytes per char
    };
    let call = batch()
        .relations
        .into_iter()
        .find(|r| r.from == g() && r.kind == RelationKind::Calls)
        .unwrap();
    let relations = vec![call; MAX_CAPSULE_RELATIONS + 3];
    let snapshot = key("s1", "d1");
    let capsule = Capsule::new(
        &snapshot,
        &long,
        Subject::Candidate(g()),
        Question::Relevance,
        Evidence {
            entity: &g_entity,
            coverage: None,
            snippet: &Snippet::Text {
                text: "x\n".repeat(MAX_CAPSULE_SNIPPET_BYTES),
                truncated: false,
            },
            channels: &[],
            seeds: &[],
            depth: Some(1),
            origins: &[],
            relations: &relations,
            relations_truncated: false,
        },
    );
    let Snippet::Text { text, truncated } = &capsule.snippet else {
        panic!("snippet kept")
    };
    assert!(*truncated && text.len() <= MAX_CAPSULE_SNIPPET_BYTES && text.ends_with('\n'));
    assert!(capsule.relations.iter().all(|r| r.evidence.is_none()));
    // Rendering is canonical: rebuilt capsules render and digest alike, and
    // withholding source changes the rendering but not the digest.
    assert_eq!(capsule.render(false), capsule.clone().render(false));
    assert!(!capsule.render(true).contains("x\\nx"));
    assert_ne!(capsule.render(true), capsule.render(false));
    assert!(capsule.query.len() <= MAX_CAPSULE_QUERY_BYTES && capsule.query_truncated);
    assert_eq!(capsule.relations.len(), MAX_CAPSULE_RELATIONS);
    assert!(capsule.relations_truncated);
    assert_eq!(capsule.subject, Subject::Candidate(g()));
    assert_eq!(capsule.version, CAPSULE_VERSION);

    let tight = NavLimits {
        children: 1,
        cross_edges: 1,
    };
    let r = region(&view, &RegionId::of(g()), tight).unwrap();
    let branch = Capsule::branch(&snapshot, &Query { text: "q".into() }, &r);
    assert!(branch.relations_truncated && !branch.query_truncated);
    assert_eq!(branch.question, Question::BranchValue);
}
