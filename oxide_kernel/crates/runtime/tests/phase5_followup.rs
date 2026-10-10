//! Phase 5 follow-up (docs/phase-5/followup/preregistration.md): JEV
//! orchestration latency, offline at each answer's recorded live round
//! trip, and where routing loses gold. Opt-in and machine-local, like
//! `phase5_contextbench.rs`, whose recorded exchanges it replays:
//!
//! ```text
//! OXIDE_CB_WORKTREES=<dir>:<dir> \
//!   cargo test --release -p oxide-runtime --test phase5_followup -- --ignored
//! ```
//!
//! No live JEV call is possible here: the only transport is [`Paced`].
//! Calibration-split confirmation of a dev-accepted selective-judging
//! allowance or routing variant runs only when named, by
//! `OXIDE_FOLLOWUP_CONFIRM_D=<K>` and `OXIDE_FOLLOWUP_CONFIRM_ROUTE=<label>`.
//! The test split serves the descriptive latency and waterfall only.

mod common;

use std::collections::{BTreeMap, BTreeSet};
use std::path::Path;
use std::time::{Duration, Instant};

use common::phase5::{
    self, CandidatesOnly, Indexed, TaskInput, data_dir, fallbacks, jev_config, paired, round,
};
use common::{covers, gold_sources, metrics, repo_root, rss_peak_kib};
use oxide_kernel::capsule::{Capsule, Question};
use oxide_kernel::context::{ContextConfig, ContextRun, baseline, build_context, graph};
use oxide_kernel::decision::{
    Allowance, DecisionPolicy, DecisionProvider, Judgment, ProviderError, ProviderIdentity,
};
use oxide_kernel::id::EntityId;
use oxide_kernel::knowledge::SourceRef;
use oxide_kernel::lexical::{LexicalRequest, terms};
use oxide_kernel::retrieve::{EntryPoint, retrieve};
use oxide_kernel::route::{RouteLimits, RouteRequest, baseline_policy, route};
use oxide_kernel::store::{KnowledgeStore, MAX_REQUEST_ITEMS, ReadView};
use oxide_runtime::decisionbench::{self as db, Split};
use oxide_runtime::jev::{
    Jev, JevConfig, Recorded, Replay, Sent, Transport, TransportError, fan_out,
};
use serde_json::{Value, json};

const PREREGISTRATION_SHA256: &str =
    "482a74e726896743b6be07ab8b9b7555577cbcadde9375f049c518b650531d33";
const TOKENS: u32 = 1024;
const CONCURRENCY: [usize; 3] = [4, 8, 16];
const ALLOWANCES: [usize; 2] = [16, 32];
/// Phase 5 live run, C at 256 units (preregistration).
const LIVE_P50_MS: f64 = 21_126.0;
/// Region bound of the "unbounded" reachability routes, so one huge
/// repository cannot run away; reported when reached.
const REACH_REGIONS: usize = 200_000;

/// Recorded answers, each served after its recorded live round trip.
struct Paced<'a> {
    answers: &'a BTreeMap<String, (Recorded, Duration)>,
    /// Wall time spent inside the transport.
    wall: Duration,
}

impl Paced<'_> {
    fn serve(&self, body: &str, timeout: Duration) -> Result<String, TransportError> {
        let Some((recorded, elapsed)) = self.answers.get(&db::sha256_hex(body.as_bytes())) else {
            return Err(TransportError::Unavailable("not recorded".into()));
        };
        std::thread::sleep((*elapsed).min(timeout));
        if *elapsed > timeout {
            return Err(TransportError::Timeout);
        }
        match recorded {
            Recorded::Response(r) => Ok(r.clone()),
            Recorded::Failed(e) => Err(e.clone()),
        }
    }
}

impl Transport for Paced<'_> {
    fn post(&mut self, body: &str, timeout: Duration) -> Result<String, TransportError> {
        let started = Instant::now();
        let answer = self.serve(body, timeout);
        self.wall += started.elapsed();
        answer
    }

    fn post_all(
        &mut self,
        bodies: &[&str],
        deadline: Instant,
        concurrency: usize,
    ) -> Vec<Option<Sent>> {
        let started = Instant::now();
        let this = &*self;
        let sent = fan_out(bodies, deadline, concurrency, |b, left| this.serve(b, left));
        self.wall += started.elapsed();
        sent
    }
}

/// Every recorded body's first answer and its live round trip.
fn recorded(dirs: &[&Path]) -> BTreeMap<String, (Recorded, Duration)> {
    let mut out = BTreeMap::new();
    for path in dirs.iter().flat_map(|d| phase5::sessions(d)) {
        let text = std::fs::read_to_string(&path).unwrap();
        let v: Value = serde_json::from_str(&text).unwrap();
        let replay = Replay::from_json(&text).unwrap();
        for e in v["exchanges"].as_array().unwrap() {
            let key = e["request_sha256"].as_str().unwrap();
            let recorded = &replay.exchanges[key];
            let answered = e["response"].is_object() || e["response_text"].is_string();
            if answered && matches!(recorded, Recorded::Response(_)) {
                let elapsed = Duration::from_millis(e["elapsed_ms"].as_u64().unwrap());
                out.entry(key.to_owned())
                    .or_insert((recorded.clone(), elapsed));
            }
        }
    }
    out
}

/// Times `judge` calls: each one is a sequential barrier in `build_context`.
struct Timed<'a> {
    inner: &'a mut dyn DecisionProvider,
    calls: usize,
    time: Duration,
}

impl DecisionProvider for Timed<'_> {
    fn identity(&self) -> ProviderIdentity {
        self.inner.identity()
    }
    fn supports(&self, question: Question) -> bool {
        self.inner.supports(question)
    }
    fn judge(&mut self, capsules: &[Capsule]) -> Result<Vec<Judgment>, ProviderError> {
        let started = Instant::now();
        let answer = self.inner.judge(capsules);
        self.time += started.elapsed();
        self.calls += 1;
        answer
    }
}

fn ms(d: Duration) -> f64 {
    d.as_secs_f64() * 1e3
}

/// One JEV context run at recorded service latency.
fn jev_run(
    view: &(impl ReadView + oxide_kernel::source::SourceProvider),
    input: &TaskInput<'_>,
    config: &ContextConfig,
    answers: &BTreeMap<String, (Recorded, Duration)>,
    concurrency: usize,
) -> (ContextRun, Value) {
    let jev_config = JevConfig {
        concurrency,
        ..jev_config()
    };
    let mut jev = Jev::new(
        jev_config,
        Paced {
            answers,
            wall: Duration::ZERO,
        },
    )
    .unwrap();
    let request = phase5::request(input, TOKENS);
    let started = Instant::now();
    let (run, calls, judge) = {
        let mut only = CandidatesOnly(&mut jev);
        let mut timed = Timed {
            inner: &mut only,
            calls: 0,
            time: Duration::ZERO,
        };
        let run = build_context(view, view, &request, config, Some(&mut timed)).unwrap();
        (run, timed.calls, timed.time)
    };
    let latency = started.elapsed();
    let stats = jev.stats;
    let transport = jev.into_transport().wall;
    let mut failed: usize = 0;
    for (reason, n) in fallbacks(&run).as_object().unwrap() {
        if reason != "Unsupported" {
            failed += n.as_u64().unwrap() as usize;
        }
    }
    let row = json!({
        "latency_ms": ms(latency), "judge_ms": ms(judge), "transport_ms": ms(transport),
        "client_ms": ms(judge.saturating_sub(transport)),
        "outside_judge_ms": ms(latency.saturating_sub(judge)),
        "judge_calls": calls, "requests": stats.requests, "input_tokens": stats.input_tokens,
        "cost_usd": stats.cost_usd(), "fallbacks": failed,
        "judged": stats.answered + failed,
    });
    (run, row)
}

/// Reached entities, and whether the region bound stopped the route.
type Reach = (BTreeSet<EntityId>, bool);

/// Entities reachable by baseline-policy routing from `entries` within
/// `depth`, with region, edge and fanout work unbounded (up to
/// [`REACH_REGIONS`] regions): (reached, whether that bound stopped it).
fn reach(
    view: &impl ReadView,
    run: &ContextRun,
    query: &oxide_kernel::query::Query,
    entries: Vec<EntryPoint>,
    depth: usize,
) -> Reach {
    let request = RouteRequest {
        snapshot: run.candidates.snapshot.clone(),
        query: query.clone(),
        entry_points: entries,
        limits: RouteLimits {
            max_regions: REACH_REGIONS,
            max_depth: depth,
            max_fanout: MAX_REQUEST_ITEMS,
            max_edges: usize::MAX / 4,
            max_judgments: 0,
        },
        policy: baseline_policy(),
    };
    let mut none = Allowance { remaining: 0 };
    let routed = route(view, &request, None, &mut none, DecisionPolicy::default()).unwrap();
    let capped = routed.trace.work.regions_loaded >= REACH_REGIONS;
    (
        routed.candidates.into_iter().map(|c| c.entity).collect(),
        capped,
    )
}

/// Where one run lost (or packed) gold symbol `g`.
fn stage(run: &ContextRun, g: &EntityId, source: &SourceRef) -> &'static str {
    let planned = run.plan.items.iter().any(|p| p.entity == *g);
    if run.bundle.items.iter().any(|i| covers(&i.source, source)) {
        return if planned {
            "packed"
        } else {
            "packed_via_enclosing"
        };
    }
    if !run.route.candidates.iter().any(|c| c.entity == *g) {
        "unrouted"
    } else if !run.graph.nodes.iter().any(|n| n.entity.id == *g) {
        "graph_truncation"
    } else if !planned {
        "selection"
    } else {
        "budget"
    }
}

/// Per-gold waterfall rows of one task (preregistered decision order).
fn waterfall(
    view: &impl ReadView,
    gold: &[EntityId],
    sources: &[SourceRef],
    heuristic: &ContextRun,
    jev: &ContextRun,
    query: &oxide_kernel::query::Query,
) -> Value {
    let hits = view
        .lexical(&LexicalRequest {
            terms: terms(&query.text),
            limit: MAX_REQUEST_ITEMS,
        })
        .unwrap()
        .hits;
    let rank = |g: &EntityId| hits.iter().position(|h| h.entity == *g).map(|r| r + 1);
    let entries = heuristic.candidates.entries.clone();
    let wide: Vec<EntryPoint> = hits
        .iter()
        .take(200)
        .map(|h| EntryPoint {
            entity: h.entity.clone(),
            channels: Vec::new(),
            seeds: Vec::new(),
        })
        .collect();
    // Computed only when some gold needs it: each is a large route.
    let mut tests: Vec<(&str, Option<Reach>)> = vec![
        ("work_limit", None),
        ("entry_limit", None),
        ("depth_limit", None),
    ];
    let mut rows = Vec::new();
    for (g, source) in gold.iter().zip(sources) {
        let h = stage(heuristic, g, source);
        let j = stage(jev, g, source);
        let mut cause = Value::Null;
        if h == "unrouted" {
            cause = json!("lexical_miss_and_graph_gap");
            for (name, reached) in tests.iter_mut() {
                let reached = reached.get_or_insert_with(|| match *name {
                    "work_limit" => reach(view, heuristic, query, entries.clone(), 2),
                    "entry_limit" => reach(view, heuristic, query, wide.clone(), 2),
                    _ => reach(view, heuristic, query, entries.clone(), 3),
                });
                if reached.0.contains(g) {
                    cause = json!(name);
                    break;
                }
            }
        }
        rows.push(json!({"gold": common::name(g), "lexical_rank": rank(g),
                         "heuristic": h, "jev": j, "unrouted_cause": cause}));
    }
    let capped: Vec<&str> = tests
        .iter()
        .filter(|(_, r)| r.as_ref().is_some_and(|r| r.1))
        .map(|(n, _)| *n)
        .collect();
    json!({"gold": rows, "reach_capped": capped, "lexical_hits": hits.len()})
}

fn variants() -> Vec<(&'static str, ContextConfig)> {
    let with = |f: fn(&mut ContextConfig)| {
        let mut c = baseline();
        f(&mut c);
        c
    };
    vec![
        ("baseline", baseline()),
        ("lexical_limit=10", with(|c| c.retrieval.lexical_limit = 10)),
        ("lexical_limit=40", with(|c| c.retrieval.lexical_limit = 40)),
        ("max_depth=1", with(|c| c.route_limits.max_depth = 1)),
        ("max_depth=3", with(|c| c.route_limits.max_depth = 3)),
        ("max_fanout=8", with(|c| c.route_limits.max_fanout = 8)),
        ("max_fanout=32", with(|c| c.route_limits.max_fanout = 32)),
        (
            "lexical_only=64",
            with(|c| {
                c.retrieval.lexical_limit = 64;
                c.route_limits.max_depth = 0;
            }),
        ),
    ]
}

/// Runs every measurement of one task (cached per task by the caller).
fn measure(
    t: &Value,
    split: Split,
    data: &Path,
    answers: &BTreeMap<String, (Recorded, Duration)>,
    confirm_d: Option<usize>,
    confirm_route: Option<&str>,
) -> Value {
    let Indexed {
        store,
        key,
        gold,
        span_counts,
        index_seconds,
        dir,
        ..
    } = phase5::index_task(t, data);
    let view = store.open(&key).unwrap();
    let input = TaskInput {
        id: t["id"].as_str().unwrap(),
        repository: t["repo"].as_str().unwrap(),
        split,
        query: t["query"].as_str().unwrap(),
        symbols: Vec::new(),
        gold: gold.clone(),
    };
    let symbols: Vec<EntityId> = gold
        .verified
        .iter()
        .filter(|g| matches!(g, EntityId::Symbol(_)))
        .cloned()
        .collect();
    let sources = gold_sources(&view, &symbols);
    let all: Vec<EntityId> = gold.verified.iter().cloned().collect();
    let all_sources = gold_sources(&view, &all);
    let request = phase5::request(&input, TOKENS);
    let base = baseline();

    // A: the heuristic, end to end and by stage.
    let started = Instant::now();
    let heuristic = build_context(&view, &view, &request, &base, None).unwrap();
    let a_ms = ms(started.elapsed());
    let started = Instant::now();
    let candidates = retrieve(
        &view,
        &request.query,
        &request.context,
        request.semantic.clone(),
        base.retrieval,
    )
    .unwrap();
    let retrieve_ms = ms(started.elapsed());
    let route_request = RouteRequest {
        snapshot: candidates.snapshot.clone(),
        query: request.query.clone(),
        entry_points: candidates.entries.clone(),
        limits: base.route_limits,
        policy: base.route_policy.clone(),
    };
    let started = Instant::now();
    let mut allowance = Allowance {
        remaining: base.judgments,
    };
    let routed = route(&view, &route_request, None, &mut allowance, base.decision).unwrap();
    let route_ms = ms(started.elapsed());
    let started = Instant::now();
    graph(&view, &candidates, &routed, base.graph).unwrap();
    let graph_ms = ms(started.elapsed());
    let mut a = metrics(&all, &all_sources, &heuristic);
    a["latency_ms"] = json!(a_ms);
    a["stages_ms"] = json!({"retrieve": retrieve_ms, "route": route_ms, "graph": graph_ms,
        "capsules_select_pack": a_ms - retrieve_ms - route_ms - graph_ms});

    // B (sequential) and C-k: the same answers, so the same run.
    let mut runs = BTreeMap::new();
    let quality = |run: &ContextRun, mut row: Value| {
        let m = metrics(&all, &all_sources, run);
        for k in [
            "required_symbol_recall",
            "gold_token_share",
            "required_file_recall",
        ] {
            row[k] = m[k].clone();
        }
        row
    };
    runs.insert("A".to_owned(), a);
    let (sequential, row) = jev_run(&view, &input, &base, answers, 1);
    runs.insert("B".to_owned(), quality(&sequential, row));
    for k in CONCURRENCY {
        let (run, row) = jev_run(&view, &input, &base, answers, k);
        assert!(run == sequential, "{}: C-{k} differs from B", input.id);
        runs.insert(format!("C-{k}"), quality(&run, row));
    }
    let d_allowances: Vec<usize> = match split {
        Split::Dev => ALLOWANCES.to_vec(),
        Split::Calibration => confirm_d.into_iter().collect(),
        _ => Vec::new(),
    };
    for allowance in d_allowances {
        let config = ContextConfig {
            judgments: allowance,
            ..base.clone()
        };
        let (run, row) = jev_run(&view, &input, &config, answers, 8);
        runs.insert(format!("D-{allowance}"), quality(&run, row));
    }

    let waterfall = waterfall(
        &view,
        &symbols,
        &sources,
        &heuristic,
        &sequential,
        &request.query,
    );

    let mut routing = BTreeMap::new();
    for (label, config) in variants() {
        let wanted = match split {
            Split::Dev => true,
            Split::Calibration => label == "baseline" || Some(label) == confirm_route,
            _ => false,
        };
        if !wanted {
            continue;
        }
        let run = build_context(&view, &view, &request, &config, None).unwrap();
        let mut row = metrics(&all, &all_sources, &run);
        let work = run.route.trace.work;
        row["regions_loaded"] = json!(work.regions_loaded);
        row["edges_examined"] = json!(work.edges_examined);
        row["entry_points"] = json!(run.candidates.entries.len());
        routing.insert(label.to_owned(), row);
    }
    drop(view);
    drop(store);
    let _ = std::fs::remove_dir_all(&dir);
    json!({"task": input.id, "split": split, "index_seconds": index_seconds,
           "gold_spans": span_counts, "verified_gold": all.len(),
           "verified_file_level": all.len() - symbols.len(),
           "runs": runs, "waterfall": waterfall, "routing": routing})
}

fn quantile(xs: &mut [f64], q: f64) -> f64 {
    xs.sort_by(f64::total_cmp);
    xs[((xs.len() - 1) as f64 * q).round() as usize]
}

fn summarize(tasks: &[&Value], config: &str) -> Value {
    let rows: Vec<&Value> = tasks
        .iter()
        .map(|t| &t["runs"][config])
        .filter(|r| r.is_object())
        .collect();
    if rows.is_empty() {
        return Value::Null;
    }
    let col = |k: &str| -> Vec<f64> { rows.iter().filter_map(|r| r[k].as_f64()).collect() };
    let mut out = json!({"tasks": rows.len()});
    for k in [
        "latency_ms",
        "judge_ms",
        "transport_ms",
        "client_ms",
        "outside_judge_ms",
        "judge_calls",
        "requests",
    ] {
        let mut xs = col(k);
        if !xs.is_empty() {
            out[k] = json!({"p50": round(quantile(&mut xs, 0.5)), "p95": round(quantile(&mut xs, 0.95))});
        }
    }
    for k in [
        "input_tokens",
        "cost_usd",
        "required_symbol_recall",
        "gold_token_share",
        "required_file_recall",
    ] {
        let xs = col(k);
        if !xs.is_empty() {
            out[k] = json!({"mean": xs.iter().sum::<f64>() / xs.len() as f64});
        }
    }
    let judged: f64 = col("judged").iter().sum();
    if judged > 0.0 {
        out["fallback_rate"] = round(col("fallbacks").iter().sum::<f64>() / judged);
    }
    out
}

fn by_task(tasks: &[&Value], f: impl Fn(&Value) -> Option<f64>) -> BTreeMap<String, f64> {
    tasks
        .iter()
        .filter_map(|t| Some((t["task"].as_str()?.to_owned(), f(t)?)))
        .collect()
}

fn waterfall_summary(tasks: &[&Value]) -> Value {
    let mut out = json!({"tasks": tasks.len()});
    let mut spans: BTreeMap<String, u64> = BTreeMap::new();
    for t in tasks {
        for (k, v) in t["gold_spans"].as_object().unwrap() {
            *spans.entry(k.clone()).or_default() += v.as_u64().unwrap();
        }
    }
    out["gold_spans"] = json!(spans);
    out["verified_gold"] = json!(
        tasks
            .iter()
            .map(|t| t["verified_gold"].as_u64().unwrap())
            .sum::<u64>()
    );
    out["verified_file_level"] = json!(
        tasks
            .iter()
            .map(|t| t["verified_file_level"].as_u64().unwrap())
            .sum::<u64>()
    );
    let rows: Vec<&Value> = tasks
        .iter()
        .flat_map(|t| t["waterfall"]["gold"].as_array().unwrap())
        .collect();
    out["gold_symbols"] = json!(rows.len());
    for key in ["heuristic", "jev", "unrouted_cause"] {
        let mut counts: BTreeMap<String, usize> = BTreeMap::new();
        for r in &rows {
            if let Some(s) = r[key].as_str() {
                *counts.entry(s.to_owned()).or_default() += 1;
            }
        }
        out[key] = json!(counts);
    }
    let unrouted: Vec<&&Value> = rows
        .iter()
        .filter(|r| r["heuristic"] == "unrouted")
        .collect();
    let ranks = |lo: u64, hi: u64| {
        unrouted
            .iter()
            .filter(|r| {
                r["lexical_rank"]
                    .as_u64()
                    .is_some_and(|x| (lo..=hi).contains(&x))
            })
            .count()
    };
    out["unrouted_lexical_rank"] = json!({
        "1-20": ranks(1, 20), "21-200": ranks(21, 200), ">200": ranks(201, u64::MAX),
        "no_hit": unrouted.iter().filter(|r| r["lexical_rank"].is_null()).count(),
    });
    out["reach_capped_tasks"] = json!(
        tasks
            .iter()
            .filter(|t| !t["waterfall"]["reach_capped"]
                .as_array()
                .unwrap()
                .is_empty())
            .count()
    );
    out
}

fn routing_summary(tasks: &[&Value]) -> Value {
    let mut out = BTreeMap::new();
    for (label, _) in variants() {
        let rows: Vec<&Value> = tasks
            .iter()
            .map(|t| &t["routing"][label])
            .filter(|r| r.is_object())
            .collect();
        if rows.is_empty() {
            continue;
        }
        let mean =
            |k: &str| rows.iter().filter_map(|r| r[k].as_f64()).sum::<f64>() / rows.len() as f64;
        let mut row = json!({"tasks": rows.len()});
        for k in [
            "routed_gold_coverage",
            "required_symbol_recall",
            "gold_token_share",
            "regions_loaded",
            "edges_examined",
            "entry_points",
        ] {
            row[k] = round(mean(k));
        }
        if label != "baseline" {
            let get = |l: &'static str, k: &'static str| {
                by_task(tasks, move |t| t["routing"][l][k].as_f64())
            };
            let coverage = paired(
                &get(label, "routed_gold_coverage"),
                &get("baseline", "routed_gold_coverage"),
            );
            let recall = paired(
                &get(label, "required_symbol_recall"),
                &get("baseline", "required_symbol_recall"),
            );
            let lo = |v: &Value| v["ci95"][0].as_f64().unwrap_or(f64::NEG_INFINITY);
            let accepted = coverage["mean_diff"].as_f64().unwrap_or(0.0) >= 0.05
                && lo(&coverage) > 0.0
                && lo(&recall) > -0.02;
            row["coverage_vs_baseline"] = coverage;
            row["recall_vs_baseline"] = recall;
            row["accepted_on_dev"] = json!(accepted);
        }
        out.insert(label, row);
    }
    json!(out)
}

#[test]
#[ignore = "machine-local: needs ContextBench worktrees and the Phase 5 recordings"]
fn phase5_followup() {
    let root = repo_root();
    let prereg = std::fs::read(root.join("docs/phase-5/followup/preregistration.md")).unwrap();
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
    let answers = recorded(&[&docs.join("fixture/jev"), &data.join("jev")]);
    let confirm_d = std::env::var("OXIDE_FOLLOWUP_CONFIRM_D")
        .ok()
        .map(|k| k.parse().unwrap());
    let confirm_route = std::env::var("OXIDE_FOLLOWUP_CONFIRM_ROUTE").ok();
    let cache_dir = data.join("followup");
    std::fs::create_dir_all(&cache_dir).unwrap();

    let mut results = Vec::new();
    for t in spec["tasks"].as_array().unwrap() {
        let id = t["id"].as_str().unwrap();
        let split = splits[t["repo"].as_str().unwrap()];
        let cache = cache_dir.join(format!("{id}.json"));
        // A cached task that lacks a requested confirmation is measured again.
        let wanted = |r: &Value| {
            split != Split::Calibration
                || (confirm_d.is_none_or(|k| r["runs"][format!("D-{k}")].is_object())
                    && confirm_route
                        .as_deref()
                        .is_none_or(|l| r["routing"][l].is_object()))
        };
        let cached = std::fs::read_to_string(&cache)
            .ok()
            .map(|text| serde_json::from_str::<Value>(&text).unwrap())
            .filter(wanted);
        let result: Value = match cached {
            Some(r) => r,
            None => {
                let r = measure(
                    t,
                    split,
                    &data,
                    &answers,
                    confirm_d,
                    confirm_route.as_deref(),
                );
                std::fs::write(&cache, serde_json::to_string(&r).unwrap()).unwrap();
                eprintln!(
                    "{id}: B {:.0} ms, C-8 {:.0} ms, peak rss {} KiB",
                    r["runs"]["B"]["latency_ms"].as_f64().unwrap(),
                    r["runs"]["C-8"]["latency_ms"].as_f64().unwrap(),
                    rss_peak_kib()
                );
                r
            }
        };
        results.push(result);
    }

    let of =
        |s: Split| -> Vec<&Value> { results.iter().filter(|r| r["split"] == json!(s)).collect() };
    let all: Vec<&Value> = results.iter().collect();
    let mut latency = json!({"preregistration_sha256": PREREGISTRATION_SHA256,
                             "budget_units": TOKENS, "tasks": all.len(),
                             "peak_rss_kib": rss_peak_kib()});
    for config in ["A", "B", "C-4", "C-8", "C-16"] {
        latency[config] = summarize(&all, config);
    }
    let b_p50 = latency["B"]["latency_ms"]["p50"].as_f64().unwrap();
    latency["simulation_check"] = json!({"live_p50_ms": LIVE_P50_MS, "simulated_b_p50_ms": b_p50,
        "within_15_percent": (b_p50 - LIVE_P50_MS).abs() <= 0.15 * LIVE_P50_MS});
    let mut stages: BTreeMap<&str, Vec<f64>> = BTreeMap::new();
    for t in &all {
        for (k, v) in t["runs"]["A"]["stages_ms"].as_object().unwrap() {
            stages
                .entry(k.as_str())
                .or_default()
                .push(v.as_f64().unwrap());
        }
    }
    latency["A_stages_ms"] = json!(
        stages
            .into_iter()
            .map(|(k, mut v)| (
                k,
                json!({
        "p50": round(quantile(&mut v, 0.5)), "p95": round(quantile(&mut v, 0.95))})
            ))
            .collect::<BTreeMap<_, _>>()
    );
    let recommended = CONCURRENCY
        .iter()
        .find(|k| {
            latency[format!("C-{k}")]["latency_ms"]["p50"]
                .as_f64()
                .unwrap()
                <= 3000.0
        })
        .copied()
        .unwrap_or(16);
    latency["recommended_concurrency"] = json!(recommended);

    let mut selective = json!({});
    for (split, name) in [(Split::Dev, "dev"), (Split::Calibration, "calibration")] {
        let tasks = of(split);
        for k in ALLOWANCES {
            let label = format!("D-{k}");
            let summary = summarize(&tasks, &label);
            if summary.is_null() {
                continue;
            }
            let recall = |l: String| {
                by_task(&tasks, move |t| {
                    t["runs"][&l]["required_symbol_recall"].as_f64()
                })
            };
            let diff = paired(&recall(label.clone()), &recall("C-8".into()));
            let acceptable = diff["mean_diff"].as_f64().unwrap_or(f64::NEG_INFINITY) >= -0.01
                && diff["ci95"][0].as_f64().unwrap_or(f64::NEG_INFINITY) > -0.05;
            selective[name][&label] = json!({"summary": summary, "recall_vs_C": diff,
                                             "acceptable": acceptable});
        }
        selective[name]["C-8"] = summarize(&tasks, "C-8");
    }

    let waterfall = json!({
        "dev": waterfall_summary(&of(Split::Dev)),
        "calibration": waterfall_summary(&of(Split::Calibration)),
        "test": waterfall_summary(&of(Split::Test)),
        "all": waterfall_summary(&all),
    });
    let routing = json!({"dev": routing_summary(&of(Split::Dev)),
                         "calibration": routing_summary(&of(Split::Calibration))});

    let out = root.join("docs/phase-5/followup");
    let write = |f: &str, v: &Value| {
        std::fs::write(out.join(f), serde_json::to_string_pretty(v).unwrap() + "\n").unwrap();
    };
    write("latency.json", &latency);
    write("selective.json", &selective);
    write("waterfall.json", &waterfall);
    write("routing.json", &routing);
}
