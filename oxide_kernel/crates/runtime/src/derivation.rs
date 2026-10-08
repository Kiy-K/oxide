//! Derivation: canonical versioned component identity, distinct from
//! captured source, and the full-rebuild derivation of one capture.
use oxide_kernel::id::{DerivationId, RepoId, SnapshotKey};
use oxide_kernel::ingest::{self, Facts, InputFile, Language};
use oxide_kernel::knowledge::{Batch, RepositorySnapshot};
use oxide_kernel::source::SourceCapture;
use oxide_kernel::store::StoreError;
use std::collections::BTreeMap;

use crate::python::{GRAMMAR, PythonParser};
use crate::storage;

/// The version components that name this runtime's derivations (ADR-0010
/// § DerivationId). Changing any of them changes every `DerivationId`.
pub fn components() -> BTreeMap<String, String> {
    [
        ("id-namespace", "oxide-id-v1".to_owned()),
        ("python", oxide_kernel::python::VERSION.to_owned()),
        ("python-parser", GRAMMAR.to_owned()),
        (
            "tree-projection",
            oxide_kernel::tree::PROJECTION.0.to_string(),
        ),
        ("storage-schema", storage::SCHEMA.to_owned()),
        ("storage-format", storage::STORAGE_FORMAT.to_string()),
        ("lbug", lbug::VERSION.to_owned()),
    ]
    .into_iter()
    .map(|(k, v)| (k.to_owned(), v))
    .collect()
}

/// Full rebuild of one capture: every file is parsed by its language
/// adapter (or kept as unsupported evidence) and the kernel derives the
/// generation. Returns the manifest, its batch and the derivation
/// components named by the manifest's `DerivationId`.
pub fn derive(
    repo: &RepoId,
    capture: &SourceCapture,
) -> Result<(RepositorySnapshot, Batch, BTreeMap<String, String>), StoreError> {
    let components = components();
    let key = SnapshotKey {
        repo: repo.clone(),
        snapshot: capture.snapshot.clone(),
        derivation: derivation_id(&components),
    };
    let mut parser = PythonParser::default();
    let files = capture
        .files
        .iter()
        .map(|(path, file)| {
            let facts = match ingest::language(path) {
                Some(Language::Python) => parser.parse(&file.bytes),
                None => Facts::Unsupported("no language adapter".into()),
            };
            let input = InputFile {
                digest: file.digest.clone(),
                byte_length: file.bytes.len() as u64,
                facts,
            };
            (path.clone(), input)
        })
        .collect();
    let (manifest, batch) = ingest::derive(key, &files).map_err(StoreError::InvalidBatch)?;
    Ok((manifest, batch, components))
}

/// Components sort by byte-exact key. Every string is length-prefixed with
/// its UTF-8 byte length as little-endian u64; count is little-endian u64.
pub fn derivation_id(components: &BTreeMap<String, String>) -> DerivationId {
    let mut bytes = Vec::new();
    fn field(bytes: &mut Vec<u8>, value: &str) {
        bytes.extend_from_slice(&(value.len() as u64).to_le_bytes());
        bytes.extend_from_slice(value.as_bytes());
    }
    field(&mut bytes, "oxide-derivation-v1");
    bytes.extend_from_slice(&(components.len() as u64).to_le_bytes());
    for (key, value) in components {
        field(&mut bytes, key);
        field(&mut bytes, value);
    }
    DerivationId::new(crate::capture::digest(&bytes).as_str()).expect("SHA-256 is a valid ID")
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn components_are_order_independent_but_changes_are_isolated() {
        let a = BTreeMap::from([
            ("parser".into(), "1".into()),
            ("storage".into(), "47".into()),
        ]);
        let b = [
            ("storage".into(), "47".into()),
            ("parser".into(), "1".into()),
        ]
        .into_iter()
        .collect();
        assert_eq!(derivation_id(&a), derivation_id(&b));
        let mut changed = a.clone();
        changed.insert("parser".into(), "2".into());
        assert_ne!(derivation_id(&a), derivation_id(&changed));
        assert_ne!(
            derivation_id(&BTreeMap::from([("a".into(), "bc".into())])),
            derivation_id(&BTreeMap::from([("ab".into(), "c".into())]))
        );
    }
}
