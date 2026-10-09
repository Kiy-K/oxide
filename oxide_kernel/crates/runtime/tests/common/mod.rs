//! Shared by the frozen evaluation harnesses (Phase 3 retrieval/routing,
//! Phase 4 context): the retained Python gold mapped to v2 entity IDs,
//! readable entity names and macro averages.
#![allow(dead_code)]

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use oxide_kernel::id::{EntityId, RepoPath, Segment, SymbolId};
use oxide_kernel::store::ReadView;
use serde_json::{Value, json};

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
