//! DecisionBench v1 (SPEC § Training-data implications, Phase 5): judgment
//! examples built from real OXIDE runs, plus the statistics that evaluate
//! a judge on them.
//!
//! A record wraps one canonical capsule exactly as runtime inference
//! rendered it (`Capsule::render(false)`, the bytes [`Capsule::digest`]
//! hashes, the `state` a JEV request carries) with what must never enter
//! model input: the task's split, a label with its annotations, sampling
//! strata, how it was observed, and policy outcomes. Validation rejects a
//! capsule whose bytes, digest, version, keys or question disagree.
//!
//! Labels come from independent gold (a benchmark's annotated context),
//! directly or through a declared deterministic rule (containment). A
//! heuristic or policy outcome is never a label, and a teacher annotation
//! (JEV or any model) can be stored but never decides one. Gold is
//! incomplete, so `irrelevant` means "absent from gold" and every metric
//! built on it is labeled precision, not true noise.

use std::collections::{BTreeMap, BTreeSet};

use oxide_kernel::capsule::{CAPSULE_VERSION, Capsule, Question, Subject};
use oxide_kernel::context::ContextRun;
use oxide_kernel::decision::Heuristic;
use oxide_kernel::digest::digest;
use oxide_kernel::id::{EntityId, RepoPath, SymbolId};
use oxide_kernel::lexical::terms;
use oxide_kernel::query::Query;
use oxide_kernel::retrieve::{Channel, ChannelState, RETRIEVAL_VERSION};
use oxide_kernel::route::{Origin, ROUTER_VERSION};
use oxide_kernel::store::ReadView;
use oxide_kernel::tree::{NavLimits, region};
use serde::{Deserialize, Serialize};
use sha2::{Digest as _, Sha256};

pub const FORMAT: &str = "oxide-decisionbench-v1";
/// The one label head of v1: membership in the benchmark's gold context.
pub const HEAD: &str = "gold_context";
/// Top-level keys of a canonical capsule v2 rendering. Anything else (a
/// label, an outcome) in a stored capsule is rejected.
pub const CAPSULE_KEYS: [&str; 12] = [
    "capsule_version",
    "snapshot",
    "subject",
    "question",
    "task",
    "entity",
    "snippet",
    "retrieval",
    "route",
    "provenance_truncated",
    "relations",
    "relations_truncated",
];
/// Calibration statistics need at least this many labeled points and at
/// least [`MIN_CLASS`] of each class; AUROC needs [`MIN_CLASS`] of each.
pub const MIN_CALIBRATION_N: usize = 200;
pub const MIN_CLASS: usize = 30;
/// Queries this similar (Jaccard over OXIDE terms) are near-duplicates.
pub const NEAR_DUPLICATE: f64 = 0.8;

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Split {
    Dev,
    Calibration,
    Test,
    TrainReserved,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SubjectKind {
    /// A CandidateGraph node judged for relevance.
    Candidate,
    /// An expansion neighbor judged for neighbor value.
    Neighbor,
    /// A TreeIndex region judged for branch value during routing.
    Region,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum LabelValue {
    Relevant,
    /// Absent from (incomplete) gold.
    Irrelevant,
    /// Ambiguous or insufficiently supported; excluded from binary metrics.
    Uncertain,
    /// No trustworthy annotation.
    Unlabeled,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum AnnotationKind {
    /// Independent of OXIDE and of any judge: benchmark or human gold.
    Gold,
    /// A declared deterministic rule over independent gold.
    GoldDerived,
    /// A model's estimate (JEV, a stronger judge). Never decides a label.
    Teacher,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Annotation {
    pub source: String,
    pub kind: AnnotationKind,
    pub value: LabelValue,
    pub note: String,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Label {
    pub head: String,
    /// The adjudicated value. Annotations that disagree with it are kept.
    pub value: LabelValue,
    pub annotations: Vec<Annotation>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Observation {
    /// `graph_node`, `expansion_neighbor` or `route_judged_region`.
    pub stage: String,
    pub retrieval: String,
    pub router: String,
    /// Channel availability and routing mode of the producing run.
    pub mode: String,
    /// Position in its stage's list (graph order, judging order).
    pub position: usize,
    /// Probability that this observed subject was sampled into the dataset.
    pub inclusion_probability: f64,
}

/// What OXIDE's own policy did; for analysis, never a label.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Outcome {
    pub heuristic_value: f64,
    /// Planned by the heuristic run that produced the record (`None` for
    /// regions).
    pub planned: Option<bool>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Record {
    pub format: String,
    pub id: String,
    pub split: Split,
    pub repository: String,
    pub task: String,
    pub subject: SubjectKind,
    pub question: String,
    pub capsule_version: u32,
    /// `Capsule::digest` (`sha256:<hex>` of `capsule`): what judgments and
    /// traces correlate by.
    pub capsule_digest: String,
    /// The canonical capsule rendering, verbatim.
    pub capsule: String,
    pub label: Label,
    pub strata: Vec<String>,
    pub observation: Observation,
    pub outcome: Outcome,
}

/// Independent gold for one task, mapped to entities of its snapshot.
#[derive(Debug, Clone, Default)]
pub struct Gold {
    /// Entities the gold names, verified against the snapshot's bytes.
    pub verified: BTreeSet<EntityId>,
    /// Entities named by gold that could not be verified (content differs).
    pub unverified: BTreeSet<EntityId>,
    pub source: String,
}

pub struct TaskMeta<'a> {
    pub repository: &'a str,
    pub task: &'a str,
    pub split: Split,
    pub query: &'a str,
}

pub fn sha256_hex(bytes: &[u8]) -> String {
    Sha256::digest(bytes)
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect()
}

pub fn question_name(q: Question) -> &'static str {
    match q {
        Question::Relevance => "relevance",
        Question::Necessity => "necessity",
        Question::NeighborValue => "neighbor_value",
        Question::BranchValue => "branch_value",
    }
}

pub fn question(name: &str) -> Option<Question> {
    [
        Question::Relevance,
        Question::Necessity,
        Question::NeighborValue,
        Question::BranchValue,
    ]
    .into_iter()
    .find(|q| question_name(*q) == name)
}

/// The physical ancestors an entity's identity implies, innermost first:
/// enclosing symbols, then its file. The repository root is omitted.
pub fn ancestors(id: &EntityId) -> Vec<EntityId> {
    let EntityId::Symbol(s) = id else {
        return Vec::new();
    };
    let path = s.path();
    let mut out: Vec<EntityId> = (1..path.len())
        .rev()
        .filter_map(|n| SymbolId::new(s.file().clone(), path[..n].to_vec()).ok())
        .map(EntityId::Symbol)
        .collect();
    out.push(EntityId::File(s.file().clone()));
    out
}

fn file_of(id: &EntityId) -> Option<&RepoPath> {
    match id {
        EntityId::File(p) => Some(p),
        EntityId::Symbol(s) => Some(s.file()),
        _ => None,
    }
}

fn label(gold: &Gold, kind: AnnotationKind, value: LabelValue, note: &str) -> Label {
    Label {
        head: HEAD.into(),
        value,
        annotations: vec![Annotation {
            source: gold.source.clone(),
            kind,
            value,
            note: note.into(),
        }],
    }
}

/// Candidate/neighbor label (`gold-membership-v1`): gold membership, with
/// enclosing scopes and unverified gold left uncertain.
pub fn candidate_label(gold: &Gold, id: &EntityId) -> Label {
    let encloses = |set: &BTreeSet<EntityId>| set.iter().any(|g| ancestors(g).contains(id));
    let (kind, value, note) = if gold.verified.contains(id) {
        (
            AnnotationKind::Gold,
            LabelValue::Relevant,
            "in gold context",
        )
    } else if gold.unverified.contains(id) {
        (
            AnnotationKind::Gold,
            LabelValue::Uncertain,
            "named by a gold span whose content differs from the snapshot",
        )
    } else if encloses(&gold.verified) || encloses(&gold.unverified) {
        (
            AnnotationKind::GoldDerived,
            LabelValue::Uncertain,
            "encloses gold but is not itself gold",
        )
    } else {
        (
            AnnotationKind::Gold,
            LabelValue::Irrelevant,
            "absent from gold context (gold is incomplete)",
        )
    };
    label(gold, kind, value, note)
}

/// Region label (`subtree-contains-gold-v1`): does the anchor's physical
/// subtree hold gold?
pub fn region_label(gold: &Gold, anchor: &EntityId) -> Label {
    let under = |set: &BTreeSet<EntityId>| {
        set.iter()
            .any(|g| g == anchor || ancestors(g).contains(anchor))
    };
    let root = *anchor == EntityId::Repository;
    let (value, note) = if under(&gold.verified) || (root && !gold.verified.is_empty()) {
        (LabelValue::Relevant, "subtree contains verified gold")
    } else if under(&gold.unverified) || (root && !gold.unverified.is_empty()) {
        (
            LabelValue::Uncertain,
            "subtree contains only unverified gold",
        )
    } else {
        (LabelValue::Irrelevant, "no gold in subtree")
    };
    label(gold, AnnotationKind::GoldDerived, value, note)
}

fn candidate_strata(gold: &Gold, capsule: &Capsule, id: &EntityId, label: &Label) -> Vec<String> {
    let mut s = Vec::new();
    let lexical = capsule
        .channels
        .iter()
        .find(|c| c.channel == Channel::Lexical)
        .map(|c| c.rank);
    if let Some(rank) = lexical {
        s.push("entry:lexical".to_owned());
        if rank <= 5 {
            s.push("lexical_top5".into());
        }
    }
    if !capsule.seeds.is_empty() {
        s.push("entry:seed".into());
    }
    let routed_only = lexical.is_none() && capsule.seeds.is_empty();
    if routed_only {
        s.push("routed_only".into());
    }
    if matches!(capsule.kind.as_str(), "repository" | "module" | "file") {
        s.push("container".into());
    }
    if capsule.test {
        s.push("test".into());
    }
    let via_gold = capsule.origins.iter().any(|o| match o {
        Origin::Neighbor { via, .. } => gold.verified.contains(via),
        Origin::RegionMember(r) | Origin::Scope(r) => gold.verified.contains(&r.anchor),
        _ => false,
    });
    match label.value {
        LabelValue::Irrelevant => {
            s.push("hard_negative".into());
            if lexical.is_some_and(|r| r <= 5) {
                s.push("misleading_lexical".into());
            }
            let gold_file = |f: &RepoPath| gold.verified.iter().any(|g| file_of(g) == Some(f));
            if file_of(id).is_some_and(gold_file) {
                s.push("same_file_as_gold".into());
            }
            if via_gold {
                s.push("neighbor_of_gold".into());
            }
        }
        LabelValue::Relevant if routed_only => s.push("relevant_structural_neighbor".into()),
        _ => {}
    }
    s
}

/// Every judged subject of one heuristic run, as records. `run` must come
/// from `build_context` with the heuristic passed as an explicit provider,
/// so routing records its branch decisions; region capsules are rebuilt
/// from `view` with the router's `nav` limits and must reproduce the judged
/// digests.
pub fn records(
    meta: &TaskMeta<'_>,
    gold: &Gold,
    run: &ContextRun,
    view: &impl ReadView,
    nav: NavLimits,
) -> Result<Vec<Record>, String> {
    let query_terms: BTreeSet<String> = terms(meta.query).into_iter().collect();
    let planned: BTreeSet<&EntityId> = run.plan.items.iter().map(|i| &i.entity).collect();
    let channels: Vec<String> = run
        .candidates
        .channels
        .iter()
        .map(|c| {
            let ran = matches!(c.state, ChannelState::Ran { .. });
            format!("{:?}={}", c.channel, if ran { "ran" } else { "off" })
        })
        .collect();
    let mode = format!(
        "{}; structural seeds; heuristic routing",
        channels.join(",")
    );
    let mut out = Vec::new();
    let mut push = |capsule: &Capsule,
                    subject: SubjectKind,
                    stage: &str,
                    position: usize,
                    label: Label,
                    strata: Vec<String>,
                    planned: Option<bool>| {
        let rendering = capsule.render(false);
        let digest = capsule.digest().as_str().to_owned();
        let question = question_name(capsule.question);
        let hex = digest.trim_start_matches("sha256:");
        out.push(Record {
            format: FORMAT.into(),
            id: format!("{}/{}/{}", meta.task, question, &hex[..16]),
            split: meta.split,
            repository: meta.repository.into(),
            task: meta.task.into(),
            subject,
            question: question.into(),
            capsule_version: capsule.version,
            capsule_digest: digest,
            capsule: rendering,
            label,
            strata,
            observation: Observation {
                stage: stage.into(),
                retrieval: RETRIEVAL_VERSION.into(),
                router: ROUTER_VERSION.into(),
                mode: mode.clone(),
                position,
                inclusion_probability: 1.0,
            },
            outcome: Outcome {
                heuristic_value: Heuristic::value(capsule),
                planned,
            },
        });
    };

    let stages = [
        (&run.capsules, SubjectKind::Candidate, "graph_node"),
        (
            &run.plan.capsules,
            SubjectKind::Neighbor,
            "expansion_neighbor",
        ),
    ];
    for (capsules, kind, stage) in stages {
        for (i, capsule) in capsules.iter().enumerate() {
            let Subject::Candidate(id) = &capsule.subject else {
                return Err(format!("a {stage} capsule without a candidate subject"));
            };
            let label = candidate_label(gold, id);
            let mut strata = candidate_strata(gold, capsule, id, &label);
            if kind == SubjectKind::Neighbor {
                strata.push("expansion".into());
            }
            let planned = Some(planned.contains(id));
            push(capsule, kind, stage, i, label, strata, planned);
        }
    }
    let query = Query {
        text: meta.query.into(),
    };
    for (i, decision) in run.route.trace.decisions.iter().enumerate() {
        let Subject::Region(id) = &decision.subject else {
            return Err("a routing decision without a region subject".into());
        };
        let r = region(view, id, nav).map_err(|e| format!("{e:?}"))?;
        let capsule = Capsule::branch(&run.route.snapshot, &query, &r);
        if capsule.digest() != decision.capsule_digest {
            return Err(format!(
                "rebuilt branch capsule for {id:?} differs from the judged one"
            ));
        }
        let label = region_label(gold, &id.anchor);
        let mut strata = Vec::new();
        match label.value {
            LabelValue::Relevant if !gold.verified.contains(&id.anchor) => {
                strata.push("useful_descendants".into());
            }
            LabelValue::Irrelevant => {
                strata.push("no_gold_branch".into());
                if terms(&capsule.name).iter().any(|t| query_terms.contains(t)) {
                    strata.push("promising_no_gold".into());
                }
            }
            _ => {}
        }
        let stage = "route_judged_region";
        push(&capsule, SubjectKind::Region, stage, i, label, strata, None);
    }
    Ok(out)
}

/// Checks one record in isolation.
pub fn validate(r: &Record) -> Result<(), String> {
    let fail = |why: &str| Err(format!("{}: {why}", r.id));
    if r.format != FORMAT {
        return fail("unknown format");
    }
    if r.capsule_version != CAPSULE_VERSION {
        return fail("unsupported capsule version");
    }
    if digest(r.capsule.as_bytes()).as_str() != r.capsule_digest {
        return fail("capsule bytes do not match their digest");
    }
    let Ok(capsule) = serde_json::from_str::<serde_json::Value>(&r.capsule) else {
        return fail("capsule is not JSON");
    };
    let mut keys: Vec<&str> = capsule
        .as_object()
        .map(|o| o.keys().map(String::as_str).collect())
        .unwrap_or_default();
    keys.sort_unstable();
    let mut expected = CAPSULE_KEYS.to_vec();
    expected.sort_unstable();
    if keys != expected {
        return fail("capsule keys are not the canonical v2 set");
    }
    if capsule["capsule_version"] != r.capsule_version || capsule["question"] != r.question.as_str()
    {
        return fail("record and capsule disagree on version or question");
    }
    let region = capsule["subject"].get("region").is_some();
    if region != (r.subject == SubjectKind::Region) || question(&r.question).is_none() {
        return fail("subject kind or question does not match the capsule");
    }
    if r.label.head != HEAD {
        return fail("unknown label head");
    }
    let decided = matches!(r.label.value, LabelValue::Relevant | LabelValue::Irrelevant);
    let independent = |a: &Annotation| a.kind != AnnotationKind::Teacher;
    if decided
        && !r
            .label
            .annotations
            .iter()
            .any(|a| independent(a) && a.value == r.label.value)
    {
        return fail("a relevant/irrelevant label needs an agreeing non-teacher annotation");
    }
    if r.label.value == LabelValue::Unlabeled && r.label.annotations.iter().any(independent) {
        return fail("unlabeled despite an independent annotation");
    }
    let p = r.observation.inclusion_probability;
    if !(p > 0.0 && p <= 1.0) {
        return fail("inclusion probability outside (0, 1]");
    }
    Ok(())
}

/// Dataset-level checks: record validity, split assignment, identity,
/// repository leakage and near-duplicate tasks. `errors` must be empty.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct Leakage {
    pub errors: Vec<String>,
    /// Near-duplicate task pairs inside one split (allowed, reported).
    pub near_duplicates_within_split: Vec<(String, String)>,
    /// Capsule digests shared by records of different tasks.
    pub shared_capsules: usize,
}

pub fn jaccard(a: &str, b: &str) -> f64 {
    let a: BTreeSet<String> = terms(a).into_iter().collect();
    let b: BTreeSet<String> = terms(b).into_iter().collect();
    let union = a.union(&b).count();
    if union == 0 {
        return 0.0;
    }
    a.intersection(&b).count() as f64 / union as f64
}

/// `splits` maps repositories to splits; `tasks` maps every declared task
/// to its repository and query text.
pub fn leakage(
    records: &[Record],
    splits: &BTreeMap<String, Split>,
    tasks: &BTreeMap<String, (String, String)>,
) -> Leakage {
    let mut out = Leakage::default();
    let mut ids = BTreeSet::new();
    let mut task_split: BTreeMap<&str, Split> = BTreeMap::new();
    let mut digests: BTreeMap<&str, BTreeSet<&str>> = BTreeMap::new();
    for r in records {
        if let Err(e) = validate(r) {
            out.errors.push(e);
        }
        if !ids.insert(&r.id) {
            out.errors.push(format!("duplicate record id {}", r.id));
        }
        if splits.get(&r.repository) != Some(&r.split) {
            out.errors
                .push(format!("{}: split differs from its repository's", r.id));
        }
        if *task_split.entry(&r.task).or_insert(r.split) != r.split {
            out.errors.push(format!("task {} spans splits", r.task));
        }
        if tasks.get(&r.task).map(|t| &t.0) != Some(&r.repository) {
            out.errors
                .push(format!("{}: task not declared for its repository", r.id));
        }
        digests
            .entry(&r.capsule_digest)
            .or_default()
            .insert(&r.task);
    }
    out.shared_capsules = digests.values().filter(|t| t.len() > 1).count();
    for (repo, _) in tasks.values() {
        if !splits.contains_key(repo) {
            out.errors.push(format!("repository {repo} has no split"));
        }
    }
    let tasks: Vec<(&String, &(String, String))> = tasks.iter().collect();
    for (i, (ta, (ra, qa))) in tasks.iter().enumerate() {
        for (tb, (rb, qb)) in &tasks[i + 1..] {
            if jaccard(qa, qb) < NEAR_DUPLICATE {
                continue;
            }
            if splits.get(ra) == splits.get(rb) {
                out.near_duplicates_within_split
                    .push(((*ta).clone(), (*tb).clone()));
            } else {
                out.errors.push(format!(
                    "near-duplicate tasks {ta} and {tb} are in different splits"
                ));
            }
        }
    }
    out
}

// --- statistics -----------------------------------------------------------

/// Area under the ROC curve of the score for the label, ties counted half
/// (Mann-Whitney). `None` when a class has fewer than `min_class` points.
pub fn auroc(points: &[(f64, bool)], min_class: usize) -> Option<f64> {
    let pos = points.iter().filter(|p| p.1).count();
    let neg = points.len() - pos;
    if pos < min_class.max(1) || neg < min_class.max(1) {
        return None;
    }
    let mut sorted: Vec<&(f64, bool)> = points.iter().collect();
    sorted.sort_by(|a, b| a.0.total_cmp(&b.0));
    let mut rank_sum = 0.0;
    let mut i = 0;
    while i < sorted.len() {
        let mut j = i;
        while j < sorted.len() && sorted[j].0 == sorted[i].0 {
            j += 1;
        }
        // Average 1-based rank of the tie run.
        let mid = (i + j + 1) as f64 / 2.0;
        rank_sum += mid * sorted[i..j].iter().filter(|p| p.1).count() as f64;
        i = j;
    }
    let pos = pos as f64;
    Some((rank_sum - pos * (pos + 1.0) / 2.0) / (pos * neg as f64))
}

/// (precision, recall) of `score >= threshold`; precision is `None` when
/// nothing is predicted positive, recall when nothing is positive.
pub fn precision_recall(points: &[(f64, bool)], threshold: f64) -> (Option<f64>, Option<f64>) {
    let predicted: Vec<&(f64, bool)> = points.iter().filter(|p| p.0 >= threshold).collect();
    let tp = predicted.iter().filter(|p| p.1).count() as f64;
    let positives = points.iter().filter(|p| p.1).count() as f64;
    let ratio = |a: f64, b: f64| (b > 0.0).then(|| a / b);
    (ratio(tp, predicted.len() as f64), ratio(tp, positives))
}

pub fn sufficient_for_calibration(points: &[(f64, bool)]) -> bool {
    let pos = points.iter().filter(|p| p.1).count();
    points.len() >= MIN_CALIBRATION_N && pos >= MIN_CLASS && points.len() - pos >= MIN_CLASS
}

pub fn brier(points: &[(f64, bool)]) -> f64 {
    let sum: f64 = points
        .iter()
        .map(|(p, y)| (p - f64::from(u8::from(*y))).powi(2))
        .sum();
    sum / points.len() as f64
}

/// Equal-width bins over [0, 1]: (count, mean probability, observed rate).
pub fn reliability(points: &[(f64, bool)], bins: usize) -> Vec<(usize, f64, f64)> {
    let mut acc = vec![(0usize, 0.0, 0.0); bins];
    for (p, y) in points {
        let b = ((p * bins as f64) as usize).min(bins - 1);
        acc[b].0 += 1;
        acc[b].1 += p;
        acc[b].2 += f64::from(u8::from(*y));
    }
    acc.into_iter()
        .map(|(n, p, y)| match n {
            0 => (0, 0.0, 0.0),
            n => (n, p / n as f64, y / n as f64),
        })
        .collect()
}

/// Expected calibration error over 10 equal-width bins.
pub fn ece(points: &[(f64, bool)]) -> f64 {
    let n = points.len() as f64;
    reliability(points, 10)
        .into_iter()
        .map(|(c, p, y)| c as f64 / n * (p - y).abs())
        .sum()
}

fn by_confidence(points: &[(f64, bool)]) -> Vec<&(f64, bool)> {
    let mut sorted: Vec<&(f64, bool)> = points.iter().collect();
    sorted.sort_by(|a, b| b.0.total_cmp(&a.0));
    sorted
}

/// Selective prediction over (confidence, correct) points: the accuracy of
/// the most confident fraction `coverage` (ties keep input order).
pub fn accuracy_at_coverage(points: &[(f64, bool)], coverage: f64) -> Option<f64> {
    let sorted = by_confidence(points);
    let k = (coverage * sorted.len() as f64).ceil() as usize;
    (k > 0).then(|| sorted[..k].iter().filter(|p| p.1).count() as f64 / k as f64)
}

/// Area under the risk-coverage curve (mean selective risk over every
/// prefix); lower is better.
pub fn aurc(points: &[(f64, bool)]) -> Option<f64> {
    let sorted = by_confidence(points);
    let mut errors = 0.0;
    let mut sum = 0.0;
    for (i, p) in sorted.iter().enumerate() {
        errors += f64::from(u8::from(!p.1));
        sum += errors / (i + 1) as f64;
    }
    (!sorted.is_empty()).then(|| sum / sorted.len() as f64)
}

/// Percentile bootstrap 95% interval of the mean of per-task paired
/// differences (tasks are the resampling unit). Deterministic (xorshift
/// with a fixed seed); `None` for fewer than two tasks.
pub fn bootstrap_mean_ci(diffs: &[f64], iterations: usize, seed: u64) -> Option<(f64, f64)> {
    if diffs.len() < 2 || iterations == 0 {
        return None;
    }
    let mut state = seed.max(1);
    let mut next = || {
        state ^= state << 13;
        state ^= state >> 7;
        state ^= state << 17;
        state
    };
    let n = diffs.len() as u64;
    let mut means: Vec<f64> = (0..iterations)
        .map(|_| (0..n).map(|_| diffs[(next() % n) as usize]).sum::<f64>() / n as f64)
        .collect();
    means.sort_by(f64::total_cmp);
    let at = |q: f64| means[(q * (iterations - 1) as f64).round() as usize];
    Some((at(0.025), at(0.975)))
}

/// Histogram-binning calibration map fitted on (confidence, correct)
/// points: each of 10 bins maps to its observed rate, an empty bin to its
/// centre. A simple statistical fit, not a learned model.
pub fn fit_bins(points: &[(f64, bool)]) -> Vec<f64> {
    reliability(points, 10)
        .into_iter()
        .enumerate()
        .map(|(i, (n, _, y))| if n == 0 { (i as f64 + 0.5) / 10.0 } else { y })
        .collect()
}

pub fn apply_bins(map: &[f64], confidence: f64) -> f64 {
    map[((confidence * map.len() as f64) as usize).min(map.len() - 1)]
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn auroc_handles_ties_and_small_classes() {
        let pts = [(0.9, true), (0.8, false), (0.7, true), (0.1, false)];
        assert_eq!(auroc(&pts, 1), Some(0.75));
        assert_eq!(auroc(&[(0.5, true), (0.5, false)], 1), Some(0.5));
        assert_eq!(auroc(&pts, 3), None);
        assert_eq!(auroc(&[(0.2, true)], 1), None);
    }

    #[test]
    fn calibration_statistics() {
        let perfect = [(1.0, true), (0.0, false)];
        assert_eq!((brier(&perfect), ece(&perfect)), (0.0, 0.0));
        let over = [(0.9, true), (0.9, false)];
        assert!((ece(&over) - 0.4).abs() < 1e-12);
        assert!(!sufficient_for_calibration(&over));
        let map = fit_bins(&over);
        assert_eq!(apply_bins(&map, 0.95), 0.5);
        assert_eq!(apply_bins(&map, 0.0), 0.05);
        assert_eq!(precision_recall(&over, 0.5), (Some(0.5), Some(1.0)));
        assert_eq!(precision_recall(&over, 0.95), (None, Some(0.0)));
    }

    #[test]
    fn selective_accuracy_and_bootstrap() {
        let pts = [(0.9, true), (0.8, true), (0.3, false), (0.2, true)];
        assert_eq!(accuracy_at_coverage(&pts, 0.5), Some(1.0));
        assert_eq!(accuracy_at_coverage(&pts, 1.0), Some(0.75));
        let expected = (0.0 + 0.0 + 1.0 / 3.0 + 0.25) / 4.0;
        assert!((aurc(&pts).unwrap() - expected).abs() < 1e-12);
        let (lo, hi) = bootstrap_mean_ci(&[0.1, 0.2, 0.3, 0.0], 1000, 7).unwrap();
        assert!(lo >= 0.0 && hi <= 0.3 && lo < hi);
        let twice = || bootstrap_mean_ci(&[0.1, 0.2], 50, 7);
        assert_eq!(twice(), twice());
        assert_eq!(bootstrap_mean_ci(&[0.1], 50, 7), None);
    }

    #[test]
    fn ancestors_and_labels_follow_containment() {
        use oxide_kernel::id::Segment;
        let file = RepoPath::new("a.py").unwrap();
        let sym = |names: &[&str]| {
            let path = names
                .iter()
                .map(|n| Segment {
                    name: (*n).into(),
                    ordinal: 0,
                })
                .collect();
            EntityId::Symbol(SymbolId::new(file.clone(), path).unwrap())
        };
        let (class, method, other) = (sym(&["C"]), sym(&["C", "m"]), sym(&["f"]));
        assert_eq!(
            ancestors(&method),
            [class.clone(), EntityId::File(file.clone())]
        );
        let gold = Gold {
            verified: BTreeSet::from([method.clone()]),
            unverified: BTreeSet::new(),
            source: "test".into(),
        };
        let value = |id: &EntityId| candidate_label(&gold, id).value;
        assert_eq!(value(&method), LabelValue::Relevant);
        assert_eq!(value(&class), LabelValue::Uncertain);
        assert_eq!(value(&other), LabelValue::Irrelevant);
        let branch = |id: &EntityId| region_label(&gold, id).value;
        assert_eq!(branch(&class), LabelValue::Relevant);
        assert_eq!(branch(&EntityId::File(file.clone())), LabelValue::Relevant);
        assert_eq!(branch(&other), LabelValue::Irrelevant);
    }

    #[test]
    fn near_duplicates_use_oxide_terms() {
        assert_eq!(
            jaccard("retry the HTTP request", "Retry the http request!"),
            1.0
        );
        assert!(jaccard("retry policy", "email notifier") < NEAR_DUPLICATE);
    }
}
