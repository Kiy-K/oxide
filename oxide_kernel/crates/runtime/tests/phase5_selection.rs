//! Phase 5 follow-up 4 (docs/phase-5/followup-4/preregistration.md):
//! why the heuristic selector prefers lexical hits, on dev, with the
//! baseline route (BFS, 64 regions) and candidate graph held fixed. Opt-in
//! and machine-local like `phase5_budget.rs`; it never calls JEV (recorded
//! answers replay as a diagnostic only):
//!
//! ```text
//! OXIDE_CB_WORKTREES=<dir>:<dir> \
//!   cargo test --release -p oxide-runtime --test phase5_selection -- --ignored
//! ```

mod common;

use std::collections::{BTreeMap, BTreeSet};
use std::path::Path;

use std::time::Instant;

use common::phase5::{
    self, CandidatesOnly, GoldPriority, Indexed, Tape, TaskInput, data_dir, jev_config, paired,
    round, stages,
};
use common::{covers, gold_sources, metrics, repo_root};
use oxide_kernel::capsule::{Capsule, Question};
use oxide_kernel::context::{ContextRun, baseline, build_context};
use oxide_kernel::decision::{
    DecisionProvider, Heuristic, Judgment, ProviderError, ProviderIdentity, Verdict,
};
use oxide_kernel::id::EntityId;
use oxide_kernel::knowledge::RelationKind;
use oxide_kernel::retrieve::{Channel, RETRIEVAL_VERSION};
use oxide_kernel::route::{Origin, ROUTER_VERSION};
use oxide_kernel::select::{OmitReason, Reason, SELECTOR_VERSION};
use oxide_kernel::source::SourceProvider;
use oxide_kernel::store::{Direction, KnowledgeStore, ReadView};
use oxide_runtime::decisionbench::{self as db, Split};
use oxide_runtime::jev::Jev;
use serde_json::{Value, json};

const AUDIT_BUDGETS: [u32; 2] = [1024, 4096];

/// Provenance class of a graph node, as the heuristic sees it.
fn class(run: &ContextRun, i: usize) -> String {
    let node = &run.graph.nodes[i];
    let c = &node.candidate;
    if !matches!(node.entity.id, EntityId::Symbol(_)) {
        "container".into()
    } else if node.entity.source.is_none() {
        "no_source".into()
    } else if !c.seeds.is_empty() {
        "seed".into()
    } else if let Some(l) = c.channels.iter().find(|c| c.channel == Channel::Lexical) {
        // Heuristic value bands: ranks 1-5 >= 0.75, 6-15 0.5-0.725, 16+ 0.5.
        match l.rank {
            1..=5 => "lexical_r1-5".into(),
            6..=15 => "lexical_r6-15".into(),
            _ => "lexical_r16+".into(),
        }
    } else if c.depth == 1 {
        "routed_d1".into()
    } else {
        "routed_d2+".into()
    }
}

/// How routing reached node `i`: origin kind, relation and the class of
/// the node it came from.
fn origins(run: &ContextRun, i: usize) -> Value {
    let from = |id: &EntityId| match run.graph.nodes.iter().position(|n| &n.entity.id == id) {
        Some(j) => class(run, j),
        None => "outside_graph".into(),
    };
    let origins: Vec<Value> = run.graph.nodes[i]
        .candidate
        .origins
        .iter()
        .map(|o| match o {
            Origin::RegionMember(r) => json!({"kind": "member", "from": from(&r.anchor)}),
            Origin::Scope(r) => json!({"kind": "scope", "from": from(&r.anchor)}),
            Origin::Neighbor {
                via,
                relation,
                direction,
            } => json!({"kind": format!("{relation:?}/{direction:?}"), "from": from(via)}),
            o => json!({"kind": format!("{o:?}")}),
        })
        .collect();
    json!(origins)
}

/// What happened to `id` in `run`: planned (full or header), or the
/// recorded omission reason.
fn outcome(run: &ContextRun, id: &EntityId) -> String {
    if let Some(p) = run.plan.items.iter().find(|p| &p.entity == id) {
        let how = if p.reduced { "header" } else { "full" };
        return match p.reason {
            Reason::Expanded { .. } => format!("planned_{how}_via_expansion"),
            _ => format!("planned_{how}"),
        };
    }
    match run.bundle.omitted.iter().find(|o| &o.entity == id) {
        Some(o) => match &o.reason {
            OmitReason::BudgetExceeded { .. } => "budget_exceeded".into(),
            OmitReason::BelowFloor { .. } => "below_floor".into(),
            OmitReason::TooLarge { .. } => "too_large".into(),
            OmitReason::MaxItems => "max_items".into(),
            OmitReason::Container => "container".into(),
            OmitReason::NoSource => "no_source".into(),
            r => format!("{r:?}"),
        },
        None => "not_in_plan".into(),
    }
}

fn measure(t: &Value, data: &Path, tape: Tape) -> (Value, Tape) {
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

    let runs: BTreeMap<u32, ContextRun> = AUDIT_BUDGETS
        .iter()
        .map(|&b| {
            let run = build_context(&view, &view, &phase5::request(&input, b), &baseline(), None);
            (b, run.unwrap())
        })
        .collect();
    let base = &runs[&1024];
    // Recorded JEV relevance answers for the same capsules (diagnostic).
    let mut jev = Jev::new(jev_config(), tape).unwrap();
    let jev_run = build_context(
        &view,
        &view,
        &phase5::request(&input, 1024),
        &baseline(),
        Some(&mut CandidatesOnly(&mut jev)),
    )
    .unwrap();
    assert_eq!(
        jev_run.capsules, base.capsules,
        "replay judged other capsules"
    );

    let nodes: Vec<Value> = (0..base.graph.nodes.len())
        .map(|i| {
            let id = &base.graph.nodes[i].entity.id;
            let gold = if symbols.contains(id) {
                "symbol"
            } else if all.contains(id) {
                "other"
            } else {
                "no"
            };
            let jev = &jev_run.decisions[i];
            let lexical = base.graph.nodes[i]
                .candidate
                .channels
                .iter()
                .find(|c| c.channel == Channel::Lexical)
                .map(|c| c.rank);
            let mut row = json!({
                "class": class(base, i), "gold": gold, "lexical_rank": lexical,
                "kind": base.graph.nodes[i].entity.kind, "origins": origins(base, i),
                "depth": base.graph.nodes[i].candidate.depth,
                "heuristic": base.decisions[i].value,
                "jev": if jev.fallback.is_none() { json!(jev.value) } else { Value::Null },
            });
            for (b, run) in &runs {
                row[format!("outcome@{b}")] = json!(outcome(run, id));
                if gold == "symbol" {
                    let src = &gold_sources(&view, std::slice::from_ref(id))[0];
                    let packed = run.bundle.items.iter().any(|it| covers(&it.source, src));
                    row[format!("packed@{b}")] = json!(packed);
                }
            }
            row
        })
        .collect();
    // Gold that only selection's expansion reached.
    let expanded_gold: BTreeMap<u32, usize> = runs
        .iter()
        .map(|(b, run)| {
            let in_graph = |id: &EntityId| run.graph.nodes.iter().any(|n| &n.entity.id == id);
            let n = run
                .plan
                .items
                .iter()
                .filter(|p| symbols.contains(&p.entity) && !in_graph(&p.entity))
                .count();
            (*b, n)
        })
        .collect();
    let plan: BTreeMap<u32, Value> = runs
        .iter()
        .map(|(b, run)| {
            let items: Vec<Value> = run
                .plan
                .items
                .iter()
                .map(|p| {
                    let class = match base
                        .graph
                        .nodes
                        .iter()
                        .position(|n| n.entity.id == p.entity)
                    {
                        Some(i) if !matches!(p.reason, Reason::Expanded { .. }) => class(base, i),
                        _ => "expansion".into(),
                    };
                    json!({"class": class, "tokens": p.estimated_tokens, "reduced": p.reduced,
                           "gold": symbols.contains(&p.entity)})
                })
                .collect();
            let mut m = metrics(&all, &all_sources, run);
            m["items"] = json!(items);
            (*b, m)
        })
        .collect();
    let symbol_list: Vec<EntityId> = symbols.iter().cloned().collect();
    let symbol_metrics = metrics(&symbol_list, &gold_sources(&view, &symbol_list), base);
    let out = json!({
        "task": input.id, "inputs": inputs(),
        "gold_verified": all.len(), "gold_symbols": symbols.len(),
        "routed_gold_coverage_all": metrics(&all, &all_sources, base)["routed_gold_coverage"],
        "routed_gold_coverage_symbols": symbol_metrics["routed_gold_coverage"],
        "nodes": nodes, "expanded_gold": expanded_gold, "plan": plan,
    });
    drop(view);
    drop(store);
    let _ = std::fs::remove_dir_all(&dir);
    (out, jev.into_transport())
}

/// What a cached task result depends on besides the task itself; bump
/// `harness` on any change to the audit.
fn inputs() -> Value {
    json!({"harness": "audit-v2", "router": ROUTER_VERSION, "selector": SELECTOR_VERSION,
           "retrieval": RETRIEVAL_VERSION})
}

/// Per class over every task: nodes, gold symbols, value means, recorded
/// JEV means (gold and not), and outcomes at each budget.
fn audit_summary(tasks: &[Value]) -> Value {
    let mut classes: BTreeMap<String, Vec<&Value>> = BTreeMap::new();
    for n in tasks.iter().flat_map(|t| t["nodes"].as_array().unwrap()) {
        classes
            .entry(n["class"].as_str().unwrap().into())
            .or_default()
            .push(n);
    }
    let mean = |xs: &[f64]| {
        if xs.is_empty() {
            Value::Null
        } else {
            round(xs.iter().sum::<f64>() / xs.len() as f64)
        }
    };
    let mut out = BTreeMap::new();
    for (class, nodes) in classes {
        let gold: Vec<&Value> = nodes
            .iter()
            .copied()
            .filter(|n| n["gold"] == "symbol")
            .collect();
        let jev = |want: bool| -> Vec<f64> {
            nodes
                .iter()
                .filter(|n| (n["gold"] == "symbol") == want)
                .filter_map(|n| n["jev"].as_f64())
                .collect()
        };
        let count = |key: &str, set: &[&Value]| {
            let mut m: BTreeMap<String, usize> = BTreeMap::new();
            for n in set {
                *m.entry(n[key].as_str().unwrap().into()).or_default() += 1;
            }
            json!(m)
        };
        let mut row = json!({
            "nodes": nodes.len(), "gold_symbols": gold.len(),
            "gold_rate": round(gold.len() as f64 / nodes.len() as f64),
            "heuristic_mean": mean(&nodes.iter().map(|n| n["heuristic"].as_f64().unwrap()).collect::<Vec<_>>()),
            "jev_recorded": nodes.iter().filter(|n| !n["jev"].is_null()).count(),
            "jev_mean_gold": mean(&jev(true)), "jev_mean_other": mean(&jev(false)),
        });
        for b in AUDIT_BUDGETS {
            row[format!("outcomes@{b}")] = count(&format!("outcome@{b}"), &nodes);
            row[format!("gold_outcomes@{b}")] = count(&format!("outcome@{b}"), &gold);
            row[format!("gold_packed@{b}")] = json!(
                gold.iter()
                    .filter(|n| n[format!("packed@{b}")] == true)
                    .count()
            );
        }
        out.insert(class, row);
    }
    // Where the plan's budget went, by class.
    let mut spend = BTreeMap::new();
    for b in AUDIT_BUDGETS {
        let mut m: BTreeMap<String, (usize, u64, usize)> = BTreeMap::new();
        for it in tasks
            .iter()
            .flat_map(|t| t["plan"][b.to_string()]["items"].as_array().unwrap())
        {
            let e = m.entry(it["class"].as_str().unwrap().into()).or_default();
            e.0 += 1;
            e.1 += it["tokens"].as_u64().unwrap();
            e.2 += usize::from(it["gold"] == true);
        }
        let m: BTreeMap<String, Value> = m
            .into_iter()
            .map(|(k, (n, t, g))| (k, json!({"items": n, "tokens": t, "gold_items": g})))
            .collect();
        spend.insert(b.to_string(), json!(m));
    }
    // 0.583 (follow-up 2) vs 0.383 (follow-up 3).
    let n = tasks.len() as f64;
    let task_mean = |k: &str| round(tasks.iter().map(|t| t[k].as_f64().unwrap()).sum::<f64>() / n);
    let gold_symbols: u64 = tasks
        .iter()
        .map(|t| t["gold_symbols"].as_u64().unwrap())
        .sum();
    let routed_symbols: usize = tasks
        .iter()
        .flat_map(|t| t["nodes"].as_array().unwrap())
        .filter(|n| n["gold"] == "symbol")
        .count();
    json!({
        "classes": out, "plan_spend": spend,
        "expanded_gold": AUDIT_BUDGETS.iter().map(|b| (b.to_string(),
            tasks.iter().map(|t| t["expanded_gold"][b.to_string()].as_u64().unwrap()).sum::<u64>()))
            .collect::<BTreeMap<_, _>>(),
        "routed_coverage": {
            "task_mean_all_verified_gold": task_mean("routed_gold_coverage_all"),
            "task_mean_gold_symbols": task_mean("routed_gold_coverage_symbols"),
            "pooled_gold_symbols": round(routed_symbols as f64 / gold_symbols as f64),
            "gold_symbols": gold_symbols, "routed_gold_symbols": routed_symbols,
            "gold_verified": tasks.iter().map(|t| t["gold_verified"].as_u64().unwrap()).sum::<u64>(),
        },
    })
}

#[test]
#[ignore = "machine-local: needs ContextBench worktrees"]
fn selection_audit() {
    let root = repo_root();
    let data = data_dir();
    let docs = root.join("docs/phase-5/decisionbench-v1");
    let mut tape = Tape::load(&[&docs.join("fixture/jev"), &data.join("jev")], None);
    let cache_dir = data.join("followup-4/audit");
    std::fs::create_dir_all(&cache_dir).unwrap();
    let mut tasks = Vec::new();
    for t in dev_tasks() {
        let id = t["id"].as_str().unwrap();
        let path = cache_dir.join(format!("{id}.json"));
        let cached = std::fs::read_to_string(&path)
            .ok()
            .map(|text| serde_json::from_str::<Value>(&text).unwrap())
            .filter(|r| r["inputs"] == inputs());
        let r = match cached {
            Some(r) => r,
            None => {
                let (r, back) = measure(&t, &data, tape);
                tape = back;
                std::fs::write(&path, serde_json::to_string(&r).unwrap()).unwrap();
                r
            }
        };
        eprintln!("{id}");
        tasks.push(r);
    }
    let out = json!({"split": "dev", "tasks": tasks.len(), "summary": audit_summary(&tasks)});
    let dir = root.join("docs/phase-5/followup-4");
    std::fs::create_dir_all(&dir).unwrap();
    let text = serde_json::to_string_pretty(&out).unwrap() + "\n";
    std::fs::write(dir.join("audit.json"), &text).unwrap();
    eprintln!("{text}");
}

const PREREGISTRATION_SHA256: &str =
    "0273c60445cc750ce0c5266268a5c705d8415aecc02fd6f25c390c5886ad1b7a";
const LABELS: [&str; 5] = ["H", "L", "S", "LS", "B"];

fn container(c: &Capsule) -> bool {
    matches!(c.kind.as_str(), "repository" | "module" | "file")
}

/// Lexical rank of a symbol entry point without seed evidence.
fn lexical(c: &Capsule) -> Option<u32> {
    if container(c) || !c.seeds.is_empty() {
        return None;
    }
    let l = c.channels.iter().find(|c| c.channel == Channel::Lexical)?;
    Some(l.rank)
}

/// Routed-only at depth 1, reached as a caller of an entry point or as a
/// member of an entry-point symbol's region.
fn structural(c: &Capsule) -> bool {
    !container(c)
        && c.channels.is_empty()
        && c.seeds.is_empty()
        && c.depth == Some(1)
        && c.origins.iter().any(|o| match o {
            Origin::Neighbor {
                relation: RelationKind::Calls,
                direction: Direction::Incoming,
                ..
            } => true,
            Origin::RegionMember(r) => matches!(r.anchor, EntityId::Symbol(_)),
            _ => false,
        })
}

/// Evaluation-only relevance scoring variants (preregistered); they read
/// only the capsules, never gold. Other questions fall back to the
/// heuristic (`Unsupported`).
struct Scoring(&'static str);

impl DecisionProvider for Scoring {
    fn identity(&self) -> ProviderIdentity {
        ProviderIdentity {
            name: format!("followup-4-{}", self.0),
            version: "eval-only".into(),
        }
    }
    fn supports(&self, question: Question) -> bool {
        question == Question::Relevance
    }
    fn judge(&mut self, capsules: &[Capsule]) -> Result<Vec<Judgment>, ProviderError> {
        let lexical_l = |r: u32| (0.85 - 0.05 * (r - 1) as f64).max(0.4);
        let (mut kl, mut ks) = (0usize, 0usize);
        let mut value = |c: &Capsule| match self.0 {
            "Hp" => Heuristic::value(c),
            "L" => lexical(c).map_or(Heuristic::value(c), lexical_l),
            "S" if structural(c) => 0.74,
            "S" => Heuristic::value(c),
            "LS" if structural(c) => 0.625,
            "LS" => lexical(c).map_or(Heuristic::value(c), lexical_l),
            "B" if lexical(c).is_some() => {
                kl += 1;
                (0.85 - 0.02 * (kl - 1) as f64).max(0.46)
            }
            "B" if structural(c) => {
                ks += 1;
                (0.84 - 0.02 * (ks - 1) as f64).max(0.46)
            }
            "B" => Heuristic::value(c),
            v => unreachable!("{v}"),
        };
        Ok(capsules
            .iter()
            .map(|c| {
                let verdict = Verdict::Value {
                    value: value(c),
                    confidence: None,
                };
                Judgment::of(c, verdict)
            })
            .collect())
    }
}

fn run_variant<V: ReadView + SourceProvider>(
    view: &V,
    input: &TaskInput<'_>,
    label: &'static str,
    budget: u32,
) -> ContextRun {
    let request = phase5::request(input, budget);
    let mut scoring = Scoring(label);
    let provider: Option<&mut dyn DecisionProvider> = match label {
        "H" => None,
        _ => Some(&mut scoring),
    };
    build_context(view, view, &request, &baseline(), provider).unwrap()
}

/// Concordant and discordant symbol-node pairs between relevance values
/// and recorded JEV values (unrecorded capsules and ties skipped).
fn jev_agreement(values: &[f64], jev: &[Option<f64>]) -> (usize, usize) {
    let (mut c, mut d) = (0, 0);
    for i in 0..values.len() {
        for j in i + 1..values.len() {
            let (Some(a), Some(b)) = (jev[i], jev[j]) else {
                continue;
            };
            let s = (values[i] - values[j]) * (a - b);
            if s > 0.0 {
                c += 1;
            } else if s < 0.0 {
                d += 1;
            }
        }
    }
    (c, d)
}

fn measure_variants(t: &Value, data: &Path, labels: &[&'static str], tape: Tape) -> (Value, Tape) {
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
    let mut row = json!({"task": input.id, "split": "Dev", "inputs": variant_inputs()});
    let mut base_items: BTreeMap<u32, BTreeSet<EntityId>> = BTreeMap::new();
    let mut jev_values: Vec<Option<f64>> = Vec::new();
    for budget in AUDIT_BUDGETS {
        let h = run_variant(&view, &input, "H", budget);
        // Control: the heuristic through the provider path changes nothing.
        let control = run_variant(&view, &input, "Hp", budget);
        // Only the recorded provider identity may differ.
        let order = |r: &ContextRun| {
            r.plan
                .items
                .iter()
                .map(|p| p.entity.clone())
                .collect::<Vec<_>>()
        };
        assert_eq!(
            control.bundle.payload(),
            h.bundle.payload(),
            "provider path changed H"
        );
        assert_eq!(order(&control), order(&h), "provider path changed H");
        base_items.insert(
            budget,
            h.plan.items.iter().map(|p| p.entity.clone()).collect(),
        );
        let symbol_nodes: Vec<usize> = (0..h.graph.nodes.len())
            .filter(|&i| !container(&h.capsules[i]))
            .collect();
        if budget == AUDIT_BUDGETS[0] {
            let replay = build_context(
                &view,
                &view,
                &phase5::request(&input, budget),
                &baseline(),
                Some(&mut CandidatesOnly(&mut jev)),
            )
            .unwrap();
            assert_eq!(replay.capsules, h.capsules, "replay judged other capsules");
            jev_values = symbol_nodes
                .iter()
                .map(|&i| {
                    replay.decisions[i]
                        .fallback
                        .is_none()
                        .then_some(replay.decisions[i].value)
                })
                .collect();
        }
        for label in labels {
            let started = Instant::now();
            let run = run_variant(&view, &input, label, budget);
            let latency = started.elapsed().as_secs_f64() * 1e3;
            let again = run_variant(&view, &input, label, budget);
            assert_eq!(again.bundle, run.bundle, "{label}: not deterministic");
            // The route trace also records each run's fallback kinds.
            assert_eq!(
                (
                    &run.route.candidates,
                    &run.route.trace.steps,
                    &run.graph,
                    &run.capsules
                ),
                (
                    &h.route.candidates,
                    &h.route.trace.steps,
                    &h.graph,
                    &h.capsules
                ),
                "{label}: inputs differ from H"
            );
            let mut m = metrics(&all, &all_sources, &run);
            m["latency_ms"] = json!(latency);
            m["stages"] = stages(&run, &symbol_list, &symbol_sources);
            let items: BTreeSet<EntityId> =
                run.plan.items.iter().map(|p| p.entity.clone()).collect();
            let base = &base_items[&budget];
            m["plan_jaccard_vs_h"] = json!(
                items.intersection(base).count() as f64 / items.union(base).count().max(1) as f64
            );
            let mut composition: BTreeMap<String, (usize, u32, usize)> = BTreeMap::new();
            for p in &run.plan.items {
                let class = match run.graph.nodes.iter().position(|n| n.entity.id == p.entity) {
                    Some(i) if !matches!(p.reason, Reason::Expanded { .. }) => class(&run, i),
                    _ => "expansion".into(),
                };
                let e = composition.entry(class).or_default();
                e.0 += 1;
                e.1 += p.estimated_tokens;
                e.2 += usize::from(symbols.contains(&p.entity));
            }
            m["composition"] = json!(composition);
            let values: Vec<f64> = symbol_nodes
                .iter()
                .map(|&i| run.decisions[i].value)
                .collect();
            m["jev_agreement"] = json!(jev_agreement(&values, &jev_values));
            row[format!("{label}@{budget}")] = m;
        }
        let mut oracle = GoldPriority {
            symbols: &symbols,
            other: &other,
        };
        let ceiling = build_context(
            &view,
            &view,
            &phase5::request(&input, budget),
            &baseline(),
            Some(&mut oracle),
        )
        .unwrap();
        let mut m = metrics(&all, &all_sources, &ceiling);
        m["stages"] = stages(&ceiling, &symbol_list, &symbol_sources);
        row[format!("oracle@{budget}")] = m;
        let replay = build_context(
            &view,
            &view,
            &phase5::request(&input, budget),
            &baseline(),
            Some(&mut CandidatesOnly(&mut jev)),
        )
        .unwrap();
        let mut m = metrics(&all, &all_sources, &replay);
        m["stages"] = stages(&replay, &symbol_list, &symbol_sources);
        m["fallbacks"] = phase5::fallbacks(&replay);
        row[format!("jev_replay@{budget}")] = m;
    }
    row["symbol_gold"] = json!(symbols.len());
    row["jev_recorded"] = json!(jev_values.iter().filter(|v| v.is_some()).count());
    row["symbol_nodes"] = json!(jev_values.len());
    drop(view);
    drop(store);
    let _ = std::fs::remove_dir_all(&dir);
    (row, jev.into_transport())
}

/// Bump `harness` on any change to the variants or their measures.
fn variant_inputs() -> Value {
    json!({"harness": "variants-v1", "preregistration_sha256": PREREGISTRATION_SHA256,
           "router": ROUTER_VERSION, "selector": SELECTOR_VERSION, "retrieval": RETRIEVAL_VERSION})
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

/// Per-task values at `key` (`<run>@<budget>`), field `field`.
fn get(tasks: &[Value], key: &str, field: &str) -> BTreeMap<String, f64> {
    tasks
        .iter()
        .filter_map(|t| Some((t["task"].as_str()?.to_owned(), t[key][field].as_f64()?)))
        .collect()
}

fn p50(m: &BTreeMap<String, f64>) -> f64 {
    let mut xs: Vec<f64> = m.values().copied().collect();
    xs.sort_by(f64::total_cmp);
    xs[(xs.len() - 1) / 2]
}

/// Means, pooled gold-symbol stages and plan composition of run `key`.
fn cell(tasks: &[Value], key: &str) -> Value {
    let mean = |f: &str| {
        let m = get(tasks, key, f);
        round(m.values().sum::<f64>() / m.len() as f64)
    };
    let gold: u64 = tasks
        .iter()
        .map(|t| t["symbol_gold"].as_u64().unwrap_or(0))
        .sum();
    let stage = |s: &str| {
        tasks
            .iter()
            .map(|t| t[key]["stages"][s].as_u64().unwrap())
            .sum::<u64>()
    };
    let mut out = json!({
        "required_symbol_recall": mean("required_symbol_recall"),
        "selection_loss": mean("selection_loss"), "packing_loss": mean("packing_loss"),
        "required_symbol_header_only": mean("required_symbol_header_only"),
        "gold_token_share": mean("gold_token_share"),
        "unlabeled_token_share": mean("unlabeled_token_share"),
        "tokens_used": mean("tokens_used"), "budget_utilization": mean("budget_utilization"),
        "items": mean("items"), "expansion_considered": mean("expansion_considered"),
        "gold_symbols_graph": stage("graph"), "gold_symbols_planned": stage("planned"),
        "gold_symbols_packed": stage("packed"), "gold_symbols": gold,
    });
    if tasks[0][key].get("latency_ms").is_some() {
        out["latency_ms_p50"] = round(p50(&get(tasks, key, "latency_ms")));
        out["plan_jaccard_vs_h"] = mean("plan_jaccard_vs_h");
        let mut comp: BTreeMap<String, [u64; 3]> = BTreeMap::new();
        for t in tasks {
            for (class, v) in t[key]["composition"].as_object().unwrap() {
                let e = comp.entry(class.clone()).or_default();
                for (k, x) in e.iter_mut().enumerate() {
                    *x += v[k].as_u64().unwrap();
                }
            }
        }
        out["composition_items_units_gold"] = json!(comp);
        let (c, d) = tasks.iter().fold((0, 0), |(c, d), t| {
            let a = &t[key]["jev_agreement"];
            (c + a[0].as_u64().unwrap(), d + a[1].as_u64().unwrap())
        });
        out["jev_order_agreement"] = round(c as f64 / (c + d).max(1) as f64);
    }
    if tasks[0][key].get("fallbacks").is_some() {
        let mut counts: BTreeMap<String, u64> = BTreeMap::new();
        for t in tasks {
            for (k, v) in t[key]["fallbacks"].as_object().unwrap() {
                *counts.entry(k.clone()).or_default() += v.as_u64().unwrap();
            }
        }
        out["fallbacks"] = json!(counts);
    }
    out
}

/// Every run's cells, each variant's paired differences against `H`, and
/// the dev decision rule.
fn variant_summary(tasks: &[Value], labels: &[&str]) -> Value {
    let mut out = BTreeMap::new();
    let base_p50 = p50(&get(tasks, "H@1024", "latency_ms"));
    for label in labels.iter().copied().chain(["oracle", "jev_replay"]) {
        let mut row = json!({});
        for b in AUDIT_BUDGETS {
            let key = format!("{label}@{b}");
            let mut c = cell(tasks, &key);
            if label != "H" {
                for f in ["required_symbol_recall", "unlabeled_token_share"] {
                    c[format!("{f}_vs_h")] =
                        paired(&get(tasks, &key, f), &get(tasks, &format!("H@{b}"), f));
                }
            }
            row[b.to_string()] = c;
        }
        if labels.contains(&label) && label != "H" {
            let d = |b: u32, f: &str| row[b.to_string()][format!("{f}_vs_h")].clone();
            // A missing difference fails every criterion.
            let mean = |v: &Value| v["mean_diff"].as_f64().unwrap_or(f64::NAN);
            let lo = |v: &Value| v["ci95"][0].as_f64().unwrap_or(f64::NEG_INFINITY);
            let r1 = d(1024, "required_symbol_recall");
            let latency = p50(&get(tasks, &format!("{label}@1024"), "latency_ms"));
            let passes = mean(&r1) >= 0.02
                && lo(&r1) > 0.0
                && mean(&d(4096, "required_symbol_recall")) >= 0.0
                && mean(&d(1024, "unlabeled_token_share")) <= 0.05
                && latency <= 1.2 * base_p50;
            row["latency_ratio_vs_h"] = round(latency / base_p50);
            row["passes_dev_rule"] = json!(passes);
        }
        out.insert(label.to_owned(), row);
    }
    json!(out)
}

fn run_dev(labels: &[&'static str]) -> Vec<Value> {
    let root = repo_root();
    let data = data_dir();
    let docs = root.join("docs/phase-5/decisionbench-v1");
    let mut tape = Tape::load(&[&docs.join("fixture/jev"), &data.join("jev")], None);
    let cache_dir = data.join("followup-4/dev");
    std::fs::create_dir_all(&cache_dir).unwrap();
    let mut tasks = Vec::new();
    for t in dev_tasks() {
        let id = t["id"].as_str().unwrap();
        let path = cache_dir.join(format!("{id}.json"));
        let cached = std::fs::read_to_string(&path)
            .ok()
            .map(|text| serde_json::from_str::<Value>(&text).unwrap())
            .filter(|r| {
                r["inputs"] == variant_inputs()
                    && labels.iter().all(|l| r.get(format!("{l}@1024")).is_some())
            });
        let r = match cached {
            Some(r) => r,
            None => {
                let (r, back) = measure_variants(&t, &data, labels, tape);
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

fn check_preregistration() {
    let prereg =
        std::fs::read(repo_root().join("docs/phase-5/followup-4/preregistration.md")).unwrap();
    assert_eq!(
        db::sha256_hex(&prereg),
        PREREGISTRATION_SHA256,
        "preregistration edited"
    );
}

/// Dev comparison of the preregistered variants.
#[test]
#[ignore = "machine-local: needs ContextBench worktrees"]
fn selection_variants() {
    check_preregistration();
    let tasks = run_dev(&LABELS);
    let out = json!({"preregistration_sha256": PREREGISTRATION_SHA256, "split": "dev",
                     "tasks": tasks.len(), "runs": variant_summary(&tasks, &LABELS)});
    let text = serde_json::to_string_pretty(&out).unwrap() + "\n";
    std::fs::write(
        repo_root().join("docs/phase-5/followup-4/variants.json"),
        &text,
    )
    .unwrap();
    eprintln!("{text}");
}
