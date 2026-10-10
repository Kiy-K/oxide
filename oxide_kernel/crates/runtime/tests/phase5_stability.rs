//! Phase 5 follow-up 2 (docs/phase-5/followup-2/preregistration.md),
//! part B: JEV stability from recorded answers. Opt-in and machine-local
//! like `phase5_followup.rs`; no live JEV call is possible (the only
//! transport is [`Replay`]):
//!
//! ```text
//! OXIDE_CB_WORKTREES=<dir>:<dir> \
//!   cargo test --release -p oxide-runtime --test phase5_stability -- --ignored
//! ```
//!
//! Part A (routing load order) was rejected; its router knobs and harness
//! are kept as `docs/phase-5/followup-2/routing-order.patch`.

mod common;

use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};

use common::phase5::{
    self, CandidatesOnly, Indexed, TaskInput, data_dir, fallbacks, jev_config, round,
};
use common::{gold_sources, metrics, repo_root};
use oxide_kernel::capsule::{Capsule, Question};
use oxide_kernel::context::{ContextRun, baseline, build_context};
use oxide_kernel::decision::{
    DecisionProvider, Judgment, ProviderError, ProviderIdentity, Verdict,
};
use oxide_kernel::id::EntityId;
use oxide_kernel::store::{KnowledgeStore, ReadView};
use oxide_runtime::decisionbench::{self as db, Split};
use oxide_runtime::jev::{Disclosure, Jev, MODEL, Recorded, Replay, parse, request};
use serde_json::{Value, json};

const PREREGISTRATION_SHA256: &str =
    "e18b7f160561d76ea6b0e7eb418cfe8b81a6f75563367082fdb6b53bd5074d2f";
const TOKENS: u32 = 1024;
const REPLICATES: u64 = 20;
const FLOOR: f64 = 0.4;

fn preregistered() -> (Value, BTreeMap<String, Split>) {
    let root = repo_root();
    let prereg = std::fs::read(root.join("docs/phase-5/followup-2/preregistration.md")).unwrap();
    assert_eq!(
        db::sha256_hex(&prereg),
        PREREGISTRATION_SHA256,
        "preregistration edited"
    );
    let docs = root.join("docs/phase-5/decisionbench-v1");
    let read = |f: &str| -> Value {
        serde_json::from_str(&std::fs::read_to_string(docs.join(f)).unwrap()).unwrap()
    };
    let splits = serde_json::from_value(read("splits.json")["repositories"].clone()).unwrap();
    (read("contextbench-tasks.json"), splits)
}

/// A task's cached result, or `measure` run and cached.
fn cached(path: &Path, wanted: impl Fn(&Value) -> bool, measure: impl FnOnce() -> Value) -> Value {
    let hit = std::fs::read_to_string(path)
        .ok()
        .map(|t| serde_json::from_str::<Value>(&t).unwrap())
        .filter(&wanted);
    hit.unwrap_or_else(|| {
        let r = measure();
        std::fs::write(path, serde_json::to_string(&r).unwrap()).unwrap();
        r
    })
}

fn input<'a>(t: &'a Value, split: Split, gold: &db::Gold) -> TaskInput<'a> {
    TaskInput {
        id: t["id"].as_str().unwrap(),
        repository: t["repo"].as_str().unwrap(),
        split,
        query: t["query"].as_str().unwrap(),
        symbols: Vec::new(),
        gold: gold.clone(),
    }
}

fn quantile(xs: &mut [f64], q: f64) -> f64 {
    xs.sort_by(f64::total_cmp);
    xs[((xs.len() - 1) as f64 * q).round() as usize]
}

/// Every schema-valid recorded answer per request body, in session order:
/// (value, confidence).
type Answers = BTreeMap<String, Vec<(f64, f64)>>;

/// Every `*.json` recording under `dir`, sorted: the smoke run names its
/// files by task, not `session-*`.
fn recordings(dir: &Path) -> Vec<PathBuf> {
    let mut out: Vec<PathBuf> = std::fs::read_dir(dir)
        .unwrap()
        .map(|e| e.unwrap().path())
        .filter(|p| p.extension().is_some_and(|x| x == "json"))
        .collect();
    out.sort();
    out
}

fn exchanges(paths: &[PathBuf]) -> Vec<(String, Recorded)> {
    let mut out = Vec::new();
    for path in paths {
        let text = std::fs::read_to_string(path).unwrap();
        let replay = Replay::from_json(&text).unwrap();
        let v: Value = serde_json::from_str(&text).unwrap();
        for e in v["exchanges"].as_array().unwrap() {
            let key = e["request_sha256"].as_str().unwrap().to_owned();
            // `Replay` keeps a body's first answer; a repeat in the same file
            // needs its own, read as `Replay::from_json` reads responses.
            let recorded = if let Some(text) = e["response_text"].as_str() {
                Recorded::Response(text.to_owned())
            } else if !e["response"].is_null() {
                Recorded::Response(e["response"].to_string())
            } else {
                replay.exchanges[&key].clone()
            };
            out.push((key, recorded));
        }
    }
    out
}

fn answers(exchanges: &[(String, Recorded)]) -> Answers {
    let mut out: Answers = BTreeMap::new();
    for (key, recorded) in exchanges {
        if let Recorded::Response(body) = recorded
            && let Ok((value, confidence, _)) = parse(body, MODEL)
        {
            out.entry(key.clone())
                .or_default()
                .push((value, confidence));
        }
    }
    out
}

/// First answer wins, as in the Phase 5 replay.
fn replay(exchanges: &[(String, Recorded)]) -> Replay {
    let mut map = BTreeMap::new();
    for (key, recorded) in exchanges {
        map.entry(key.clone()).or_insert_with(|| recorded.clone());
    }
    Replay { exchanges: map }
}

/// B1: pairwise drift over bodies answered more than once, and the pool
/// of signed deltas B4 samples from.
fn drift(answers: &Answers) -> (Value, Vec<f64>) {
    let (mut dv, mut dc, mut pool) = (Vec::new(), Vec::new(), Vec::new());
    let (mut repeated, mut crossing, mut identical) = (0, 0, 0);
    for list in answers.values().filter(|l| l.len() > 1) {
        repeated += 1;
        let lo = list.iter().map(|a| a.0).fold(f64::INFINITY, f64::min);
        let hi = list.iter().map(|a| a.0).fold(f64::NEG_INFINITY, f64::max);
        crossing += usize::from(lo < FLOOR && hi >= FLOOR);
        identical += usize::from(list.iter().all(|a| a == &list[0]));
        for i in 0..list.len() {
            for j in i + 1..list.len() {
                let d = list[i].0 - list[j].0;
                dv.push(d.abs());
                dc.push((list[i].1 - list[j].1).abs());
                pool.extend([d, -d]);
            }
        }
    }
    pool.sort_by(f64::total_cmp);
    let q = |xs: &mut Vec<f64>| {
        let mean = xs.iter().sum::<f64>() / xs.len() as f64;
        json!({"mean": round(mean), "p50": round(quantile(xs, 0.5)), "p90": round(quantile(xs, 0.9)),
               "p99": round(quantile(xs, 0.99)), "max": round(quantile(xs, 1.0))})
    };
    let within = |t: f64| dv.iter().filter(|d| **d <= t + 1e-9).count();
    let report = json!({
        "repeated_bodies": repeated, "pairs": dv.len(), "bodies_identical": identical,
        "pairs_within_0.05": within(0.05), "pairs_within_0.10": within(0.10),
        "bodies_crossing_include_floor": crossing,
        "abs_value_delta": q(&mut dv.clone()), "abs_confidence_delta": q(&mut dc),
    });
    (report, pool)
}

/// Shifts every provider value by a pool delta chosen by the capsule's
/// digest and the replicate, clamped to [0, 1].
struct Perturbed<'a> {
    inner: &'a mut dyn DecisionProvider,
    pool: &'a [f64],
    replicate: u64,
}

impl DecisionProvider for Perturbed<'_> {
    fn identity(&self) -> ProviderIdentity {
        self.inner.identity()
    }
    fn supports(&self, question: Question) -> bool {
        self.inner.supports(question)
    }
    fn judge(&mut self, capsules: &[Capsule]) -> Result<Vec<Judgment>, ProviderError> {
        let mut out = self.inner.judge(capsules)?;
        for j in &mut out {
            if let Verdict::Value { value, .. } = &mut j.verdict {
                let seed = format!("{:?}/{}", j.capsule_digest, self.replicate);
                let h = db::sha256_hex(seed.as_bytes());
                let i = u64::from_str_radix(&h[..16], 16).unwrap() % self.pool.len() as u64;
                *value = (*value + self.pool[i as usize]).clamp(0.0, 1.0);
            }
        }
        Ok(out)
    }
}

fn jev_context(
    view: &(impl ReadView + oxide_kernel::source::SourceProvider),
    input: &TaskInput<'_>,
    tape: &Replay,
    perturb: Option<(&[f64], u64)>,
) -> ContextRun {
    let transport = Replay {
        exchanges: tape.exchanges.clone(),
    };
    let mut jev = Jev::new(jev_config(), transport).unwrap();
    let mut only = CandidatesOnly(&mut jev);
    let request = phase5::request(input, TOKENS);
    let base = baseline();
    match perturb {
        None => build_context(view, view, &request, &base, Some(&mut only)).unwrap(),
        Some((pool, replicate)) => {
            let mut p = Perturbed {
                inner: &mut only,
                pool,
                replicate,
            };
            build_context(view, view, &request, &base, Some(&mut p)).unwrap()
        }
    }
}

fn planned(run: &ContextRun) -> Vec<EntityId> {
    run.plan.items.iter().map(|p| p.entity.clone()).collect()
}

fn jaccard<T: Ord>(a: impl IntoIterator<Item = T>, b: impl IntoIterator<Item = T>) -> f64 {
    let a: BTreeSet<T> = a.into_iter().collect();
    let b: BTreeSet<T> = b.into_iter().collect();
    let union = a.union(&b).count();
    if union == 0 {
        1.0
    } else {
        a.intersection(&b).count() as f64 / union as f64
    }
}

fn items(run: &ContextRun) -> Vec<String> {
    run.bundle
        .items
        .iter()
        .map(|i| format!("{:?}", i.source))
        .collect()
}

/// Provider judgments that fell back for a reason other than an
/// unsupported question (routing is heuristic in config C).
fn unanswered(run: &ContextRun) -> i64 {
    fallbacks(run)
        .as_object()
        .unwrap()
        .iter()
        .filter(|(k, _)| k.as_str() != "Unsupported")
        .map(|(_, n)| n.as_i64().unwrap())
        .sum()
}

/// Kendall τ-b between two paired score lists.
fn kendall(a: &[f64], b: &[f64]) -> f64 {
    let (mut c, mut d, mut ta, mut tb) = (0.0f64, 0.0, 0.0, 0.0);
    for i in 0..a.len() {
        for j in i + 1..a.len() {
            let (x, y) = (a[i] - a[j], b[i] - b[j]);
            match (x == 0.0, y == 0.0) {
                (true, true) => {}
                (true, false) => ta += 1.0,
                (false, true) => tb += 1.0,
                _ if (x > 0.0) == (y > 0.0) => c += 1.0,
                _ => d += 1.0,
            }
        }
    }
    (c - d) / ((c + d + ta) * (c + d + tb)).sqrt()
}

fn top(values: &[f64], k: usize) -> BTreeSet<usize> {
    let mut idx: Vec<usize> = (0..values.len()).collect();
    idx.sort_by(|&i, &j| values[j].total_cmp(&values[i]).then(i.cmp(&j)));
    idx.into_iter().take(k).collect()
}

/// The Phase 5 and smoke-run tapes with their parsed answers.
struct Smoke<'a> {
    tape: &'a Replay,
    phase5: &'a Answers,
    smoke: &'a Answers,
}

fn measure_stability(
    t: &Value,
    split: Split,
    data: &Path,
    phase5_tape: &Replay,
    smoke: Option<Smoke<'_>>,
    pool: &[f64],
) -> Value {
    let Indexed {
        store,
        key,
        gold,
        dir,
        ..
    } = phase5::index_task(t, data);
    let view = store.open(&key).unwrap();
    let input = input(t, split, &gold);
    let all: Vec<EntityId> = gold.verified.iter().cloned().collect();
    let sources = gold_sources(&view, &all);
    let recall = |run: &ContextRun| {
        metrics(&all, &sources, run)["required_symbol_recall"]
            .as_f64()
            .unwrap()
    };
    let base = jev_context(&view, &input, phase5_tape, None);
    let base_plan = planned(&base);
    let mut reps = Vec::new();
    for r in 0..REPLICATES {
        let run = jev_context(&view, &input, phase5_tape, Some((pool, r)));
        let plan = planned(&run);
        let class = if plan == base_plan {
            "identical"
        } else if jaccard(&plan, &base_plan) == 1.0 {
            "reordered"
        } else {
            "set_changed"
        };
        reps.push(
            json!({"class": class, "plan_jaccard": jaccard(&plan, &base_plan),
            "item_jaccard": jaccard(items(&run), items(&base)),
            "recall_diff": recall(&run) - recall(&base),
            "extra_unanswered": unanswered(&run) - unanswered(&base)}),
        );
    }
    let mut row = json!({"task": input.id, "split": split, "recall": recall(&base),
                         "unanswered": unanswered(&base), "replicates": reps});
    if let Some(s) = smoke {
        let other = jev_context(&view, &input, s.tape, None);
        // B2 over this task's relevance capsules answered in both runs.
        let (mut a, mut b) = (Vec::new(), Vec::new());
        for c in &base.capsules {
            let k = db::sha256_hex(request(c, MODEL, Disclosure::Source).as_bytes());
            if let (Some(x), Some(y)) = (s.phase5.get(&k), s.smoke.get(&k)) {
                a.push(x[0].0);
                b.push(y[0].0);
            }
        }
        let flips = a
            .iter()
            .zip(&b)
            .filter(|(x, y)| (**x >= FLOOR) != (**y >= FLOOR))
            .count();
        row["smoke"] = json!({
            "shared_relevance_capsules": a.len(), "kendall_tau_b": round(kendall(&a, &b)),
            "top10_overlap": top(&a, 10).intersection(&top(&b, 10)).count(),
            "include_floor_flips": flips,
            "plan_jaccard": jaccard(planned(&other), base_plan.clone()),
            "item_jaccard": jaccard(items(&other), items(&base)),
            "recall_phase5": recall(&base), "recall_smoke": recall(&other),
        });
    }
    drop(view);
    drop(store);
    let _ = std::fs::remove_dir_all(&dir);
    row
}

#[test]
#[ignore = "machine-local: needs ContextBench worktrees and the Phase 5 recordings"]
fn jev_stability() {
    let (spec, splits) = preregistered();
    let data = data_dir();
    let fixture = repo_root().join("docs/phase-5/decisionbench-v1/fixture/jev");
    let smoke_files = recordings(&data.join("followup-live"));
    assert!(!smoke_files.is_empty(), "no smoke recordings");
    let phase5_files: Vec<PathBuf> = [fixture.as_path(), &data.join("jev")]
        .iter()
        .flat_map(|d| phase5::sessions(d))
        .collect();
    let phase5_ex = exchanges(&phase5_files);
    let smoke_ex = exchanges(&smoke_files);
    let all_ex: Vec<_> = phase5_ex.iter().chain(&smoke_ex).cloned().collect();
    let (b1, pool) = drift(&answers(&all_ex));
    let phase5_tape = replay(&all_ex);
    let smoke_first: Vec<_> = smoke_ex.iter().chain(&phase5_ex).cloned().collect();
    let smoke_tape = replay(&smoke_first);
    let (p5_answers, smoke_answers) = (answers(&phase5_ex), answers(&smoke_ex));
    let smoke_ids: BTreeSet<String> = smoke_files
        .iter()
        .map(|p| p.file_stem().unwrap().to_string_lossy().into_owned())
        .collect();
    // A cached row is reused only for the same delta pool and replicates.
    let pool_bytes: Vec<u8> = pool.iter().flat_map(|d| d.to_le_bytes()).collect();
    let inputs = json!({"pool_sha256": db::sha256_hex(&pool_bytes), "replicates": REPLICATES});
    let cache_dir = data.join("followup-2/stability");
    std::fs::create_dir_all(&cache_dir).unwrap();
    let mut rows = Vec::new();
    for t in spec["tasks"].as_array().unwrap() {
        let id = t["id"].as_str().unwrap();
        let split = splits[t["repo"].as_str().unwrap()];
        let smoke = smoke_ids.contains(id).then_some(Smoke {
            tape: &smoke_tape,
            phase5: &p5_answers,
            smoke: &smoke_answers,
        });
        let path = cache_dir.join(format!("{id}.json"));
        let r = cached(
            &path,
            |r| r["inputs"] == inputs && (!smoke_ids.contains(id) || r["smoke"].is_object()),
            || {
                let mut r = measure_stability(t, split, &data, &phase5_tape, smoke, &pool);
                r["inputs"] = inputs.clone();
                r
            },
        );
        eprintln!("{id}");
        rows.push(r);
    }
    let reps: Vec<&Value> = rows
        .iter()
        .flat_map(|r| r["replicates"].as_array().unwrap())
        .collect();
    let count = |c: &str| reps.iter().filter(|r| r["class"] == c).count();
    let diff = |r: &Value| r["recall_diff"].as_f64().unwrap();
    let changed: Vec<f64> = reps
        .iter()
        .map(|r| diff(r))
        .filter(|d| d.abs() > 1e-9)
        .collect();
    let mean =
        |k: &str| reps.iter().map(|r| r[k].as_f64().unwrap()).sum::<f64>() / reps.len() as f64;
    // Per-task mean recall change over replicates, then a task bootstrap.
    let per_task: Vec<f64> = rows
        .iter()
        .map(|r| {
            let x = r["replicates"].as_array().unwrap();
            x.iter().map(diff).sum::<f64>() / x.len() as f64
        })
        .collect();
    let ci = db::bootstrap_mean_ci(&per_task, phase5::BOOTSTRAP.0, phase5::BOOTSTRAP.1);
    let tasks_changed = rows
        .iter()
        .filter(|r| {
            let x = r["replicates"].as_array().unwrap();
            x.iter().any(|y| diff(y).abs() > 1e-9)
        })
        .count();
    let smoke: Vec<Value> = rows
        .iter()
        .filter(|r| r["smoke"].is_object())
        .map(|r| json!({"task": r["task"], "smoke": r["smoke"]}))
        .collect();
    let out = json!({
        "preregistration_sha256": PREREGISTRATION_SHA256, "budget_units": TOKENS,
        "b1_drift": b1,
        "b2_b3_smoke": smoke,
        "b4_simulated": {
            "tasks": rows.len(), "replicates": reps.len(),
            "identical": count("identical"), "reordered": count("reordered"),
            "set_changed": count("set_changed"),
            "recall_changed": changed.len(),
            "recall_up": changed.iter().filter(|d| **d > 0.0).count(),
            "recall_down": changed.iter().filter(|d| **d < 0.0).count(),
            "tasks_with_any_recall_change": tasks_changed,
            "mean_recall_diff": round(mean("recall_diff")),
            "mean_recall_diff_ci95": ci.map(|(lo, hi)| json!([round(lo), round(hi)])),
            "mean_plan_jaccard": round(mean("plan_jaccard")),
            "mean_item_jaccard": round(mean("item_jaccard")),
            "mean_extra_unanswered": round(mean("extra_unanswered")),
        },
    });
    let text = serde_json::to_string_pretty(&out).unwrap() + "\n";
    std::fs::write(
        repo_root().join("docs/phase-5/followup-2/stability.json"),
        &text,
    )
    .unwrap();
    eprintln!("{text}");
}
