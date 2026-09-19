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
    // Not `path.extension()?`: a few languages' most load-bearing files
    // carry no extension at all (`Rakefile`, `Gemfile`), and bailing here
    // would make the name arms below unreachable.
    let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
    match (name, ext) {
        (_, "py") | (_, "pyi") => Some(Python),
        (_, "ts") if !name.ends_with(".d.ts") => Some(TypeScript),
        ("", "tsx") | (_, "tsx") => Some(Tsx),
        // JavaScript and JSX both go through the TSX grammar
        // (`languages::JAVASCRIPT_PROFILE`), so one Language covers all four
        // extensions. `.min.js` is already rejected by DENYLIST_SUFFIXES.
        (_, "js") | (_, "jsx") | (_, "mjs") | (_, "cjs") => Some(JavaScript),
        (_, "rs") => Some(Rust),
        (_, "go") => Some(Go),
        (_, "java") => Some(Java),
        // `.rake`/`.gemspec` are Ruby source with a different extension, and
        // the three Ruby files a repo root is most likely to carry
        // (`Rakefile`, `Gemfile`, `*.gemspec`) have no extension at all —
        // hence the name arm.
        (_, "rb") | (_, "rake") | (_, "gemspec") => Some(Ruby),
        ("Rakefile" | "Gemfile", _) => Some(Ruby),
        (_, "php") | (_, "phtml") => Some(Php),
        (_, "c") | (_, "h") => Some(C),
        // `.h` remains C until corpus measurements establish whether the
        // C++ grammar is the better default; that decision is not inferable
        // from a file name or its siblings.
        (_, "cc") | (_, "cpp") | (_, "cxx") | (_, "hh") | (_, "hpp") | (_, "hxx") => Some(Cpp),
        (_, "md") => Some(Markdown),
        _ => None,
    }
}

/// Selective cap on indexed documentation, independent of (and much tighter
/// than) `MAX_SCANNED_FILE_BYTES`: a survey of this repo's own hand-written
/// docs (the largest, `docs/literal-search-eval/README.md`, is ~36 KB) and
/// its README (~20 KB) shows legitimate developer documentation comfortably
/// fits well under 64 KB, while a sprawling changelog or an accidentally
/// vendored doc that slipped past the denylist would not. Markdown gets its
/// own, tighter cap here rather than a change to `MAX_SCANNED_FILE_BYTES`
/// because that constant is shared with `scan_repo_text`'s literal search,
/// which has no reason to hide a large file from a grep-style match.
///
/// `watcher.rs::IgnoreCache::candidate`'s fast path treats any
/// already-indexable path as a candidate without rechecking anything, by
/// design ("no rescan" is the whole point of caching the indexable set) —
/// true for every language, including markdown. That's safe because
/// `index.rs::update_base_for_files` (not the cache) is what actually
/// enforces size eligibility: it calls `is_indexable` on every path before
/// reading it, and treats "exists but no longer eligible" — oversized
/// markdown, or any language past `MAX_SCANNED_FILE_BYTES` — exactly like
/// deletion (stale symbol removed, oversized content never parsed). A
/// version of this fix once lived in the cache layer for markdown only and
/// left the generic 1.5 MB cap's watcher-freshness gap unaddressed; moving
/// the check downstream, where it naturally covers every language through
/// one shared code path, closed both at once (found by review).
const MAX_MARKDOWN_BYTES: u64 = 64 * 1024;

fn error_nodes(language: tree_sitter::Language, src: &str) -> usize {
    fn count(node: tree_sitter::Node<'_>) -> usize {
        let mut total = usize::from(node.is_error() || node.is_missing());
        let mut cursor = node.walk();
        for child in node.children(&mut cursor) {
            total += count(child);
        }
        total
    }

    let mut parser = tree_sitter::Parser::new();
    if parser.set_language(&language).is_err() {
        return usize::MAX;
    }
    parser
        .parse(src, None)
        .map_or(usize::MAX, |tree| count(tree.root_node()))
}

fn has_cpp_header_syntax(src: &str) -> bool {
    src.lines().map(str::trim).any(|line| {
        line.contains("::")
            || line.starts_with("namespace ") && line.contains('{')
            || line.starts_with("class ") && line.contains('{')
            || line.starts_with("template<")
            || line.starts_with("template <")
            || matches!(line, "public:" | "private:" | "protected:")
    })
}

/// Resolve an extension, then disambiguate `.h` by parsing its actual source
/// with both C-family grammars. Explicit C++ syntax breaks a parser-score
/// tie; otherwise C wins so an ordinary C header keeps its historic language
/// and persisted ids. No build system or unrelated sibling file is consulted.
pub fn language_for_source(path: &Path, src: &str) -> Option<crate::symbols::Language> {
    use crate::symbols::Language::{Cpp, C};

    let language = language_for_path(path)?;
    if language != C || path.extension().and_then(|ext| ext.to_str()) != Some("h") {
        return Some(language);
    }
    let c_errors = error_nodes(tree_sitter_c::LANGUAGE.into(), src);
    let cpp_errors = error_nodes(tree_sitter_cpp::LANGUAGE.into(), src);
    Some(
        if cpp_errors < c_errors || (cpp_errors == c_errors && has_cpp_header_syntax(src)) {
            Cpp
        } else {
            C
        },
    )
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

/// Cheap rejection for a repo-relative path that sits under a directory
/// `scan_repo` would never descend into (denylisted, or hidden the way
/// `WalkBuilder::hidden(true)` treats any dot-prefixed entry). Used by the
/// watcher (`src/watcher.rs`) to reject known build/cache/VCS noise without
/// falling back to a full rescan — a build process writing continuously into
/// `target/` must never trigger repeated full-tree walks just because those
/// paths aren't in a cached indexable set.
pub fn has_denied_ancestor(rel: &Path) -> bool {
    rel.parent().is_some_and(|parent| {
        parent.components().any(|c| match c {
            std::path::Component::Normal(name) => {
                let name = name.to_str().unwrap_or("");
                DENYLIST_DIRS.contains(&name) || name.starts_with('.')
            }
            _ => false,
        })
    })
}

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

/// Files larger than this are skipped by both walks below: too big to be a
/// hand-written source or text file worth indexing/searching, and reading
/// one in full for a literal scan would be an unbounded-latency footgun.
const MAX_SCANNED_FILE_BYTES: u64 = 1_500_000;

/// Shared walk behind [`scan_repo`] and [`scan_repo_text`]: ignore policy,
/// denylist, size cap, and binary sniffing are identical between "index
/// this" and "literal-search this"; the only difference is whether a
/// recognized language is required. Returns sorted, deduplicated
/// repo-relative slash paths regardless of the parallel walk's arrival
/// order, which callers that need deterministic output (e.g. literal
/// search's line/column results) depend on.
fn walk_repo(
    root: &Path,
    keep: impl Fn(&Path) -> bool + Send + Sync + 'static,
) -> Result<Vec<PathBuf>> {
    let root = root.canonicalize()?;
    let (tx, rx) = mpsc::channel();
    let walker_root = root.clone();
    let tx_builder = tx.clone();
    let keep = std::sync::Arc::new(keep);
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
            let keep = keep.clone();
            Box::new(move |entry| {
                let Ok(entry) = entry else {
                    return WalkState::Continue;
                };
                let path = entry.path();
                let is_dir = entry.file_type().map(|f| f.is_dir()).unwrap_or(false);
                if path == root.as_path() {
                    return WalkState::Continue;
                }
                // Denylisted directories (node_modules, venv, vendor, build, ...)
                // must not just be excluded from results but pruned from descent:
                // otherwise any source file underneath (e.g. venv/lib/*/site-packages/*.py)
                // still gets walked and indexed as noise.
                if is_dir {
                    return if is_denied(path, true) {
                        WalkState::Skip
                    } else {
                        WalkState::Continue
                    };
                }
                if is_denied(path, false) {
                    return WalkState::Continue;
                }
                if !keep(path) {
                    return WalkState::Continue;
                }
                let Ok(meta) = std::fs::metadata(path) else {
                    return WalkState::Continue;
                };
                if meta.len() > MAX_SCANNED_FILE_BYTES {
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

/// Discover indexable source files under `root` as repo-relative slash paths.
/// Respects `.gitignore`/`.ignore` via the `ignore` crate; applies the built-in
/// denylist on top. Returns sorted, deduplicated paths.
///
/// Markdown gets one extra, selective check here (`MAX_MARKDOWN_BYTES`) on
/// top of every other file's shared `MAX_SCANNED_FILE_BYTES` cap in
/// `walk_repo` — see that constant's doc comment for why documentation
/// needs its own, tighter bound.
pub fn scan_repo(root: &Path) -> Result<Vec<PathBuf>> {
    walk_repo(root, is_indexable)
}

/// Whether `path` belongs in `scan_repo`'s result: a recognized language,
/// and (markdown only) under `MAX_MARKDOWN_BYTES`. Factored out of
/// `scan_repo` so `watcher.rs::IgnoreCache::candidate` can re-apply the same
/// check on its fast path — an already-cached-indexable markdown file that
/// grows past the cap between watcher events must be re-excluded, not just
/// a freshly-discovered one (found by review: the fast path's own "already
/// known indexable, no rescan" optimization would otherwise never notice).
pub fn is_indexable(path: &Path) -> bool {
    let Some(lang) = language_for_path(path) else {
        return false;
    };
    let cap = if lang == crate::symbols::Language::Markdown {
        MAX_MARKDOWN_BYTES
    } else {
        MAX_SCANNED_FILE_BYTES
    };
    std::fs::metadata(path)
        .map(|m| m.len() <= cap)
        .unwrap_or(false)
}

/// Discover every non-denylisted, non-binary, size-capped file under `root`
/// as repo-relative slash paths — the same ignore policy as [`scan_repo`],
/// minus the "has a recognized language" gate, so literal search can find
/// matches in READMEs, configs, and any other repository text a symbol
/// index has no reason to parse. Hidden files (including `.env*`) are
/// excluded the same way `scan_repo` excludes them: by the walker's own
/// `hidden(true)`, before either function's `keep` predicate ever runs —
/// a deliberate choice for a scan whose snippets get piped straight into an
/// agent's context.
pub fn scan_repo_text(root: &Path) -> Result<Vec<PathBuf>> {
    walk_repo(root, |_| true)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn write(path: &Path, content: &str) {
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(path, content).unwrap();
    }

    #[test]
    fn markdown_over_the_selective_size_cap_is_excluded_a_small_one_is_kept() {
        let tmp = tempfile::tempdir().unwrap();
        let root = tmp.path();
        write(&root.join("README.md"), "# small doc\n");
        write(
            &root.join("HUGE.md"),
            &"x".repeat(MAX_MARKDOWN_BYTES as usize + 1),
        );
        std::process::Command::new("git")
            .args(["init", "-q"])
            .current_dir(root)
            .status()
            .unwrap();

        let files = scan_repo(root).unwrap();
        let names: Vec<String> = files.iter().map(|p| p.display().to_string()).collect();
        assert!(names.contains(&"README.md".to_string()), "{names:?}");
        assert!(
            !names.iter().any(|n| n == "HUGE.md"),
            "a markdown file over MAX_MARKDOWN_BYTES must be excluded: {names:?}"
        );
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
        // Non-hidden denylisted dirs containing real supported-extension
        // files: these are only pruned by DENYLIST_DIRS (not by extension
        // filtering or dotfile hiding), so they exercise directory pruning
        // specifically rather than the extension whitelist.
        write(
            &root.join("venv/lib/python3.11/site-packages/pkg/mod.py"),
            "def vendored():\n    return 1\n",
        );
        write(
            &root.join("vendor/thirdparty/lib.py"),
            "def vendored2():\n    return 1\n",
        );
        write(&root.join("README.md"), "# hi\n");
        write(&root.join("package-lock.json"), "{}");
        write(&root.join(".gitignore"), "/ignored/\n*.log\n");
        write(&root.join("debug.log"), "noise");
        // Documentation inherits the same denylist/gitignore/vendor exclusion
        // every other indexable file already gets — proven here, not assumed.
        write(&root.join("ignored/SECRET_NOTES.md"), "api_key=deadbeef\n");
        write(&root.join("vendor/thirdparty/README.md"), "vendored docs\n");
        write(
            &root.join("node_modules/pkg/README.md"),
            "vendored js docs\n",
        );

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
        assert!(names.contains(&"README.md".to_string()), "{names:?}");
        assert!(!names.iter().any(|n| n.contains("node_modules")));
        assert!(!names.iter().any(|n| n.contains("__pycache__")));
        assert!(!names.iter().any(|n| n.contains("venv")));
        assert!(!names.iter().any(|n| n.contains("vendor")));
        assert!(!names.iter().any(|n| n.ends_with(".min.js")));
        assert!(!names.iter().any(|n| n.contains("ignored")));
        assert!(
            !names.iter().any(|n| n.contains("SECRET_NOTES")),
            "a gitignored .md file must never reach the index: {names:?}"
        );
        assert!(!names.iter().any(|n| n.ends_with(".log")));
        assert_eq!(files.len(), 5, "{names:?}");
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
    fn ambiguous_headers_choose_the_lower_error_grammar_and_keep_c_on_a_tie() {
        assert_eq!(
            language_for_source(Path::new("store.h"), "struct Store { int id; };"),
            Some(crate::symbols::Language::C)
        );
        assert_eq!(
            language_for_source(
                Path::new("store.h"),
                include_str!("../fixtures/conformance/cpp/src/Store.cpp")
            ),
            Some(crate::symbols::Language::Cpp)
        );
        assert_eq!(
            language_for_source(Path::new("empty.h"), "// no declarations\n"),
            Some(crate::symbols::Language::C),
            "a tie preserves the historic C routing"
        );
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
