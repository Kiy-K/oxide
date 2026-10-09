//! Captured source: the typed input the runtime hands the kernel (ADR-0010
//! § Source capture). The kernel never reads the filesystem; it sees exactly
//! these bytes, and their snapshot identity, or nothing.

use std::collections::BTreeMap;
use std::sync::Arc;

use crate::id::{Digest, RepoPath, SnapshotId};
use crate::store::StoreError;

/// Bytes of one in-scope regular file as read during one capture.
/// `digest` is the runtime's digest of exactly `bytes`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CapturedFile {
    pub digest: Digest,
    pub bytes: Vec<u8>,
}

/// Why an in-scope entry was not captured. Skips are explicit evidence, not
/// silent omissions, and they count toward snapshot identity.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Skip {
    /// Symlinks are never followed, inside or outside the root.
    Symlink,
    /// FIFO, socket, device: never opened.
    NotRegularFile,
    TooLarge {
        bytes: u64,
        limit: u64,
    },
    /// The OS refused the read; the message is diagnostic only.
    Unreadable(String),
    /// The name is not valid UTF-8 or not a valid [`RepoPath`].
    InvalidPath,
}

/// One consistent capture of a repository worktree.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SourceCapture {
    /// Digest of the captured manifest (paths, file digests, skips, scope).
    pub snapshot: SnapshotId,
    pub files: BTreeMap<RepoPath, CapturedFile>,
    /// Keyed by the entry's repository-relative path text (lossy when the
    /// name is not UTF-8).
    pub skipped: BTreeMap<String, Skip>,
    /// Captured paths equal under case folding. Identity stays byte-exact;
    /// these cannot coexist in a checkout on a case-insensitive filesystem.
    pub case_collisions: Vec<Vec<RepoPath>>,
}

/// Captured bytes by file and digest, for hydration (SPEC § Identity and
/// snapshots). The runtime serves retained bytes; a [`SourceCapture`] serves
/// its own. Callers verify what they get ([`Sources`]), so a provider that
/// returns other bytes (a changed worktree, a stale cache) is caught, never
/// trusted.
pub trait SourceProvider {
    /// The whole captured file, or `None` when bytes for that digest are
    /// not retained.
    fn file(&self, file: &RepoPath, digest: &Digest) -> Result<Option<Vec<u8>>, StoreError>;
}

impl SourceProvider for SourceCapture {
    fn file(&self, file: &RepoPath, digest: &Digest) -> Result<Option<Vec<u8>>, StoreError> {
        Ok(self
            .files
            .get(file)
            .filter(|f| f.digest == *digest)
            .map(|f| f.bytes.clone()))
    }
}

/// Why source could not be hydrated.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum SourceIssue {
    /// No bytes are retained for that digest.
    Unavailable,
    /// Bytes came back but do not hash to the digest (or the digest is not
    /// `sha256:`, which the kernel cannot verify).
    Mismatch,
}

/// One file's verified bytes, or why there are none.
pub type Hydrated = Result<Arc<Vec<u8>>, SourceIssue>;

/// Verified, per-request cache over a [`SourceProvider`]: each file is
/// fetched once and checked against its digest with the kernel's SHA-256.
pub struct Sources<'a> {
    provider: &'a dyn SourceProvider,
    files: BTreeMap<(RepoPath, Digest), Hydrated>,
}

impl<'a> Sources<'a> {
    pub fn new(provider: &'a dyn SourceProvider) -> Self {
        Self {
            provider,
            files: BTreeMap::new(),
        }
    }

    pub fn file(&mut self, file: &RepoPath, digest: &Digest) -> Result<Hydrated, StoreError> {
        let key = (file.clone(), digest.clone());
        if let Some(found) = self.files.get(&key) {
            return Ok(found.clone());
        }
        let found = match self.provider.file(file, digest)? {
            None => Err(SourceIssue::Unavailable),
            Some(bytes) if crate::digest::digest(&bytes) == *digest => Ok(Arc::new(bytes)),
            Some(_) => Err(SourceIssue::Mismatch),
        };
        self.files.insert(key, found.clone());
        Ok(found)
    }
}
