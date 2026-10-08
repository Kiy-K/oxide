//! Private JSON arrays provide an injective, versioned domain encoding.
use oxide_kernel::store::StoreError;
use oxide_kernel::{id::*, knowledge::*};
use serde_json::{Value as V, json};
type R<T> = Result<T, StoreError>;
fn bad() -> StoreError {
    StoreError::Corrupt("invalid domain codec; rebuild required".into())
}
fn strv(v: &V) -> R<&str> {
    v.as_str().ok_or_else(bad)
}
fn num(v: &V) -> R<u64> {
    v.as_u64().ok_or_else(bad)
}
fn arr(v: &V) -> R<&Vec<V>> {
    v.as_array().ok_or_else(bad)
}
pub fn text(v: &V) -> String {
    v.to_string()
}
pub fn parse(s: &str) -> R<V> {
    serde_json::from_str(s).map_err(|_| bad())
}
pub fn id(x: &EntityId) -> V {
    match x {
        EntityId::Repository => json!([0]),
        EntityId::Module(x) => json!([1, x.as_str()]),
        EntityId::File(x) => json!([2, x.as_str()]),
        EntityId::Symbol(x) => json!([
            3,
            x.file().as_str(),
            x.path()
                .iter()
                .map(|s| json!([s.name, s.ordinal]))
                .collect::<Vec<_>>()
        ]),
    }
}
pub fn de_id(v: &V) -> R<EntityId> {
    Ok(match num(&v[0])? {
        0 => EntityId::Repository,
        1 => EntityId::Module(ModuleId::new(strv(&v[1])?).map_err(|_| bad())?),
        2 => EntityId::File(RepoPath::new(strv(&v[1])?).map_err(|_| bad())?),
        3 => EntityId::Symbol(
            SymbolId::new(
                RepoPath::new(strv(&v[1])?).map_err(|_| bad())?,
                arr(&v[2])?
                    .iter()
                    .map(|s| {
                        Ok(Segment {
                            name: strv(&s[0])?.into(),
                            ordinal: u32::try_from(num(&s[1])?).map_err(|_| bad())?,
                        })
                    })
                    .collect::<R<_>>()?,
            )
            .map_err(|_| bad())?,
        ),
        _ => return Err(bad()),
    })
}
pub fn key(k: &SnapshotKey) -> V {
    json!([k.repo.as_str(), k.snapshot.as_str(), k.derivation.as_str()])
}
pub fn de_key(v: &V) -> R<SnapshotKey> {
    Ok(SnapshotKey {
        repo: RepoId::new(strv(&v[0])?).map_err(|_| bad())?,
        snapshot: SnapshotId::new(strv(&v[1])?).map_err(|_| bad())?,
        derivation: DerivationId::new(strv(&v[2])?).map_err(|_| bad())?,
    })
}
fn source(s: &Option<SourceRef>) -> V {
    s.as_ref().map_or(V::Null, |s| {
        json!([
            s.file.as_str(),
            s.range.start,
            s.range.end,
            s.digest.as_str()
        ])
    })
}
fn de_source(v: &V) -> R<Option<SourceRef>> {
    if v.is_null() {
        return Ok(None);
    }
    Ok(Some(SourceRef {
        file: RepoPath::new(strv(&v[0])?).map_err(|_| bad())?,
        range: ByteRange {
            start: num(&v[1])?,
            end: num(&v[2])?,
        },
        digest: Digest::new(strv(&v[3])?).map_err(|_| bad())?,
    }))
}
pub fn entity(e: &Entity) -> V {
    json!([
        id(&e.id),
        e.kind,
        e.name,
        e.signature,
        source(&e.source),
        e.test
    ])
}
pub fn de_entity(v: &V) -> R<Entity> {
    Ok(Entity {
        id: de_id(&v[0])?,
        kind: strv(&v[1])?.into(),
        name: strv(&v[2])?.into(),
        signature: if v[3].is_null() {
            None
        } else {
            Some(strv(&v[3])?.into())
        },
        source: de_source(&v[4])?,
        test: v[5].as_bool().ok_or_else(bad)?,
    })
}
pub fn kind(k: RelationKind) -> usize {
    RelationKind::ALL.iter().position(|x| *x == k).unwrap()
}
pub fn relation(r: &Relation) -> V {
    let t = match &r.to {
        Target::Resolved(x) => json!([0, id(x)]),
        Target::Ambiguous(xs) => json!([1, xs.iter().map(id).collect::<Vec<_>>()]),
        Target::Unresolved { name } => json!([2, name]),
    };
    json!([
        kind(r.kind),
        id(&r.from),
        t,
        match r.basis {
            Basis::Syntactic => 0,
            Basis::Resolved => 1,
            Basis::Heuristic => 2,
        },
        source(&r.evidence)
    ])
}
pub fn de_relation(v: &V) -> R<Relation> {
    let t = &v[2];
    Ok(Relation {
        kind: *RelationKind::ALL
            .get(num(&v[0])? as usize)
            .ok_or_else(bad)?,
        from: de_id(&v[1])?,
        to: match num(&t[0])? {
            0 => Target::Resolved(de_id(&t[1])?),
            1 => Target::Ambiguous(arr(&t[1])?.iter().map(de_id).collect::<R<_>>()?),
            2 => Target::Unresolved {
                name: strv(&t[1])?.into(),
            },
            _ => return Err(bad()),
        },
        basis: match num(&v[3])? {
            0 => Basis::Syntactic,
            1 => Basis::Resolved,
            2 => Basis::Heuristic,
            _ => return Err(bad()),
        },
        evidence: de_source(&v[4])?,
    })
}
pub fn manifest(m: &RepositorySnapshot) -> V {
    json!([
        super::STORAGE_FORMAT,
        key(&m.key),
        m.files
            .iter()
            .map(|(p, f)| {
                let c = match &f.coverage {
                    Coverage::Complete => json!([0]),
                    Coverage::Partial { diagnostics } => json!([1, diagnostics]),
                    Coverage::Unsupported { reason } => json!([2, reason]),
                };
                json!([p.as_str(), f.digest.as_str(), f.language, c, f.byte_length])
            })
            .collect::<Vec<_>>()
    ])
}
pub fn de_manifest(v: &V) -> R<RepositorySnapshot> {
    if num(&v[0])? != super::STORAGE_FORMAT {
        return Err(StoreError::Corrupt(
            "storage format mismatch; rebuild required".into(),
        ));
    }
    Ok(RepositorySnapshot {
        key: de_key(&v[1])?,
        files: arr(&v[2])?
            .iter()
            .map(|f| {
                let c = &f[3];
                Ok((
                    RepoPath::new(strv(&f[0])?).map_err(|_| bad())?,
                    FileManifest {
                        digest: Digest::new(strv(&f[1])?).map_err(|_| bad())?,
                        language: strv(&f[2])?.into(),
                        coverage: match num(&c[0])? {
                            0 => Coverage::Complete,
                            1 => Coverage::Partial {
                                diagnostics: arr(&c[1])?
                                    .iter()
                                    .map(|s| strv(s).map(String::from))
                                    .collect::<R<_>>()?,
                            },
                            2 => Coverage::Unsupported {
                                reason: strv(&c[1])?.into(),
                            },
                            _ => return Err(bad()),
                        },
                        byte_length: num(&f[4])?,
                    },
                ))
            })
            .collect::<R<_>>()?,
    })
}
