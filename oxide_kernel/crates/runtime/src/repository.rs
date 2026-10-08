//! Linux repository discovery, external derived-store ownership and the
//! full-rebuild pipeline: capture → derive → stage → publish.
use crate::capture::{self, CaptureError, Scope};
use crate::storage::LadybugStore;
use oxide_kernel::id::{RepoId, RepoPath, SnapshotKey};
use oxide_kernel::source::Skip;
use oxide_kernel::store::{KnowledgeStore, StoreError};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::fs::{self, DirBuilder, File, OpenOptions};
use std::io::{Read, Write};
use std::os::unix::fs::DirBuilderExt;
use std::path::{Component, Path, PathBuf};

/// Application data root; callers can explicitly configure this seam.
#[derive(Debug, Clone)]
pub struct DataLocation(pub PathBuf);

impl DataLocation {
    /// XDG application data; absolute XDG_DATA_HOME takes priority.
    pub fn linux() -> Result<Self, StoreError> {
        if let Some(path) = std::env::var_os("XDG_DATA_HOME")
            .map(PathBuf::from)
            .filter(|p| p.is_absolute())
        {
            return Ok(Self(path.join("oxide/v2")));
        }
        let home = std::env::var_os("HOME")
            .map(PathBuf::from)
            .filter(|p| p.is_absolute())
            .ok_or_else(|| {
                StoreError::Unsupported(
                    "Linux application data location unavailable; configure DataLocation".into(),
                )
            })?;
        Ok(Self(home.join(".local/share/oxide/v2")))
    }
}

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Metadata {
    version: u32,
    repo_id: String,
    checkout: PathBuf,
}
fn io(error: std::io::Error) -> StoreError {
    StoreError::Io(error.to_string())
}

fn external_directory(path: &Path, root: &Path) -> Result<PathBuf, StoreError> {
    if !path.is_absolute() {
        return Err(StoreError::Unsupported(
            "application data directory must be absolute".into(),
        ));
    }
    let mut resolved = PathBuf::new();
    for component in path.components() {
        match component {
            Component::RootDir => resolved.push("/"),
            Component::Normal(part) => {
                resolved.push(part);
                if resolved.exists() {
                    resolved = fs::canonicalize(&resolved).map_err(io)?;
                }
            }
            Component::ParentDir => {
                resolved.pop();
            }
            Component::CurDir => {}
            Component::Prefix(_) => {
                return Err(StoreError::Unsupported(
                    "Linux data location required".into(),
                ));
            }
        }
    }
    if resolved.starts_with(root) {
        return Err(StoreError::Unsupported(
            "derived storage must be outside the repository".into(),
        ));
    }
    Ok(resolved)
}
fn private_directory(path: &Path) -> Result<(), StoreError> {
    DirBuilder::new()
        .recursive(true)
        .mode(0o700)
        .create(path)
        .map_err(io)
}

pub struct RepositorySession {
    pub repo_id: RepoId,
    pub store_directory: PathBuf,
    pub store: LadybugStore,
    root: PathBuf,
}

impl RepositorySession {
    pub fn open(root: &Path, location: &DataLocation) -> Result<Self, StoreError> {
        let root = fs::canonicalize(root).map_err(io)?;
        if !root.is_dir() {
            return Err(StoreError::Unsupported(
                "repository root must be a directory".into(),
            ));
        }
        let data = external_directory(&location.0, &root)?;
        private_directory(&data)?;
        let registry = OpenOptions::new()
            .create(true)
            .truncate(false)
            .read(true)
            .write(true)
            .open(data.join("registry.lock"))
            .map_err(io)?;
        registry.lock().map_err(io)?;
        let repositories = data.join("repositories");
        private_directory(&repositories)?;
        let mut found = None;
        for entry in fs::read_dir(&repositories).map_err(io)? {
            let entry = entry.map_err(io)?;
            if !entry.file_type().map_err(io)?.is_dir() {
                return Err(StoreError::Corrupt(
                    "invalid repository registry entry".into(),
                ));
            }
            if entry.file_name().to_string_lossy().ends_with(".staging") {
                fs::remove_dir_all(entry.path()).map_err(io)?;
                continue;
            }
            let metadata: Metadata =
                serde_json::from_slice(&fs::read(entry.path().join("REPO.json")).map_err(io)?)
                    .map_err(|e| StoreError::Corrupt(e.to_string()))?;
            if metadata.version != 1
                || metadata.repo_id.len() != 32
                || !metadata
                    .repo_id
                    .bytes()
                    .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
                || entry.file_name() != std::ffi::OsStr::new(&metadata.repo_id)
            {
                return Err(StoreError::Corrupt(
                    "invalid repository metadata; rebuild required".into(),
                ));
            }
            if metadata.checkout == root {
                if found.is_some() {
                    return Err(StoreError::Corrupt(
                        "duplicate repository discovery entries".into(),
                    ));
                }
                found = Some((
                    RepoId::new(metadata.repo_id)
                        .map_err(|e| StoreError::Corrupt(e.to_string()))?,
                    entry.path(),
                ));
            }
        }
        let (repo_id, store_directory) = if let Some(existing) = found {
            existing
        } else {
            let mut bytes = [0u8; 16];
            let mut assigned = None;
            for _ in 0..16 {
                File::open("/dev/urandom")
                    .map_err(io)?
                    .read_exact(&mut bytes)
                    .map_err(io)?;
                let value: String = bytes.iter().map(|b| format!("{b:02x}")).collect();
                if repositories.join(&value).exists() {
                    continue;
                }
                let directory = repositories.join(format!("{value}.staging"));
                match DirBuilder::new().mode(0o700).create(&directory) {
                    Ok(()) => {
                        assigned = Some((RepoId::new(value).expect("hex identifier"), directory));
                        break;
                    }
                    Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => continue,
                    Err(error) => return Err(io(error)),
                }
            }
            let (id, directory) = assigned.ok_or(StoreError::Conflict)?;
            let metadata = Metadata {
                version: 1,
                repo_id: id.as_str().into(),
                checkout: root.clone(),
            };
            let bytes = serde_json::to_vec(&metadata)
                .map_err(|e| StoreError::Unsupported(e.to_string()))?;
            let temporary = directory.join("REPO.tmp");
            let mut file = OpenOptions::new()
                .create_new(true)
                .write(true)
                .open(&temporary)
                .map_err(io)?;
            file.write_all(&bytes).map_err(io)?;
            file.sync_all().map_err(io)?;
            fs::rename(temporary, directory.join("REPO.json")).map_err(io)?;
            File::open(&directory).map_err(io)?.sync_all().map_err(io)?;
            let sealed = repositories.join(id.as_str());
            fs::rename(&directory, &sealed).map_err(io)?;
            File::open(&repositories)
                .map_err(io)?
                .sync_all()
                .map_err(io)?;
            (id, sealed)
        };
        let store = LadybugStore::new(&store_directory)?;
        drop(registry);
        Ok(Self {
            repo_id,
            store_directory,
            store,
            root,
        })
    }

    pub fn root(&self) -> &Path {
        &self.root
    }

    /// Full rebuild from a fresh capture, published atomically. A capture
    /// that matches the current generation publishes nothing; one matching
    /// an earlier generation of this session is re-pointed, not rebuilt.
    /// Any failure leaves the previous generation current.
    pub fn rebuild(&mut self, scope: &Scope) -> Result<BuildReport, BuildError> {
        let capture = capture::capture(&self.root, scope).map_err(BuildError::Capture)?;
        let (manifest, batch, components) = crate::derivation::derive(&self.repo_id, &capture)?;
        let key = manifest.key.clone();
        let report = |outcome| BuildReport {
            key: key.clone(),
            outcome,
            skipped: capture.skipped.clone(),
            case_collisions: capture.case_collisions.clone(),
        };
        match self.store.current(&self.repo_id) {
            Ok(current) if current == key => return Ok(report(Outcome::Unchanged)),
            Ok(_) | Err(StoreError::MissingSnapshot) => {}
            Err(error) => return Err(error.into()),
        }
        match self.store.begin(manifest) {
            Ok(()) => {}
            Err(StoreError::Conflict) => {
                self.store.activate(&key)?;
                return Ok(report(Outcome::Reactivated));
            }
            Err(error) => return Err(error.into()),
        }
        let staged = self
            .store
            .retain_derivation(&key, &components)
            .and_then(|()| self.store.retain_source(&capture))
            .and_then(|()| self.store.write(&key, batch))
            .and_then(|()| self.store.publish(&key));
        if let Err(error) = staged {
            // The generation was never published; discarding it is cleanup,
            // and startup removes it anyway if this fails too.
            let _ = self.store.discard(&key);
            return Err(error.into());
        }
        Ok(report(Outcome::Published))
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Outcome {
    Published,
    /// The capture matched the current generation.
    Unchanged,
    /// The capture matched an earlier generation still held by this store.
    Reactivated,
}

/// What a rebuild captured and published, with capture diagnostics that are
/// not file evidence: skipped entries and case-fold path collisions.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BuildReport {
    pub key: SnapshotKey,
    pub outcome: Outcome,
    pub skipped: BTreeMap<String, Skip>,
    pub case_collisions: Vec<Vec<RepoPath>>,
}

#[derive(Debug)]
pub enum BuildError {
    Capture(CaptureError),
    Store(StoreError),
}

impl From<StoreError> for BuildError {
    fn from(error: StoreError) -> Self {
        Self::Store(error)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    fn dir(name: &str) -> PathBuf {
        let path =
            std::env::temp_dir().join(format!("oxide-registry-{}-{name}", std::process::id()));
        let _ = fs::remove_dir_all(&path);
        fs::create_dir_all(&path).unwrap();
        path
    }
    #[test]
    fn random_identity_persists_outside_worktree() {
        let base = dir("identity");
        let root = base.join("repo");
        fs::create_dir(&root).unwrap();
        let location = DataLocation(base.join("data"));
        let first = RepositorySession::open(&root, &location).unwrap();
        let id = first.repo_id.clone();
        let store_path = first.store_directory.clone();
        assert_eq!(id.as_str().len(), 32);
        assert!(id.as_str().bytes().all(|b| b.is_ascii_hexdigit()));
        assert!(!store_path.starts_with(&root));
        assert_eq!(fs::read_dir(&root).unwrap().count(), 0);
        assert!(matches!(
            RepositorySession::open(&root, &location),
            Err(StoreError::OwnershipConflict)
        ));
        drop(first);
        let reopened = RepositorySession::open(&root, &location).unwrap();
        assert_eq!(reopened.repo_id, id);
        assert_eq!(reopened.store_directory, store_path);
        drop(reopened);
        let root2 = base.join("repo2");
        fs::create_dir(&root2).unwrap();
        let different = RepositorySession::open(&root2, &location).unwrap();
        assert_ne!(different.repo_id, id);
        drop(different);
        fs::remove_dir_all(base).unwrap();
    }
    #[test]
    fn incomplete_repository_metadata_is_recovered_under_registry_lock() {
        let base = dir("metadata-recovery");
        let root = base.join("source");
        fs::create_dir(&root).unwrap();
        let location = DataLocation(base.join("data"));
        let incomplete = location
            .0
            .join("repositories/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.staging");
        fs::create_dir_all(&incomplete).unwrap();
        fs::write(incomplete.join("REPO.tmp"), "partial").unwrap();
        let session = RepositorySession::open(&root, &location).unwrap();
        assert!(!incomplete.exists());
        drop(session);
        fs::remove_dir_all(base).unwrap();
    }

    #[test]
    fn configured_storage_inside_source_is_refused_without_writes() {
        let root = dir("inside");
        assert!(matches!(
            RepositorySession::open(&root, &DataLocation(root.join("state"))),
            Err(StoreError::Unsupported(_))
        ));
        assert_eq!(fs::read_dir(&root).unwrap().count(), 0);
        fs::remove_dir_all(root).unwrap();
    }
}
