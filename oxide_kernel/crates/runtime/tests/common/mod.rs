//! Shared by the frozen evaluation harnesses (Phase 3 retrieval/routing,
//! Phase 4 context, Phase 5 DecisionBench): the retained Python gold
//! mapped to v2 entity IDs, readable entity names, macro averages, the
//! constant no-judgment control and the context-quality metrics.
#![allow(dead_code)]

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use oxide_kernel::capsule::{Capsule, Question};
use oxide_kernel::context::ContextRun;
use oxide_kernel::decision::{
    DecisionProvider, Judgment, ProviderError, ProviderIdentity, Verdict,
};
use oxide_kernel::id::{EntityId, RepoPath, Segment, SymbolId};
use oxide_kernel::knowledge::SourceRef;
use oxide_kernel::pack::View;
use oxide_kernel::select::{OmitReason, Reason, Stage};
use oxide_kernel::store::ReadView;
use serde_json::{Value, json};

pub mod phase5;

pub fn repo_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../../..")
}

/// Stable, readable entity names: the gold notation for symbols
/// (`file#Outer.inner`, `~n` for a nonzero ordinal).
pub fn name(id: &EntityId) -> String {
    match id {
        EntityId::Repository => "repository".into(),
        EntityId::Module(m) => format!("module:{}", m.as_str()),
        EntityId::File(p) => p.as_str().into(),
        EntityId::Symbol(s) => {
            let path: Vec<String> = s
                .path()
                .iter()
                .map(|seg| match seg.ordinal {
                    0 => seg.name.clone(),
                    n => format!("{}~{n}", seg.name),
                })
                .collect();
            format!("{}#{}", s.file().as_str(), path.join("."))
        }
    }
}

pub struct Task {
    pub id: String,
    pub source: &'static str,
    pub kind: String,
    pub text: String,
    pub symbols: Vec<String>,
    pub gold: Vec<EntityId>,
    pub mapping: Vec<Value>,
}

/// Gold `file#A.b` maps to the symbol with ordinal 0 at every step. A
/// missing entity, or a same-named sibling at ordinal 1 (ambiguous), is
/// recorded and left out of the denominator, never silently dropped.
pub fn tasks(view: &impl ReadView) -> Vec<Task> {
    let mut out = Vec::new();
    for source in ["benchmark.json", "structural_benchmark.json"] {
        let file = repo_root().join("fixtures").join(source);
        let bench: Value = serde_json::from_slice(&std::fs::read(file).unwrap()).unwrap();
        for q in bench["queries"].as_array().unwrap() {
            if q["repo"] != "py" {
                continue;
            }
            let mut gold = Vec::new();
            let mut mapping = Vec::new();
            for g in q["relevant"].as_array().unwrap() {
                let g = g.as_str().unwrap();
                let (file, path) = g.split_once('#').unwrap();
                let symbol = |last: u32| {
                    let names: Vec<&str> = path.split('.').collect();
                    let n = names.len();
                    let segs = names.iter().enumerate().map(|(i, s)| Segment {
                        name: (*s).into(),
                        ordinal: if i + 1 == n { last } else { 0 },
                    });
                    let file = RepoPath::new(file).unwrap();
                    EntityId::Symbol(SymbolId::new(file, segs.collect()).unwrap())
                };
                let (id, sibling) = (symbol(0), symbol(1));
                let found = view.entities(&[id.clone(), sibling]).unwrap();
                let status = match (&found[0], &found[1]) {
                    (Some(_), None) => "mapped",
                    (Some(_), Some(_)) => "ambiguous",
                    (None, _) => "missing",
                };
                mapping.push(json!({"gold": g, "entity": name(&id), "status": status}));
                if status == "mapped" {
                    gold.push(id);
                }
            }
            let symbols = q["anchor_symbol"]
                .as_str()
                .map(|s| vec![s.to_owned()])
                .unwrap_or_default();
            out.push(Task {
                id: q["id"].as_str().unwrap().into(),
                source,
                kind: q["task"].as_str().unwrap().into(),
                text: q["text"].as_str().unwrap().into(),
                symbols,
                gold,
                mapping,
            });
        }
    }
    out
}

/// Macro average of every numeric field over tasks.
pub fn mean(rows: &[Value]) -> Value {
    let mut sums: BTreeMap<String, f64> = BTreeMap::new();
    for row in rows {
        for (k, v) in row.as_object().unwrap() {
            if let Some(x) = v.as_f64() {
                *sums.entry(k.clone()).or_default() += x;
            }
        }
    }
    let n = rows.len() as f64;
    sums.into_iter()
        .map(|(k, v)| (k, json!((v / n * 1e4).round() / 1e4)))
        .collect::<serde_json::Map<_, _>>()
        .into()
}

/// Control A: no judgment at all. Every subject gets the same constant, so
/// greedy order is the pool order (entry points in CandidateSet order, then
/// routed-only candidates by depth) and routing is unchanged (0.5 never
/// prunes).
pub struct Constant;

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

pub fn covers(item: &SourceRef, gold: &SourceRef) -> bool {
    item.file == gold.file
        && item.range.start <= gold.range.start
        && gold.range.end <= item.range.end
}

pub fn overlaps(item: &SourceRef, gold: &SourceRef) -> bool {
    item.file == gold.file && item.range.start < gold.range.end && gold.range.start < item.range.end
}

/// Context quality of one packed bundle against mapped gold (`ids`, with
/// their source refs in `gold`): the Phase 4 metric definitions.
pub fn metrics(ids: &[EntityId], gold: &[SourceRef], run: &ContextRun) -> Value {
    let bundle = &run.bundle;
    let items: Vec<&SourceRef> = bundle.items.iter().map(|i| &i.source).collect();
    let n = ids.len() as f64;
    let share = |f: &dyn Fn(usize) -> bool| (0..gold.len()).filter(|&i| f(i)).count() as f64 / n;
    let complete = |i: usize| items.iter().any(|s| covers(s, &gold[i]));
    let partial = |i: usize| !complete(i) && items.iter().any(|s| overlaps(s, &gold[i]));
    let in_file = |i: usize| items.iter().any(|s| s.file == gold[i].file);
    let routed = |i: usize| run.graph.nodes.iter().any(|n| n.entity.id == ids[i]);
    let planned = |i: usize| run.plan.items.iter().any(|p| p.entity == ids[i]);
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

pub fn gold_sources(view: &impl ReadView, ids: &[EntityId]) -> Vec<SourceRef> {
    view.entities(ids)
        .unwrap()
        .into_iter()
        .map(|e| e.unwrap().source.unwrap())
        .collect()
}

pub fn rss_peak_kib() -> u64 {
    let status = std::fs::read_to_string("/proc/self/status").unwrap_or_default();
    let line = status.lines().find(|l| l.starts_with("VmHWM:"));
    line.and_then(|l| l.split_whitespace().nth(1)?.parse().ok())
        .unwrap_or(0)
}
