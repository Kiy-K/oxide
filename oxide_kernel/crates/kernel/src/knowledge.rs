//! Repository knowledge model: snapshot manifests, entities, typed relations
//! and source references, plus the publication invariants every store
//! enforces before a generation becomes visible.

use std::collections::BTreeMap;

use crate::id::{Digest, EntityId, RepoPath, SnapshotKey, SymbolId};

/// Half-open byte range `[start, end)` into the captured file bytes. Byte
/// ranges, not lines, attribute same-line and nested declarations; line
/// ranges are a derived view.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct ByteRange {
    pub start: u64,
    pub end: u64,
}

/// Source evidence: a range of one captured file, tied to that file's digest.
/// Snapshot identity comes from the read view the reference was read from.
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct SourceRef {
    pub file: RepoPath,
    pub range: ByteRange,
    pub digest: Digest,
}

/// How completely a file was understood. A failed parse is never `Complete`
/// with no entities.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Coverage {
    Complete,
    Partial { diagnostics: Vec<String> },
    Unsupported { reason: String },
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FileManifest {
    pub digest: Digest,
    pub language: String,
    pub coverage: Coverage,
}

/// Immutable manifest of one generation: its key and every captured file
/// with digest and coverage. Only published generations are readable, so a
/// manifest obtained from a read view is always a published one.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RepositorySnapshot {
    pub key: SnapshotKey,
    pub files: BTreeMap<RepoPath, FileManifest>,
}

/// A repository entity. `kind` is the language adapter's label ("function",
/// "class", "file", ...); its vocabulary is open until the Phase 2 language
/// slice. Tests are a facet (`test`), not a duplicate identity.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Entity {
    pub id: EntityId,
    pub kind: String,
    pub name: String,
    pub signature: Option<String>,
    pub source: Option<SourceRef>,
    pub test: bool,
}

/// Physical containment (repository ⊃ module/file, file ⊃ symbol,
/// symbol ⊃ symbol, exactly as the IDs nest) forms the TreeIndex hierarchy; logical containment
/// (module ⊃ file, ...) stays a cross-edge.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum Containment {
    Physical,
    Logical,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum RelationKind {
    Contains(Containment),
    Defines,
    References,
    Calls,
    Imports,
    Implements,
    TestedBy,
}

impl RelationKind {
    pub const ALL: [RelationKind; 8] = [
        RelationKind::Contains(Containment::Physical),
        RelationKind::Contains(Containment::Logical),
        RelationKind::Defines,
        RelationKind::References,
        RelationKind::Calls,
        RelationKind::Imports,
        RelationKind::Implements,
        RelationKind::TestedBy,
    ];
}

/// Relation target. Unresolved names and ambiguous candidates are kept as
/// evidence; a resolved endpoint is never invented.
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum Target {
    Resolved(EntityId),
    /// Two or more possible endpoints, strictly sorted.
    Ambiguous(Vec<EntityId>),
    Unresolved {
        name: String,
    },
}

impl Target {
    pub fn entities(&self) -> &[EntityId] {
        match self {
            Target::Resolved(id) => std::slice::from_ref(id),
            Target::Ambiguous(ids) => ids,
            Target::Unresolved { .. } => &[],
        }
    }
}

/// How a relation was established. A name coincidence or shared directory is
/// at most `Heuristic`, never a proven call or test dependency.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum Basis {
    /// Read directly off syntax (containment, an import statement).
    Syntactic,
    /// Name resolution proved the target.
    Resolved,
    /// Convention or proximity; evidence, not proof.
    Heuristic,
}

/// A typed, directed relation. Its derived order is the stable adjacency
/// order every store returns.
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct Relation {
    pub kind: RelationKind,
    pub from: EntityId,
    pub to: Target,
    pub basis: Basis,
    pub evidence: Option<SourceRef>,
}

/// Derived facts written into a staged generation.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Batch {
    pub entities: Vec<Entity>,
    pub relations: Vec<Relation>,
}

/// Publication invariants (SPEC § Storage abstraction), checked over a whole
/// generation before it becomes visible. Every store runs this; a failure
/// leaves the generation unpublished.
pub fn validate(
    manifest: &RepositorySnapshot,
    entities: &BTreeMap<EntityId, Entity>,
    relations: &[Relation],
) -> Result<(), String> {
    if !entities.contains_key(&EntityId::Repository) {
        return Err("no repository entity".into());
    }
    for path in manifest.files.keys() {
        if !entities.contains_key(&EntityId::File(path.clone())) {
            return Err(format!("manifest file {} has no entity", path.as_str()));
        }
    }
    for entity in entities.values() {
        let file = match &entity.id {
            EntityId::File(path) => Some(path),
            EntityId::Symbol(symbol) => Some(symbol.file()),
            _ => None,
        };
        if let Some(path) = file
            && !manifest.files.contains_key(path)
        {
            return Err(format!("{:?} is outside the manifest", entity.id));
        }
        if let Some(source) = &entity.source {
            check_source(manifest, source, &entity.id)?;
            if file.is_some_and(|path| *path != source.file) {
                return Err(format!("{:?} has source in another file", entity.id));
            }
        }
    }

    let mut parents: BTreeMap<&EntityId, Vec<&EntityId>> = BTreeMap::new();
    for relation in relations {
        if !entities.contains_key(&relation.from) {
            return Err(format!("dangling source {:?}", relation.from));
        }
        if let Target::Ambiguous(ids) = &relation.to
            && (ids.len() < 2 || !ids.is_sorted_by(|a, b| a < b))
        {
            return Err(format!(
                "ambiguous target of {:?} is not 2+ sorted ids",
                relation.from
            ));
        }
        for id in relation.to.entities() {
            if !entities.contains_key(id) {
                return Err(format!("dangling target {id:?}"));
            }
        }
        if let Some(source) = &relation.evidence {
            check_source(manifest, source, &relation.from)?;
        }
        if relation.kind == RelationKind::Contains(Containment::Physical) {
            let Target::Resolved(child) = &relation.to else {
                return Err("physical containment needs a resolved child".into());
            };
            parents.entry(child).or_default().push(&relation.from);
        }
    }

    // The physical hierarchy is exactly the one the IDs encode: every entity
    // but the repository has one parent, the one its ID implies. Each parent's
    // ID is strictly shorter, so the hierarchy is a tree rooted at the
    // repository and cannot cycle.
    for id in entities.keys() {
        let found = parents.get(id).map_or(&[][..], Vec::as_slice);
        let Some(expected) = physical_parent(id) else {
            if !found.is_empty() {
                return Err(format!("{id:?} needs exactly 0 physical parent(s)"));
            }
            continue;
        };
        match found {
            [parent] if **parent == expected => {}
            [parent] => {
                return Err(format!(
                    "{id:?} is physically contained by {parent:?}, not {expected:?}"
                ));
            }
            _ => return Err(format!("{id:?} needs exactly 1 physical parent(s)")),
        }
    }
    Ok(())
}

/// The physical parent an entity's identity implies: repository for modules
/// and files, the file for a top-level symbol, the enclosing declaration for a
/// nested one.
fn physical_parent(id: &EntityId) -> Option<EntityId> {
    match id {
        EntityId::Repository => None,
        EntityId::Module(_) | EntityId::File(_) => Some(EntityId::Repository),
        EntityId::Symbol(symbol) => Some(match symbol.path() {
            [_] => EntityId::File(symbol.file().clone()),
            [outer @ .., _] => EntityId::Symbol(
                SymbolId::new(symbol.file().clone(), outer.to_vec()).expect("non-empty prefix"),
            ),
            [] => unreachable!("SymbolId paths are non-empty"),
        }),
    }
}

fn check_source(
    manifest: &RepositorySnapshot,
    source: &SourceRef,
    owner: &EntityId,
) -> Result<(), String> {
    let digest_matches = manifest
        .files
        .get(&source.file)
        .is_some_and(|file| file.digest == source.digest);
    if !digest_matches || source.range.start > source.range.end {
        return Err(format!(
            "{owner:?} has source evidence outside the manifest"
        ));
    }
    Ok(())
}
