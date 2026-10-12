//! Repository sessions owned by one runtime process (ADR-0003 § Resource
//! ownership plan). An open session holds its repository's store (and so
//! the store's exclusive lock) and pins the generation that was current
//! when it opened. Sessions end when closed or when the runtime exits.

use std::fs::{self, File};
use std::io::Read;
use std::path::{Path, PathBuf};

use oxide_kernel::id::SnapshotKey;
use oxide_kernel::store::{KnowledgeStore, ReadView, StoreError};
use serde::Serialize;

use crate::ErrorCode;
use crate::derivation::{components, derivation_id};
use crate::repository::{DataLocation, lookup};
use crate::storage::{LadybugStore, LadybugView};

/// An operation failure, answered as a contract error.
pub struct Failure(pub ErrorCode, pub String);

struct Session {
    handle: String,
    root: PathBuf,
    // Field order is drop order: the pinned view before the store that
    // owns the lock.
    view: LadybugView,
    _store: LadybugStore,
}

/// The wire result of `open_repository`.
#[derive(Serialize)]
pub struct Opened {
    handle: String,
    repo_id: String,
    root: String,
    snapshot: String,
    derivation: String,
}

impl Session {
    fn describe(&self) -> Opened {
        let key = &self.view.snapshot().key;
        Opened {
            handle: self.handle.clone(),
            repo_id: key.repo.as_str().into(),
            root: self.root.to_string_lossy().into_owned(),
            snapshot: key.snapshot.as_str().into(),
            derivation: key.derivation.as_str().into(),
        }
    }
}

pub struct Sessions {
    location: Option<DataLocation>,
    open: Vec<Session>,
}

impl Sessions {
    /// `location` is the application data root; without one, opening fails.
    pub fn new(location: Option<DataLocation>) -> Self {
        Self {
            location,
            open: Vec::new(),
        }
    }

    /// Opens an already indexed repository read-only: never registers,
    /// captures or rebuilds. Opening a root this runtime already has open
    /// returns that session unchanged (same handle, same pinned snapshot).
    pub fn open(&mut self, path: &str) -> Result<Opened, Failure> {
        let invalid = |why: String| Failure(ErrorCode::InvalidPath, why);
        let path = Path::new(path);
        if !path.is_absolute() {
            return Err(invalid("repository path must be absolute".into()));
        }
        let root = fs::canonicalize(path)
            .map_err(|e| invalid(format!("cannot resolve repository path: {e}")))?;
        if !root.is_dir() || root.to_str().is_none() {
            return Err(invalid(
                "repository path is not a UTF-8 directory path".into(),
            ));
        }
        if let Some(session) = self.open.iter().find(|s| s.root == root) {
            return Ok(session.describe());
        }
        let location = self.location.as_ref().ok_or_else(|| {
            Failure(
                ErrorCode::InternalError,
                "no application data location (XDG_DATA_HOME or HOME)".into(),
            )
        })?;
        let (repo, directory) = match lookup(&root, location) {
            Ok(Some(found)) => found,
            Ok(None) => {
                return Err(Failure(
                    ErrorCode::NotIndexed,
                    "repository is not registered; index it first".into(),
                ));
            }
            // The data location lies inside the requested path.
            Err(StoreError::Unsupported(why)) => return Err(invalid(why)),
            Err(error) => return Err(store_failure(error)),
        };
        let store = LadybugStore::new(&directory).map_err(store_failure)?;
        let current = store.current(&repo).map_err(store_failure)?;
        // Pinned under this runtime's own derivation: a generation derived
        // by other parser/schema/projection versions is reported, not read.
        let key = SnapshotKey {
            derivation: derivation_id(&components()),
            ..current
        };
        let view = store.open(&key).map_err(store_failure)?;
        let handle = random_handle()
            .map_err(|e| Failure(ErrorCode::InternalError, format!("no handle: {e}")))?;
        let session = Session {
            handle,
            root,
            view,
            _store: store,
        };
        let opened = session.describe();
        self.open.push(session);
        Ok(opened)
    }

    /// Releases the session's view, store and lock. Unknown handles,
    /// including already closed ones and other runtimes', are rejected.
    pub fn close(&mut self, handle: &str) -> Result<(), Failure> {
        let at = self
            .open
            .iter()
            .position(|s| s.handle == handle)
            .ok_or_else(|| Failure(ErrorCode::UnknownHandle, "unknown repository handle".into()))?;
        self.open.remove(at);
        Ok(())
    }
}

fn store_failure(error: StoreError) -> Failure {
    let (code, message) = match error {
        StoreError::OwnershipConflict => (
            ErrorCode::OwnershipConflict,
            "another runtime owns this repository's store".into(),
        ),
        StoreError::MissingSnapshot => (
            ErrorCode::NotIndexed,
            "repository has no published generation; index or rebuild it".into(),
        ),
        StoreError::IncompatibleDerivation { available } => (
            ErrorCode::IncompatibleDerivation,
            format!(
                "published only under other derivations ({}); rebuild required",
                available
                    .iter()
                    .map(|d| d.as_str())
                    .collect::<Vec<_>>()
                    .join(", ")
            ),
        ),
        // A newer storage format or adapter encoding than this runtime's.
        StoreError::Unsupported(why) => (ErrorCode::IncompatibleDerivation, why),
        StoreError::Corrupt(why) => (ErrorCode::RepositoryUnavailable, why),
        other => (ErrorCode::InternalError, other.to_string()),
    };
    Failure(code, message)
}

/// 128 random bits: handles are unguessable and never repeat across runtimes.
fn random_handle() -> std::io::Result<String> {
    let mut bytes = [0u8; 16];
    File::open("/dev/urandom")?.read_exact(&mut bytes)?;
    Ok(bytes.iter().map(|b| format!("{b:02x}")).collect())
}
