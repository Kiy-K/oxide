//! Phase 3 frozen retrieval/routing evaluation (docs/phase-3.md).
//!
//! Builds the retained `fixtures/py_repo` into LadybugDB under a fixed
//! `RepoId`, maps the retained Python gold (`fixtures/benchmark.json`,
//! `fixtures/structural_benchmark.json`) to v2 entity IDs, and runs every
//! frozen configuration. Entry-point retrieval and routing are measured
//! separately; nothing here measures context quality or agent success.
//!
//! The raw artifacts under `docs/phase-3/baseline-v1/` are the frozen
//! baseline: this test fails unless a run reproduces them byte for byte.
//! Changing a frozen configuration means a new baseline directory, not
//! rewriting this one. `OXIDE_FREEZE_PHASE3=1` writes the files instead.

mod common;

use std::collections::BTreeMap;

use common::{Task, mean, name, repo_root, tasks};

use oxide_kernel::decision::{Allowance, DecisionPolicy, Heuristic};
use oxide_kernel::id::{EntityId, RepoId};
use oxide_kernel::lexical::{LexicalRequest, terms};
use oxide_kernel::query::{Query, QueryContext};
use oxide_kernel::retrieve::{
    self, BASELINE, CandidateSet, ChannelState, RETRIEVAL_VERSION, RetrievalConfig,
};
use oxide_kernel::route::{
    BASELINE_LIMITS, Bound, ROUTER_VERSION, RouteRequest, RouteResult, Visit, baseline_policy,
    route,
};
use oxide_kernel::store::{KnowledgeStore, MemoryStore, ReadView};
use oxide_runtime::capture::{Scope, capture};
use oxide_runtime::derivation::derive;
use oxide_runtime::storage::LadybugStore;
use serde_json::{Value, json};

const RETRIEVAL: [(&str, RetrievalConfig); 3] = [
    (
        "lexical-only",
        RetrievalConfig {
            structural: false,
            ..BASELINE
        },
    ),
    (
        "structural-only",
        RetrievalConfig {
            lexical: false,
            ..BASELINE
        },
    ),
    ("lexical+structural", BASELINE),
];

fn retrieval(task: &Task, set: &CandidateSet) -> Value {
    let entries: Vec<&EntityId> = set.entries.iter().map(|e| &e.entity).collect();
    let rank = |g: &EntityId| entries.iter().position(|e| *e == g).map(|p| p + 1);
    let recall = |k: usize| {
        let hit = task.gold.iter().filter(|g| rank(g).is_some_and(|r| r <= k));
        hit.count() as f64 / task.gold.len() as f64
    };
    let first = task.gold.iter().filter_map(rank).min();
    let mut by_channel = BTreeMap::from([("lexical", 0), ("seed", 0), ("both", 0)]);
    for g in &task.gold {
        if let Some(e) = set.entries.iter().find(|e| e.entity == *g) {
            let key = match (e.channels.is_empty(), e.seeds.is_empty()) {
                (false, true) => "lexical",
                (true, false) => "seed",
                _ => "both",
            };
            *by_channel.get_mut(key).unwrap() += 1;
        }
    }
    json!({
        "recall@1": recall(1), "recall@5": recall(5), "recall@10": recall(10),
        "recall@20": recall(20),
        "mrr": first.map_or(0.0, |r| 1.0 / r as f64),
        "entry_coverage": recall(usize::MAX),
        "gold_via_lexical_only": by_channel["lexical"],
        "gold_via_seed_only": by_channel["seed"],
        "gold_via_both": by_channel["both"],
        "entries": set.entries.len(),
    })
}

fn routing(task: &Task, result: &RouteResult) -> Value {
    let routed = |g: &EntityId| result.candidates.iter().any(|c| c.entity == *g);
    let file_of = |g: &EntityId| match g {
        EntityId::Symbol(s) => EntityId::File(s.file().clone()),
        other => other.clone(),
    };
    let stepped = |g: &EntityId, pred: fn(&Visit) -> bool| {
        result
            .trace
            .steps
            .iter()
            .any(|s| s.region.anchor == *g && pred(&s.visit))
    };
    let n = task.gold.len() as f64;
    let share =
        |f: &dyn Fn(&EntityId) -> bool| task.gold.iter().filter(|g| f(g)).count() as f64 / n;
    let trace = &result.trace;
    let w = trace.work;
    json!({
        "entity_coverage": share(&|g| routed(g)),
        "region_coverage": share(&|g| routed(&file_of(g))),
        "pruning_loss": share(&|g| !routed(g) && stepped(g, |v| *v == Visit::Pruned)),
        "deferral_loss": share(&|g| !routed(g) && stepped(g, |v| matches!(v, Visit::Deferred(_)))),
        "unreached": share(&|g| !routed(g) && !stepped(g, |_| true)),
        "candidates": result.candidates.len(),
        "regions_loaded": w.regions_loaded,
        "edges_examined": w.edges_examined,
        "edges_followed": w.edges_followed,
        "revisits": w.revisits,
        "judgments": w.judgments_requested,
        "max_depth_reached": w.max_depth_reached,
        "max_fanout_seen": w.max_fanout_seen,
        "fanout_truncations": trace.truncations.len(),
        "stopped_by": trace.stopped_by.map(bound),
        "root_fallback": trace.root_fallback,
    })
}

fn bound(b: Bound) -> &'static str {
    match b {
        Bound::Regions => "regions",
        Bound::Depth => "depth",
        Bound::Fanout => "fanout",
        Bound::Edges => "edges",
    }
}

fn raw_set(set: &CandidateSet) -> Value {
    let channels = set.channels.iter();
    json!({
        "channels": channels.map(|c| format!("{:?}: {:?}", c.channel, c.state)).collect::<Vec<_>>(),
        "hints": set.hints.iter().map(|h| format!("{h:?}")).collect::<Vec<_>>(),
        "entries": set.entries.iter().map(|e| json!({
            "entity": name(&e.entity),
            "lexical": e.channels.iter().map(|c| json!([c.rank, c.score])).collect::<Vec<_>>(),
            "seeds": e.seeds.iter().map(|s| format!("{:?}:{}", s.kind, s.hint)).collect::<Vec<_>>(),
        })).collect::<Vec<_>>(),
    })
}

fn raw_route(result: &RouteResult) -> Value {
    let visit = |v: &Visit| match v {
        Visit::Explored => "explored".to_owned(),
        Visit::Pruned => "pruned".to_owned(),
        Visit::Deferred(b) => format!("deferred:{}", bound(*b)),
    };
    let origin = |o: String| o.split([' ', '(']).next().unwrap().to_owned();
    let trace = &result.trace;
    json!({
        "candidates": result.candidates.iter().map(|c| json!({
            "entity": name(&c.entity), "depth": c.depth,
            "origin": origin(format!("{:?}", c.origins[0])),
        })).collect::<Vec<_>>(),
        "steps": trace.steps.iter()
            .map(|s| json!([name(&s.region.anchor), s.depth, visit(&s.visit)]))
            .collect::<Vec<_>>(),
        "truncations": trace.truncations.iter()
            .map(|(r, b)| json!([name(&r.anchor), bound(*b)]))
            .collect::<Vec<_>>(),
    })
}

fn request(set: &CandidateSet, text: &str) -> RouteRequest {
    RouteRequest {
        snapshot: set.snapshot.clone(),
        query: Query { text: text.into() },
        entry_points: set.entries.clone(),
        limits: BASELINE_LIMITS,
        policy: baseline_policy(),
    }
}

fn run_route(view: &impl ReadView, set: &CandidateSet, text: &str) -> RouteResult {
    let mut allowance = Allowance { remaining: 0 };
    let policy = DecisionPolicy::default();
    route(view, &request(set, text), None, &mut allowance, policy).unwrap()
}

#[test]
fn phase3_frozen_baseline_reproduces() {
    let source = capture(&repo_root().join("fixtures/py_repo"), &Scope::default()).unwrap();
    let repo = RepoId::new("phase3-eval").unwrap();
    let (manifest, batch, components) = derive(&repo, &source).unwrap();
    let key = manifest.key.clone();
    let dir = std::env::temp_dir().join(format!("oxide-phase3-eval-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    let mut store = LadybugStore::new(&dir).unwrap();
    store.begin(manifest.clone()).unwrap();
    store.retain_derivation(&key, &components).unwrap();
    store.retain_source(&source).unwrap();
    store.write(&key, batch.clone()).unwrap();
    store.publish(&key).unwrap();
    let view = store.open(&key).unwrap();
    let mut memory = MemoryStore::default();
    memory.begin(manifest).unwrap();
    memory.write(&key, batch).unwrap();
    memory.publish(&key).unwrap();
    let memory = memory.open(&key).unwrap();

    let tasks = tasks(&view);
    let mut raw = Vec::new();
    let mut table: BTreeMap<String, Vec<Value>> = BTreeMap::new();
    for task in &tasks {
        let query = Query {
            text: task.text.clone(),
        };
        let context = QueryContext {
            symbols: task.symbols.clone(),
            ..Default::default()
        };
        let mut per_task = serde_json::Map::new();
        for (label, config) in RETRIEVAL {
            let semantic = ChannelState::Unconfigured;
            let set = retrieve::retrieve(&view, &query, &context, semantic, config).unwrap();
            let routed = run_route(&view, &set, &task.text);
            assert_eq!((&set.snapshot, &routed.snapshot), (&key, &key));
            // Fake/real parity: the same entry points route identically.
            assert_eq!(run_route(&memory, &set, &task.text), routed, "{label}");
            // The heuristic judge is asked, recorded, and changes nothing.
            let mut allowance = Allowance { remaining: 1000 };
            let judged = route(
                &view,
                &request(&set, &task.text),
                Some(&mut Heuristic),
                &mut allowance,
                DecisionPolicy::default(),
            )
            .unwrap();
            assert_eq!(judged.candidates, routed.candidates);
            assert_eq!(judged.trace.steps, routed.trace.steps);

            let row = |kind: &str| format!("{kind}/{label}");
            let rows = table.entry(row("retrieval")).or_default();
            rows.push(retrieval(task, &set));
            let rows = table.entry(row("routing")).or_default();
            rows.push(routing(task, &routed));
            let run = json!({"candidate_set": raw_set(&set), "route": raw_route(&routed)});
            per_task.insert(label.into(), run);

            // Diagnostic ablation, not architecture: plain lexical top-N
            // with N the size of the routed universe, no routing.
            if label == "lexical+structural" {
                let n = routed.candidates.len();
                let request = LexicalRequest {
                    terms: terms(&task.text),
                    limit: n,
                };
                let hits = view.lexical(&request).unwrap().hits;
                let found = task
                    .gold
                    .iter()
                    .filter(|g| hits.iter().any(|h| h.entity == **g));
                let coverage = found.count() as f64 / task.gold.len() as f64;
                let rows = table.entry("ablation/no-routing-lexical-top-N".into());
                rows.or_default()
                    .push(json!({"coverage_at_routed_size": coverage, "n": n}));
            }
        }
        raw.push(json!({
            "task": task.id, "source": task.source, "kind": task.kind, "text": task.text,
            "symbol_hints": task.symbols, "gold": task.mapping, "runs": per_task,
        }));
    }

    let probe = LexicalRequest {
        terms: vec!["x".into()],
        limit: 1,
    };
    let configs = RETRIEVAL.iter();
    let manifest = json!({
        "baseline": "phase3-baseline-v1",
        "claims": "entry-point retrieval and TreeRouter routing only; no context quality, no agent success; a fixture regression floor, not generalization",
        "snapshot": {"repo": key.repo.as_str(), "snapshot": key.snapshot.as_str(), "derivation": key.derivation.as_str()},
        "derivation_components": components,
        "fixture": "fixtures/py_repo",
        "gold_sources": ["fixtures/benchmark.json (py)", "fixtures/structural_benchmark.json (py)"],
        "gold_mapping": "file#A.b -> SymbolId(file, [(A,0),(b,0)]); missing/ambiguous excluded and listed per task",
        "metric_definitions": {
            "recall@k": "mapped gold in the first k CandidateSet entries / mapped gold, macro over tasks",
            "mrr": "1 / CandidateSet position of the first gold, 0 if none",
            "entry_coverage": "mapped gold anywhere in the CandidateSet",
            "entity_coverage": "mapped gold among routed candidates",
            "region_coverage": "the file region of mapped gold among routed candidates",
            "pruning_loss": "gold reached but pruned by a branch judgment",
            "deferral_loss": "gold reached but not explored because a bound fired",
            "unreached": "gold never reached by routing",
            "coverage_at_routed_size": "diagnostic: gold in plain lexical top-N, N = routed universe size",
        },
        "retrieval_version": RETRIEVAL_VERSION,
        "retrieval_configs": configs.map(|(l, c)| json!({"label": l, "config": format!("{c:?}")})).collect::<Vec<_>>(),
        "semantic": "unconfigured (no vector channel in Phase 3)",
        "lexical_scorer": view.lexical(&probe).unwrap().scorer,
        "router_version": ROUTER_VERSION,
        "route_limits": format!("{BASELINE_LIMITS:?}"),
        "route_policy": format!("{:?}", baseline_policy()),
        "decision_provider": "none (heuristic equivalence asserted per run)",
        "tasks": tasks.len(),
    });
    let metrics: serde_json::Map<String, Value> = table
        .iter()
        .map(|(k, rows)| (k.clone(), mean(rows)))
        .collect();
    let artifacts = [
        ("manifest.json", manifest),
        ("metrics.json", Value::from(metrics)),
        ("results.json", Value::from(raw)),
    ];
    let out = repo_root().join("docs/phase-3/baseline-v1");
    for (file, value) in artifacts {
        let text = serde_json::to_string_pretty(&value).unwrap() + "\n";
        let path = out.join(file);
        if std::env::var_os("OXIDE_FREEZE_PHASE3").is_some() {
            std::fs::create_dir_all(&out).unwrap();
            std::fs::write(&path, text).unwrap();
        } else {
            let frozen = std::fs::read_to_string(&path).unwrap_or_default();
            assert!(
                frozen == text,
                "{} no longer reproduces; a changed baseline needs a new baseline directory",
                path.display()
            );
        }
    }
    drop(view);
    drop(store);
    let _ = std::fs::remove_dir_all(&dir);
}
