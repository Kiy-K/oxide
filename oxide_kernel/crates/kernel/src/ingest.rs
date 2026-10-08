//! Ingestion: captured files plus per-language syntax facts become one
//! generation's manifest and write batch. The runtime parses (it owns the
//! parser substrate); the kernel decides language ownership, identity,
//! coverage and relation resolution. Phase 2B has one language slice,
//! Python ([`crate::python`]); every other file is unsupported evidence.

use std::collections::BTreeMap;

use crate::id::{Digest, EntityId, RepoPath, SnapshotKey};
use crate::knowledge::{
    Basis, Batch, ByteRange, Containment, Coverage, Entity, FileManifest, Relation, RelationKind,
    RepositorySnapshot, SourceRef, Target,
};
use crate::python;

/// Languages with an ingestion adapter.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Language {
    Python,
}

/// The language that owns a path, by file name. `None` files are kept as
/// unsupported file evidence, never as empty successfully parsed files.
pub fn language(path: &RepoPath) -> Option<Language> {
    path.as_str().ends_with(".py").then_some(Language::Python)
}

/// What the runtime learned from one file's bytes.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Facts {
    Python(python::ParsedFile),
    /// No adapter, or the adapter could not read the bytes at all.
    Unsupported(String),
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct InputFile {
    pub digest: Digest,
    pub byte_length: u64,
    pub facts: Facts,
}

/// Derives one generation from every captured file. Errors mean the facts
/// broke their own structural contract (a runtime bug), not bad source.
pub fn derive(
    key: SnapshotKey,
    files: &BTreeMap<RepoPath, InputFile>,
) -> Result<(RepositorySnapshot, Batch), String> {
    let mut batch = Batch::default();
    batch.entities.push(Entity {
        id: EntityId::Repository,
        kind: "repository".into(),
        name: key.repo.as_str().into(),
        signature: None,
        source: None,
        test: false,
    });
    let mut manifest = RepositorySnapshot {
        key,
        files: BTreeMap::new(),
    };
    let mut python_files = BTreeMap::new();
    for (path, file) in files {
        let coverage = match &file.facts {
            Facts::Python(parsed) => {
                python_files.insert(path, (&file.digest, parsed));
                if parsed.diagnostics.is_empty() {
                    Coverage::Complete
                } else {
                    Coverage::Partial {
                        diagnostics: parsed.diagnostics.clone(),
                    }
                }
            }
            Facts::Unsupported(reason) => Coverage::Unsupported {
                reason: reason.clone(),
            },
        };
        let language = match language(path) {
            Some(Language::Python) => "python",
            None => "unknown",
        };
        manifest.files.insert(
            path.clone(),
            FileManifest {
                digest: file.digest.clone(),
                byte_length: file.byte_length,
                language: language.into(),
                coverage,
            },
        );
        let id = EntityId::File(path.clone());
        batch.entities.push(Entity {
            id: id.clone(),
            kind: "file".into(),
            name: path.as_str().rsplit('/').next().unwrap_or_default().into(),
            signature: None,
            source: Some(SourceRef {
                file: path.clone(),
                range: ByteRange {
                    start: 0,
                    end: file.byte_length,
                },
                digest: file.digest.clone(),
            }),
            test: false,
        });
        batch.relations.push(contains(EntityId::Repository, id));
    }
    python::derive(&python_files, &mut batch)?;
    Ok((manifest, batch))
}

pub(crate) fn contains(parent: EntityId, child: EntityId) -> Relation {
    Relation {
        kind: RelationKind::Contains(Containment::Physical),
        from: parent,
        to: Target::Resolved(child),
        basis: Basis::Syntactic,
        evidence: None,
    }
}
