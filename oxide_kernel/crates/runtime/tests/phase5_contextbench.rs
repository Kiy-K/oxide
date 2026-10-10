//! Phase 5 DecisionBench over ContextBench Python tasks (docs/phase-5.md).
//! Opt-in and machine-local: `#[ignore]`d, it needs the tasks' public
//! repositories checked out at their base commits. Run it with
//!
//! ```text
//! OXIDE_CB_WORKTREES=<dir>:<dir> OXIDE_PHASE5_JEV=off|replay|live \
//!   cargo test --release -p oxide-runtime --test phase5_contextbench -- --ignored
//! ```
//!
//! Source-bearing records and JEV exchanges stay in `OXIDE_DECISIONBENCH_DATA`
//! (default `~/Projects/oxide-eval-data/oxide-decisionbench`); only
//! manifests and metrics go to `docs/phase-5/decisionbench-v1/contextbench/`.
//! The test split stays sealed unless `OXIDE_PHASE5_UNSEAL_TEST=1` and the
//! preregistration exists; its digest is recorded with the results.

mod common;

use std::collections::BTreeMap;
use std::path::Path;

use common::phase5::{
    self, BUFFER_POOL, GOLD_SOURCE, Indexed, PREREGISTRATION_SHA256, Tape, TaskInput, TaskOutput,
    composition, data_dir, evaluate, jev_confidence_points, jev_config, judge_branches, repeat_dev,
    run_task,
};
use common::{repo_root, rss_peak_kib};
use oxide_kernel::store::KnowledgeStore;
use oxide_runtime::decisionbench::{self as db, Record, Split};
use oxide_runtime::jev::{self, Http, Jev, MODEL};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};

/// A task's outputs, cached per JEV mode so evaluation can be rerun
/// without re-indexing.
#[derive(Serialize, Deserialize)]
struct Cached {
    records: Vec<Record>,
    runs: BTreeMap<String, Value>,
    jev: BTreeMap<String, (f64, Option<f64>, Option<String>)>,
    routing: Value,
    provenance: Value,
}

fn merged_validity(dirs: &[&Path]) -> Value {
    let mut exchanges = Vec::new();
    for path in dirs.iter().flat_map(|d| phase5::sessions(d)) {
        let v: Value = serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap();
        exchanges.extend(v["exchanges"].as_array().unwrap().iter().cloned());
    }
    if exchanges.is_empty() {
        return json!("no recorded live exchanges");
    }
    let tokens: u64 = exchanges
        .iter()
        .filter_map(|e| e["response"]["usage"]["input_tokens"].as_u64())
        .sum();
    let doc = json!({"format": jev::REPLAY_FORMAT, "model": MODEL, "exchanges": exchanges});
    let v = jev::validity(&doc.to_string(), MODEL).unwrap();
    let pct = |q: f64| {
        let n = v.latency_ms.len();
        (n > 0).then(|| v.latency_ms[((n - 1) as f64 * q).round() as usize])
    };
    let parsed = v.valid + v.malformed;
    json!({
        "exchanges": v.exchanges, "valid": v.valid, "malformed": v.malformed, "failed": v.failed,
        "schema_valid_rate": (parsed > 0).then(|| v.valid as f64 / parsed as f64),
        "repeated_requests": v.repeated, "identical_on_repeat": v.identical,
        "stable_on_repeat": v.stable, "stable_spread": jev::STABLE_SPREAD,
        "max_value_spread": v.max_value_spread, "mean_value_spread": v.mean_value_spread,
        "max_confidence_spread": v.max_confidence_spread,
        "models": v.models,
        "latency_ms": {"p50": pct(0.5), "p95": pct(0.95), "max": v.latency_ms.last()},
        "input_tokens": tokens, "cost_usd": tokens as f64 * jev::USD_PER_INPUT_TOKEN,
    })
}

/// Indexes one task's worktree and runs it.
fn build(
    t: &Value,
    split: Split,
    data: &Path,
    judge: Option<&mut Jev<Tape>>,
) -> (TaskOutput, Value) {
    let id = t["id"].as_str().unwrap();
    let repo = t["repo"].as_str().unwrap();
    let Indexed {
        store,
        key,
        gold,
        span_counts,
        head,
        files,
        skipped,
        entities,
        index_seconds,
        dir: store_dir,
    } = phase5::index_task(t, data);
    let view = store.open(&key).unwrap();
    let input = TaskInput {
        id,
        repository: repo,
        split,
        query: t["query"].as_str().unwrap(),
        symbols: Vec::new(),
        gold,
    };
    let out = run_task(&view, &view, &input, judge);
    drop(view);
    drop(store);
    let _ = std::fs::remove_dir_all(&store_dir);
    let jsonl: String = out
        .records
        .iter()
        .map(|r| serde_json::to_string(r).unwrap() + "\n")
        .collect();
    let file = data.join("records").join(format!("{id}.jsonl"));
    std::fs::create_dir_all(file.parent().unwrap()).unwrap();
    std::fs::write(&file, &jsonl).unwrap();
    let provenance = json!({
        "task": id, "original_id": t["original_id"], "benchmark_source": t["benchmark_source"],
        "repo": repo, "repo_url": t["repo_url"], "base_commit": t["base_commit"], "head": head,
        "split": split,
        "snapshot": {"repo": key.repo.as_str(), "snapshot": key.snapshot.as_str(),
                     "derivation": key.derivation.as_str()},
        "files": files, "skipped": skipped, "entities": entities,
        "gold_spans": span_counts,
        "records": out.records.len(),
        "records_sha256": db::sha256_hex(jsonl.as_bytes()),
        "index_seconds": index_seconds,
        "buffer_pool_bytes": BUFFER_POOL,
    });
    (out, provenance)
}

#[test]
#[ignore = "needs local ContextBench worktrees; see the module docs"]
fn contextbench_decisionbench() {
    let docs = repo_root().join("docs/phase-5/decisionbench-v1");
    let read = |f: &str| -> Value {
        serde_json::from_str(&std::fs::read_to_string(docs.join(f)).unwrap()).unwrap()
    };
    let spec = read("contextbench-tasks.json");
    let splits: BTreeMap<String, Split> =
        serde_json::from_value(read("splits.json")["repositories"].clone()).unwrap();
    let mode = std::env::var("OXIDE_PHASE5_JEV").unwrap_or_else(|_| "off".into());
    let data = data_dir();
    let jev_dir = data.join("jev");
    let fixture_jev = docs.join("fixture/jev");
    let live = match mode.as_str() {
        "off" | "replay" => None,
        "live" => Some(Http::from_env().expect("live mode needs TYPESAFE_API_KEY")),
        other => panic!("OXIDE_PHASE5_JEV={other}"),
    };
    let tape = Tape::load(&[&fixture_jev, &jev_dir], live);
    let mut judge = (mode != "off").then(|| Jev::new(jev_config(), tape).unwrap());

    let mut outputs: Vec<TaskOutput> = Vec::new();
    let mut queries = BTreeMap::new();
    let mut provenance = Vec::new();
    // A smoke-test filter: only task ids containing this text.
    let only = std::env::var("OXIDE_PHASE5_ONLY").ok();
    for t in spec["tasks"].as_array().unwrap() {
        let id = t["id"].as_str().unwrap();
        if only.as_deref().is_some_and(|o| !id.contains(o)) {
            continue;
        }
        let repo = t["repo"].as_str().unwrap();
        queries.insert(
            id.to_owned(),
            (repo.to_owned(), t["query"].as_str().unwrap().to_owned()),
        );
        let split = splits[repo];
        let cache = data.join("results").join(format!("{id}.{mode}.json"));
        let cached: Cached = match std::fs::read_to_string(&cache) {
            Ok(text) => serde_json::from_str(&text).unwrap(),
            Err(_) => {
                let (out, prov) = build(t, split, &data, judge.as_mut());
                if let Some(j) = judge.as_mut() {
                    // Persist live exchanges task by task.
                    j.transport_mut().save(&jev_dir);
                    if let Some(l) = j.transport_mut().live.as_mut() {
                        l.exchanges.clear();
                    }
                }
                let cached = Cached {
                    records: out.records,
                    runs: out.runs,
                    jev: out.jev,
                    routing: out.routing,
                    provenance: prov,
                };
                std::fs::create_dir_all(cache.parent().unwrap()).unwrap();
                std::fs::write(&cache, serde_json::to_string(&cached).unwrap()).unwrap();
                eprintln!(
                    "{id}: {} records, peak rss {} KiB",
                    cached.records.len(),
                    rss_peak_kib()
                );
                cached
            }
        };
        provenance.push(cached.provenance);
        outputs.push(TaskOutput {
            id: id.into(),
            split,
            records: cached.records,
            runs: cached.runs,
            jev: cached.jev,
            routing: cached.routing,
        });
    }

    let mut branches = BTreeMap::new();
    let mut repeated = 0;
    if let Some(j) = judge.as_mut() {
        branches = judge_branches(&outputs, j.transport_mut());
        // Persist paid exchanges before the next live phase.
        j.transport_mut().save(&jev_dir);
        if let Some(l) = j.transport_mut().live.as_mut() {
            l.exchanges.clear();
        }
        let recorded = merged_validity(&[&jev_dir]);
        if recorded["repeated_requests"].as_u64().unwrap_or(0) == 0 {
            repeated = repeat_dev(&outputs, j.transport_mut());
        }
        j.transport_mut().save(&jev_dir);
    }

    let all: Vec<Record> = outputs.iter().flat_map(|t| t.records.clone()).collect();
    let leakage = db::leakage(&all, &splits, &queries);
    assert!(leakage.errors.is_empty(), "{:?}", leakage.errors);

    let prereg = repo_root().join("docs/phase-5/preregistration.md");
    let unsealed = std::env::var("OXIDE_PHASE5_UNSEAL_TEST").as_deref() == Ok("1");
    let prereg_sha = std::fs::read(&prereg).ok().map(|b| db::sha256_hex(&b));
    assert!(
        !unsealed || prereg_sha.as_deref() == Some(PREREGISTRATION_SHA256),
        "unsealing the test split needs the frozen preregistration, unedited"
    );
    // A filtered smoke run never overwrites the committed results.
    let out = match &only {
        Some(_) => data.join("smoke"),
        None => docs.join("contextbench"),
    };
    std::fs::create_dir_all(&out).unwrap();
    let write = |file: &str, v: &Value| {
        let text = serde_json::to_string_pretty(v).unwrap() + "\n";
        std::fs::write(out.join(file), text).unwrap();
    };
    let tasks_of = |s: Split| outputs.iter().filter(|t| t.split == s).collect::<Vec<_>>();
    let suffix = if mode == "off" { "" } else { "-jev" };
    let named = [
        (Split::Dev, "dev"),
        (Split::Calibration, "calibration"),
        (Split::Test, "test"),
    ];
    for (split, name) in named {
        if split == Split::Test && !unsealed {
            continue;
        }
        let mut m = evaluate(&tasks_of(split), &branches);
        m["preregistration_sha256"] = json!(prereg_sha);
        write(&format!("metrics-{name}{suffix}.json"), &m);
        if split == Split::Test && mode != "off" {
            let validity = merged_validity(&[&fixture_jev, &jev_dir]);
            write("gates.json", &gates(&m, &validity, &tasks_of(split)));
        }
    }

    // Calibration of JEV's reported confidence (preregistration § 5).
    if mode != "off" {
        let cal = jev_confidence_points(&tasks_of(Split::Calibration));
        let sufficient = db::sufficient_for_calibration(&cal);
        let mut report = json!({
            "points": cal.len(), "sufficient": sufficient,
            "calibration_split": phase5::confidence(&cal),
        });
        report["status"] = json!("NOT CALIBRATED");
        report["reason"] = json!(if !sufficient {
            "insufficient calibration-split sample"
        } else if !unsealed {
            "test split sealed: the fitted map has not been checked"
        } else {
            "binned test-split ECE above 0.05"
        });
        if sufficient && unsealed {
            let map = db::fit_bins(&cal);
            let test = jev_confidence_points(&tasks_of(Split::Test));
            let mapped: Vec<(f64, bool)> = test
                .iter()
                .map(|(c, y)| (db::apply_bins(&map, *c), *y))
                .collect();
            report["bins_fitted_on_calibration"] = json!(map);
            report["test_raw"] = phase5::confidence(&test);
            report["test_after_binning"] = phase5::confidence(&mapped);
            let ece = db::ece(&mapped);
            if db::sufficient_for_calibration(&mapped) && ece <= 0.05 {
                report["status"] = json!("CALIBRATED");
                report["reason"] = json!("binned test-split ECE <= 0.05");
            } else if !db::sufficient_for_calibration(&mapped) {
                report["reason"] = json!("insufficient test-split sample");
            }
        }
        write("calibration.json", &report);
        write(
            "jev-validity.json",
            &json!({
                "contextbench": merged_validity(&[&jev_dir]),
                "fixture": merged_validity(&[&fixture_jev]),
                "repeats_sent_this_run": repeated,
            }),
        );
    }

    let records: Vec<&Record> = all.iter().collect();
    let by_split: BTreeMap<String, Value> = named
        .iter()
        .map(|(s, name)| {
            let r: Vec<&Record> = records.iter().copied().filter(|r| r.split == *s).collect();
            // Sealed test labels stay unread, even as counts.
            let composition = if *s == Split::Test && !unsealed {
                json!("sealed")
            } else {
                composition(&r)
            };
            let v = json!({"tasks": tasks_of(*s).len(), "records": r.len(), "composition": composition});
            ((*name).to_owned(), v)
        })
        .collect();
    write(
        "manifest.json",
        &json!({
            "dataset": db::FORMAT,
            "gold": GOLD_SOURCE,
            "label_rules": ["gold-membership-v1 (candidates, neighbors)",
                            "subtree-contains-gold-v1 (regions)",
                            "cb-span-innermost-v1 (span -> entities)"],
            "records_location": "machine-local (source-bearing): OXIDE_DECISIONBENCH_DATA/records/<task>.jsonl; sha256 per task below",
            "tasks": provenance,
            "splits": by_split,
            "leakage": {"errors": leakage.errors,
                        "near_duplicates_within_split": leakage.near_duplicates_within_split,
                        "shared_capsules": leakage.shared_capsules},
            "test_split": if unsealed { "unsealed under the preregistration" } else { "sealed: no test metric computed" },
            "peak_rss_kib": rss_peak_kib(),
        }),
    );
}

/// The preregistered promotion gates, read on the test split. A gate
/// without the evidence it needs is INSUFFICIENT; promotion needs all PASS.
fn gates(m: &Value, validity: &Value, tasks: &[&TaskOutput]) -> Value {
    let verdict = |ok: Option<bool>| match ok {
        Some(true) => "PASS",
        Some(false) => "FAIL",
        None => "INSUFFICIENT",
    };
    let ci_lo = |v: &Value| v["ci95"][0].as_f64();
    let repeated = validity["repeated_requests"].as_u64().unwrap_or(0);
    let stable = validity["stable_on_repeat"].as_u64().unwrap_or(0) as f64;
    let g1 = (repeated > 0).then(|| {
        validity["schema_valid_rate"].as_f64().unwrap_or(0.0) >= 0.99
            && validity["models"] == json!([MODEL])
            && stable / repeated as f64 >= 0.9
    });
    let auroc = &m["paired_within_task_auroc"]["C-minus-B"];
    // Amendment 1: +0.02 AUROC; recall non-inferiority only.
    let g2 = ci_lo(auroc).map(|lo| auroc["mean_diff"].as_f64().unwrap_or(0.0) >= 0.02 && lo > 0.0);
    let pc = &m["paired_context"];
    let recall = ci_lo(&pc["C-minus-B@1024:required_symbol_recall"]);
    let g3 = recall.map(|r| r > -0.05);
    // Mean requests per context request (candidates and neighbors at
    // 1,024 units) times mean billed tokens per valid answer.
    let tokens = validity["input_tokens"].as_f64().unwrap_or(0.0);
    let valid = validity["valid"].as_f64().unwrap_or(0.0);
    let per_task =
        tasks.iter().map(|t| t.jev.len() as f64).sum::<f64>() / tasks.len().max(1) as f64;
    let cost = (valid > 0.0).then(|| per_task * tokens / valid * jev::USD_PER_INPUT_TOKEN);
    let p95 = validity["latency_ms"]["p95"].as_f64();
    let g4 = p95.zip(cost).map(|(p, c)| p <= 2000.0 && c <= 0.01);
    let all = [g1, g2, g3, g4];
    json!({
        "preregistration_sha256": PREREGISTRATION_SHA256,
        "G1_validity": verdict(g1),
        "G2_candidate_quality": verdict(g2),
        "G3_context_quality_1024": verdict(g3),
        "G4_cost": verdict(g4),
        "inputs": {"repeated": repeated, "stable": stable, "auroc_c_minus_b": auroc,
                   "recall_ci_lo": recall,
                   "p95_latency_ms": p95, "cost_per_context_request_usd": cost},
        "promotable": all.iter().all(|g| *g == Some(true)),
    })
}
