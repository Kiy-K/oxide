//! Phase 4 end to end on the retained Python fixture: capsules, judgments,
//! deterministic selection and expansion, and the ContextPacker under hard
//! budgets, with real captured bytes and digests. Offline: no model, runner
//! or network.

use std::path::{Path, PathBuf};

use oxide_kernel::capsule::{CAPSULE_VERSION, Capsule, Question, Snippet};
use oxide_kernel::context::{ContextConfig, ContextRequest, ContextRun, baseline, build_context};
use oxide_kernel::decision::{
    DecisionProvider, Fallback, Heuristic, Judgment, ProviderError, ProviderIdentity, Replay,
    Verdict,
};
use oxide_kernel::id::{Digest, RepoId, RepoPath};
use oxide_kernel::pack::{ContextBundle, View, count, counter};
use oxide_kernel::query::{ContextBudget, Query, QueryContext};
use oxide_kernel::retrieve::ChannelState;
use oxide_kernel::select::{ExpansionBound, OmitReason, Reason};
use oxide_kernel::source::{SourceCapture, SourceProvider};
use oxide_kernel::store::{KnowledgeStore, MemoryStore, MemoryView, StoreError};
use oxide_runtime::capture::{Scope, capture};
use oxide_runtime::derivation::derive;
use oxide_runtime::jev::{Disclosure, Jev, JevConfig, MODEL, Replay as JevReplay, request};
use oxide_runtime::storage::LadybugStore;

fn repo_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../../..")
}

fn fixture() -> (SourceCapture, MemoryView) {
    let source = capture(&repo_root().join("fixtures/py_repo"), &Scope::default()).unwrap();
    let repo = RepoId::new("phase4-context").unwrap();
    let (manifest, batch, _) = derive(&repo, &source).unwrap();
    let key = manifest.key.clone();
    let mut store = MemoryStore::default();
    store.begin(manifest).unwrap();
    store.write(&key, batch).unwrap();
    store.publish(&key).unwrap();
    (source, store.open(&key).unwrap())
}

fn ask(text: &str, symbols: &[&str], tokens: u32) -> ContextRequest {
    ContextRequest {
        query: Query { text: text.into() },
        context: QueryContext {
            symbols: symbols.iter().map(|s| (*s).into()).collect(),
            ..Default::default()
        },
        budget: ContextBudget {
            tokens,
            counter: counter(),
        },
        semantic: ChannelState::Unconfigured,
    }
}

fn run(
    view: &MemoryView,
    source: &dyn SourceProvider,
    request: &ContextRequest,
    config: &ContextConfig,
    provider: Option<&mut dyn DecisionProvider>,
) -> ContextRun {
    build_context(view, source, request, config, provider).unwrap()
}

fn names(bundle: &ContextBundle) -> Vec<String> {
    bundle
        .items
        .iter()
        .flat_map(|i| i.entities.iter().map(|e| format!("{:?}", e.entity)))
        .collect()
}

/// Every packed byte is the captured byte at its attributed range, the
/// payload costs exactly what the bundle reports, items never overlap, and
/// the budget holds.
fn assert_source_backed(bundle: &ContextBundle, source: &SourceCapture) {
    assert!(bundle.used_tokens <= bundle.budget.tokens);
    assert_eq!(count(&bundle.payload()), bundle.used_tokens);
    let mut spans: Vec<(&RepoPath, u64, u64)> = Vec::new();
    for item in &bundle.items {
        let file = &source.files[&item.source.file];
        assert_eq!(file.digest, item.source.digest);
        let range = item.source.range.start as usize..item.source.range.end as usize;
        let text = std::str::from_utf8(&file.bytes[range]).unwrap();
        assert!(item.text.contains(text), "{}", item.text);
        assert_eq!(count(&item.text), item.tokens);
        for (path, start, end) in &spans {
            let disjoint = item.source.range.end <= *start || *end <= item.source.range.start;
            assert!(*path != &item.source.file || disjoint, "overlapping items");
        }
        spans.push((
            &item.source.file,
            item.source.range.start,
            item.source.range.end,
        ));
    }
}

#[test]
fn kernel_sha256_matches_capture() {
    let (source, _) = fixture();
    for file in source.files.values() {
        assert_eq!(oxide_kernel::digest::digest(&file.bytes), file.digest);
    }
}

#[test]
fn offline_context_is_deterministic_bounded_and_source_backed() {
    let (source, view) = fixture();
    let req = ask("refresh the auth token after it expires", &[], 1024);
    let first = run(&view, &source, &req, &baseline(), None);
    assert_eq!(first, run(&view, &source, &req, &baseline(), None));
    let bundle = &first.bundle;
    assert!(!bundle.items.is_empty());
    assert_source_backed(bundle, &source);
    // No provider: every decision is the heuristic's, and nothing was asked.
    let no_provider =
        |d: &oxide_kernel::decision::Decision| d.fallback == Some(Fallback::NoProvider);
    assert!(first.decisions.iter().all(no_provider));
    assert!(first.judgments.is_empty() && first.provider.is_none());
    // Graph membership is not inclusion.
    assert!(first.graph.nodes.len() > first.plan.items.len());
    // The plan estimate bounds the packed cost (merging only saves).
    assert!(bundle.used_tokens <= first.plan.estimated_tokens);
    assert!(names(bundle).iter().any(|n| n.contains("refresh_token")));
    // Semantic is reported unconfigured, never scored.
    assert!(
        bundle
            .degraded
            .iter()
            .any(|d| format!("{d:?}").contains("Unconfigured"))
    );
    // One versioned capsule per graph node.
    assert_eq!(first.capsules.len(), first.graph.nodes.len());
    assert!(first.capsules.iter().all(|c| c.version == CAPSULE_VERSION));
}

#[test]
fn fake_and_real_stores_build_the_same_context() {
    let (source, memory) = fixture();
    let repo = RepoId::new("phase4-context").unwrap();
    let (manifest, batch, components) = derive(&repo, &source).unwrap();
    let key = manifest.key.clone();
    let dir = std::env::temp_dir().join(format!("oxide-phase4-context-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    let mut store = LadybugStore::new(&dir).unwrap();
    store.begin(manifest).unwrap();
    store.retain_derivation(&key, &components).unwrap();
    store.retain_source(&source).unwrap();
    store.write(&key, batch).unwrap();
    store.publish(&key).unwrap();
    let real = store.open(&key).unwrap();
    // Lexical scorers differ by design; structural seeds are shared.
    let mut config = baseline();
    config.retrieval.lexical = false;
    let req = ask("auth", &["AuthService", "should_retry"], 512);
    let fake = run(&memory, &source, &req, &config, None);
    // The real view hydrates from its own retained bytes.
    let real = build_context(&real, &real, &req, &config, None).unwrap();
    assert!(!fake.bundle.items.is_empty());
    assert_eq!(fake.plan, real.plan);
    assert_eq!(fake.bundle.items, real.bundle.items);
    drop(store);
    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn zero_and_tiny_budgets_give_valid_smaller_bundles() {
    let (source, view) = fixture();
    let zero = run(
        &view,
        &source,
        &ask("RetryPolicy", &[], 0),
        &baseline(),
        None,
    );
    assert!(zero.bundle.items.is_empty() && zero.bundle.payload().is_empty());
    assert_eq!(
        (zero.bundle.used_tokens, zero.bundle.remaining_tokens()),
        (0, 0)
    );
    let too_large = zero
        .bundle
        .omitted
        .iter()
        .any(|o| matches!(o.reason, OmitReason::TooLarge { .. }));
    assert!(too_large);
    for tokens in [1, 7, 30, 64] {
        let tiny = run(
            &view,
            &source,
            &ask("RetryPolicy", &[], tokens),
            &baseline(),
            None,
        );
        assert_source_backed(&tiny.bundle, &source);
    }
}

#[test]
fn oversized_entities_are_reduced_to_their_header_or_omitted_never_cut() {
    let (source, view) = fixture();
    let hint = ["RetryPolicy"];
    let big = run(
        &view,
        &source,
        &ask("RetryPolicy", &hint, 4096),
        &baseline(),
        None,
    );
    let class = big.plan.items[0].clone();
    assert!(format!("{:?}", class.entity).contains("RetryPolicy") && class.view == View::Full);
    let full = class.estimated_tokens;
    let probe = run(
        &view,
        &source,
        &ask("RetryPolicy", &hint, full - 1),
        &baseline(),
        None,
    );
    let header = &probe.plan.items[0];
    assert_eq!(header.entity, class.entity);
    assert_eq!((header.view, header.reduced), (View::Header, true));
    assert!(header.estimated_tokens < full);
    let item = &probe.bundle.items[0];
    assert!(
        item.text
            .lines()
            .next()
            .unwrap()
            .ends_with("RetryPolicy (header)")
    );
    assert!(!item.entities[0].complete);
    assert_source_backed(&probe.bundle, &source);

    let tight = header.estimated_tokens - 1;
    let none = run(
        &view,
        &source,
        &ask("RetryPolicy", &hint, tight),
        &baseline(),
        None,
    );
    let omitted = none
        .bundle
        .omitted
        .iter()
        .find(|o| o.entity == class.entity);
    assert!(matches!(
        omitted.unwrap().reason,
        OmitReason::TooLarge { .. }
    ));
    let packed =
        |i: &oxide_kernel::pack::ContextItem| i.entities.iter().any(|e| e.entity == class.entity);
    assert!(!none.bundle.items.iter().any(packed));
    assert_source_backed(&none.bundle, &source);
}

#[test]
fn overlapping_views_merge_into_one_item() {
    let (source, view) = fixture();
    let req = ask(
        "retry policy",
        &["RetryPolicy", "RetryPolicy.should_retry"],
        4096,
    );
    let run = run(&view, &source, &req, &baseline(), None);
    assert_source_backed(&run.bundle, &source);
    let class = run
        .bundle
        .items
        .iter()
        .find(|i| i.text.contains("class RetryPolicy"));
    let members: Vec<String> = class
        .unwrap()
        .entities
        .iter()
        .map(|e| format!("{:?}", e.entity))
        .collect();
    assert!(
        members.iter().any(|m| m.contains("should_retry")),
        "{members:?}"
    );
    assert_eq!(run.bundle.payload().matches("def should_retry").count(), 1);
    // Planned after its class body, the method is evidence already paid
    // for: it costs only its name in the class item's label.
    let method = run
        .plan
        .items
        .iter()
        .find(|i| format!("{:?}", i.entity).contains("should_retry"));
    let label = count(", RetryPolicy.should_retry");
    assert_eq!(method.unwrap().estimated_tokens, label);
    assert!(run.bundle.used_tokens <= run.plan.estimated_tokens);
}

/// Returns other bytes than were captured: a changed worktree.
struct Tampered<'a>(&'a SourceCapture);

impl SourceProvider for Tampered<'_> {
    fn file(&self, file: &RepoPath, digest: &Digest) -> Result<Option<Vec<u8>>, StoreError> {
        Ok(self.0.file(file, digest)?.map(|mut b| {
            b.extend_from_slice(b"\n# edited after capture\n");
            b
        }))
    }
}

struct Gone;

impl SourceProvider for Gone {
    fn file(&self, _: &RepoPath, _: &Digest) -> Result<Option<Vec<u8>>, StoreError> {
        Ok(None)
    }
}

#[test]
fn mismatched_or_missing_source_is_omitted_never_hydrated() {
    let (source, view) = fixture();
    let req = ask("RetryPolicy", &[], 1024);
    let tampered = Tampered(&source);
    let cases: [(&dyn SourceProvider, OmitReason); 2] = [
        (&tampered, OmitReason::SourceMismatch),
        (&Gone, OmitReason::SourceUnavailable),
    ];
    for (provider, reason) in cases {
        let run = run(&view, provider, &req, &baseline(), None);
        assert!(run.bundle.items.is_empty() && run.bundle.used_tokens == 0);
        assert!(run.bundle.omitted.iter().any(|o| o.reason == reason));
        assert!(
            run.capsules
                .iter()
                .all(|c| c.snippet != Snippet::Absent || c.source.is_none())
        );
        assert!(
            !run.capsules
                .iter()
                .any(|c| matches!(c.snippet, Snippet::Text { .. }))
        );
    }
}

#[test]
fn expansion_is_bounded_recorded_and_competes_for_the_budget() {
    let (source, view) = fixture();
    let req = ask("refresh the auth token after it expires", &[], 4096);
    // Under baseline routing (depth 2) every resolved neighbor of this
    // query's selections is already routed, so expansion only finds
    // duplicates. Entry points alone (depth 0) leave it work to do.
    let baseline_run = run(&view, &source, &req, &baseline(), None);
    assert_eq!(baseline_run.plan.expansion.considered, 0);
    assert!(baseline_run.plan.expansion.duplicates > 0);
    let mut shallow = baseline();
    shallow.route_limits.max_depth = 0;
    let expanded = run(&view, &source, &req, &shallow, None);
    let mut off = shallow.clone();
    off.select.expansion.max_depth = 0;
    let plain = run(&view, &source, &req, &off, None);
    let is_expanded = |r: &Reason| matches!(r, Reason::Expanded { .. });
    assert!(plain.plan.items.iter().all(|i| !is_expanded(&i.reason)));
    assert_eq!(plain.plan.expansion.considered, 0);
    let trace = &expanded.plan.expansion;
    assert!(trace.considered > 0);
    assert!(trace.considered <= baseline().select.expansion.max_nodes);
    for item in expanded
        .plan
        .items
        .iter()
        .filter(|i| is_expanded(&i.reason))
    {
        let Reason::Expanded { seed, depth, .. } = &item.reason else {
            unreachable!()
        };
        assert_eq!(*depth, 1);
        assert!(expanded.plan.items.iter().any(|i| i.entity == *seed));
    }
    // Every considered neighbor was judged through a capsule.
    assert_eq!(expanded.plan.capsules.len(), trace.considered);
    assert_eq!(expanded.plan.capsules.len(), expanded.plan.decisions.len());
    let neighbor = |c: &Capsule| c.question == Question::NeighborValue;
    assert!(expanded.plan.capsules.iter().all(neighbor));

    assert!(expanded.plan.items.iter().any(|i| is_expanded(&i.reason)));
    let mut one = shallow.clone();
    one.select.expansion.max_nodes = 1;
    let bounded = run(&view, &source, &req, &one, None);
    assert_eq!(bounded.plan.expansion.considered, 1);
    assert_eq!(
        bounded.plan.expansion.stopped_by,
        Some(ExpansionBound::Nodes)
    );
    // Expanded items share the budget: a tight budget still holds.
    let tight = run(
        &view,
        &source,
        &ask(&req.query.text, &[], 120),
        &baseline(),
        None,
    );
    assert_source_backed(&tight.bundle, &source);
}

/// A non-heuristic judge: tests are very relevant, everything else as the
/// heuristic, all with a high reported confidence.
struct LikesTests;

impl DecisionProvider for LikesTests {
    fn identity(&self) -> ProviderIdentity {
        ProviderIdentity {
            name: "likes-tests".into(),
            version: "1".into(),
        }
    }
    fn supports(&self, _: Question) -> bool {
        true
    }
    fn judge(&mut self, capsules: &[Capsule]) -> Result<Vec<Judgment>, ProviderError> {
        Ok(capsules
            .iter()
            .map(|c| {
                let value = if c.test { 1.0 } else { Heuristic::value(c) };
                let confidence = Some(0.9);
                Judgment::of(c, Verdict::Value { value, confidence })
            })
            .collect())
    }
}

#[test]
fn recorded_judgments_replay_to_the_same_plan_and_bundle() {
    let (source, view) = fixture();
    let req = ask("refresh the auth token after it expires", &[], 300);
    let original = run(&view, &source, &req, &baseline(), Some(&mut LikesTests));
    assert!(!original.judgments.is_empty());
    let mut replay = Replay::new(
        original.provider.clone().unwrap(),
        &original.judgments,
        &original.failures,
    );
    let replayed = run(&view, &source, &req, &baseline(), Some(&mut replay));
    assert_eq!(replayed.plan, original.plan);
    assert_eq!(replayed.bundle, original.bundle);
    // The judge changed the outcome relative to the heuristic alone.
    let offline = run(&view, &source, &req, &baseline(), None);
    assert_ne!(offline.bundle.payload(), original.bundle.payload());
}

#[test]
fn failing_abstaining_or_absent_judges_cannot_break_context_construction() {
    let (source, view) = fixture();
    let req = ask("where failed HTTP requests are retried", &[], 800);
    let offline = run(&view, &source, &req, &baseline(), None);
    let identity = ProviderIdentity {
        name: "dead".into(),
        version: "1".into(),
    };
    let mut down = Replay::new(identity.clone(), &[], &[]);
    down.fail = Some(ProviderError::Timeout);
    // Abstains on every relevance and neighbor question the offline run asked.
    let abstentions: Vec<Judgment> = offline
        .capsules
        .iter()
        .chain(&offline.plan.capsules)
        .map(|c| Judgment::of(c, Verdict::Abstain))
        .collect();
    let mut abstains = Replay::new(identity, &abstentions, &[]);
    let jev_config = JevConfig {
        model: MODEL.into(),
        deadline: std::time::Duration::from_secs(1),
        max_requests: 1000,
        max_input_tokens: 10_000_000,
        retries: 0,
        disclosure: Disclosure::Metadata,
    };
    let mut jev = Jev::new(jev_config, JevReplay::default()).unwrap();
    for provider in [
        &mut down as &mut dyn DecisionProvider,
        &mut abstains,
        &mut jev,
    ] {
        let judged = run(&view, &source, &req, &baseline(), Some(provider));
        assert_eq!(judged.bundle.payload(), offline.bundle.payload());
        assert!(judged.decisions.iter().all(|d| d.fallback.is_some()));
        let fallback = |d: &oxide_kernel::pack::Degradation| format!("{d:?}").contains("Fallback");
        assert!(judged.bundle.degraded.iter().any(fallback));
    }
    assert_eq!(jev.stats.answered, 0);

    // A recorded failing run replays exactly, fallback reasons included.
    let failed = run(&view, &source, &req, &baseline(), Some(&mut down));
    assert!(!failed.failures.is_empty() && failed.judgments.is_empty());
    let mut again = Replay::new(failed.provider.clone().unwrap(), &[], &failed.failures);
    let replayed = run(&view, &source, &req, &baseline(), Some(&mut again));
    assert_eq!(
        (replayed.plan, replayed.bundle),
        (failed.plan, failed.bundle)
    );
    assert!(jev.stats.requests > 0);
}

#[test]
fn capsules_render_one_label_free_canonical_record() {
    let (source, view) = fixture();
    let req = ask("TTLCache", &[], 512);
    let first = run(&view, &source, &req, &baseline(), None);
    let mut expected = vec![
        "capsule_version",
        "entity",
        "provenance_truncated",
        "question",
        "relations",
        "relations_truncated",
        "retrieval",
        "route",
        "snapshot",
        "snippet",
        "subject",
        "task",
    ];
    expected.sort();
    for capsule in first.capsules.iter().chain(&first.plan.capsules) {
        let record: serde_json::Value = serde_json::from_str(&capsule.render(false)).unwrap();
        let keys: Vec<&str> = record
            .as_object()
            .unwrap()
            .keys()
            .map(String::as_str)
            .collect();
        assert_eq!(keys, expected, "no label, outcome or policy field");
        // Inference input (the JEV state with source disclosed) is the
        // dataset record.
        let body: serde_json::Value =
            serde_json::from_str(&request(capsule, MODEL, Disclosure::Source)).unwrap();
        assert_eq!(body["state"], record);
    }
    let again = run(&view, &source, &req, &baseline(), None);
    let digests = |r: &ContextRun| r.capsules.iter().map(Capsule::digest).collect::<Vec<_>>();
    assert_eq!(digests(&first), digests(&again));
}
