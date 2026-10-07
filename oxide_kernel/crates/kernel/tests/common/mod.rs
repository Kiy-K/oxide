//! Deterministic fixture repository shared by the kernel contract tests.
//!
//! ```text
//! repository
//! ├── module app            (logically ⊃ src/a.rs, src/b.rs)
//! ├── src/a.rs   Complete
//! │   ├── C                 bytes 0..60, one line
//! │   │   ├── f#0           bytes 11..20 (same line as C)
//! │   │   └── f#1           bytes 21..32 (overload)
//! │   └── g                 calls h; calls f (ambiguous f#0/f#1); references `missing`
//! ├── src/b.rs   Partial
//! │   └── h                 calls g (cycle); tested_by test_h (heuristic)
//! └── tests/t.rs Complete
//!     └── test_h            test facet
//! ```
#![allow(dead_code)]

use std::collections::BTreeMap;

use oxide_kernel::id::*;
use oxide_kernel::knowledge::*;
use oxide_kernel::store::KnowledgeStore;

pub fn key(snapshot: &str, derivation: &str) -> SnapshotKey {
    SnapshotKey {
        repo: RepoId::new("fixture").unwrap(),
        snapshot: SnapshotId::new(snapshot).unwrap(),
        derivation: DerivationId::new(derivation).unwrap(),
    }
}

pub fn path(p: &str) -> RepoPath {
    RepoPath::new(p).unwrap()
}

pub fn file(p: &str) -> EntityId {
    EntityId::File(path(p))
}

pub fn sym(p: &str, segments: &[(&str, u32)]) -> EntityId {
    let segments = segments
        .iter()
        .map(|(name, ordinal)| Segment {
            name: (*name).into(),
            ordinal: *ordinal,
        })
        .collect();
    EntityId::Symbol(SymbolId::new(path(p), segments).unwrap())
}

pub fn module() -> EntityId {
    EntityId::Module(ModuleId::new("app").unwrap())
}

pub fn c() -> EntityId {
    sym("src/a.rs", &[("C", 0)])
}
pub fn f0() -> EntityId {
    sym("src/a.rs", &[("C", 0), ("f", 0)])
}
pub fn f1() -> EntityId {
    sym("src/a.rs", &[("C", 0), ("f", 1)])
}
pub fn g() -> EntityId {
    sym("src/a.rs", &[("g", 0)])
}
pub fn h() -> EntityId {
    sym("src/b.rs", &[("h", 0)])
}
pub fn test_h() -> EntityId {
    sym("tests/t.rs", &[("test_h", 0)])
}

pub fn digest(p: &str) -> Digest {
    Digest::new(format!("test:{p}")).unwrap()
}

pub fn manifest(key: SnapshotKey) -> RepositorySnapshot {
    let entry = |p: &str, coverage| {
        let manifest = FileManifest {
            digest: digest(p),
            byte_length: 100,
            language: "rust".into(),
            coverage,
        };
        (path(p), manifest)
    };
    let partial = Coverage::Partial {
        diagnostics: vec!["syntax error at byte 31".into()],
    };
    RepositorySnapshot {
        key,
        files: BTreeMap::from([
            entry("src/a.rs", Coverage::Complete),
            entry("src/b.rs", partial),
            entry("tests/t.rs", Coverage::Complete),
        ]),
    }
}

pub fn entity(id: EntityId, kind: &str, name: &str, range: Option<(u64, u64)>) -> Entity {
    let source = range.map(|(start, end)| {
        let file = match &id {
            EntityId::File(p) => p.clone(),
            EntityId::Symbol(s) => s.file().clone(),
            _ => unreachable!("only files and symbols have source"),
        };
        SourceRef {
            digest: digest(file.as_str()),
            file,
            range: ByteRange { start, end },
        }
    });
    Entity {
        id,
        kind: kind.into(),
        name: name.into(),
        signature: None,
        source,
        test: false,
    }
}

pub fn rel(kind: RelationKind, from: EntityId, to: Target, basis: Basis) -> Relation {
    Relation {
        kind,
        from,
        to,
        basis,
        evidence: None,
    }
}

pub fn contains(from: EntityId, to: EntityId) -> Relation {
    rel(
        RelationKind::Contains(Containment::Physical),
        from,
        Target::Resolved(to),
        Basis::Syntactic,
    )
}

pub fn batch() -> Batch {
    let mut test = entity(test_h(), "function", "test_h", Some((0, 40)));
    test.test = true;
    let entities = vec![
        entity(EntityId::Repository, "repository", "fixture", None),
        entity(module(), "module", "app", None),
        entity(file("src/a.rs"), "file", "a.rs", Some((0, 90))),
        entity(file("src/b.rs"), "file", "b.rs", Some((0, 30))),
        entity(file("tests/t.rs"), "file", "t.rs", Some((0, 40))),
        entity(c(), "struct", "C", Some((0, 60))),
        entity(f0(), "method", "f", Some((11, 20))),
        entity(f1(), "method", "f", Some((21, 32))),
        entity(g(), "function", "g", Some((61, 90))),
        entity(h(), "function", "h", Some((0, 30))),
        test,
    ];
    let logical = RelationKind::Contains(Containment::Logical);
    let relations = vec![
        contains(EntityId::Repository, module()),
        contains(EntityId::Repository, file("src/a.rs")),
        contains(EntityId::Repository, file("src/b.rs")),
        contains(EntityId::Repository, file("tests/t.rs")),
        contains(file("src/a.rs"), c()),
        contains(c(), f0()),
        contains(c(), f1()),
        contains(file("src/a.rs"), g()),
        contains(file("src/b.rs"), h()),
        contains(file("tests/t.rs"), test_h()),
        rel(
            logical,
            module(),
            Target::Resolved(file("src/a.rs")),
            Basis::Resolved,
        ),
        rel(
            logical,
            module(),
            Target::Resolved(file("src/b.rs")),
            Basis::Resolved,
        ),
        rel(
            RelationKind::Calls,
            g(),
            Target::Resolved(h()),
            Basis::Resolved,
        ),
        rel(
            RelationKind::Calls,
            h(),
            Target::Resolved(g()),
            Basis::Resolved,
        ),
        rel(
            RelationKind::Calls,
            g(),
            Target::Ambiguous(vec![f0(), f1()]),
            Basis::Syntactic,
        ),
        rel(
            RelationKind::References,
            g(),
            Target::Unresolved {
                name: "missing".into(),
            },
            Basis::Syntactic,
        ),
        rel(
            RelationKind::TestedBy,
            h(),
            Target::Resolved(test_h()),
            Basis::Heuristic,
        ),
    ];
    Batch {
        entities,
        relations,
    }
}

/// Stages, writes and publishes `batch` under `key`.
pub fn publish<S: KnowledgeStore>(store: &mut S, key: &SnapshotKey, batch: Batch) {
    store.begin(manifest(key.clone())).unwrap();
    store.write(key, batch).unwrap();
    store.publish(key).unwrap();
}

pub mod store_cases;
