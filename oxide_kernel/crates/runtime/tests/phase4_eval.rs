//! Phase 4 frozen context-quality evaluation (docs/phase-4.md).
//!
//! Same fixture, `RepoId` and gold mapping as the Phase 3 baseline
//! (`tests/common`), so routed coverage here is the Phase 3 ceiling. It
//! measures what reaches the packed ContextBundle, separately from entry
//! retrieval and routing, for every configuration and budget below.
//!
//! The raw artifacts under `docs/phase-4/baseline-v1/` are the frozen
//! baseline: this test fails unless a run reproduces them byte for byte.
//! `OXIDE_FREEZE_PHASE4=1` writes them instead. Latency and memory are
//! printed, never frozen (they are not deterministic).

mod common;

use std::collections::BTreeMap;
use std::time::Instant;

use common::{Task, mean, name, repo_root, tasks};
use oxide_kernel::capsule::{Capsule, Question};
use oxide_kernel::context::{ContextConfig, ContextRequest, ContextRun, baseline, build_context};
use oxide_kernel::decision::{
    DecisionProvider, Judgment, ProviderError, ProviderIdentity, Verdict,
};
use oxide_kernel::digest::digest;
use oxide_kernel::id::RepoId;
use oxide_kernel::knowledge::SourceRef;
use oxide_kernel::pack::{COUNTER, PAYLOAD_VERSION, View, count, counter};
use oxide_kernel::query::{ContextBudget, Query, QueryContext};
use oxide_kernel::retrieve::ChannelState;
use oxide_kernel::select::{OmitReason, Reason, SELECTOR_VERSION, Stage};
use oxide_kernel::store::{KnowledgeStore, ReadView};
use oxide_runtime::capture::{Scope, capture};
use oxide_runtime::derivation::derive;
use oxide_runtime::storage::{LadybugStore, LadybugView};
use serde_json::{Value, json};

const BUDGETS: [u32; 2] = [256, 1024];

/// Control A: no judgment at all. Every subject gets the same constant, so
/// greedy order is the pool order (entry points in CandidateSet order, then
/// routed-only candidates by depth) and routing is unchanged (0.5 never
/// prunes).
struct Constant;

impl DecisionProvider for Constant {
    fn identity(&self) -> ProviderIdentity {
        ProviderIdentity {
            name: "control-constant".into(),
            version: "1".into(),
        }
    }
    fn supports(&self, _: Question) -> bool {
        true
    }
    fn judge(&mut self, capsules: &[Capsule]) -> Result<Vec<Judgment>, ProviderError> {
        let verdict = Verdict::Value {
            value: 0.5,
            confidence: None,
        };
        Ok(capsules.iter().map(|c| Judgment::of(c, verdict)).collect())
    }
}

fn configs() -> Vec<(&'static str, ContextConfig, bool)> {
    let b = baseline();
    let mut no_expansion = b.clone();
    no_expansion.select.expansion.max_depth = 0;
    let mut no_routing = b.clone();
    no_routing.route_limits.max_depth = 0;
    let mut neither = no_routing.clone();
    neither.select.expansion.max_depth = 0;
    vec![
        ("A-constant-greedy", b.clone(), true),
        ("B-heuristic-greedy", b, false),
        ("B-no-expansion", no_expansion, false),
        ("B-no-routing", no_routing, false),
        ("B-no-routing-no-expansion", neither, false),
    ]
}

fn covers(item: &SourceRef, gold: &SourceRef) -> bool {
    item.file == gold.file
        && item.range.start <= gold.range.start
        && gold.range.end <= item.range.end
}

fn overlaps(item: &SourceRef, gold: &SourceRef) -> bool {
    item.file == gold.file && item.range.start < gold.range.end && gold.range.start < item.range.end
}

fn metrics(task: &Task, gold: &[SourceRef], run: &ContextRun) -> Value {
    let bundle = &run.bundle;
    let items: Vec<&SourceRef> = bundle.items.iter().map(|i| &i.source).collect();
    let n = task.gold.len() as f64;
    let share = |f: &dyn Fn(usize) -> bool| (0..gold.len()).filter(|&i| f(i)).count() as f64 / n;
    let complete = |i: usize| items.iter().any(|s| covers(s, &gold[i]));
    let partial = |i: usize| !complete(i) && items.iter().any(|s| overlaps(s, &gold[i]));
    let in_file = |i: usize| items.iter().any(|s| s.file == gold[i].file);
    let routed = |i: usize| run.graph.nodes.iter().any(|n| n.entity.id == task.gold[i]);
    let planned = |i: usize| run.plan.items.iter().any(|p| p.entity == task.gold[i]);
    let gold_tokens: u32 = bundle
        .items
        .iter()
        .filter(|i| gold.iter().any(|g| overlaps(&i.source, g)))
        .map(|i| i.tokens)
        .sum();
    let used = bundle.used_tokens;
    let ratio = |a: u32| {
        if used == 0 {
            0.0
        } else {
            a as f64 / used as f64
        }
    };
    let expanded = |r: &Reason| matches!(r, Reason::Expanded { .. });
    let omitted = |stage: Stage| bundle.omitted.iter().filter(|o| o.stage == stage).count();
    let budget_omission = |r: &OmitReason| {
        matches!(
            r,
            OmitReason::BudgetExceeded { .. } | OmitReason::TooLarge { .. }
        )
    };
    let expanded_items = run.plan.items.iter().filter(|i| expanded(&i.reason));
    json!({
        "required_symbol_recall": share(&complete),
        "required_symbol_header_only": share(&partial),
        "required_file_recall": share(&in_file),
        "routed_gold_coverage": share(&routed),
        "selection_loss": share(&|i| routed(i) && !planned(i)),
        "packing_loss": share(&|i| planned(i) && !complete(i)),
        "gold_token_share": ratio(gold_tokens),
        "unlabeled_token_share": if used == 0 { 0.0 } else { 1.0 - ratio(gold_tokens) },
        "tokens_used": used,
        "budget_utilization": used as f64 / bundle.budget.tokens as f64,
        "items": bundle.items.len(),
        "planned": run.plan.items.len(),
        "expanded_planned": expanded_items.clone().count(),
        "expansion_tokens_estimated": expanded_items.map(|i| i.estimated_tokens).sum::<u32>(),
        "expansion_considered": run.plan.expansion.considered,
        "expansion_duplicates": run.plan.expansion.duplicates,
        "reduced_to_header": run.plan.items.iter().filter(|i| i.view == View::Header).count(),
        "omitted_selection": omitted(Stage::Selection),
        "omitted_expansion": omitted(Stage::Expansion),
        "omitted_packing": omitted(Stage::Packing),
        "budget_omissions": bundle.omitted.iter().filter(|o| budget_omission(&o.reason)).count(),
        "graph_nodes": run.graph.nodes.len(),
        "judgments_answered": run.judgments.len(),
    })
}

fn reason(r: &Reason) -> Value {
    match r {
        Reason::Primary {
            value,
            provider,
            entry,
            depth,
            ..
        } => {
            json!({"primary": {"value": value, "provider": provider.name, "entry": entry, "depth": depth}})
        }
        Reason::Prerequisite { of } => json!({"prerequisite_of": name(of)}),
        Reason::Expanded {
            seed,
            relation,
            direction,
            value,
            provider,
            ..
        } => json!({"expanded": {"seed": name(seed), "relation": format!("{relation:?}"),
                                 "direction": format!("{direction:?}"), "value": value,
                                 "provider": provider.name}}),
    }
}

fn raw(run: &ContextRun) -> Value {
    let plan = run.plan.items.iter().map(|i| {
        json!({
            "entity": name(&i.entity),
            "view": format!("{:?}", i.view),
            "reason": reason(&i.reason),
            "estimated_tokens": i.estimated_tokens,
            "prerequisites": i.prerequisites.iter().map(|p| name(&p.entity)).collect::<Vec<_>>(),
        })
    });
    let items = run.bundle.items.iter().map(|i| {
        json!({
            "file": i.source.file.as_str(),
            "range": [i.source.range.start, i.source.range.end],
            "lines": [i.lines.0, i.lines.1],
            "tokens": i.tokens,
            "entities": i.entities.iter().map(|e| json!([name(&e.entity), e.complete])).collect::<Vec<_>>(),
        })
    });
    let omitted = run.bundle.omitted.iter().map(|o| {
        json!([
            name(&o.entity),
            format!("{:?}", o.stage),
            format!("{:?}", o.reason)
        ])
    });
    let e = &run.plan.expansion;
    let capsules = run.capsules.iter().chain(&run.plan.capsules);
    json!({
        "plan": plan.collect::<Vec<_>>(),
        "bundle": {"used_tokens": run.bundle.used_tokens, "items": items.collect::<Vec<_>>(),
                   "payload_sha256": digest(run.bundle.payload().as_bytes()).as_str()},
        "omitted": omitted.collect::<Vec<_>>(),
        "expansion": {"seeds": e.seeds, "edges_examined": e.edges_examined, "considered": e.considered,
                      "duplicates": e.duplicates, "ambiguous_skipped": e.ambiguous_skipped,
                      "unresolved_skipped": e.unresolved_skipped,
                      "stopped_by": e.stopped_by.map(|b| format!("{b:?}"))},
        "degraded": run.bundle.degraded.iter().map(|d| format!("{d:?}")).collect::<Vec<_>>(),
        "capsules": capsules.count(),
    })
}

fn gold_sources(view: &LadybugView, task: &Task) -> Vec<SourceRef> {
    view.entities(&task.gold)
        .unwrap()
        .into_iter()
        .map(|e| e.unwrap().source.unwrap())
        .collect()
}

fn rss_peak_kib() -> u64 {
    let status = std::fs::read_to_string("/proc/self/status").unwrap_or_default();
    let line = status.lines().find(|l| l.starts_with("VmHWM:"));
    line.and_then(|l| l.split_whitespace().nth(1)?.parse().ok())
        .unwrap_or(0)
}

#[test]
fn phase4_frozen_baseline_reproduces() {
    let source = capture(&repo_root().join("fixtures/py_repo"), &Scope::default()).unwrap();
    // The Phase 3 baseline's RepoId: the same snapshot and derivation keys.
    let repo = RepoId::new("phase3-eval").unwrap();
    let (manifest, batch, components) = derive(&repo, &source).unwrap();
    let key = manifest.key.clone();
    let dir = std::env::temp_dir().join(format!("oxide-phase4-eval-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    let mut store = LadybugStore::new(&dir).unwrap();
    store.begin(manifest).unwrap();
    store.retain_derivation(&key, &components).unwrap();
    store.retain_source(&source).unwrap();
    store.write(&key, batch).unwrap();
    store.publish(&key).unwrap();
    let view = store.open(&key).unwrap();

    let tasks = tasks(&view);
    let mut results = Vec::new();
    let mut table: BTreeMap<String, Vec<Value>> = BTreeMap::new();
    let mut capsules = String::new();
    let mut timings: Vec<f64> = Vec::new();
    for task in &tasks {
        let gold = gold_sources(&view, task);
        let mut runs = serde_json::Map::new();
        for (label, config, constant) in configs() {
            for tokens in BUDGETS {
                let request = ContextRequest {
                    query: Query {
                        text: task.text.clone(),
                    },
                    context: QueryContext {
                        symbols: task.symbols.clone(),
                        ..Default::default()
                    },
                    budget: ContextBudget {
                        tokens,
                        counter: counter(),
                    },
                    semantic: ChannelState::Unconfigured,
                };
                let mut control = Constant;
                let provider = constant.then_some(&mut control as &mut dyn DecisionProvider);
                let started = Instant::now();
                let run = build_context(&view, &view, &request, &config, provider).unwrap();
                if label == "B-heuristic-greedy" && tokens == 1024 {
                    timings.push(started.elapsed().as_secs_f64() * 1e3);
                    // The offline path is deterministic run to run.
                    let again = build_context(&view, &view, &request, &config, None).unwrap();
                    assert_eq!(again, run);
                    for c in run.capsules.iter().chain(&run.plan.capsules) {
                        let capsule: Value = serde_json::from_str(&c.render(false)).unwrap();
                        let line = json!({"task": task.id, "digest": c.digest().as_str(), "capsule": capsule});
                        capsules.push_str(&(line.to_string() + "\n"));
                    }
                }
                // Hard invariants on every run.
                assert!(run.bundle.used_tokens <= tokens);
                assert_eq!(count(&run.bundle.payload()), run.bundle.used_tokens);
                // The plan's estimate bounds the packed cost, so packing
                // never drops a planned item for budget.
                assert!(run.bundle.used_tokens <= run.plan.estimated_tokens);
                let packing_budget = run.bundle.omitted.iter().any(|o| {
                    o.stage == Stage::Packing
                        && matches!(
                            o.reason,
                            OmitReason::BudgetExceeded { .. } | OmitReason::TooLarge { .. }
                        )
                });
                assert!(!packing_budget, "{}", task.id);
                let row = format!("{label}@{tokens}");
                table
                    .entry(row.clone())
                    .or_default()
                    .push(metrics(task, &gold, &run));
                runs.insert(row, raw(&run));
            }
        }
        results.push(
            json!({"task": task.id, "text": task.text, "symbol_hints": task.symbols,
                            "gold": task.mapping, "runs": runs}),
        );
    }
    timings.sort_by(f64::total_cmp);
    eprintln!(
        "phase4 B-heuristic-greedy@1024 build_context ms: min {:.1} median {:.1} max {:.1}; peak RSS {} KiB",
        timings[0],
        timings[timings.len() / 2],
        timings[timings.len() - 1],
        rss_peak_kib()
    );

    let manifest = json!({
        "baseline": "phase4-baseline-v1",
        "claims": "context quality of the packed ContextBundle on a 65-entity fixture: a regression floor, not generalization; no downstream agent success; no JEV result",
        "snapshot": {"repo": key.repo.as_str(), "snapshot": key.snapshot.as_str(), "derivation": key.derivation.as_str()},
        "phase3_baseline": "docs/phase-3/baseline-v1 (same snapshot, retrieval and routing configuration)",
        "fixture": "fixtures/py_repo",
        "gold_mapping": "as phase3-baseline-v1",
        "versions": {"capsule": oxide_kernel::capsule::CAPSULE_VERSION, "selector": SELECTOR_VERSION,
                     "payload": PAYLOAD_VERSION, "counter": COUNTER, "heuristic": "heuristic-evidence/1"},
        "budgets": BUDGETS,
        "configs": configs().iter().map(|(l, c, constant)| json!({
            "label": l,
            "decision_provider": if *constant { "control-constant/1 (0.5 for every subject)" } else { "none: heuristic-evidence/1 offline" },
            "route_limits": format!("{:?}", c.route_limits),
            "graph": format!("{:?}", c.graph),
            "select": format!("{:?}", c.select),
            "judgments": c.judgments,
        })).collect::<Vec<_>>(),
        "jev": "not run: no captured live JEV responses exist; the adapter is tested only against synthetic, docs-shaped fixtures",
        "metric_definitions": {
            "required_symbol_recall": "gold whose whole source range lies inside one packed item / mapped gold",
            "required_symbol_header_only": "gold only partly shown (header view or partial overlap)",
            "required_file_recall": "gold whose file has any packed item",
            "routed_gold_coverage": "gold among CandidateGraph nodes (the selection ceiling)",
            "selection_loss": "gold routed but not planned",
            "packing_loss": "gold planned but not packed complete",
            "gold_token_share": "tokens of packed items overlapping a gold range / tokens used (labeled precision; 0 when empty)",
            "unlabeled_token_share": "1 - gold_token_share: an upper bound on noise; there are no irrelevance labels, so true noise is not measured",
            "budget_utilization": "tokens used / budget (not a target)",
            "tokens": "oxide-units-v1, the canonical payload unit; not any model's tokenizer",
        },
        "tasks": tasks.len(),
    });
    let metrics: serde_json::Map<String, Value> = table
        .iter()
        .map(|(k, rows)| (k.clone(), mean(rows)))
        .collect();
    let artifacts = [
        (
            "manifest.json",
            serde_json::to_string_pretty(&manifest).unwrap() + "\n",
        ),
        (
            "metrics.json",
            serde_json::to_string_pretty(&Value::from(metrics)).unwrap() + "\n",
        ),
        (
            "results.json",
            serde_json::to_string_pretty(&Value::from(results)).unwrap() + "\n",
        ),
        ("capsules.jsonl", capsules),
    ];
    let out = repo_root().join("docs/phase-4/baseline-v1");
    for (file, text) in artifacts {
        let path = out.join(file);
        if std::env::var_os("OXIDE_FREEZE_PHASE4").is_some() {
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
