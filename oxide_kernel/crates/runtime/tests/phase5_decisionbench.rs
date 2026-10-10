//! Phase 5 DecisionBench on the retained Python fixture (docs/phase-5.md),
//! offline, part of `cargo test`:
//!
//! - the fixture's DecisionBench records, metrics and manifest reproduce
//!   byte for byte from `docs/phase-5/decisionbench-v1/fixture/`
//!   (`OXIDE_FREEZE_PHASE5=1` writes them); config C replays recorded live
//!   JEV exchanges from `fixture/jev/`, never the network;
//! - the frozen task and split manifests have no repository leakage;
//! - every judge failure mode falls back observably and deterministically.
//!
//! `OXIDE_JEV_LIVE=1` (with `TYPESAFE_API_KEY`) records missing exchanges
//! live, within the authorized caps of `common::phase5`; a changed result
//! is still refused unless `OXIDE_FREEZE_PHASE5=1` writes it.

mod common;

use std::collections::{BTreeMap, BTreeSet};
use std::time::Duration;

use common::phase5::{
    self, Tape, TaskInput, TaskOutput, composition, evaluate, jev_config, judge_branches, run_task,
};
use common::{repo_root, tasks};
use oxide_kernel::capsule::{Capsule, Question};
use oxide_kernel::context::{ContextConfig, ContextRequest, ContextRun, baseline, build_context};
use oxide_kernel::decision::{
    DecisionPolicy, DecisionProvider, Judgment, ProviderError, ProviderIdentity, Verdict,
};
use oxide_kernel::id::RepoId;
use oxide_kernel::pack::{Degradation, counter};
use oxide_kernel::query::{ContextBudget, Query, QueryContext};
use oxide_kernel::retrieve::ChannelState;
use oxide_kernel::source::SourceCapture;
use oxide_kernel::store::{KnowledgeStore, MemoryStore, MemoryView, ReadView};
use oxide_runtime::capture::{Scope, capture};
use oxide_runtime::decisionbench::{self as db, Gold, Split};
use oxide_runtime::derivation::derive;
use oxide_runtime::jev::{self, Http, Jev, JevConfig, MODEL, Transport, TransportError};
use oxide_runtime::storage::LadybugStore;
use serde_json::{Value, json};

const REPOSITORY: &str = "fixture/py_repo";
const GOLD_SOURCE: &str = "fixtures/benchmark.json + fixtures/structural_benchmark.json \
     (relevant symbols hand-written by OXIDE maintainers, historical)";

fn dir() -> std::path::PathBuf {
    repo_root().join("docs/phase-5/decisionbench-v1")
}

fn live() -> Option<Http> {
    (std::env::var("OXIDE_JEV_LIVE").as_deref() == Ok("1"))
        .then(|| Http::from_env().expect("OXIDE_JEV_LIVE=1 needs TYPESAFE_API_KEY"))
}

/// Validity of every recorded live exchange under `dir`.
pub fn jev_dir_validity(dir: &std::path::Path) -> Value {
    let sessions = phase5::sessions(dir);
    if sessions.is_empty() {
        return json!("no recorded live exchanges");
    }
    let mut out = Vec::new();
    for path in sessions {
        let v = jev::validity(&std::fs::read_to_string(&path).unwrap(), MODEL).unwrap();
        out.push(json!({
            "session": path.file_name().unwrap().to_string_lossy(),
            "exchanges": v.exchanges, "valid": v.valid, "malformed": v.malformed,
            "failed": v.failed, "repeated": v.repeated, "identical": v.identical,
            "models": v.models,
        }));
    }
    json!(out)
}

#[test]
fn fixture_decisionbench_reproduces() {
    let source = capture(&repo_root().join("fixtures/py_repo"), &Scope::default()).unwrap();
    // The Phase 3/4 baselines' RepoId: the same snapshot and derivation.
    let repo = RepoId::new("phase3-eval").unwrap();
    let (manifest, batch, components) = derive(&repo, &source).unwrap();
    let key = manifest.key.clone();
    let store_dir = std::env::temp_dir().join(format!("oxide-phase5-eval-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&store_dir);
    let mut store = LadybugStore::new(&store_dir).unwrap();
    store.begin(manifest).unwrap();
    store.retain_derivation(&key, &components).unwrap();
    store.retain_source(&source).unwrap();
    store.write(&key, batch).unwrap();
    store.publish(&key).unwrap();
    let view = store.open(&key).unwrap();

    let out = dir().join("fixture");
    let jev_dir = out.join("jev");
    let live = live();
    let mut judge = Jev::new(jev_config(), Tape::load(&[&jev_dir], live)).unwrap();
    let fixture_tasks = tasks(&view);
    let mut outputs: Vec<TaskOutput> = Vec::new();
    let mut queries = BTreeMap::new();
    for task in &fixture_tasks {
        let input = TaskInput {
            id: &task.id,
            repository: REPOSITORY,
            split: Split::Dev,
            query: &task.text,
            symbols: task.symbols.clone(),
            gold: Gold {
                verified: task.gold.iter().cloned().collect(),
                unverified: BTreeSet::new(),
                source: GOLD_SOURCE.into(),
            },
        };
        queries.insert(task.id.clone(), (REPOSITORY.to_owned(), task.text.clone()));
        outputs.push(run_task(&view, &view, &input, Some(&mut judge)));
    }
    let branches = judge_branches(&outputs, judge.transport_mut());
    judge.transport_mut().save(&jev_dir);

    let records: Vec<&db::Record> = outputs.iter().flat_map(|t| &t.records).collect();
    let owned: Vec<db::Record> = records.iter().map(|r| (*r).clone()).collect();
    let splits = BTreeMap::from([(REPOSITORY.to_owned(), Split::Dev)]);
    let leakage = db::leakage(&owned, &splits, &queries);
    assert!(leakage.errors.is_empty(), "{:?}", leakage.errors);
    let all: Vec<&TaskOutput> = outputs.iter().collect();
    let metrics = evaluate(&all, &branches);
    let manifest = json!({
        "dataset": db::FORMAT,
        "repository": REPOSITORY,
        "snapshot": {"repo": key.repo.as_str(), "snapshot": key.snapshot.as_str(), "derivation": key.derivation.as_str()},
        "split": "dev (the fixture is the CI regression set, never held out)",
        "gold": GOLD_SOURCE,
        "label_rules": ["gold-membership-v1 (candidates, neighbors)", "subtree-contains-gold-v1 (regions)"],
        "versions": {"capsule": oxide_kernel::capsule::CAPSULE_VERSION, "heuristic": "heuristic-evidence/1",
                     "retrieval": oxide_kernel::retrieve::RETRIEVAL_VERSION, "router": oxide_kernel::route::ROUTER_VERSION,
                     "selector": oxide_kernel::select::SELECTOR_VERSION, "jev_model": MODEL},
        "tasks": fixture_tasks.len(),
        "records": records.len(),
        "composition": composition(&records),
        "leakage": {"errors": leakage.errors, "near_duplicates_within_split": leakage.near_duplicates_within_split,
                    "shared_capsules": leakage.shared_capsules},
        "jev_recordings": jev_dir_validity(&jev_dir),
        "claims": "a 65-entity regression floor with maintainer-written gold; not a generalization benchmark",
    });
    let text = |v: &Value| serde_json::to_string_pretty(v).unwrap() + "\n";
    let jsonl: String = records
        .iter()
        .map(|r| serde_json::to_string(r).unwrap() + "\n")
        .collect();
    // What is written parses back to the same records.
    let parsed: Vec<db::Record> = jsonl
        .lines()
        .map(|l| serde_json::from_str(l).unwrap())
        .collect();
    assert_eq!(parsed, owned);
    let artifacts = [
        ("records.jsonl", jsonl),
        ("metrics.json", text(&metrics)),
        ("manifest.json", text(&manifest)),
    ];
    for (file, text) in artifacts {
        let path = out.join(file);
        if std::env::var_os("OXIDE_FREEZE_PHASE5").is_some() {
            std::fs::create_dir_all(&out).unwrap();
            std::fs::write(&path, text).unwrap();
        } else {
            let frozen = std::fs::read_to_string(&path).unwrap_or_default();
            assert!(
                frozen == text,
                "{} no longer reproduces; a changed dataset needs a new version",
                path.display()
            );
        }
    }
    drop(view);
    drop(store);
    let _ = std::fs::remove_dir_all(&store_dir);
}

#[test]
fn frozen_task_and_split_manifests_do_not_leak() {
    let read = |f: &str| -> Value {
        serde_json::from_str(&std::fs::read_to_string(dir().join(f)).unwrap()).unwrap()
    };
    for (file, sha) in [
        ("contextbench-tasks.json", phase5::TASKS_SHA256),
        ("splits.json", phase5::SPLITS_SHA256),
        ("../preregistration.md", phase5::PREREGISTRATION_SHA256),
    ] {
        let bytes = std::fs::read(dir().join(file)).unwrap();
        assert_eq!(
            db::sha256_hex(&bytes),
            sha,
            "{file} changed after it was frozen"
        );
    }
    let tasks = read("contextbench-tasks.json");
    let splits = read("splits.json");
    assert_eq!(tasks["format"], "oxide-decisionbench-tasks-v1");
    assert_eq!(splits["format"], "oxide-decisionbench-splits-v1");
    let splits: BTreeMap<String, Split> =
        serde_json::from_value(splits["repositories"].clone()).unwrap();
    assert_eq!(splits.get(REPOSITORY), Some(&Split::Dev));
    let mut queries = BTreeMap::new();
    for t in tasks["tasks"].as_array().unwrap() {
        let id = t["id"].as_str().unwrap().to_owned();
        let repo = t["repo"].as_str().unwrap().to_owned();
        let query = t["query"].as_str().unwrap().to_owned();
        assert!(
            !t["gold"].as_array().unwrap().is_empty(),
            "{id} has no gold"
        );
        assert!(queries.insert(id, (repo, query)).is_none());
    }
    let leakage = db::leakage(&[], &splits, &queries);
    assert!(leakage.errors.is_empty(), "{:?}", leakage.errors);
    // The held-out splits are disjoint by repository and each nonempty.
    let mut by_split: BTreeMap<Split, usize> = BTreeMap::new();
    for (repo, _) in queries.values() {
        *by_split.entry(splits[repo]).or_default() += 1;
    }
    for split in [Split::Dev, Split::Calibration, Split::Test] {
        assert!(by_split.get(&split).is_some_and(|n| *n > 0), "{split:?}");
    }
    assert_eq!(by_split.get(&Split::TrainReserved), None);

    // A record placed in another split than its repository is an error.
    let wrong = BTreeMap::from([("r".to_owned(), Split::Dev)]);
    let r = sample_record(Split::Test);
    let tasks = BTreeMap::from([("t".to_owned(), ("r".to_owned(), "q".to_owned()))]);
    assert!(!db::leakage(&[r], &wrong, &tasks).errors.is_empty());
}

fn sample_record(split: Split) -> db::Record {
    let (source, view) = memory_fixture();
    let run = build_context(&view, &source, &ask(1024), &baseline(), None).unwrap();
    let meta = db::TaskMeta {
        repository: "r",
        task: "t",
        split,
        query: "q",
    };
    let mut records = db::records(&meta, &Gold::default(), &run, &view, phase5::nav()).unwrap();
    records.remove(0)
}

#[test]
fn records_reject_tampering_and_teacher_only_labels() {
    let good = sample_record(Split::Dev);
    db::validate(&good).unwrap();
    let mut edited = good.clone();
    edited.capsule = edited.capsule.replace("\"question\"", "\"question\" ");
    assert!(
        db::validate(&edited).is_err(),
        "bytes no longer match the digest"
    );
    let mut labeled = good.clone();
    labeled.capsule = labeled.capsule.replacen('{', "{\"gold\":true,", 1);
    labeled.capsule_digest = oxide_kernel::digest::digest(labeled.capsule.as_bytes())
        .as_str()
        .to_owned();
    assert!(
        db::validate(&labeled).is_err(),
        "a label inside the capsule"
    );
    let mut teacher = good.clone();
    teacher.label.value = db::LabelValue::Relevant;
    teacher.label.annotations = vec![db::Annotation {
        source: MODEL.into(),
        kind: db::AnnotationKind::Teacher,
        value: db::LabelValue::Relevant,
        note: String::new(),
    }];
    assert!(
        db::validate(&teacher).is_err(),
        "a teacher cannot decide a label"
    );
    teacher.label.value = db::LabelValue::Unlabeled;
    db::validate(&teacher).unwrap();
}

// --- deterministic fallback (Phase 5 § 7) --------------------------------

fn memory_fixture() -> (SourceCapture, MemoryView) {
    let source = capture(&repo_root().join("fixtures/py_repo"), &Scope::default()).unwrap();
    let repo = RepoId::new("phase5-fallback").unwrap();
    let (manifest, batch, _) = derive(&repo, &source).unwrap();
    let key = manifest.key.clone();
    let mut store = MemoryStore::default();
    store.begin(manifest).unwrap();
    store.write(&key, batch).unwrap();
    store.publish(&key).unwrap();
    (source, store.open(&key).unwrap())
}

fn ask(tokens: u32) -> ContextRequest {
    ContextRequest {
        query: Query {
            text: "retry policy for failed http requests".into(),
        },
        context: QueryContext::default(),
        budget: ContextBudget {
            tokens,
            counter: counter(),
        },
        semantic: ChannelState::Unconfigured,
    }
}

/// A provider answering every batch by a fixed rule.
struct Fake(fn(&[Capsule]) -> Result<Vec<Judgment>, ProviderError>);

impl DecisionProvider for Fake {
    fn identity(&self) -> ProviderIdentity {
        ProviderIdentity {
            name: "fake".into(),
            version: "1".into(),
        }
    }
    fn supports(&self, _: Question) -> bool {
        true
    }
    fn judge(&mut self, capsules: &[Capsule]) -> Result<Vec<Judgment>, ProviderError> {
        (self.0)(capsules)
    }
}

fn value(c: &Capsule, value: f64, confidence: Option<f64>) -> Judgment {
    Judgment::of(c, Verdict::Value { value, confidence })
}

/// Scripted JEV transport: every call gets the same outcome after `.1`.
struct Always(Result<String, TransportError>, Duration);

impl Transport for Always {
    fn post(&mut self, _: &str, _: Duration) -> Result<String, TransportError> {
        std::thread::sleep(self.1);
        self.0.clone()
    }
}

fn fallback_reasons(run: &ContextRun) -> BTreeSet<String> {
    run.bundle
        .degraded
        .iter()
        .filter_map(|d| match d {
            Degradation::Fallback { reason, .. } => Some(format!("{reason:?}")),
            _ => None,
        })
        .collect()
}

type Make = Box<dyn Fn() -> Box<dyn DecisionProvider>>;

fn scripted(outcome: Result<String, TransportError>, config: JevConfig, delay: Duration) -> Make {
    Box::new(move || Box::new(Jev::new(config.clone(), Always(outcome.clone(), delay)).unwrap()))
}

fn fake(rule: fn(&[Capsule]) -> Result<Vec<Judgment>, ProviderError>) -> Make {
    Box::new(move || Box::new(Fake(rule)))
}

#[test]
fn every_judge_failure_falls_back_observably_and_deterministically() {
    let (source, view) = memory_fixture();
    let snapshot = view.snapshot().clone();
    let request = ask(1024);
    let offline = build_context(&view, &source, &request, &baseline(), None).unwrap();
    assert!(fallback_reasons(&offline).is_empty());
    let payload = offline.bundle.payload();

    let with = |min_confidence, confidence_calibrated, judgments| ContextConfig {
        decision: DecisionPolicy {
            min_confidence,
            confidence_calibrated,
        },
        judgments,
        ..baseline()
    };
    let dead_port = {
        let l = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        l.local_addr().unwrap().port()
    };
    let other_model = r#"{"model":"jev-1.12.0","answers":{"judgment":{"type":"score","score":1,"confidence":0.5,"probabilities":{"0":0,"1":1,"2":0}}}}"#;
    let other_type = r#"{"model":"jev-1.13.0","answers":{"judgment":{"type":"noul","noul":0.5}}}"#;
    let now = Duration::ZERO;
    let cases: Vec<(&str, Make, ContextConfig, &str)> = vec![
        (
            "unavailable service",
            Box::new(move || {
                let url = format!("http://127.0.0.1:{dead_port}/v1/systemone");
                let http = Http::new(&url, "k".into()).unwrap();
                Box::new(Jev::new(jev_config(), http).unwrap())
            }),
            baseline(),
            "ProviderFailed(Unavailable)",
        ),
        (
            "timeout",
            scripted(
                Err(TransportError::Timeout),
                JevConfig {
                    deadline: Duration::from_millis(1),
                    ..jev_config()
                },
                Duration::from_millis(2),
            ),
            baseline(),
            "ProviderFailed(Timeout)",
        ),
        (
            "malformed response",
            scripted(Ok("<html>".into()), jev_config(), now),
            baseline(),
            "ProviderFailed(Malformed)",
        ),
        (
            "invalid schema: other model",
            scripted(Ok(other_model.into()), jev_config(), now),
            baseline(),
            "ProviderFailed(Malformed)",
        ),
        (
            "invalid schema: other answer type",
            scripted(Ok(other_type.into()), jev_config(), now),
            baseline(),
            "ProviderFailed(Malformed)",
        ),
        (
            "request limit",
            scripted(
                Ok(String::new()),
                JevConfig {
                    max_requests: 0,
                    ..jev_config()
                },
                now,
            ),
            baseline(),
            "ProviderFailed(Unavailable)",
        ),
        (
            "cost limit",
            scripted(
                Ok(String::new()),
                JevConfig {
                    max_input_tokens: 0,
                    ..jev_config()
                },
                now,
            ),
            baseline(),
            "ProviderFailed(Unavailable)",
        ),
        (
            "shared judgment allowance",
            fake(|c| Ok(c.iter().map(|c| value(c, 1.0, None)).collect())),
            with(None, false, 0),
            "AllowanceExhausted",
        ),
        (
            "mismatched capsule digest",
            fake(|c| {
                Ok(c.iter()
                    .map(|c| {
                        let mut other = c.clone();
                        other.query.push('!');
                        Judgment {
                            capsule_digest: other.digest(),
                            ..value(c, 1.0, None)
                        }
                    })
                    .collect())
            }),
            baseline(),
            "Invalid",
        ),
        (
            "duplicate subject",
            fake(|c| {
                Ok(c.iter()
                    .flat_map(|c| [value(c, 1.0, None), value(c, 0.0, None)])
                    .collect())
            }),
            baseline(),
            "Duplicate",
        ),
        (
            "missing subject",
            fake(|_| Ok(Vec::new())),
            baseline(),
            "Missing",
        ),
        (
            "abstention",
            fake(|c| {
                Ok(c.iter()
                    .map(|c| Judgment::of(c, Verdict::Abstain))
                    .collect())
            }),
            baseline(),
            "Abstained",
        ),
        (
            "low calibrated confidence",
            fake(|c| Ok(c.iter().map(|c| value(c, 1.0, Some(0.1))).collect())),
            with(Some(0.5), true, 256),
            "LowConfidence",
        ),
        (
            "uncalibrated confidence",
            fake(|c| Ok(c.iter().map(|c| value(c, 1.0, Some(0.99))).collect())),
            with(Some(0.5), false, 256),
            "Uncalibrated",
        ),
    ];
    for (name, make, config, reason) in cases {
        let runs: Vec<ContextRun> = (0..2)
            .map(|_| {
                let mut provider = make();
                build_context(&view, &source, &request, &config, Some(provider.as_mut())).unwrap()
            })
            .collect();
        assert_eq!(runs[0], runs[1], "{name}: not deterministic");
        assert_eq!(
            fallback_reasons(&runs[0]),
            BTreeSet::from([reason.to_owned()]),
            "{name}"
        );
        // Every subject fell back to the heuristic, so the context is the
        // offline one, byte for byte.
        assert_eq!(runs[0].bundle.payload(), payload, "{name}");
        let plan = |r: &ContextRun| {
            let items = r.plan.items.iter();
            items
                .map(|i| (i.entity.clone(), i.view, i.estimated_tokens))
                .collect::<Vec<_>>()
        };
        assert_eq!(plan(&runs[0]), plan(&offline), "{name}");
    }
    // Nothing a judge did touched the published knowledge.
    assert_eq!(view.snapshot(), &snapshot);
    let again = build_context(&view, &source, &request, &baseline(), None).unwrap();
    assert_eq!(again, offline);
}
