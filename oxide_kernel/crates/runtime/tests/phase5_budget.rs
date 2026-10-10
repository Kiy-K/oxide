//! Phase 5 follow-up 3 (docs/phase-5/followup-3/preregistration.md):
//! routing budgets of 64, 96 and 128 regions on dev, with the heuristic
//! and an evaluation-only gold oracle judge. Opt-in and machine-local like
//! `phase5_followup.rs`; it never calls JEV:
//!
//! ```text
//! OXIDE_CB_WORKTREES=<dir>:<dir> \
//!   cargo test --release -p oxide-runtime --test phase5_budget -- --ignored
//! ```

mod common;

use std::collections::{BTreeMap, BTreeSet};
use std::path::Path;
use std::time::Instant;

use common::phase5::{self, Indexed, TaskInput, data_dir, fallbacks, paired, round};
use common::{covers, gold_sources, metrics, repo_root, rss_peak_kib};
use oxide_kernel::capsule::{Capsule, Question, Subject};
use oxide_kernel::context::{ContextConfig, ContextRun, baseline, build_context};
use oxide_kernel::decision::{
    Allowance, DecisionProvider, Judgment, ProviderError, ProviderIdentity, Verdict,
};
use oxide_kernel::id::EntityId;
use oxide_kernel::knowledge::SourceRef;
use oxide_kernel::retrieve::RETRIEVAL_VERSION;
use oxide_kernel::retrieve::retrieve;
use oxide_kernel::route::{ROUTER_VERSION, RouteRequest, route};
use oxide_kernel::select::SELECTOR_VERSION;
use oxide_kernel::store::KnowledgeStore;
use oxide_runtime::decisionbench::{self as db, Split};
use oxide_runtime::jev::{Disclosure, MODEL, USD_PER_INPUT_TOKEN, request};
use serde_json::{Value, json};

const PREREGISTRATION_SHA256: &str =
    "46afec24e1ad2f136654f8c1b2117bb506ea7441c313e876b81101787de49263";
const REGIONS: [usize; 3] = [64, 96, 128];
const BUDGETS: [u32; 3] = [256, 1024, 4096];
/// Recorded p50 JEV round trip and the concurrency it is estimated at.
const ROUND_TRIP_MS: f64 = 306.0;
const CONCURRENCY: f64 = 16.0;

fn config(regions: usize) -> ContextConfig {
    let mut c = baseline();
    c.route_limits.max_regions = regions;
    c.graph.max_nodes = regions;
    c
}

/// Evaluation-only judge that knows the gold: never a product path.
struct Oracle<'a> {
    symbols: &'a BTreeSet<EntityId>,
    other: &'a BTreeSet<EntityId>,
}

impl DecisionProvider for Oracle<'_> {
    fn identity(&self) -> ProviderIdentity {
        ProviderIdentity {
            name: "gold-oracle".into(),
            version: "eval-only".into(),
        }
    }
    // Routing stays unjudged, so the oracle sees the heuristic's route.
    fn supports(&self, question: Question) -> bool {
        question != Question::BranchValue
    }
    fn judge(&mut self, capsules: &[Capsule]) -> Result<Vec<Judgment>, ProviderError> {
        let value = |c: &Capsule| match &c.subject {
            Subject::Candidate(id) if self.symbols.contains(id) => 1.0,
            Subject::Candidate(id) if self.other.contains(id) => 0.5,
            _ => 0.0,
        };
        Ok(capsules
            .iter()
            .map(|c| {
                let verdict = Verdict::Value {
                    value: value(c),
                    confidence: Some(1.0),
                };
                Judgment::of(c, verdict)
            })
            .collect())
    }
}

/// Gold symbols at each stage of one run.
fn stages(run: &ContextRun, gold: &[EntityId], sources: &[SourceRef]) -> Value {
    let routed: BTreeSet<&EntityId> = run.route.candidates.iter().map(|c| &c.entity).collect();
    let graph: BTreeSet<&EntityId> = run.graph.nodes.iter().map(|n| &n.entity.id).collect();
    let planned: BTreeSet<&EntityId> = run.plan.items.iter().map(|p| &p.entity).collect();
    let count = |s: &BTreeSet<&EntityId>| gold.iter().filter(|g| s.contains(g)).count();
    let packed = sources
        .iter()
        .filter(|s| run.bundle.items.iter().any(|i| covers(&i.source, s)))
        .count();
    json!({"routed": count(&routed), "graph": count(&graph), "planned": count(&planned),
           "packed": packed})
}

/// How many capsules a JEV run would send here (relevance, then
/// expansion), and their rendered request bytes.
fn capsule_load(run: &ContextRun) -> (usize, usize) {
    let all = run.capsules.iter().chain(&run.plan.capsules);
    let bytes = all.map(|c| request(c, MODEL, Disclosure::Source).len());
    (run.capsules.len() + run.plan.capsules.len(), bytes.sum())
}

fn measure(t: &Value, data: &Path) -> Value {
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
    let symbols: Vec<EntityId> = all
        .iter()
        .filter(|g| matches!(g, EntityId::Symbol(_)))
        .cloned()
        .collect();
    let symbol_sources = gold_sources(&view, &symbols);
    let symbol_set: BTreeSet<EntityId> = symbols.iter().cloned().collect();
    let other: BTreeSet<EntityId> = all
        .iter()
        .filter(|g| !symbol_set.contains(g))
        .cloned()
        .collect();
    let recall =
        |run: &ContextRun| metrics(&all, &all_sources, run)["required_symbol_recall"].clone();
    let mut rows = BTreeMap::new();
    for regions in REGIONS {
        let config = config(regions);
        if regions == 64 {
            assert_eq!(config, baseline(), "N = 64 must be the baseline");
        }
        // One timed route, as the context pipeline runs it.
        let request = phase5::request(&input, 1024);
        let candidates = retrieve(
            &view,
            &request.query,
            &request.context,
            request.semantic.clone(),
            config.retrieval,
        )
        .unwrap();
        let route_request = RouteRequest {
            snapshot: candidates.snapshot.clone(),
            query: request.query.clone(),
            entry_points: candidates.entries.clone(),
            limits: config.route_limits,
            policy: config.route_policy.clone(),
        };
        let mut allowance = Allowance {
            remaining: config.judgments,
        };
        let started = Instant::now();
        let routed = route(&view, &route_request, None, &mut allowance, config.decision).unwrap();
        let route_ms = started.elapsed().as_secs_f64() * 1e3;

        let mut row = json!({"route_ms": route_ms, "gold_symbols": symbols.len()});
        for budget in BUDGETS {
            let request = phase5::request(&input, budget);
            let started = Instant::now();
            let heuristic = build_context(&view, &view, &request, &config, None).unwrap();
            let heuristic_ms = started.elapsed().as_secs_f64() * 1e3;
            assert_eq!(heuristic.route, routed, "{regions}: route not reproducible");
            let mut oracle = Oracle {
                symbols: &symbol_set,
                other: &other,
            };
            let ceiling =
                build_context(&view, &view, &request, &config, Some(&mut oracle)).unwrap();
            // Its trace also records the unsupported branch questions.
            assert_eq!(
                (&ceiling.route.candidates, &ceiling.route.trace.steps),
                (&routed.candidates, &routed.trace.steps),
                "{regions}: the oracle changed the route"
            );
            row[format!("heuristic@{budget}")] = json!({
                "stages": stages(&heuristic, &symbols, &symbol_sources),
                "required_symbol_recall": recall(&heuristic), "latency_ms": heuristic_ms});
            // Unsupported: the routing questions it declines. Any other
            // fallback would mean the ceiling ran partly on the heuristic.
            let mut oracle_fallbacks = fallbacks(&ceiling);
            oracle_fallbacks
                .as_object_mut()
                .unwrap()
                .remove("Unsupported");
            row[format!("oracle@{budget}")] = json!({
                "stages": stages(&ceiling, &symbols, &symbol_sources),
                "required_symbol_recall": recall(&ceiling), "fallbacks": oracle_fallbacks});
            if budget == 1024 {
                let work = heuristic.route.trace.work;
                let (capsules, bytes) = capsule_load(&heuristic);
                let edges: usize = heuristic.graph.nodes.iter().map(|n| n.edges.len()).sum();
                row["work"] = json!({
                    "regions_loaded": work.regions_loaded, "edges_examined": work.edges_examined,
                    "max_depth_reached": work.max_depth_reached,
                    "stopped_by": format!("{:?}", heuristic.route.trace.stopped_by),
                    "graph_nodes": heuristic.graph.nodes.len(),
                    "graph_omitted": heuristic.graph.omitted, "graph_edges": edges,
                    "jev_capsules": capsules, "jev_request_bytes": bytes});
            }
        }
        rows.insert(regions.to_string(), row);
    }
    drop(view);
    drop(store);
    let _ = std::fs::remove_dir_all(&dir);
    json!({"task": input.id, "budgets": rows, "inputs": inputs(), "peak_rss_kib": rss_peak_kib()})
}

/// What a cached task result depends on besides the task itself.
fn inputs() -> Value {
    json!({"preregistration_sha256": PREREGISTRATION_SHA256, "router": ROUTER_VERSION,
           "selector": SELECTOR_VERSION, "retrieval": RETRIEVAL_VERSION})
}

/// Input tokens per request byte over every recorded Phase 5 exchange.
fn tokens_per_byte(dirs: &[&Path]) -> f64 {
    let (mut tokens, mut bytes) = (0u64, 0usize);
    for path in dirs.iter().flat_map(|d| phase5::sessions(d)) {
        let v: Value = serde_json::from_str(&std::fs::read_to_string(&path).unwrap()).unwrap();
        for e in v["exchanges"].as_array().unwrap() {
            if let Some(t) = e["response"]["usage"]["input_tokens"].as_u64() {
                tokens += t;
                bytes += e["request"].to_string().len();
            }
        }
    }
    tokens as f64 / bytes as f64
}

fn p50(m: &BTreeMap<String, f64>) -> f64 {
    let mut xs: Vec<f64> = m.values().copied().collect();
    xs.sort_by(f64::total_cmp);
    xs[(xs.len() - 1) / 2]
}

/// Per-task values at `path` under budget `n`.
fn get(tasks: &[Value], n: usize, path: &[&str]) -> BTreeMap<String, f64> {
    tasks
        .iter()
        .filter_map(|t| {
            let mut v = &t["budgets"][n.to_string()];
            for p in path {
                v = &v[*p];
            }
            Some((t["task"].as_str()?.to_owned(), v.as_f64()?))
        })
        .collect()
}

fn summary(tasks: &[Value], tpb: f64) -> Value {
    let mean = |m: &BTreeMap<String, f64>| m.values().sum::<f64>() / m.len() as f64;
    let sum = |m: &BTreeMap<String, f64>| m.values().sum::<f64>();
    let base_latency = p50(&get(tasks, 64, &["heuristic@1024", "latency_ms"]));
    let base_capsules = mean(&get(tasks, 64, &["work", "jev_capsules"]));
    let mut out = BTreeMap::new();
    for n in REGIONS {
        let g = |path: &[&str]| get(tasks, n, path);
        let work = |k: &str| {
            let v: BTreeSet<String> = tasks
                .iter()
                .map(|t| t["budgets"][n.to_string()]["work"][k].to_string())
                .collect();
            json!(v)
        };
        let gold = sum(&g(&["gold_symbols"]));
        let capsules = mean(&g(&["work", "jev_capsules"]));
        let tokens = mean(&g(&["work", "jev_request_bytes"])) * tpb;
        let stage = |k: &str, s: &str| round(sum(&g(&[k, "stages", s])) / gold);
        let mut row = json!({
            "gold_symbols": gold,
            "routed_gold_symbol_recall": stage("heuristic@1024", "routed"),
            "graph_gold_symbol_recall": stage("heuristic@1024", "graph"),
            "route_ms_p50": round(p50(&g(&["route_ms"]))),
            "heuristic_latency_ms_p50": round(p50(&g(&["heuristic@1024", "latency_ms"]))),
            "regions_loaded": round(mean(&g(&["work", "regions_loaded"]))),
            "edges_examined": round(mean(&g(&["work", "edges_examined"]))),
            "graph_nodes": round(mean(&g(&["work", "graph_nodes"]))),
            "graph_edges": round(mean(&g(&["work", "graph_edges"]))),
            "stopped_by": work("stopped_by"), "max_depth_reached": work("max_depth_reached"),
            "est_jev_requests": round(capsules),
            "est_jev_request_kib": round(mean(&g(&["work", "jev_request_bytes"])) / 1024.0),
            "est_jev_input_tokens": round(tokens),
            "est_jev_cost_usd": round(tokens * USD_PER_INPUT_TOKEN),
        });
        for judge in ["heuristic", "oracle"] {
            for b in BUDGETS {
                let k = format!("{judge}@{b}");
                let mut cell = json!({
                    "required_symbol_recall": round(mean(&g(&[&k, "required_symbol_recall"]))),
                    "planned_gold_symbols": stage(&k, "planned"),
                    "packed_gold_symbols": stage(&k, "packed")});
                if n != 64 {
                    cell["recall_vs_64"] = paired(
                        &g(&[&k, "required_symbol_recall"]),
                        &get(tasks, 64, &[&k, "required_symbol_recall"]),
                    );
                }
                row[k] = cell;
            }
        }
        if n != 64 {
            let gain = &row["heuristic@1024"]["recall_vs_64"];
            let lo = gain["ci95"][0].as_f64().unwrap_or(f64::NEG_INFINITY);
            let latency = p50(&g(&["heuristic@1024", "latency_ms"]));
            row["passes_decision_rule"] = json!(
                gain["mean_diff"].as_f64().unwrap_or(0.0) >= 0.02
                    && lo > 0.0
                    && latency <= 1.5 * base_latency
            );
            let extra = capsules - base_capsules;
            row["est_extra_jev_requests"] = round(extra);
            row["est_extra_jev_latency_ms_at_16"] =
                round((extra / CONCURRENCY).ceil().max(0.0) * ROUND_TRIP_MS);
        }
        out.insert(n.to_string(), row);
    }
    json!(out)
}

#[test]
#[ignore = "machine-local: needs ContextBench worktrees"]
fn routing_budget() {
    let root = repo_root();
    let prereg = std::fs::read(root.join("docs/phase-5/followup-3/preregistration.md")).unwrap();
    assert_eq!(
        db::sha256_hex(&prereg),
        PREREGISTRATION_SHA256,
        "preregistration edited"
    );
    let docs = root.join("docs/phase-5/decisionbench-v1");
    let read = |f: &str| -> Value {
        serde_json::from_str(&std::fs::read_to_string(docs.join(f)).unwrap()).unwrap()
    };
    let spec = read("contextbench-tasks.json");
    let splits: BTreeMap<String, Split> =
        serde_json::from_value(read("splits.json")["repositories"].clone()).unwrap();
    let data = data_dir();
    let tpb = tokens_per_byte(&[&docs.join("fixture/jev"), &data.join("jev")]);
    let cache_dir = data.join("followup-3");
    std::fs::create_dir_all(&cache_dir).unwrap();
    let mut tasks = Vec::new();
    for t in spec["tasks"].as_array().unwrap() {
        // Dev only: calibration and test stay untouched.
        if splits[t["repo"].as_str().unwrap()] != Split::Dev {
            continue;
        }
        let id = t["id"].as_str().unwrap();
        let path = cache_dir.join(format!("{id}.json"));
        let cached = std::fs::read_to_string(&path)
            .ok()
            .map(|text| serde_json::from_str::<Value>(&text).unwrap())
            .filter(|r| r["inputs"] == inputs());
        let r = cached.unwrap_or_else(|| {
            let r = measure(t, &data);
            std::fs::write(&path, serde_json::to_string(&r).unwrap()).unwrap();
            r
        });
        eprintln!("{id}");
        tasks.push(r);
    }
    let out = json!({"preregistration_sha256": PREREGISTRATION_SHA256, "split": "dev",
                     "tasks": tasks.len(), "tokens_per_request_byte": round(tpb),
                     "usd_per_input_token": USD_PER_INPUT_TOKEN,
                     // Measured when each task ran, so valid from cache too.
                     "process_peak_rss_kib": tasks.iter().filter_map(|t| t["peak_rss_kib"].as_u64()).max(),
                     "oracle_fallbacks": tasks.iter().flat_map(|t| t["budgets"].as_object().unwrap().values())
                         .flat_map(|n| n.as_object().unwrap().iter())
                         .filter(|(k, v)| k.starts_with("oracle@") && !v["fallbacks"].as_object().unwrap().is_empty())
                         .count(),
                     "regions": summary(&tasks, tpb)});
    let text = serde_json::to_string_pretty(&out).unwrap() + "\n";
    std::fs::write(root.join("docs/phase-5/followup-3/budget.json"), &text).unwrap();
    eprintln!("{text}");
}
