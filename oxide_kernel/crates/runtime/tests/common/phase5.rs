//! Phase 5 DecisionBench harness shared by the CI fixture test
//! (`phase5_decisionbench.rs`) and the opt-in ContextBench run
//! (`phase5_contextbench.rs`). Protocol: `docs/phase-5/preregistration.md`.
//!
//! Every task runs the same pinned snapshot, query, routed universe,
//! capsules, budgets and labels under each configuration:
//!
//! - `A-constant`: 0.5 for every subject (no judgment);
//! - `B-heuristic`: the offline default, `heuristic-evidence/1`;
//! - `B-no-expansion`, `B-no-routing`: the Phase 4 diagnostic ablations;
//! - `C-jev`: JEV judges candidates and neighbors; branch questions are
//!   unsupported, so routing (and with it the candidate universe and its
//!   capsules) is exactly B's and the comparison is paired.
//!
//! JEV goes through a [`Tape`]: recorded exchanges first, then (only when
//! explicitly enabled) the live service under hard global request and
//! token caps. A rerun with the recordings needs no network.

use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

use oxide_kernel::capsule::{Capsule, Question};
use oxide_kernel::context::{ContextRequest, ContextRun, baseline, build_context};
use oxide_kernel::decision::{
    DecisionProvider, Fallback, Heuristic, Judgment, ProviderError, ProviderIdentity,
};
use oxide_kernel::id::{EntityId, RepoId, SnapshotKey};
use oxide_kernel::knowledge::Entity;
use oxide_kernel::pack::counter;
use oxide_kernel::query::{ContextBudget, Query, QueryContext};
use oxide_kernel::retrieve::ChannelState;
use oxide_kernel::source::{SourceCapture, SourceProvider};
use oxide_kernel::store::{KnowledgeStore, ReadView};
use oxide_kernel::tree::NavLimits;
use oxide_runtime::capture::{Scope, capture};
use oxide_runtime::decisionbench::{self as db, Gold, LabelValue, Record, Split, SubjectKind};
use oxide_runtime::derivation::derive;
use oxide_runtime::jev::{
    self, Disclosure, Http, Jev, JevConfig, MODEL, Recorded, Recording, Replay, Transport,
    TransportError,
};
use oxide_runtime::storage::LadybugStore;
use serde_json::{Value, json};

use super::{Constant, gold_sources, mean, metrics};

pub const BUDGETS: [u32; 3] = [256, 1024, 4096];
/// SHA-256 of the frozen protocol files (2026-10-09, before any result;
/// the preregistration with Amendment 1).
/// The test split unseals only against the preregistration's exact bytes;
/// tasks and splits cannot change silently.
pub const PREREGISTRATION_SHA256: &str =
    "42fca4473ba84cae85019d39bbca6e8e95d205107bd2c65451ea090d56607c0a";
pub const SPLITS_SHA256: &str = "2cae5b510eeb38dbbf7ff23dea5017dd71a2cee27a19a3789c3c4999187eb6ec";
pub const TASKS_SHA256: &str = "ffba9b784eb211fc5fdd21225aaab4ba8723832cc089eb4435fbc2868c3d5a1c";
/// Hard caps on live JEV use in one process, set by the user's
/// authorization (2026-10-09): 6,000 requests, 20M input tokens. Earlier
/// recorded sessions count against them.
pub const LIVE_REQUESTS: usize = 6000;
pub const LIVE_TOKENS: u64 = 20_000_000;
/// Region records judged by JEV per task (smallest digests first), and
/// dev relevance capsules re-sent for repeatability.
pub const BRANCH_SAMPLE: usize = 8;
pub const REPEAT_SAMPLE: usize = 100;
pub const REPEATS: usize = 2;
const BOOTSTRAP: (usize, u64) = (10_000, 0x5eed_0005);

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Judge {
    Constant,
    Offline,
    Jev,
}

pub fn configs() -> Vec<(&'static str, oxide_kernel::context::ContextConfig, Judge)> {
    let b = baseline();
    let mut no_expansion = b.clone();
    no_expansion.select.expansion.max_depth = 0;
    let mut no_routing = b.clone();
    no_routing.route_limits.max_depth = 0;
    vec![
        ("A-constant", b.clone(), Judge::Constant),
        ("B-heuristic", b.clone(), Judge::Offline),
        ("B-no-expansion", no_expansion, Judge::Offline),
        ("B-no-routing", no_routing, Judge::Offline),
        ("C-jev", b, Judge::Jev),
    ]
}

/// The router's region view limits, to rebuild judged branch capsules.
pub fn nav() -> NavLimits {
    let l = baseline().route_limits;
    NavLimits {
        children: l.max_fanout,
        cross_edges: l.max_fanout,
    }
}

/// JEV for candidate and neighbor questions only: branch capsules fall
/// back (`Unsupported`), so routing stays the heuristic's.
pub struct CandidatesOnly<'a, P: DecisionProvider>(pub &'a mut P);

impl<P: DecisionProvider> DecisionProvider for CandidatesOnly<'_, P> {
    fn identity(&self) -> ProviderIdentity {
        self.0.identity()
    }
    fn supports(&self, question: Question) -> bool {
        question != Question::BranchValue && self.0.supports(question)
    }
    fn judge(&mut self, capsules: &[Capsule]) -> Result<Vec<Judgment>, ProviderError> {
        self.0.judge(capsules)
    }
}

/// Recorded exchanges first; live calls only when `live` is set, within
/// global caps that also count every earlier recorded exchange.
pub struct Tape {
    pub replay: BTreeMap<String, Recorded>,
    pub live: Option<Recording<Http>>,
    pub live_requests: usize,
    pub live_tokens: u64,
}

fn usage(body: &str) -> Option<u64> {
    let v: Value = serde_json::from_str(body).ok()?;
    v["usage"]["input_tokens"].as_u64()
}

pub fn sessions(dir: &Path) -> Vec<PathBuf> {
    let mut out: Vec<PathBuf> = std::fs::read_dir(dir)
        .map(|d| d.filter_map(|e| Some(e.ok()?.path())).collect())
        .unwrap_or_default();
    out.retain(|p| {
        let name = p.file_name().unwrap().to_string_lossy();
        name.starts_with("session-") && name.ends_with(".json")
    });
    out.sort();
    out
}

impl Tape {
    /// Loads every `session-*.json` under `dirs`, in order: their answers
    /// replay and their exchanges count against the live caps.
    pub fn load(dirs: &[&Path], live: Option<Http>) -> Self {
        let mut tape = Tape {
            replay: BTreeMap::new(),
            live: live.map(|inner| Recording {
                inner,
                exchanges: Vec::new(),
            }),
            live_requests: 0,
            live_tokens: 0,
        };
        for path in dirs.iter().flat_map(|d| sessions(d)) {
            let text = std::fs::read_to_string(&path).unwrap();
            let v: Value = serde_json::from_str(&text).unwrap();
            for e in v["exchanges"].as_array().unwrap() {
                tape.live_requests += 1;
                // As live_post counts: reported usage, else the body's
                // length (a 2xx response without usage).
                let reported = e["response"]["usage"]["input_tokens"].as_u64();
                let billed = e["response"].is_object() || e["response_text"].is_string();
                let body = e["request"].to_string().len() as u64;
                tape.live_tokens += reported.unwrap_or(if billed { body } else { 0 });
            }
            for (key, recorded) in Replay::from_json(&text).unwrap().exchanges {
                Replay::keep(&mut tape.replay, key, recorded);
            }
        }
        tape
    }

    fn live_post(&mut self, body: &str, timeout: Duration) -> Result<String, TransportError> {
        let Some(live) = &mut self.live else {
            return Err(TransportError::Unavailable("not recorded".into()));
        };
        if self.live_requests >= LIVE_REQUESTS || self.live_tokens + body.len() as u64 > LIVE_TOKENS
        {
            return Err(TransportError::Unavailable("live budget exhausted".into()));
        }
        self.live_requests += 1;
        let answer = live.post(body, timeout);
        if let Ok(text) = &answer {
            self.live_tokens += usage(text).unwrap_or(body.len() as u64);
        }
        answer
    }

    /// Re-sends a body live, bypassing the replay (repeatability). `None`
    /// offline.
    pub fn repeat(&mut self, body: &str) -> Option<Result<String, TransportError>> {
        self.live.as_ref()?;
        Some(self.live_post(body, Duration::from_secs(60)))
    }

    /// Writes this process's live exchanges as a new session file.
    pub fn save(&self, dir: &Path) {
        let Some(live) = &self.live else { return };
        if live.exchanges.is_empty() {
            return;
        }
        std::fs::create_dir_all(dir).unwrap();
        // Next free index: never overwrite a recorded (paid) session.
        let n = sessions(dir)
            .iter()
            .filter_map(|p| {
                let name = p.file_name()?.to_str()?;
                name.strip_prefix("session-")?
                    .strip_suffix(".json")?
                    .parse::<usize>()
                    .ok()
            })
            .max()
            .map_or(0, |m| m + 1);
        let path = dir.join(format!("session-{n:03}.json"));
        std::fs::write(path, live.to_json(MODEL)).unwrap();
    }
}

impl Transport for Tape {
    fn post(&mut self, body: &str, timeout: Duration) -> Result<String, TransportError> {
        let key = db::sha256_hex(body.as_bytes());
        match self.replay.get(&key) {
            Some(Recorded::Response(r)) => return Ok(r.clone()),
            Some(Recorded::Failed(e)) if self.live.is_none() => return Err(e.clone()),
            _ => {}
        }
        let answer = self.live_post(body, timeout);
        if self.live.is_some() {
            let recorded = match &answer {
                Ok(r) => Recorded::Response(r.clone()),
                Err(e) => Recorded::Failed(e.clone()),
            };
            Replay::keep(&mut self.replay, key, recorded);
        }
        answer
    }
}

/// The adapter's own caps would also count answers the [`Tape`] serves
/// from its recordings (every budget rerun of a cached capsule), so they
/// are lifted here: the tape's global caps bound the calls that reach the
/// service, which is what the authorization limits.
pub fn jev_config() -> JevConfig {
    JevConfig {
        model: MODEL.into(),
        deadline: Duration::from_secs(600),
        max_requests: usize::MAX,
        max_input_tokens: u64::MAX,
        retries: 2,
        disclosure: Disclosure::Source,
        concurrency: 1,
    }
}

/// One task of the bench.
pub struct TaskInput<'a> {
    pub id: &'a str,
    pub repository: &'a str,
    pub split: Split,
    pub query: &'a str,
    pub symbols: Vec<String>,
    pub gold: Gold,
}

pub struct TaskOutput {
    pub id: String,
    pub split: Split,
    pub records: Vec<Record>,
    /// `config@budget` -> context metrics (Phase 4 definitions plus the
    /// run's fallbacks and latency).
    pub runs: BTreeMap<String, Value>,
    /// C's relevance and neighbor decisions by capsule digest:
    /// (value, provider confidence, fallback reason).
    pub jev: BTreeMap<String, (f64, Option<f64>, Option<String>)>,
    /// Routing work and gold observation of the baseline route.
    pub routing: Value,
}

pub fn request(input: &TaskInput<'_>, tokens: u32) -> ContextRequest {
    ContextRequest {
        query: Query {
            text: input.query.into(),
        },
        context: QueryContext {
            symbols: input.symbols.clone(),
            ..Default::default()
        },
        budget: ContextBudget {
            tokens,
            counter: counter(),
        },
        semantic: ChannelState::Unconfigured,
    }
}

pub fn fallbacks(run: &ContextRun) -> Value {
    let mut counts: BTreeMap<String, usize> = BTreeMap::new();
    let all = run
        .route
        .trace
        .decisions
        .iter()
        .chain(&run.decisions)
        .chain(&run.plan.decisions);
    for d in all {
        if let Some(f) = d.fallback.filter(|f| *f != Fallback::NoProvider) {
            *counts.entry(format!("{f:?}")).or_default() += 1;
        }
    }
    json!(counts)
}

/// Runs every configuration and budget for one task and builds its
/// records from the heuristic run at 1,024 units.
pub fn run_task(
    view: &impl ReadView,
    source: &dyn SourceProvider,
    input: &TaskInput<'_>,
    mut jev: Option<&mut Jev<Tape>>,
) -> TaskOutput {
    let meta = db::TaskMeta {
        repository: input.repository,
        task: input.id,
        split: input.split,
        query: input.query,
    };
    let mut heuristic = Heuristic;
    let dataset_run = build_context(
        view,
        source,
        &request(input, 1024),
        &baseline(),
        Some(&mut heuristic),
    )
    .unwrap();
    let records = db::records(&meta, &input.gold, &dataset_run, view, nav()).unwrap();
    let gold: Vec<EntityId> = input.gold.verified.iter().cloned().collect();
    let sources = gold_sources(view, &gold);
    let routed: BTreeSet<&EntityId> = dataset_run
        .route
        .candidates
        .iter()
        .map(|c| &c.entity)
        .collect();
    let entries: BTreeSet<&EntityId> = dataset_run
        .candidates
        .entries
        .iter()
        .map(|e| &e.entity)
        .collect();
    let work = dataset_run.route.trace.work;
    let routing = json!({
        "gold_verified": gold.len(),
        "gold_unverified": input.gold.unverified.len(),
        "gold_entry": gold.iter().filter(|g| entries.contains(g)).count(),
        "gold_routed": gold.iter().filter(|g| routed.contains(g)).count(),
        "routed": routed.len(),
        "regions_loaded": work.regions_loaded,
        "edges_examined": work.edges_examined,
        "branch_judgments": work.judgments_requested,
    });

    let mut runs = BTreeMap::new();
    let mut jev_decisions = BTreeMap::new();
    for (label, config, judge) in configs() {
        for tokens in BUDGETS {
            let req = request(input, tokens);
            let mut constant = Constant;
            let started = Instant::now();
            let run = match judge {
                Judge::Constant => build_context(view, source, &req, &config, Some(&mut constant)),
                Judge::Offline => build_context(view, source, &req, &config, None),
                Judge::Jev => {
                    let Some(jev) = jev.as_deref_mut() else {
                        continue;
                    };
                    let mut only = CandidatesOnly(jev);
                    build_context(view, source, &req, &config, Some(&mut only))
                }
            }
            .unwrap();
            let elapsed = started.elapsed().as_secs_f64() * 1e3;
            assert!(run.bundle.used_tokens <= tokens);
            let mut row = metrics(&gold, &sources, &run);
            row["latency_ms"] = json!(elapsed);
            row["fallbacks"] = fallbacks(&run);
            // C's decisions for the records: the 1,024-unit run is the one
            // the records come from.
            if judge == Judge::Jev && tokens == 1024 {
                assert_eq!(
                    run.capsules, dataset_run.capsules,
                    "C must judge B's capsules"
                );
                for d in run.decisions.iter().chain(&run.plan.decisions) {
                    let reason = d.fallback.map(|f| format!("{f:?}"));
                    let entry = (d.value, d.confidence, reason);
                    jev_decisions.insert(d.capsule_digest.as_str().to_owned(), entry);
                }
            }
            runs.insert(format!("{label}@{tokens}"), row);
        }
    }
    TaskOutput {
        id: input.id.into(),
        split: input.split,
        records,
        runs,
        jev: jev_decisions,
        routing,
    }
}

/// JEV branch-value answer for one region record, or why there is none.
pub type BranchAnswer = (Result<(f64, f64), String>, f64);

/// JEV branch-value judgments of a deterministic sample of region records
/// (the [`BRANCH_SAMPLE`] smallest digests per task), outside routing:
/// digest -> (answer, the record's inclusion probability).
pub fn judge_branches(tasks: &[TaskOutput], tape: &mut Tape) -> BTreeMap<String, BranchAnswer> {
    let mut out = BTreeMap::new();
    for task in tasks {
        let mut regions: Vec<&Record> = task
            .records
            .iter()
            .filter(|r| r.subject == SubjectKind::Region)
            .collect();
        let n = regions.len();
        regions.sort_by(|a, b| a.capsule_digest.cmp(&b.capsule_digest));
        for r in regions.into_iter().take(BRANCH_SAMPLE) {
            let body = jev::request_for(&r.capsule, Question::BranchValue, MODEL).unwrap();
            let answer = tape
                .post(&body, Duration::from_secs(60))
                .map_err(|e| format!("{e:?}"))
                .and_then(|b| jev::parse(&b, MODEL).map(|(v, c, _)| (v, c)));
            let p = BRANCH_SAMPLE.min(n) as f64 / n as f64;
            out.insert(r.capsule_digest.clone(), (answer, p));
        }
    }
    out
}

/// Re-sends [`REPEAT_SAMPLE`] dev relevance capsules (smallest digests)
/// [`REPEATS`] more times, live, for repeatability. A no-op offline.
pub fn repeat_dev(tasks: &[TaskOutput], tape: &mut Tape) -> usize {
    let mut dev: Vec<&Record> = tasks
        .iter()
        .filter(|t| t.split == Split::Dev)
        .flat_map(|t| &t.records)
        .filter(|r| r.subject == SubjectKind::Candidate)
        .collect();
    dev.sort_by(|a, b| a.capsule_digest.cmp(&b.capsule_digest));
    let mut sent = 0;
    for r in dev.into_iter().take(REPEAT_SAMPLE) {
        let body = jev::request_for(&r.capsule, Question::Relevance, MODEL).unwrap();
        for _ in 0..REPEATS {
            if tape.repeat(&body).is_some() {
                sent += 1;
            }
        }
    }
    sent
}

fn labeled(r: &Record) -> Option<bool> {
    match r.label.value {
        LabelValue::Relevant => Some(true),
        LabelValue::Irrelevant => Some(false),
        _ => None,
    }
}

pub fn round(x: f64) -> Value {
    json!((x * 1e4).round() / 1e4)
}

fn opt(x: Option<f64>) -> Value {
    x.map_or(Value::Null, round)
}

type Points = BTreeMap<String, Vec<(f64, bool)>>;

/// Discrimination and (when the sample allows) value-as-probability
/// calibration of one scorer over labeled records, pooled and within task.
fn discrimination(points: &Points, floor: f64) -> Value {
    let pooled: Vec<(f64, bool)> = points.values().flatten().copied().collect();
    let pos = pooled.iter().filter(|p| p.1).count();
    let within: Vec<f64> = points.values().filter_map(|p| db::auroc(p, 1)).collect();
    let within_mean =
        (!within.is_empty()).then(|| within.iter().sum::<f64>() / within.len() as f64);
    let (precision, recall) = db::precision_recall(&pooled, floor);
    let sufficient = db::sufficient_for_calibration(&pooled);
    let gated = |x: f64| {
        if sufficient {
            round(x)
        } else {
            json!("insufficient sample")
        }
    };
    json!({
        "n": pooled.len(), "relevant": pos, "irrelevant": pooled.len() - pos,
        "auroc_pooled": opt(db::auroc(&pooled, db::MIN_CLASS)),
        "auroc_within_task_mean": opt(within_mean),
        "tasks_with_both_classes": within.len(),
        "precision_at_floor": opt(precision), "recall_at_floor": opt(recall), "floor": floor,
        "value_brier": gated(db::brier(&pooled)),
        "value_ece": gated(db::ece(&pooled)),
    })
}

fn within_task(points: &Points) -> BTreeMap<String, f64> {
    points
        .iter()
        .filter_map(|(t, p)| Some((t.clone(), db::auroc(p, 1)?)))
        .collect()
}

pub fn paired(a: &BTreeMap<String, f64>, b: &BTreeMap<String, f64>) -> Value {
    let diffs: Vec<f64> = a.iter().filter_map(|(t, x)| Some(x - b.get(t)?)).collect();
    if diffs.is_empty() {
        return json!({"tasks": 0});
    }
    let mean = diffs.iter().sum::<f64>() / diffs.len() as f64;
    let ci = db::bootstrap_mean_ci(&diffs, BOOTSTRAP.0, BOOTSTRAP.1);
    json!({"tasks": diffs.len(), "mean_diff": round(mean),
           "ci95": ci.map(|(lo, hi)| json!([round(lo), round(hi)]))})
}

/// Selective prediction with provider confidence over (confidence,
/// correct) points, correct meaning `value >= 0.5` agrees with the label.
pub fn confidence(points: &[(f64, bool)]) -> Value {
    let sufficient = db::sufficient_for_calibration(points);
    let gated = |x: f64| {
        if sufficient {
            round(x)
        } else {
            json!("insufficient sample")
        }
    };
    let coverage: Vec<Value> = [0.25, 0.5, 0.75, 1.0]
        .iter()
        .map(|c| json!([c, opt(db::accuracy_at_coverage(points, *c))]))
        .collect();
    let bins: Vec<Value> = db::reliability(points, 10)
        .iter()
        .map(|(n, p, y)| json!([n, round(*p), round(*y)]))
        .collect();
    json!({
        "n": points.len(), "correct": points.iter().filter(|p| p.1).count(),
        "accuracy_at_coverage": coverage,
        "aurc": opt(db::aurc(points)),
        "confidence_ece": gated(db::ece(points)),
        "confidence_brier": gated(db::brier(points)),
        "reliability_bins": bins,
    })
}

/// (confidence, correct) points of C's answered relevance judgments.
pub fn jev_confidence_points(tasks: &[&TaskOutput]) -> Vec<(f64, bool)> {
    let mut out = Vec::new();
    for t in tasks {
        for r in t
            .records
            .iter()
            .filter(|r| r.subject == SubjectKind::Candidate)
        {
            if let (Some(y), Some((v, Some(c), None))) = (labeled(r), t.jev.get(&r.capsule_digest))
            {
                out.push((*c, (*v >= 0.5) == y));
            }
        }
    }
    out
}

/// Every metric of one split.
pub fn evaluate(tasks: &[&TaskOutput], branches: &BTreeMap<String, BranchAnswer>) -> Value {
    let floor = baseline().select.include_floor;
    let prune = baseline().route_policy.prune_below;
    let records = || tasks.iter().flat_map(|t| t.records.iter());
    let has_jev = tasks.iter().any(|t| !t.jev.is_empty());

    // Candidate relevance: graph-node records with a decided label.
    let mut by: BTreeMap<&str, Points> = BTreeMap::new();
    let mut answered: Points = BTreeMap::new();
    let (mut uncertain, mut jev_fallback, mut jev_total) = (0, 0, 0);
    for t in tasks {
        for r in t
            .records
            .iter()
            .filter(|r| r.subject == SubjectKind::Candidate)
        {
            let Some(y) = labeled(r) else {
                uncertain += 1;
                continue;
            };
            let mut add = |k: &'static str, v: f64| {
                let task = by.entry(k).or_default().entry(r.task.clone()).or_default();
                task.push((v, y));
            };
            add("A-constant", 0.5);
            add("B-heuristic", r.outcome.heuristic_value);
            if let Some((v, c, fallback)) = t.jev.get(&r.capsule_digest) {
                add("C-jev", *v);
                jev_total += 1;
                if fallback.is_none() && c.is_some() {
                    answered.entry(r.task.clone()).or_default().push((*v, y));
                } else {
                    jev_fallback += 1;
                }
            }
        }
    }
    let mut candidate = serde_json::Map::new();
    for (k, points) in &by {
        candidate.insert((*k).into(), discrimination(points, floor));
    }
    if has_jev {
        let rate = (jev_total > 0).then(|| jev_fallback as f64 / jev_total as f64);
        candidate.insert(
            "C-jev-answered-only".into(),
            discrimination(&answered, floor),
        );
        candidate.insert("C-jev-fallback-rate".into(), opt(rate));
        candidate.insert(
            "C-jev-confidence".into(),
            confidence(&jev_confidence_points(tasks)),
        );
    }
    candidate.insert("uncertain_excluded".into(), json!(uncertain));
    let w = |k: &str| by.get(k).map(within_task).unwrap_or_default();
    let mut paired_auroc = serde_json::Map::new();
    paired_auroc.insert(
        "B-minus-A".into(),
        paired(&w("B-heuristic"), &w("A-constant")),
    );
    if has_jev {
        paired_auroc.insert("C-minus-B".into(), paired(&w("C-jev"), &w("B-heuristic")));
    }

    // Branch value: route-judged region records; B is also read on C's
    // sample so the comparison is paired.
    let mut b_points = Vec::new();
    let mut b_sampled = Vec::new();
    let mut c_points = Vec::new();
    let mut c_failed = 0;
    let mut weights = Vec::new();
    for r in records().filter(|r| r.subject == SubjectKind::Region) {
        let Some(y) = labeled(r) else { continue };
        b_points.push((0.5, y));
        if let Some((answer, p)) = branches.get(&r.capsule_digest) {
            weights.push(*p);
            match answer {
                Ok((v, _)) => {
                    c_points.push((*v, y));
                    b_sampled.push((0.5, y));
                }
                Err(_) => c_failed += 1,
            }
        }
    }
    let branch_stats = |pts: &[(f64, bool)]| {
        let useful = pts.iter().filter(|p| p.1).count();
        let kept = |p: &(f64, bool)| p.0 >= prune;
        let correct = pts.iter().filter(|p| kept(p) == p.1).count();
        let lost = pts.iter().filter(|p| p.1 && !kept(p)).count();
        let wasted = pts.iter().filter(|p| !p.1 && kept(p)).count();
        let ratio = |a: usize, b: usize| opt((b > 0).then(|| a as f64 / b as f64));
        json!({
            "n": pts.len(), "useful": useful,
            "auroc": opt(db::auroc(pts, db::MIN_CLASS)),
            "selection_accuracy_at_prune_threshold": ratio(correct, pts.len()),
            "pruning_loss": ratio(lost, useful),
            "unnecessary_exploration": ratio(wasted, pts.len() - useful),
            "prune_threshold": prune,
        })
    };
    let mut branch = json!({"B-heuristic": branch_stats(&b_points)});
    if !branches.is_empty() {
        let mean_p =
            (!weights.is_empty()).then(|| weights.iter().sum::<f64>() / weights.len() as f64);
        branch["B-heuristic-on-C-sample"] = branch_stats(&b_sampled);
        branch["C-jev-sampled"] = branch_stats(&c_points);
        branch["C-jev-sampled"]["failed"] = json!(c_failed);
        branch["C-jev-sampled"]["mean_inclusion_probability"] = opt(mean_p);
    }

    // End-to-end context quality: macro means over tasks with verified
    // gold (recall is undefined without it; those tasks are counted).
    let gold_of = |t: &TaskOutput| t.routing["gold_verified"].as_u64().unwrap();
    let without_gold = tasks.iter().filter(|t| gold_of(t) == 0).count();
    let tasks_with_gold: Vec<&&TaskOutput> = tasks.iter().filter(|t| gold_of(t) > 0).collect();
    let mut table: BTreeMap<String, Vec<Value>> = BTreeMap::new();
    for t in &tasks_with_gold {
        for (k, row) in &t.runs {
            let mut row = row.clone();
            let o = row.as_object_mut().unwrap();
            o.remove("fallbacks");
            o.remove("latency_ms");
            table.entry(k.clone()).or_default().push(row);
        }
    }
    let context: serde_json::Map<String, Value> = table
        .iter()
        .map(|(k, rows)| (k.clone(), mean(rows)))
        .collect();
    let per_task = |key: &str, metric: &str| -> BTreeMap<String, f64> {
        tasks_with_gold
            .iter()
            .filter_map(|t| Some((t.id.clone(), t.runs.get(key)?[metric].as_f64()?)))
            .collect()
    };
    let mut paired_context = serde_json::Map::new();
    for tokens in BUDGETS {
        for metric in ["required_symbol_recall", "gold_token_share"] {
            let b = per_task(&format!("B-heuristic@{tokens}"), metric);
            let a = per_task(&format!("A-constant@{tokens}"), metric);
            paired_context.insert(format!("B-minus-A@{tokens}:{metric}"), paired(&b, &a));
            if has_jev {
                let c = per_task(&format!("C-jev@{tokens}"), metric);
                paired_context.insert(format!("C-minus-B@{tokens}:{metric}"), paired(&c, &b));
            }
        }
    }
    let routing: Vec<Value> = tasks.iter().map(|t| t.routing.clone()).collect();
    let sum = |k: &str| -> u64 { routing.iter().map(|r| r[k].as_u64().unwrap()).sum() };
    let (gold, routed) = (sum("gold_verified"), sum("gold_routed"));
    json!({
        "tasks": tasks.len(),
        "records": records().count(),
        "candidate_relevance": candidate,
        "paired_within_task_auroc": paired_auroc,
        "branch_value": branch,
        "context": context,
        "context_tasks_without_verified_gold": without_gold,
        "paired_context": paired_context,
        "routing_mean": mean(&routing),
        "gold_unobserved_by_routing": opt((gold > 0).then(|| 1.0 - routed as f64 / gold as f64)),
    })
}

/// Counts of records by subject, label and stratum.
pub fn composition(records: &[&Record]) -> Value {
    let mut subjects: BTreeMap<String, BTreeMap<String, usize>> = BTreeMap::new();
    let mut strata: BTreeMap<&str, usize> = BTreeMap::new();
    for r in records {
        let s = format!("{:?}", r.subject).to_lowercase();
        let l = format!("{:?}", r.label.value).to_lowercase();
        *subjects.entry(s).or_default().entry(l).or_default() += 1;
        for x in &r.strata {
            *strata.entry(x).or_default() += 1;
        }
    }
    json!({"by_subject_and_label": subjects, "by_stratum": strata})
}

/// Real repositories outgrow the default 32 MiB pool at publication.
pub const BUFFER_POOL: u64 = 1 << 30;
pub const GOLD_SOURCE: &str =
    "ContextBench gold_context (benchmark-curated, independent of OXIDE and JEV)";

pub fn data_dir() -> PathBuf {
    std::env::var_os("OXIDE_DECISIONBENCH_DATA")
        .map(PathBuf::from)
        .unwrap_or_else(|| {
            let home = std::env::var_os("HOME").unwrap();
            Path::new(&home).join("Projects/oxide-eval-data/oxide-decisionbench")
        })
}

pub fn worktree(id: &str) -> Option<PathBuf> {
    let dirs = std::env::var("OXIDE_CB_WORKTREES").expect("set OXIDE_CB_WORKTREES");
    dirs.split(':')
        .map(|d| Path::new(d).join(id))
        .find(|p| p.is_dir())
}

/// The worktree's checked-out commit, read from its git files (git itself
/// is never run: SPEC § Security and privacy).
pub fn head(root: &Path) -> Option<String> {
    let dot = root.join(".git");
    let gitdir = if dot.is_file() {
        let text = std::fs::read_to_string(&dot).ok()?;
        let p = PathBuf::from(text.strip_prefix("gitdir:")?.trim());
        if p.is_absolute() { p } else { root.join(p) }
    } else {
        dot
    };
    let head = std::fs::read_to_string(gitdir.join("HEAD")).ok()?;
    Some(head.trim().to_owned())
}

/// One ContextBench task indexed into a fresh store under `data/stores`.
pub struct Indexed {
    pub store: LadybugStore,
    pub key: SnapshotKey,
    pub gold: Gold,
    pub span_counts: BTreeMap<&'static str, usize>,
    pub head: Option<String>,
    pub files: usize,
    pub skipped: usize,
    pub entities: usize,
    pub index_seconds: f64,
    pub dir: PathBuf,
}

/// Captures, derives and publishes task `t` (a `contextbench-tasks.json`
/// entry) from its worktree at the benchmark base commit, and maps its gold.
pub fn index_task(t: &Value, data: &Path) -> Indexed {
    let id = t["id"].as_str().unwrap();
    let repo = t["repo"].as_str().unwrap();
    let root = worktree(id).unwrap_or_else(|| panic!("no worktree for {id}"));
    let head = head(&root);
    assert_eq!(
        head.as_deref(),
        t["base_commit"].as_str(),
        "{id}: not at base_commit"
    );
    let started = Instant::now();
    let source = capture(&root, &Scope::default()).unwrap();
    let slug: String = repo
        .chars()
        .map(|c| if c.is_ascii_alphanumeric() { c } else { '-' })
        .collect();
    let repo_id = RepoId::new(format!("decisionbench-{slug}")).unwrap();
    let (manifest, batch, components) = derive(&repo_id, &source).unwrap();
    let key = manifest.key.clone();
    let spans = t["gold"].as_array().unwrap();
    let (gold, span_counts) = map_gold(&source, &batch.entities, spans, GOLD_SOURCE);
    let entities = batch.entities.len();
    let dir = data.join("stores").join(id);
    let _ = std::fs::remove_dir_all(&dir);
    let mut store = LadybugStore::with_buffer_pool(&dir, BUFFER_POOL).unwrap();
    store.begin(manifest).unwrap();
    store.retain_derivation(&key, &components).unwrap();
    store.retain_source(&source).unwrap();
    store.write(&key, batch).unwrap();
    store.publish(&key).unwrap();
    Indexed {
        store,
        key,
        gold,
        span_counts,
        head,
        files: source.files.len(),
        skipped: source.skipped.len(),
        entities,
        index_seconds: started.elapsed().as_secs_f64(),
        dir,
    }
}

/// ContextBench gold spans (1-based inclusive lines) mapped to entities
/// (`cb-span-innermost-v1`): a span names the innermost symbols whose
/// range intersects it, or its file when no symbol does. A span is
/// verified when its whitespace-collapsed text hashes to the benchmark's
/// recorded content digest. Returns the gold and span counts by status.
pub fn map_gold(
    capture: &SourceCapture,
    entities: &[Entity],
    spans: &[Value],
    source: &str,
) -> (Gold, BTreeMap<&'static str, usize>) {
    let mut gold = Gold {
        source: source.into(),
        ..Default::default()
    };
    let mut counts: BTreeMap<&'static str, usize> = BTreeMap::new();
    for span in spans {
        let path = span["file"].as_str().unwrap();
        let Some((file, captured)) = capture.files.iter().find(|(p, _)| p.as_str() == path) else {
            *counts.entry("file_not_captured").or_default() += 1;
            continue;
        };
        let bytes = &captured.bytes;
        let start = span["start_line"].as_u64().unwrap() as usize;
        let end = span["end_line"].as_u64().unwrap() as usize;
        let mut offsets = vec![0];
        offsets.extend(
            bytes
                .iter()
                .enumerate()
                .filter(|(_, b)| **b == b'\n')
                .map(|(i, _)| i + 1),
        );
        if start == 0 || start > offsets.len() || end < start {
            *counts.entry("lines_out_of_range").or_default() += 1;
            continue;
        }
        let lo = offsets[start - 1];
        let hi = offsets.get(end).copied().unwrap_or(bytes.len());
        let text = String::from_utf8_lossy(&bytes[lo..hi]);
        let collapsed = text.split_whitespace().collect::<Vec<_>>().join(" ");
        let verified =
            db::sha256_hex(collapsed.as_bytes()) == span["content_sha256"].as_str().unwrap();
        let (lo, hi) = (lo as u64, hi as u64);
        let hits: Vec<&EntityId> = entities
            .iter()
            .filter(|e| matches!(e.id, EntityId::Symbol(_)))
            .filter(|e| {
                e.source
                    .as_ref()
                    .is_some_and(|s| s.file == *file && s.range.start < hi && lo < s.range.end)
            })
            .map(|e| &e.id)
            .collect();
        let mut named: Vec<EntityId> = hits
            .iter()
            .filter(|h| !hits.iter().any(|o| db::ancestors(o).contains(h)))
            .map(|h| (*h).clone())
            .collect();
        if named.is_empty() {
            *counts.entry("file_level").or_default() += 1;
            named.push(EntityId::File(file.clone()));
        }
        let status = if verified { "verified" } else { "unverified" };
        *counts.entry(status).or_default() += 1;
        if verified {
            gold.verified.extend(named);
        } else {
            gold.unverified.extend(named);
        }
    }
    let verified = gold.verified.clone();
    gold.unverified.retain(|e| !verified.contains(e));
    (gold, counts)
}
