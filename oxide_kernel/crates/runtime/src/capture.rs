//! Source capture (ADR-0010 § Source capture): read the in-scope worktree
//! under a root into the kernel's [`SourceCapture`] input.
//!
//! Capture only reads. It never runs git, hooks, filters or repository code,
//! and it never follows symlinks. What is on disk in scope is the snapshot, so
//! dirty and untracked files are captured like any other; `.gitignore` is not
//! applied (open question, ADR-0010). A capture is consistent: every file is
//! read once, then the whole tree is re-stat'ed, and any change retries the
//! capture (up to [`MAX_ATTEMPTS`]) instead of publishing mixed states.

use std::collections::BTreeMap;
use std::fmt::Write as _;
use std::fs::{self, File, Metadata};
use std::io::{self, Read};
use std::path::{Path, PathBuf};
use std::time::SystemTime;

use oxide_kernel::id::{Digest, RepoPath, SnapshotId};
use oxide_kernel::source::{CapturedFile, Skip, SourceCapture};
use sha2::{Digest as _, Sha256};

/// Version of the snapshot-identity encoding in [`snapshot_id`]. Changing
/// the encoding, or what counts toward it, bumps this.
pub const CAPTURE_VERSION: &str = "oxide-capture-v1";

/// VCS internals, skipped at any depth without a trace.
const VCS_DIRS: &[&str] = &[".git", ".hg", ".jj", ".svn"];

pub const MAX_ATTEMPTS: u32 = 3;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Scope {
    /// Excluded subtrees: each path excludes itself and everything below.
    pub exclude: Vec<RepoPath>,
    /// Larger files are recorded as [`Skip::TooLarge`], not read.
    pub max_file_bytes: u64,
    /// Capturing more than this in total fails the capture.
    pub max_total_bytes: u64,
}

impl Default for Scope {
    fn default() -> Self {
        Self {
            exclude: Vec::new(),
            max_file_bytes: 1 << 20,
            max_total_bytes: 1 << 30,
        }
    }
}

#[derive(Debug)]
pub enum CaptureError {
    /// The root is missing, not a directory, or cannot be listed.
    Root(io::Error),
    /// The worktree kept changing: no consistent capture after `attempts`.
    Conflict {
        attempts: u32,
    },
    TotalTooLarge {
        limit: u64,
    },
}

/// Captures the worktree under `root` within `scope`.
pub fn capture(root: &Path, scope: &Scope) -> Result<SourceCapture, CaptureError> {
    capture_with(root, scope, &mut || {})
}

/// `between` runs after the read pass and before the verification pass; tests
/// use it to change the worktree mid-capture.
fn capture_with(
    root: &Path,
    scope: &Scope,
    between: &mut dyn FnMut(),
) -> Result<SourceCapture, CaptureError> {
    let root = fs::canonicalize(root).map_err(CaptureError::Root)?;
    for _ in 0..MAX_ATTEMPTS {
        let Some(pass) = walk(&root, scope, true)? else {
            continue;
        };
        between();
        let check = walk(&root, scope, false)?.expect("stat-only walks do not abort");
        if pass.stamps == check.stamps {
            return Ok(finish(pass, scope));
        }
    }
    Err(CaptureError::Conflict {
        attempts: MAX_ATTEMPTS,
    })
}

/// What identifies an entry's on-disk state between the read and the check.
/// ponytail: stat-based, so a same-size rewrite within the filesystem's mtime
/// granularity, or a swap that is undone before the check, goes unseen; walk
/// with openat (cap-std) and re-hash if hostile concurrent writers matter.
#[derive(Debug, PartialEq, Eq)]
struct Stamp {
    kind: &'static str,
    len: u64,
    modified: Option<SystemTime>,
    inode: (u64, u64),
}

fn stamp(meta: &Metadata) -> Stamp {
    let ft = meta.file_type();
    let kind = if ft.is_symlink() {
        "symlink"
    } else if ft.is_dir() {
        "dir"
    } else if ft.is_file() {
        "file"
    } else {
        "other"
    };
    #[cfg(unix)]
    let inode = {
        use std::os::unix::fs::MetadataExt;
        (meta.dev(), meta.ino())
    };
    #[cfg(not(unix))]
    let inode = (0, 0);
    Stamp {
        kind,
        len: meta.len(),
        modified: meta.modified().ok(),
        inode,
    }
}

#[derive(Default)]
struct Pass {
    files: BTreeMap<RepoPath, CapturedFile>,
    skipped: BTreeMap<String, Skip>,
    stamps: BTreeMap<PathBuf, Stamp>,
    total: u64,
}

/// One walk of the tree. With `read`, files are read and digested; `None`
/// means a file changed while it was being read.
fn walk(root: &Path, scope: &Scope, read: bool) -> Result<Option<Pass>, CaptureError> {
    let mut pass = Pass::default();
    let mut dirs = vec![(root.to_path_buf(), String::new())];
    while let Some((dir, rel)) = dirs.pop() {
        let listed = fs::read_dir(&dir).and_then(|entries| entries.collect::<io::Result<Vec<_>>>());
        let entries = match listed {
            Ok(entries) => entries,
            Err(error) if rel.is_empty() => return Err(CaptureError::Root(error)),
            Err(error) => {
                pass.skipped
                    .insert(rel, Skip::Unreadable(error.to_string()));
                continue;
            }
        };
        for entry in entries {
            let name = entry.file_name();
            let text = name.to_string_lossy();
            if VCS_DIRS.contains(&&*text) {
                continue;
            }
            let rel_text = if rel.is_empty() {
                text.into_owned()
            } else {
                format!("{rel}/{text}")
            };
            // A lossy name is never turned into a RepoPath, and an invalid
            // directory is skipped whole rather than lending lossy names to
            // its children.
            let path = name
                .to_str()
                .and_then(|_| RepoPath::new(rel_text.as_str()).ok());
            if path.as_ref().is_some_and(|path| excluded(path, scope)) {
                continue;
            }
            let meta = match entry.metadata() {
                Ok(meta) => meta,
                Err(error) => {
                    pass.skipped
                        .insert(rel_text, Skip::Unreadable(error.to_string()));
                    continue;
                }
            };
            pass.stamps.insert(entry.path(), stamp(&meta));
            let Some(path) = path else {
                pass.skipped.insert(rel_text, Skip::InvalidPath);
                continue;
            };
            let ft = meta.file_type();
            if ft.is_symlink() {
                pass.skipped.insert(rel_text, Skip::Symlink);
            } else if ft.is_dir() {
                dirs.push((entry.path(), rel_text));
            } else if !ft.is_file() {
                pass.skipped.insert(rel_text, Skip::NotRegularFile);
            } else if meta.len() > scope.max_file_bytes {
                let skip = Skip::TooLarge {
                    bytes: meta.len(),
                    limit: scope.max_file_bytes,
                };
                pass.skipped.insert(rel_text, skip);
            } else if read {
                match read_file(&entry.path(), &meta) {
                    Ok(Some(bytes)) => {
                        pass.total += bytes.len() as u64;
                        if pass.total > scope.max_total_bytes {
                            return Err(CaptureError::TotalTooLarge {
                                limit: scope.max_total_bytes,
                            });
                        }
                        let digest = digest(&bytes);
                        pass.files.insert(path, CapturedFile { digest, bytes });
                    }
                    Ok(None) => return Ok(None),
                    Err(error) => {
                        pass.skipped
                            .insert(rel_text, Skip::Unreadable(error.to_string()));
                    }
                }
            }
        }
    }
    Ok(Some(pass))
}

fn excluded(path: &RepoPath, scope: &Scope) -> bool {
    scope.exclude.iter().any(|ex| {
        path.as_str()
            .strip_prefix(ex.as_str())
            .is_some_and(|rest| rest.is_empty() || rest.starts_with('/'))
    })
}

/// Reads a regular file whose lstat was `before`. `Ok(None)` when the opened
/// file is not that entry (swapped, e.g. for a symlink) or changed during the
/// read. ponytail: a regular file swapped for a FIFO between lstat and open
/// blocks the open; use O_NONBLOCK/openat if that threat is in scope.
fn read_file(path: &Path, before: &Metadata) -> io::Result<Option<Vec<u8>>> {
    let mut file = File::open(path)?;
    if stamp(&file.metadata()?) != stamp(before) {
        return Ok(None);
    }
    let mut bytes = Vec::with_capacity(before.len() as usize);
    (&mut file).take(before.len() + 1).read_to_end(&mut bytes)?;
    let unchanged = bytes.len() as u64 == before.len() && stamp(&file.metadata()?) == stamp(before);
    Ok(unchanged.then_some(bytes))
}

fn finish(pass: Pass, scope: &Scope) -> SourceCapture {
    let mut folded: BTreeMap<String, Vec<RepoPath>> = BTreeMap::new();
    for path in pass.files.keys() {
        // ponytail: Unicode lowercase approximates case folding; NFC/NFD
        // equivalents are not detected (needs normalization tables).
        folded
            .entry(path.as_str().to_lowercase())
            .or_default()
            .push(path.clone());
    }
    SourceCapture {
        snapshot: snapshot_id(scope, &pass.files, &pass.skipped),
        case_collisions: folded
            .into_values()
            .filter(|group| group.len() > 1)
            .collect(),
        files: pass.files,
        skipped: pass.skipped,
    }
}

/// `sha256:` over a length-prefixed (so injective) encoding of the capture
/// version, the scope that affects content, every captured path and digest,
/// and every skipped path and skip kind. Diagnostic text (OS error messages,
/// sizes of skipped files) and the git commit are not identity.
fn snapshot_id(
    scope: &Scope,
    files: &BTreeMap<RepoPath, CapturedFile>,
    skipped: &BTreeMap<String, Skip>,
) -> SnapshotId {
    fn field(hasher: &mut Sha256, value: &str) {
        hasher.update((value.len() as u64).to_le_bytes());
        hasher.update(value.as_bytes());
    }
    let mut hasher = Sha256::new();
    field(&mut hasher, CAPTURE_VERSION);
    field(&mut hasher, &scope.max_file_bytes.to_string());
    let mut exclude: Vec<&str> = scope.exclude.iter().map(RepoPath::as_str).collect();
    exclude.sort_unstable();
    exclude.dedup();
    field(&mut hasher, &exclude.len().to_string());
    for path in exclude {
        field(&mut hasher, path);
    }
    field(&mut hasher, &files.len().to_string());
    for (path, file) in files {
        field(&mut hasher, path.as_str());
        field(&mut hasher, file.digest.as_str());
    }
    field(&mut hasher, &skipped.len().to_string());
    for (path, skip) in skipped {
        field(&mut hasher, path);
        field(
            &mut hasher,
            match skip {
                Skip::Symlink => "symlink",
                Skip::NotRegularFile => "not_regular_file",
                Skip::TooLarge { .. } => "too_large",
                Skip::Unreadable(_) => "unreadable",
                Skip::InvalidPath => "invalid_path",
            },
        );
    }
    SnapshotId::new(sha256_text(hasher.finalize().as_slice())).expect("hex is a valid id")
}

/// The source digest: `sha256:` plus the lowercase hex of the bytes' SHA-256.
pub fn digest(bytes: &[u8]) -> Digest {
    Digest::new(sha256_text(Sha256::digest(bytes).as_slice())).expect("hex is a valid id")
}

fn sha256_text(hash: &[u8]) -> String {
    let mut text = String::from("sha256:");
    for byte in hash {
        write!(text, "{byte:02x}").expect("writing to a String");
    }
    text
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tempdir(name: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("oxide-capture-{}-{name}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        dir
    }

    fn write(root: &Path, rel: &str, bytes: &str) {
        let path = root.join(rel);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, bytes).unwrap();
    }

    fn paths(capture: &SourceCapture) -> Vec<&str> {
        capture.files.keys().map(RepoPath::as_str).collect()
    }

    fn repo_path(path: &str) -> RepoPath {
        RepoPath::new(path).unwrap()
    }

    #[test]
    fn captures_scoped_files_with_digests() {
        let root = tempdir("scope");
        write(&root, "src/lib.rs", "fn a() {}\n");
        write(&root, "untracked.txt", "new");
        write(&root, ".git/config", "[core]\n\tfsmonitor = evil\n");
        write(&root, "sub/.git", "gitdir: ../.git/modules/sub");
        write(&root, "target/debug/out", "build output");
        write(
            &root,
            "target-notes.md",
            "kept: only the subtree is excluded",
        );
        write(&root, "big.bin", &"x".repeat(64));
        let scope = Scope {
            exclude: vec![repo_path("target")],
            max_file_bytes: 40,
            ..Scope::default()
        };
        let capture = capture(&root, &scope).unwrap();
        assert_eq!(
            paths(&capture),
            ["src/lib.rs", "target-notes.md", "untracked.txt"]
        );
        let file = &capture.files[&repo_path("src/lib.rs")];
        assert_eq!(file.bytes, b"fn a() {}\n");
        assert_eq!(file.digest, digest(b"fn a() {}\n"));
        assert_eq!(
            digest(b"").as_str(),
            "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        );
        assert_eq!(
            capture.skipped,
            BTreeMap::from([(
                "big.bin".to_string(),
                Skip::TooLarge {
                    bytes: 64,
                    limit: 40
                }
            )])
        );
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn snapshot_identity_follows_content_and_scope_only() {
        let root = tempdir("identity");
        write(&root, "a.rs", "one");
        write(&root, "b.rs", "two");
        let scope = Scope::default();
        let first = capture(&root, &scope).unwrap().snapshot;
        assert_eq!(capture(&root, &scope).unwrap().snapshot, first);

        // Rewriting identical bytes (new mtime) is the same snapshot.
        std::thread::sleep(std::time::Duration::from_millis(10));
        write(&root, "a.rs", "one");
        assert_eq!(capture(&root, &scope).unwrap().snapshot, first);

        write(&root, "a.rs", "One");
        assert_ne!(capture(&root, &scope).unwrap().snapshot, first);
        write(&root, "a.rs", "one");
        assert_eq!(capture(&root, &scope).unwrap().snapshot, first);

        let narrower = Scope {
            exclude: vec![repo_path("b.rs")],
            ..Scope::default()
        };
        assert_ne!(capture(&root, &narrower).unwrap().snapshot, first);
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn changes_during_capture_retry_then_conflict() {
        let root = tempdir("race");
        write(&root, "a.rs", "v0");
        let mut edits = 0;
        let settled = capture_with(&root, &Scope::default(), &mut || {
            if edits == 0 {
                write(&root, "a.rs", "v1-longer");
            }
            edits += 1;
        })
        .unwrap();
        assert_eq!(edits, 2, "first capture discarded, second kept");
        assert_eq!(settled.files[&repo_path("a.rs")].bytes, b"v1-longer");

        let mut n = 0;
        let result = capture_with(&root, &Scope::default(), &mut || {
            n += 1;
            write(&root, &format!("new{n}.rs"), "appeared");
        });
        assert!(matches!(
            result,
            Err(CaptureError::Conflict {
                attempts: MAX_ATTEMPTS
            })
        ));
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn missing_root_is_an_error() {
        let root = tempdir("missing").join("nope");
        assert!(matches!(
            capture(&root, &Scope::default()),
            Err(CaptureError::Root(_))
        ));
    }

    #[test]
    fn total_limit_fails_the_capture() {
        let root = tempdir("total");
        write(&root, "a.rs", "12345");
        write(&root, "b.rs", "12345");
        let scope = Scope {
            max_total_bytes: 8,
            ..Scope::default()
        };
        assert!(matches!(
            capture(&root, &scope),
            Err(CaptureError::TotalTooLarge { limit: 8 })
        ));
        let _ = fs::remove_dir_all(root);
    }

    #[cfg(unix)]
    #[test]
    fn unix_special_entries_are_explicit_skips() {
        use std::ffi::OsStr;
        use std::os::unix::ffi::OsStrExt;
        use std::os::unix::fs::{PermissionsExt, symlink};

        let root = tempdir("special");
        let outside = tempdir("special-outside");
        write(&outside, "secret.txt", "outside the root");
        write(&root, "ok.rs", "ok");
        symlink(outside.join("secret.txt"), root.join("link.txt")).unwrap();
        symlink(&outside, root.join("linkdir")).unwrap();
        let bad = OsStr::from_bytes(b"bad\xff");
        fs::create_dir(root.join(bad)).unwrap();
        fs::write(root.join(bad).join("inner.rs"), "x").unwrap();
        write(&root, "locked.rs", "x");
        fs::set_permissions(root.join("locked.rs"), fs::Permissions::from_mode(0o000)).unwrap();
        let fifo = std::process::Command::new("mkfifo")
            .arg(root.join("pipe"))
            .status()
            .is_ok_and(|s| s.success());

        let capture = capture(&root, &Scope::default()).unwrap();
        assert_eq!(paths(&capture), ["ok.rs"]);
        let skipped = &capture.skipped;
        assert_eq!(skipped["link.txt"], Skip::Symlink);
        assert_eq!(skipped["linkdir"], Skip::Symlink);
        assert_eq!(skipped["bad\u{fffd}"], Skip::InvalidPath);
        assert!(!skipped.keys().any(|k| k.contains("inner")));
        if fifo {
            assert_eq!(skipped["pipe"], Skip::NotRegularFile);
        }
        // Root can read mode-000 files; only check where permissions apply.
        if fs::read(root.join("locked.rs")).is_err() {
            assert!(matches!(skipped["locked.rs"], Skip::Unreadable(_)));
        }
        fs::set_permissions(root.join("locked.rs"), fs::Permissions::from_mode(0o644)).unwrap();
        let _ = fs::remove_dir_all(root);
        let _ = fs::remove_dir_all(outside);
    }

    #[test]
    fn case_variants_keep_distinct_ids_and_are_reported() {
        let root = tempdir("case");
        write(&root, "A.rs", "upper");
        if fs::read(root.join("a.rs")).is_ok() {
            return; // case-insensitive filesystem: the variants cannot coexist
        }
        write(&root, "a.rs", "lower");
        write(&root, "b.rs", "other");
        let capture = capture(&root, &Scope::default()).unwrap();
        assert_eq!(paths(&capture), ["A.rs", "a.rs", "b.rs"]);
        assert_eq!(
            capture.case_collisions,
            [vec![repo_path("A.rs"), repo_path("a.rs")]]
        );
        let _ = fs::remove_dir_all(root);
    }
}
