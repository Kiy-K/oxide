//! Source capture (ADR-0010 § Source capture): read the in-scope worktree
//! under a root into the kernel's [`SourceCapture`] input.
//!
//! Capture only reads. It never runs git, hooks, filters or repository code,
//! and it never follows symlinks. What is on disk in scope is the snapshot, so
//! dirty and untracked files are captured like any other. Repository `.gitignore`
//! rules are parsed by `ignore`, never by Git. Files are read twice and
//! verified by bytes and metadata; any change retries the
//! capture (up to [`MAX_ATTEMPTS`]) instead of publishing mixed states.

use ignore::gitignore::{Gitignore, GitignoreBuilder};
use std::collections::BTreeMap;
use std::fmt::Write as _;
use std::fs::{self, File, Metadata};
use std::io::{self, Read};
use std::os::fd::{AsRawFd, FromRawFd};
use std::os::unix::ffi::OsStrExt;
use std::path::{Path, PathBuf};
use std::time::SystemTime;

use oxide_kernel::id::{Digest, RepoPath, SnapshotId};
use oxide_kernel::source::{CapturedFile, Skip, SourceCapture};
use sha2::{Digest as _, Sha256};

/// Version of the snapshot-identity encoding in [`snapshot_id`]. Changing
/// the encoding, or what counts toward it, bumps this.
pub const CAPTURE_VERSION: &str = "oxide-capture-v2-gitignore";

/// VCS internals, skipped at any depth without a trace.
const VCS_DIRS: &[&str] = &[".git", ".hg", ".jj", ".svn", ".oxide"];

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
    /// Ignore rules could not be read or parsed; fail closed for scope.
    Ignore {
        path: PathBuf,
        error: String,
    },
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
    let root_handle = File::open(&root).map_err(CaptureError::Root)?;
    if !root_handle.metadata().map_err(CaptureError::Root)?.is_dir() {
        return Err(CaptureError::Root(io::Error::other(
            "capture root is not a directory",
        )));
    }
    for _ in 0..MAX_ATTEMPTS {
        let Some(pass) = walk(&root, &root_handle, scope)? else {
            continue;
        };
        between();
        let Some(check) = walk(&root, &root_handle, scope)? else {
            continue;
        };
        if pass.stamps == check.stamps
            && pass.files == check.files
            && pass.skipped == check.skipped
            && pass.ignore_rules == check.ignore_rules
        {
            return Ok(finish(pass, scope));
        }
    }
    Err(CaptureError::Conflict {
        attempts: MAX_ATTEMPTS,
    })
}

/// Metadata evidence supplements the two byte-verification passes.
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
    ignore_rules: BTreeMap<PathBuf, Vec<u8>>,
}

/// One complete read pass; a changed file discards the attempt.
fn walk(root: &Path, root_handle: &File, scope: &Scope) -> Result<Option<Pass>, CaptureError> {
    let mut pass = Pass::default();
    let mut dirs = vec![(root.to_path_buf(), String::new(), Vec::<Gitignore>::new())];
    while let Some((dir, rel, mut rules)) = dirs.pop() {
        let handle = if rel.is_empty() {
            root_handle.try_clone()
        } else {
            secure_open(
                root_handle,
                Path::new(&rel),
                libc::O_RDONLY | libc::O_DIRECTORY,
            )
        };
        let handle = match handle {
            Ok(handle) => handle,
            Err(error) if rel.is_empty() => return Err(CaptureError::Root(error)),
            Err(error) => {
                pass.skipped
                    .insert(rel, Skip::Unreadable(error.to_string()));
                continue;
            }
        };
        pass.stamps.insert(
            dir.clone(),
            stamp(&handle.metadata().map_err(CaptureError::Root)?),
        );
        let anchored = PathBuf::from(format!("/proc/self/fd/{}", handle.as_raw_fd()));
        let ignore_path = dir.join(".gitignore");
        match fs::symlink_metadata(anchored.join(".gitignore")) {
            Ok(meta) if meta.is_file() => {
                if meta.len() > scope.max_file_bytes {
                    return Err(CaptureError::Ignore {
                        path: ignore_path,
                        error: "ignore file exceeds capture size limit".into(),
                    });
                }
                let ignore_rel = if rel.is_empty() {
                    ".gitignore".to_string()
                } else {
                    format!("{rel}/.gitignore")
                };
                let bytes = match read_file(root_handle, Path::new(&ignore_rel), &meta) {
                    Ok(Some(bytes)) => bytes,
                    Ok(None) => return Ok(None),
                    Err(error) => {
                        return Err(CaptureError::Ignore {
                            path: ignore_path,
                            error: error.to_string(),
                        });
                    }
                };
                let text = std::str::from_utf8(&bytes).map_err(|error| CaptureError::Ignore {
                    path: ignore_path.clone(),
                    error: error.to_string(),
                })?;
                let mut builder = GitignoreBuilder::new(&dir);
                for line in text.lines() {
                    builder
                        .add_line(Some(ignore_path.clone()), line)
                        .map_err(|error| CaptureError::Ignore {
                            path: ignore_path.clone(),
                            error: error.to_string(),
                        })?;
                }
                rules.push(builder.build().map_err(|error| CaptureError::Ignore {
                    path: ignore_path.clone(),
                    error: error.to_string(),
                })?);
                pass.ignore_rules.insert(PathBuf::from(ignore_rel), bytes);
            }
            Ok(_) => {} // Symlink/special ignore files are explicit skips below.
            Err(error) if error.kind() == io::ErrorKind::NotFound => {}
            Err(error) => {
                return Err(CaptureError::Ignore {
                    path: ignore_path,
                    error: error.to_string(),
                });
            }
        }
        let listed =
            fs::read_dir(&anchored).and_then(|entries| entries.collect::<io::Result<Vec<_>>>());
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
            let absolute = dir.join(&name);
            let matching = rules
                .iter()
                .rev()
                .map(|rule| rule.matched(&absolute, meta.is_dir()))
                .find(|m| !m.is_none());
            if matching.is_some_and(|m| m.is_ignore()) {
                continue;
            }
            pass.stamps.insert(absolute, stamp(&meta));
            let Some(path) = path else {
                pass.skipped.insert(rel_text, Skip::InvalidPath);
                continue;
            };
            let ft = meta.file_type();
            if ft.is_symlink() {
                pass.skipped.insert(rel_text, Skip::Symlink);
            } else if ft.is_dir() {
                dirs.push((dir.join(name), rel_text, rules.clone()));
            } else if !ft.is_file() {
                pass.skipped.insert(rel_text, Skip::NotRegularFile);
            } else if meta.len() > scope.max_file_bytes {
                let skip = Skip::TooLarge {
                    bytes: meta.len(),
                    limit: scope.max_file_bytes,
                };
                pass.skipped.insert(rel_text, skip);
            } else {
                match read_file(root_handle, Path::new(&rel_text), &meta) {
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

/// Linux openat2 anchors every component to the captured root and refuses
/// symlinks; O_NONBLOCK prevents a file swapped for a FIFO from blocking.
fn secure_open(root: &File, path: &Path, flags: i32) -> io::Result<File> {
    let path = std::ffi::CString::new(path.as_os_str().as_bytes())?;
    let mut how: libc::open_how = unsafe { std::mem::zeroed() };
    how.flags = (flags | libc::O_CLOEXEC | libc::O_NOFOLLOW | libc::O_NONBLOCK) as u64;
    how.resolve = libc::RESOLVE_BENEATH | libc::RESOLVE_NO_SYMLINKS;
    // SAFETY: root is live, path is NUL-terminated, how has the kernel ABI
    // layout/size, and a successful descriptor is transferred exactly once.
    let fd = unsafe {
        libc::syscall(
            libc::SYS_openat2,
            root.as_raw_fd(),
            path.as_ptr(),
            &how,
            std::mem::size_of::<libc::open_how>(),
        )
    };
    if fd < 0 {
        return Err(io::Error::last_os_error());
    }
    Ok(unsafe { File::from_raw_fd(fd as i32) })
}

fn read_file(root: &File, path: &Path, before: &Metadata) -> io::Result<Option<Vec<u8>>> {
    let mut file = secure_open(root, path, libc::O_RDONLY)?;
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
        snapshot: snapshot_id(scope, &pass.files, &pass.skipped, &pass.ignore_rules),
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
    ignore_rules: &BTreeMap<PathBuf, Vec<u8>>,
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
    field(&mut hasher, &ignore_rules.len().to_string());
    for (path, bytes) in ignore_rules {
        field(&mut hasher, path.to_str().expect("UTF-8 repository path"));
        field(&mut hasher, digest(bytes).as_str());
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
    fn ignored_rule_file_still_changes_scope_identity() {
        let root = tempdir("ignored-rules");
        write(&root, ".gitignore", ".gitignore\na.py\n");
        write(&root, "b.py", "kept");
        let before = capture(&root, &Scope::default()).unwrap();
        write(&root, ".gitignore", ".gitignore\na.py\nc.py\n");
        let after = capture(&root, &Scope::default()).unwrap();
        assert_eq!(before.files, after.files);
        assert_ne!(before.snapshot, after.snapshot);
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn undecodable_ignore_fails_closed() {
        let root = tempdir("invalid-rules");
        fs::write(root.join(".gitignore"), [0xff]).unwrap();
        assert!(matches!(
            capture(&root, &Scope::default()),
            Err(CaptureError::Ignore { .. })
        ));
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn anchored_open_refuses_symlinks_in_every_component() {
        use std::os::unix::fs::symlink;
        let root = tempdir("anchor");
        let outside = tempdir("anchor-outside");
        write(&outside, "secret", "outside");
        symlink(&outside, root.join("link")).unwrap();
        let handle = File::open(&root).unwrap();
        assert!(secure_open(&handle, Path::new("link/secret"), libc::O_RDONLY).is_err());
        assert!(
            secure_open(
                &handle,
                Path::new("../anchor-outside/secret"),
                libc::O_RDONLY
            )
            .is_err()
        );
        fs::remove_dir_all(root).unwrap();
        fs::remove_dir_all(outside).unwrap();
    }

    #[test]
    fn gitignore_scope_and_nested_overrides() {
        let root = tempdir("gitignore");
        write(&root, ".gitignore", "*.py\n!keep.py\nignored/\n");
        write(&root, "hide.py", "hidden");
        write(&root, "keep.py", "dirty");
        write(&root, "nested/.gitignore", "!show.py\n");
        write(&root, "nested/show.py", "untracked");
        write(&root, "nested/hide.py", "hidden");
        write(&root, "ignored/.gitignore", "!show.py\n");
        write(&root, "ignored/show.py", "hidden parent");
        write(&root, ".oxide/internal.py", "derived");
        let captured = capture(&root, &Scope::default()).unwrap();
        assert_eq!(
            paths(&captured),
            [
                ".gitignore",
                "keep.py",
                "nested/.gitignore",
                "nested/show.py"
            ]
        );
        assert!(captured.skipped.is_empty());
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn ignore_change_during_capture_retries() {
        let root = tempdir("ignore-race");
        write(&root, ".gitignore", "a.py\n");
        write(&root, "a.py", "new");
        let mut n = 0;
        let captured = capture_with(&root, &Scope::default(), &mut || {
            if n == 0 {
                write(&root, ".gitignore", "b.py\n");
            }
            n += 1;
        })
        .unwrap();
        assert_eq!(n, 2);
        assert!(captured.files.contains_key(&repo_path("a.py")));
        fs::remove_dir_all(root).unwrap();
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
