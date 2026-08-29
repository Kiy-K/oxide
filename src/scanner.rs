//! Repository file discovery: respects .gitignore, skips VCS/build/cache/vendor
//! dirs, binaries, lockfiles and generated artifacts.

use anyhow::Result;
use ignore::{WalkBuilder, WalkState};
use std::path::{Path, PathBuf};
use std::sync::mpsc;

/// Extensions OXIDE indexes in v0.1.
pub fn language_for_path(path: &Path) -> Option<crate::symbols::Language> {
    use crate::symbols::Language::*;
    let name = path.file_name()?.to_str()?;
    let ext = path.extension()?.to_str()?;
    match (name, ext) {
        (_, "py") | (_, "pyi") => Some(Python),
        (_, "ts") if !name.ends_with(".d.ts") => Some(TypeScript),
        ("", "tsx") | (_, "tsx") => Some(Tsx),
        _ => None,
    }
}

/// Directories never worth indexing even when not gitignored.
const DENYLIST_DIRS: &[&str] = &[
    ".git",
    ".hg",
    ".svn",
    "node_modules",
    "target",
    "dist",
    "build",
    "out",
    ".next",
    ".nuxt",
    ".turbo",
    "__pycache__",
    ".venv",
    "venv",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "coverage",
    ".nyc_output",
    "vendor",
    ".idea",
    ".vscode",
];

/// Exact filenames that are generated or non-source.
const DENYLIST_FILES: &[&str] = &[
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "poetry.lock",
    "Pipfile.lock",
    "Cargo.lock",
    "uv.lock",
    "tsconfig.tsbuildinfo",
];

/// Filename suffixes marking minified/generated output.
const DENYLIST_SUFFIXES: &[&str] = &[
    ".min.js",
    ".min.css",
    ".min.mjs",
    ".d.ts",
    ".generated.",
    "-gen.py",
];

fn is_denied(path: &Path, is_dir: bool) -> bool {
    let Some(name) = path.file_name().and_then(|n| n.to_str()) else {
        return true;
    };
    if is_dir {
        return DENYLIST_DIRS.contains(&name);
    }
    if name.starts_with('.') && !name.starts_with(".env") {
        return true;
    }
    if DENYLIST_FILES.contains(&name) {
        return true;
    }
    if DENYLIST_SUFFIXES.iter().any(|s| name.contains(s)) {
        return true;
    }
    false
}

fn looks_binary(bytes: &[u8]) -> bool {
    bytes.contains(&0)
}

/// Discover indexable source files under `root` as repo-relative slash paths.
/// Respects `.gitignore`/`.ignore` via the `ignore` crate; applies the built-in
/// denylist on top. Returns sorted, deduplicated paths.
pub fn scan_repo(root: &Path) -> Result<Vec<PathBuf>> {
    let root = root.canonicalize()?;
    let (tx, rx) = mpsc::channel();
    let walker_root = root.clone();
    let tx_builder = tx.clone();
    WalkBuilder::new(&root)
        .hidden(true)
        .git_ignore(true)
        .git_global(false)
        .git_exclude(true)
        .parents(true)
        .build_parallel()
        .run(move || {
            let tx = tx_builder.clone();
            let root = walker_root.clone();
            Box::new(move |entry| {
                let Ok(entry) = entry else {
                    return WalkState::Continue;
                };
                let path = entry.path();
                let is_dir = entry.file_type().map(|f| f.is_dir()).unwrap_or(false);
                if path == root.as_path() {
                    return WalkState::Continue;
                }
                // The walker descends into gitignored dirs only if configured to
                // not skip; with git_ignore enabled they are pruned already.
                if is_dir {
                    return WalkState::Continue;
                }
                if is_denied(path, false) {
                    return WalkState::Continue;
                }
                if language_for_path(path).is_none() {
                    return WalkState::Continue;
                }
                let Ok(meta) = std::fs::metadata(path) else {
                    return WalkState::Continue;
                };
                if meta.len() > 1_500_000 {
                    return WalkState::Continue;
                }
                let mut buf = [0u8; 1024];
                if let Ok(mut f) = std::fs::File::open(path) {
                    use std::io::Read;
                    if let Ok(n) = f.read(&mut buf) {
                        if looks_binary(&buf[..n]) {
                            return WalkState::Continue;
                        }
                    }
                }
                if tx.send(path.to_path_buf()).is_err() {
                    return WalkState::Quit;
                }
                WalkState::Continue
            })
        });
    drop(tx);
    let mut files: Vec<PathBuf> = rx.into_iter().collect();
    for p in &mut files {
        *p = p.strip_prefix(&root).unwrap_or(p).to_path_buf();
    }
    files.sort();
    files.dedup();
    Ok(files)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn write(path: &Path, content: &str) {
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(path, content).unwrap();
    }

    #[test]
    fn finds_sources_and_respects_gitignore_and_denylist() {
        let tmp = tempfile::tempdir().unwrap();
        let root = tmp.path();
        write(&root.join("src/app.py"), "x = 1\n");
        write(&root.join("src/util.py"), "y = 2\n");
        write(&root.join("lib/main.ts"), "export const a = 1;\n");
        write(&root.join("lib/comp.tsx"), "export const B = () => null;\n");
        write(&root.join("ignored/gen.py"), "z = 3\n");
        write(&root.join("node_modules/pkg/index.js"), "q\n");
        write(&root.join("__pycache__/app.cpython-311.pyc"), "\x00\x01");
        write(&root.join("public/app.min.js"), "var a=1;");
        write(&root.join("README.md"), "# hi\n");
        write(&root.join("package-lock.json"), "{}");
        write(&root.join(".gitignore"), "/ignored/\n*.log\n");
        write(&root.join("debug.log"), "noise");

        std::process::Command::new("git")
            .args(["init", "-q"])
            .current_dir(root)
            .status()
            .unwrap();

        let files = scan_repo(root).unwrap();
        let names: Vec<String> = files.iter().map(|p| p.display().to_string()).collect();
        assert!(names.contains(&"src/app.py".to_string()), "{names:?}");
        assert!(names.contains(&"lib/main.ts".to_string()));
        assert!(names.contains(&"lib/comp.tsx".to_string()));
        assert!(!names.iter().any(|n| n.contains("node_modules")));
        assert!(!names.iter().any(|n| n.contains("__pycache__")));
        assert!(!names.iter().any(|n| n.ends_with(".min.js")));
        assert!(!names.iter().any(|n| n.contains("ignored")));
        assert!(!names.iter().any(|n| n.ends_with(".md")));
        assert!(!names.iter().any(|n| n.ends_with(".log")));
        assert_eq!(files.len(), 4);
    }

    #[test]
    fn binary_sniffing_skips_nul_files() {
        let tmp = tempfile::tempdir().unwrap();
        let root = tmp.path();
        std::fs::create_dir_all(root.join("src")).unwrap();
        std::fs::write(root.join("src/blob.py"), b"ok = 1\n\x00binary").unwrap();
        std::process::Command::new("git")
            .args(["init", "-q"])
            .current_dir(root)
            .status()
            .unwrap();
        let files = scan_repo(root).unwrap();
        assert!(files.is_empty(), "{files:?}");
    }

    #[test]
    fn dangling_symlink_does_not_crash_the_scan_and_siblings_are_still_found() {
        // Item 6: a symlink whose target disappears (or never existed)
        // between directory listing and `std::fs::metadata` following it is
        // a scanner-level race distinct from update_index's file-hash
        // accounting. `scan_repo` must degrade gracefully (skip the broken
        // entry, never panic or error the whole walk) rather than lose
        // visibility into the rest of the repository.
        let tmp = tempfile::tempdir().unwrap();
        let root = tmp.path();
        write(&root.join("src/real.py"), "def real():\n    return 1\n");
        #[cfg(unix)]
        std::os::unix::fs::symlink(
            root.join("src/does_not_exist.py"),
            root.join("src/dangling.py"),
        )
        .unwrap();

        let files = scan_repo(root).unwrap();
        let names: Vec<String> = files.iter().map(|p| p.display().to_string()).collect();
        assert!(names.contains(&"src/real.py".to_string()), "{names:?}");
        assert!(
            !names.iter().any(|n| n.contains("dangling")),
            "a dangling symlink must not appear as a discovered file: {names:?}"
        );
    }
}
