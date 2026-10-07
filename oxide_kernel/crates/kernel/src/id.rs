//! Domain identity. Proposed policy (README § Identity policy): every ID is a
//! structural value built from its identity inputs, so it is deterministic,
//! equal exactly when the inputs are equal, and unrelated to storage
//! allocation order. File, module and symbol IDs mean something only inside a
//! [`SnapshotKey`]; the store read view supplies that scope.

use std::fmt;

/// An identity input that violates the identity policy.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct IdError {
    pub kind: &'static str,
    pub input: String,
}

impl fmt::Display for IdError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "invalid {}: {:?}", self.kind, self.input)
    }
}

impl std::error::Error for IdError {}

/// Opaque identifiers whose derivation belongs to source capture (Phase 2):
/// the kernel only requires them to be non-empty and free of control
/// characters.
macro_rules! opaque_id {
    ($($(#[$doc:meta])* $name:ident),* $(,)?) => {$(
        $(#[$doc])*
        #[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash)]
        pub struct $name(String);

        impl $name {
            pub fn new(value: impl Into<String>) -> Result<Self, IdError> {
                let value = value.into();
                if value.is_empty() || value.chars().any(char::is_control) {
                    return Err(IdError { kind: stringify!($name), input: value });
                }
                Ok(Self(value))
            }

            pub fn as_str(&self) -> &str {
                &self.0
            }
        }
    )*};
}

opaque_id! {
    /// OXIDE repository namespace, independent of database identity and
    /// checkout location.
    RepoId,
    /// Captured source state: commit plus dirty/untracked content and scope.
    SnapshotId,
    /// Versioned derivation of a snapshot: parser, schema, ID namespace and
    /// provider versions that produced the derived knowledge.
    DerivationId,
    /// Language/build namespace, independent of file identity.
    ModuleId,
    /// Content digest of captured source bytes, algorithm-prefixed by capture.
    Digest,
}

/// One published (or staged) generation of repository knowledge. Every read
/// is pinned to exactly one key; there is no "any derivation" lookup.
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct SnapshotKey {
    pub repo: RepoId,
    pub snapshot: SnapshotId,
    pub derivation: DerivationId,
}

/// Repository-relative file path: UTF-8, `/`-separated, case-sensitive,
/// compared byte for byte. No empty, `.` or `..` segments, no leading or
/// trailing `/`, no NUL. Platform separators and case folding are capture's
/// job (it rejects case-fold collisions before they reach the domain).
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct RepoPath(String);

impl RepoPath {
    pub fn new(path: impl Into<String>) -> Result<Self, IdError> {
        let path = path.into();
        let valid = !path.contains('\0')
            && path
                .split('/')
                .all(|segment| !segment.is_empty() && segment != "." && segment != "..");
        if !valid {
            return Err(IdError {
                kind: "RepoPath",
                input: path,
            });
        }
        Ok(Self(path))
    }

    pub fn as_str(&self) -> &str {
        &self.0
    }
}

/// One step of a symbol's declaration path. `ordinal` is the 0-based position
/// among same-named siblings in source byte order, which separates overloads
/// and duplicate names without language-specific signature rules.
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct Segment {
    pub name: String,
    pub ordinal: u32,
}

/// Declaration identity: its file plus the nesting path of declarations from
/// the file scope down. Byte ranges are attribution, not identity.
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct SymbolId {
    file: RepoPath,
    path: Vec<Segment>,
}

impl SymbolId {
    pub fn new(file: RepoPath, path: Vec<Segment>) -> Result<Self, IdError> {
        if path.is_empty() || path.iter().any(|s| s.name.is_empty()) {
            return Err(IdError {
                kind: "SymbolId",
                input: format!("{}#{path:?}", file.as_str()),
            });
        }
        Ok(Self { file, path })
    }

    pub fn file(&self) -> &RepoPath {
        &self.file
    }

    pub fn path(&self) -> &[Segment] {
        &self.path
    }
}

/// Typed union of entity identities. The derived order (repository, modules,
/// files, symbols, then by value) is the stable domain order.
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum EntityId {
    Repository,
    Module(ModuleId),
    File(RepoPath),
    Symbol(SymbolId),
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn repo_paths_follow_the_documented_rules() {
        for ok in ["a", "src/lib.rs", "A/a", "dir/.hidden", "back\\slash"] {
            assert!(RepoPath::new(ok).is_ok(), "{ok}");
        }
        for bad in ["", "/abs", "a/", "a//b", "./a", "a/../b", "..", "a\0b"] {
            assert!(RepoPath::new(bad).is_err(), "{bad:?}");
        }
        assert_ne!(RepoPath::new("A").unwrap(), RepoPath::new("a").unwrap());
    }

    #[test]
    fn opaque_ids_reject_empty_and_control_input() {
        assert!(RepoId::new("").is_err());
        assert!(SnapshotId::new("a\nb").is_err());
        assert_eq!(DerivationId::new("d1").unwrap().as_str(), "d1");
    }

    #[test]
    fn overloads_nesting_and_duplicates_get_distinct_ids() {
        let file = RepoPath::new("a.rs").unwrap();
        let seg = |name: &str, ordinal| Segment {
            name: name.into(),
            ordinal,
        };
        let f0 = SymbolId::new(file.clone(), vec![seg("f", 0)]).unwrap();
        let f1 = SymbolId::new(file.clone(), vec![seg("f", 1)]).unwrap();
        let nested = SymbolId::new(file.clone(), vec![seg("C", 0), seg("f", 0)]).unwrap();
        assert!(f0 != f1 && f0 != nested && f1 != nested);
        assert!(SymbolId::new(file.clone(), vec![]).is_err());
        assert!(SymbolId::new(file, vec![seg("", 0)]).is_err());
    }

    #[test]
    fn entity_order_is_the_domain_order() {
        let file = EntityId::File(RepoPath::new("a").unwrap());
        let module = EntityId::Module(ModuleId::new("m").unwrap());
        let mut ids = vec![file.clone(), EntityId::Repository, module.clone()];
        ids.sort();
        assert_eq!(ids, [EntityId::Repository, module, file]);
    }
}
