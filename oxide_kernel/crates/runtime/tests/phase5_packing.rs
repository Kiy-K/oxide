//! Phase 5 follow-up 5: where the selection plan and the packer lose
//! budget and gold, on dev, for the heuristic, recorded JEV (replay only,
//! never live) and the evaluation-only gold reference. The route (BFS, 64
//! regions), graph and relevance capsules are the baseline's. Opt-in and
//! machine-local like `phase5_selection.rs`:
//!
//! ```text
//! OXIDE_CB_WORKTREES=<dir>:<dir> \
//!   cargo test --release -p oxide-runtime --test phase5_packing -- --ignored
//! ```

mod common;

use std::collections::{BTreeMap, BTreeSet};
use std::path::Path;
use std::time::Instant;

use common::phase5::{
    self, CandidatesOnly, GoldPriority, Indexed, Tape, TaskInput, data_dir, jev_config, paired,
    round, stages,
};
use common::{covers, gold_sources, metrics, repo_root, rss_peak_kib};
use oxide_kernel::capsule::CAPSULE_VERSION;
use oxide_kernel::context::{ContextRun, baseline, build_context};
use oxide_kernel::decision::DecisionProvider;
use oxide_kernel::decision::Fallback;
use oxide_kernel::id::EntityId;
use oxide_kernel::knowledge::SourceRef;
use oxide_kernel::pack::PAYLOAD_VERSION;
use oxide_kernel::pack::View;
use oxide_kernel::retrieve::RETRIEVAL_VERSION;
use oxide_kernel::route::ROUTER_VERSION;
use oxide_kernel::select::{OmitReason, Reason, SELECTOR_VERSION, Stage};
use oxide_kernel::store::KnowledgeStore;
use oxide_runtime::decisionbench::{Split, sha256_hex};
use oxide_runtime::jev::Jev;
use serde_json::{Value, json};

const BUDGETS: [u32; 2] = [1024, 4096];
const RUNS: [&str; 3] = ["heuristic", "jev_replay", "gold_priority"];
/// The preregistered item-cap variant (docs/phase-5/followup-5/preregistration.md).
const CAP_RUNS: [&str; 4] = [
    "heuristic",
    "jev_replay",
    "heuristic_cap48",
    "jev_replay_cap48",
];
const PREREGISTRATION_SHA256: &str =
    "31f4eceb15c23defc97a643dd4619d361e947c9fc83f6e9f8a422b6a1332ec20";

fn reason_name(r: &OmitReason) -> String {
    let debug = format!("{r:?}");
    debug.split([' ', '{', '(']).next().unwrap().to_owned()
}

/// Where one gold symbol ended up in `run`.
fn gold_outcome(run: &ContextRun, g: &EntityId, s: &SourceRef) -> String {
    if let Some(item) = run.bundle.items.iter().find(|i| covers(&i.source, s)) {
        let own = item.entities.iter().any(|e| &e.entity == g);
        return if own { "full" } else { "full_inside_other" }.into();
    }
    match run.plan.items.iter().find(|p| &p.entity == g) {
        Some(p) if p.view == View::Header => return "planned_header".into(),
        Some(_) => return "packer_lost".into(),
        None => {}
    }
    if run
        .plan
        .items
        .iter()
        .any(|p| p.prerequisites.iter().any(|q| &q.entity == g))
    {
        return "prerequisite_header".into();
    }
    match run.bundle.omitted.iter().rev().find(|o| &o.entity == g) {
        Some(o) => format!("{:?}:{}", o.stage, reason_name(&o.reason)),
        None => "unreached".into(),
    }
}

/// Units one gold symbol cost or needed in `run`: its planned estimate, or
/// what a budget omission needed with what remained.
fn gold_cost(run: &ContextRun, g: &EntityId) -> Value {
    if let Some(p) = run.plan.items.iter().find(|p| &p.entity == g) {
        return json!({"planned": p.estimated_tokens});
    }
    match run.plan.excluded.iter().rev().find(|o| &o.entity == g) {
        Some(o) => match o.reason {
            OmitReason::BudgetExceeded { needed, remaining } => {
                json!({"needed": needed, "remaining": remaining})
            }
            OmitReason::TooLarge { needed } => json!({"needed": needed}),
            _ => Value::Null,
        },
        None => Value::Null,
    }
}

/// How `run` spent its budget and why candidates were left out.
fn diagnose(run: &ContextRun, budget: u32, cap: usize, gold: &BTreeSet<EntityId>) -> Value {
    let used = run.bundle.used_tokens;
    let estimated = run.plan.estimated_tokens;
    let free = budget - used;
    // A planned full view the packer did not render whole.
    let packer_reduced = run
        .plan
        .items
        .iter()
        .filter(|p| p.view == View::Full)
        .filter(|p| {
            !run.bundle.items.iter().any(|i| {
                i.entities
                    .iter()
                    .any(|e| e.entity == p.entity && e.complete)
            })
        })
        .count();
    let headers: Vec<_> = run.plan.items.iter().filter(|p| p.reduced).collect();
    let mut budget_omitted = 0;
    let mut fits_unused = 0;
    let mut fits_unused_gold = 0;
    let mut max_items = 0;
    let mut max_items_gold = 0;
    for o in &run.plan.excluded {
        match o.reason {
            OmitReason::BudgetExceeded { needed, .. } => {
                budget_omitted += 1;
                // Would fit in what the packer left unused (estimate-based).
                if needed <= free {
                    fits_unused += 1;
                    fits_unused_gold += usize::from(gold.contains(&o.entity));
                }
            }
            OmitReason::MaxItems => {
                max_items += 1;
                max_items_gold += usize::from(gold.contains(&o.entity));
            }
            _ => {}
        }
    }
    let expanded = |r: &Reason| matches!(r, Reason::Expanded { .. });
    let cap_reached = run.plan.items.len() >= cap;
    json!({
        "estimated": estimated, "used": used,
        "estimate_slack": i64::from(estimated) - i64::from(used),
        "estimate_below_actual": used > estimated,
        "unused": free, "planned": run.plan.items.len(),
        "cap_reached": cap_reached,
        "header_items": headers.len(),
        "header_units": headers.iter().map(|p| p.estimated_tokens).sum::<u32>(),
        "header_gold": headers.iter().filter(|p| gold.contains(&p.entity)).count(),
        "with_prerequisites": run.plan.items.iter().filter(|p| !p.prerequisites.is_empty())
            .count(),
        "packer_omitted": run.bundle.omitted.iter().filter(|o| o.stage == Stage::Packing).count(),
        "packer_reduced": packer_reduced,
        "budget_omitted": budget_omitted,
        "budget_omitted_fits_unused": fits_unused,
        "budget_omitted_fits_unused_gold": fits_unused_gold,
        "max_items_omitted": max_items, "max_items_omitted_gold": max_items_gold,
        "expanded_planned": run.plan.items.iter().filter(|p| expanded(&p.reason)).count(),
        "expanded_planned_gold": run.plan.items.iter()
            .filter(|p| expanded(&p.reason) && gold.contains(&p.entity)).count(),
    })
}

fn measure(t: &Value, data: &Path, runs: &[&str], tape: Tape) -> (Value, Tape) {
    let Indexed {
        store,
        key,
        gold,
        dir,
        ..
    } = phase5::index_task(t, data);
    let view = store.open(&key).unwrap();
    let input = TaskInput {
        id: t["id"].as_str().unwrap(),
        repository: t["repo"].as_str().unwrap(),
        split: Split::Dev,
        query: t["query"].as_str().unwrap(),
        symbols: Vec::new(),
        gold: gold.clone(),
    };
    let all: Vec<EntityId> = gold.verified.iter().cloned().collect();
    let all_sources = gold_sources(&view, &all);
    let symbols: BTreeSet<EntityId> = all
        .iter()
        .filter(|g| matches!(g, EntityId::Symbol(_)))
        .cloned()
        .collect();
    let symbol_list: Vec<EntityId> = symbols.iter().cloned().collect();
    let symbol_sources = gold_sources(&view, &symbol_list);
    let other: BTreeSet<EntityId> = all
        .iter()
        .filter(|g| !symbols.contains(g))
        .cloned()
        .collect();

    let mut jev = Jev::new(jev_config(), tape).unwrap();
    let mut row = json!({"task": input.id, "inputs": inputs(runs), "symbol_gold": symbols.len()});
    let mut outcomes: BTreeMap<String, BTreeMap<String, String>> = BTreeMap::new();
    let mut costs: BTreeMap<String, BTreeMap<String, Value>> = BTreeMap::new();
    for budget in BUDGETS {
        let request = phase5::request(&input, budget);
        let mut first: Option<ContextRun> = None;
        for &label in runs {
            let mut config = baseline();
            if label.ends_with("_cap48") {
                config.select.max_items = 48;
            }
            let context = |jev: &mut Jev<Tape>| {
                let mut replay = CandidatesOnly(jev);
                let mut reference = GoldPriority {
                    symbols: &symbols,
                    other: &other,
                };
                let provider: Option<&mut dyn DecisionProvider> =
                    match label.trim_end_matches("_cap48") {
                        "heuristic" => None,
                        "jev_replay" => Some(&mut replay),
                        _ => Some(&mut reference),
                    };
                build_context(&view, &view, &request, &config, provider).unwrap()
            };
            let started = Instant::now();
            let run = context(&mut jev);
            let latency = started.elapsed().as_secs_f64() * 1e3;
            assert!(run.bundle.used_tokens <= budget, "{label}: budget exceeded");
            if label.ends_with("_cap48") {
                assert_eq!(
                    context(&mut jev).bundle,
                    run.bundle,
                    "{label}: not deterministic"
                );
            }
            // Same candidate universe for every run.
            if let Some(f) = &first {
                assert!(
                    (&f.route.candidates, &f.graph, &f.capsules)
                        == (&run.route.candidates, &run.graph, &run.capsules),
                    "{label}: candidate universe differs"
                );
            }
            let mut m = metrics(&all, &all_sources, &run);
            m["latency_ms"] = json!(latency);
            m["stages"] = stages(&run, &symbol_list, &symbol_sources);
            m["diagnosis"] = diagnose(&run, budget, config.select.max_items, &symbols);
            if label.starts_with("jev_replay") {
                // All fallbacks, then those of relevance and neighbor judgments only.
                m["fallbacks"] = phase5::fallbacks(&run);
                let judged = run.decisions.iter().chain(&run.plan.decisions);
                m["judgment_fallbacks"] = json!(
                    judged
                        .filter(|d| d.fallback.is_some_and(|f| f != Fallback::NoProvider))
                        .count()
                );
            }
            for (g, s) in symbol_list.iter().zip(&symbol_sources) {
                outcomes
                    .entry(format!("{g:?}"))
                    .or_default()
                    .insert(format!("{label}@{budget}"), gold_outcome(&run, g, s));
                costs
                    .entry(format!("{g:?}"))
                    .or_default()
                    .insert(format!("{label}@{budget}"), gold_cost(&run, g));
            }
            row[format!("{label}@{budget}")] = m;
            first.get_or_insert(run);
        }
    }
    row["gold_outcomes"] = json!(outcomes);
    row["gold_costs"] = json!(costs);
    drop(view);
    drop(store);
    let _ = std::fs::remove_dir_all(&dir);
    (row, jev.into_transport())
}

/// Bump `harness` on any change to the measures.
fn inputs(runs: &[&str]) -> Value {
    // The variant's rows depend on its preregistration; the audit's do not.
    let prereg = (runs == CAP_RUNS).then_some(PREREGISTRATION_SHA256);
    json!({"harness": "packing-audit-v3", "runs": runs, "budgets": BUDGETS,
           "preregistration_sha256": prereg, "router": ROUTER_VERSION,
           "selector": SELECTOR_VERSION, "retrieval": RETRIEVAL_VERSION,
           "capsule": CAPSULE_VERSION, "payload": PAYLOAD_VERSION})
}

fn dev_tasks() -> Vec<Value> {
    let docs = repo_root().join("docs/phase-5/decisionbench-v1");
    let read = |f: &str| -> Value {
        serde_json::from_str(&std::fs::read_to_string(docs.join(f)).unwrap()).unwrap()
    };
    let splits: BTreeMap<String, Split> =
        serde_json::from_value(read("splits.json")["repositories"].clone()).unwrap();
    read("contextbench-tasks.json")["tasks"]
        .as_array()
        .unwrap()
        .iter()
        // Dev only: calibration and test stay untouched.
        .filter(|t| splits[t["repo"].as_str().unwrap()] == Split::Dev)
        .cloned()
        .collect()
}

fn run_dev(runs: &[&str], cache: &str) -> Vec<Value> {
    let data = data_dir();
    let docs = repo_root().join("docs/phase-5/decisionbench-v1");
    let mut tape = Tape::load(&[&docs.join("fixture/jev"), &data.join("jev")], None);
    let cache_dir = data.join("followup-5").join(cache);
    std::fs::create_dir_all(&cache_dir).unwrap();
    let mut tasks = Vec::new();
    for t in dev_tasks() {
        let id = t["id"].as_str().unwrap();
        let path = cache_dir.join(format!("{id}.json"));
        let cached = std::fs::read_to_string(&path)
            .ok()
            .map(|text| serde_json::from_str::<Value>(&text).unwrap())
            .filter(|r| r["inputs"] == inputs(runs));
        let r = match cached {
            Some(r) => r,
            None => {
                let (r, back) = measure(&t, &data, runs, tape);
                tape = back;
                std::fs::write(&path, serde_json::to_string(&r).unwrap()).unwrap();
                r
            }
        };
        eprintln!("{id}");
        tasks.push(r);
    }
    tasks
}

/// Task means of metrics, sums of diagnosis counts and gold outcomes, per
/// run and budget.
fn summary(tasks: &[Value], runs: &[&str]) -> Value {
    let mut out = BTreeMap::new();
    for &label in runs {
        for b in BUDGETS {
            let key = format!("{label}@{b}");
            let mean = |f: &str| {
                let sum: f64 = tasks.iter().map(|t| t[&key][f].as_f64().unwrap()).sum();
                round(sum / tasks.len() as f64)
            };
            let mut cell = json!({});
            for f in [
                "required_symbol_recall",
                "required_symbol_header_only",
                "selection_loss",
                "packing_loss",
                "gold_token_share",
                "unlabeled_token_share",
                "tokens_used",
                "budget_utilization",
                "items",
                "latency_ms",
            ] {
                cell[f] = mean(f);
            }
            let mut latency: Vec<f64> = tasks
                .iter()
                .map(|t| t[&key]["latency_ms"].as_f64().unwrap())
                .collect();
            latency.sort_by(f64::total_cmp);
            cell["latency_ms_p50"] = round(latency[(latency.len() - 1) / 2]);
            if label.starts_with("jev_replay") {
                let sum: u64 = tasks
                    .iter()
                    .map(|t| t[&key]["judgment_fallbacks"].as_u64().unwrap())
                    .sum();
                cell["judgment_fallbacks"] = json!(sum);
            }
            let mut sums: BTreeMap<String, i64> = BTreeMap::new();
            for t in tasks {
                for (k, v) in t[&key]["diagnosis"].as_object().unwrap() {
                    let x = v.as_i64().or(v.as_bool().map(i64::from)).unwrap();
                    *sums.entry(k.clone()).or_default() += x;
                }
            }
            cell["diagnosis_sum"] = json!(sums);
            let mut gold: BTreeMap<String, usize> = BTreeMap::new();
            for t in tasks {
                for o in t["gold_outcomes"].as_object().unwrap().values() {
                    *gold
                        .entry(o[&key].as_str().unwrap().to_owned())
                        .or_default() += 1;
                }
            }
            cell["gold_symbol_outcomes"] = json!(gold);
            out.insert(key, cell);
        }
    }
    json!(out)
}

/// Dev audit of plan cost, packing and gold outcomes.
#[test]
#[ignore = "machine-local: needs ContextBench worktrees"]
fn packing_audit() {
    let tasks = run_dev(&RUNS, "audit");
    let out = json!({"split": "dev", "tasks": tasks.len(), "rss_peak_kib": rss_peak_kib(),
                     "runs": summary(&tasks, &RUNS), "per_task": tasks});
    let text = serde_json::to_string_pretty(&out).unwrap() + "\n";
    let dir = repo_root().join("docs/phase-5/followup-5");
    std::fs::create_dir_all(&dir).unwrap();
    std::fs::write(dir.join("audit.json"), &text).unwrap();
    eprintln!("{}", serde_json::to_string_pretty(&out["runs"]).unwrap());
}

/// The preregistered item-cap variant against the baseline cap, on dev.
#[test]
#[ignore = "machine-local: needs ContextBench worktrees"]
fn item_cap_variant() {
    let prereg = std::fs::read(repo_root().join("docs/phase-5/followup-5/preregistration.md"));
    assert_eq!(
        sha256_hex(&prereg.unwrap()),
        PREREGISTRATION_SHA256,
        "preregistration edited"
    );
    let tasks = run_dev(&CAP_RUNS, "cap");
    let get = |key: &str, f: &str| -> BTreeMap<String, f64> {
        tasks
            .iter()
            .map(|t| {
                (
                    t["task"].as_str().unwrap().to_owned(),
                    t[key][f].as_f64().unwrap(),
                )
            })
            .collect()
    };
    let p50 = |key: &str| {
        let mut xs: Vec<f64> = get(key, "latency_ms").into_values().collect();
        xs.sort_by(f64::total_cmp);
        xs[(xs.len() - 1) / 2]
    };
    let mut diffs = json!({});
    for provider in ["heuristic", "jev_replay"] {
        for b in BUDGETS {
            let (base, cap) = (format!("{provider}@{b}"), format!("{provider}_cap48@{b}"));
            for f in ["required_symbol_recall", "unlabeled_token_share"] {
                diffs[&cap][f] = paired(&get(&cap, f), &get(&base, f));
            }
        }
    }
    let mean = |key: &str, f: &str| diffs[key][f]["mean_diff"].as_f64().unwrap_or(f64::NAN);
    let lo = |key: &str| {
        diffs[key]["required_symbol_recall"]["ci95"][0]
            .as_f64()
            .unwrap_or(f64::NEG_INFINITY)
    };
    let latency_ratio = p50("heuristic_cap48@4096") / p50("heuristic@4096");
    let passes = mean("heuristic_cap48@4096", "required_symbol_recall") >= 0.02
        && lo("heuristic_cap48@4096") > 0.0
        && [
            "heuristic_cap48@1024",
            "jev_replay_cap48@1024",
            "jev_replay_cap48@4096",
        ]
        .iter()
        .all(|k| mean(k, "required_symbol_recall") >= 0.0)
        && mean("heuristic_cap48@4096", "unlabeled_token_share") <= 0.05
        && latency_ratio <= 1.2;
    let out = json!({"preregistration_sha256": PREREGISTRATION_SHA256, "split": "dev",
                     "tasks": tasks.len(), "rss_peak_kib": rss_peak_kib(),
                     "runs": summary(&tasks, &CAP_RUNS), "vs_baseline": diffs,
                     "latency_ratio_heuristic_4096": round(latency_ratio), "passes": passes});
    let text = serde_json::to_string_pretty(&out).unwrap() + "\n";
    std::fs::write(
        repo_root().join("docs/phase-5/followup-5/item-cap.json"),
        &text,
    )
    .unwrap();
    eprintln!("{text}");
}
