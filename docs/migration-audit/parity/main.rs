// Parity dump: f79808a kernel derivation + runtime tree-sitter extraction over a
// given file list (one repo path per stdin line), so both engines see the same bytes.
#[path = "../../rust/oxide_kernel/crates/runtime/src/python.rs"]
#[allow(dead_code)]
mod python;

use oxide_kernel::id::*;
use oxide_kernel::ingest::{self, Facts, InputFile, Language};
use oxide_kernel::knowledge::*;
use std::collections::BTreeMap;
use std::io::BufRead;

fn key(id: &EntityId) -> String {
    match id {
        EntityId::Repository => "repo".into(),
        EntityId::Module(m) => format!("module:{}", m.as_str()),
        EntityId::File(p) => format!("file:{}", p.as_str()),
        EntityId::Symbol(s) => {
            let path: Vec<String> = s.path().iter().map(|g| if g.ordinal == 0 { g.name.clone() } else { format!("{}~{}", g.name, g.ordinal) }).collect();
            format!("symbol:{}#{}", s.file().as_str(), path.join("."))
        }
    }
}

fn kind(k: &RelationKind) -> &'static str {
    match k {
        RelationKind::Contains(Containment::Physical) => "contains",
        RelationKind::Contains(Containment::Logical) => "contains_logical",
        RelationKind::Defines => "defines",
        RelationKind::References => "references",
        RelationKind::Calls => "calls",
        RelationKind::Imports => "imports",
        RelationKind::Implements => "implements",
        RelationKind::TestedBy => "tested_by",
    }
}

fn span(s: &Option<SourceRef>) -> String {
    s.as_ref().map(|s| format!("{}:{}-{}", s.file.as_str(), s.range.start, s.range.end)).unwrap_or("-".into())
}

fn main() {
    let root = std::path::PathBuf::from(std::env::args().nth(1).expect("root"));
    let mut parser = python::PythonParser::default();
    let mut files = BTreeMap::new();
    for line in std::io::stdin().lock().lines() {
        let path = line.unwrap();
        let bytes = std::fs::read(root.join(&path)).unwrap();
        let rp = RepoPath::new(path).unwrap();
        let facts = match ingest::language(&rp) {
            Some(Language::Python) => parser.parse(&bytes),
            None => Facts::Unsupported("no language adapter".into()),
        };
        files.insert(rp, InputFile { digest: Digest::new("d").unwrap(), byte_length: bytes.len() as u64, facts });
    }
    let snap = SnapshotKey { repo: RepoId::new("r").unwrap(), snapshot: SnapshotId::new("s").unwrap(), derivation: DerivationId::new("x").unwrap() };
    let (_, batch) = ingest::derive(snap, &files).unwrap();
    for e in &batch.entities {
        println!("E\t{}\t{}\t{}\t{}\t{}\t{}", key(&e.id), e.kind, if matches!(e.id, EntityId::Repository) { "-" } else { &e.name }, e.signature.as_deref().unwrap_or(""), span(&e.source), e.test);
    }
    for r in &batch.relations {
        let (res, targets, name) = match &r.to {
            Target::Resolved(t) => ("resolved", key(t), String::new()),
            Target::Ambiguous(ts) => ("ambiguous", ts.iter().map(key).collect::<Vec<_>>().join(","), String::new()),
            Target::Unresolved { name } => ("unresolved", String::new(), name.clone()),
        };
        println!("R\t{}\t{}\t{}\t{}\t{:?}\t{}\t{}", kind(&r.kind), key(&r.from), res, targets, r.basis, name, span(&r.evidence));
    }
}
