//! Import-string → file resolution: map a raw module string a symbol
//! recorded (`./util`, `.store`, `crate::net::Client`, `./util.h`) to the
//! one indexed file it names, or to nothing. Pure path arithmetic over an
//! existence predicate — no symbols, no parsing; [`super::RelationGraph`]'s
//! `imported-definition` edge is its one production consumer.

use std::collections::HashSet;

/// Directory of `file` with `ups` extra levels stripped, as a slash-suffixed
/// prefix (empty string at the repo root). `None` when `ups` would climb
/// past the root, which means the import cannot be resolved at all.
fn dir_of(file: &str, ups: usize) -> Option<String> {
    let mut parts: Vec<&str> = file.split('/').collect();
    parts.pop()?; // drop the file name
    if ups > parts.len() {
        return None;
    }
    for _ in 0..ups {
        parts.pop();
    }
    let mut p = parts.join("/");
    if !p.is_empty() {
        p.push('/');
    }
    Some(p)
}

/// Map `./utils/token` (+ language extensions / `__init__` / `index` / Rust
/// `::` paths) to a file present in `files`. Returns None when ambiguous or
/// missing.
///
/// Go is deliberately unresolvable here: a Go import names a *package
/// directory* holding many files, not one file, and most imports are
/// module-qualified (`github.com/…`) or stdlib. Since this function's whole
/// contract is "exactly one unambiguous file", Go imports are recorded on
/// the symbol but never produce an `imported-definition` edge — a known gap
/// listed in `docs/language-support/README.md`, not an accident.
pub fn resolve_module(module: &str, from_file: &str, files: &HashSet<&str>) -> Option<String> {
    resolve_module_with(module, from_file, &|p| files.contains(p))
}

/// [`resolve_module`] over any existence predicate — what [`RelationGraph`]
/// uses so the indexed file set never has to be materialized as a set of
/// borrowed strings.
pub fn resolve_module_with(
    module: &str,
    from_file: &str,
    exists: &dyn Fn(&str) -> bool,
) -> Option<String> {
    let norm = module.trim_start_matches("@/");
    // `.name`/`..pkg.name` (dots followed by a module path, no slash) is
    // Python's syntax and nobody else's, so its candidates are Python's
    // alone: a `store.ts`/`store.rb` next to a Python package must neither
    // satisfy `.store` when `store.py` is absent nor make it ambiguous when
    // present (Greptile review). A bare `.`/`..` stays language-agnostic —
    // TypeScript's `import x from '.'` names `index.ts` the same way.
    let mut python_only = false;
    let joined = if let Some(rest) = norm.strip_prefix("./").or_else(|| norm.strip_prefix("../")) {
        let ups = norm.matches("../").count();
        let mut parts: std::collections::VecDeque<&str> = from_file.split('/').collect();
        parts.pop_back(); // drop file name
        for _ in 0..ups.min(parts.len()) {
            parts.pop_back();
        }
        let mut p = parts.into_iter().collect::<Vec<_>>().join("/");
        if !p.is_empty() {
            p.push('/');
        }
        format!("{p}{rest}")
    } else if norm.starts_with('.') {
        // Python relative import with no path separator: `.store`,
        // `..pkg.util`, or a bare `.`/`..` (`from . import x`). A single
        // leading dot names the current package (0 levels up); each
        // additional dot climbs one level — the same contract `dir_of`
        // already gives Rust's `self::`/`super::` below, reused here rather
        // than duplicated. `from . import submodule` records only `.` as
        // the import string (the name after `import` is never captured by
        // `collect_meta`'s `import_from_statement` arm — see tags.rs), so
        // an empty `rest` resolves to the package's own `__init__.py`
        // rather than to the submodule. That's a pre-existing limitation of
        // what gets recorded, not something this fix introduces or is
        // responsible for closing.
        let dots = norm.chars().take_while(|&c| c == '.').count();
        let rest = &norm[dots..];
        let dir = dir_of(from_file, dots - 1)?;
        if rest.is_empty() {
            dir.trim_end_matches('/').to_string()
        } else {
            python_only = true;
            format!("{dir}{}", rest.replace('.', "/"))
        }
    } else {
        // Absolute python-style import: try as path anywhere.
        norm.replace('.', "/")
    };

    // Package/directory forms. A bare `.`/`..` that lands on the repo root
    // itself leaves `joined` empty, and `"/__init__.py"` would never match
    // an indexed path (none carries a leading slash) — so the separator is
    // only added when there is a directory to separate from.
    let dir_prefix = if joined.is_empty() {
        String::new()
    } else {
        format!("{joined}/")
    };
    let mut candidates = vec![
        format!("{joined}.py"),
        format!("{joined}.pyi"),
        format!("{dir_prefix}__init__.py"),
    ];
    if !python_only {
        candidates.extend([
            format!("{joined}.ts"),
            format!("{joined}.tsx"),
            // Ruby `require_relative './base'` is a real path, minus the
            // extension — the same shape TypeScript's `./base` already has.
            format!("{joined}.rb"),
            // The path as written, extension included — C's `#include
            // "util.h"` (recorded as `./util.h`) already names a file, so
            // appending a language extension to it could only miss.
            joined.clone(),
            format!("{dir_prefix}index.ts"),
            format!("{dir_prefix}index.tsx"),
        ]);
    }
    // Rust `use` trees are `::`-separated and normally end in the *item*
    // name, not the module: `crate::backend::Backend` names `backend`. Try
    // the path with and without its last segment, at the repo root and under
    // a `src/` layout, as both `X.rs` and `X/mod.rs`. Extra candidates are
    // safe: more than one match still resolves to None below, so a wrong
    // guess degrades to no edge rather than a false one.
    if module.contains("::") {
        let path = norm.replace("::", "/");
        // `self::x` is relative to the current file's own directory and
        // `super::x` climbs one level per `super`, exactly like `./` and
        // `../` — resolving either from the crate root probed a file that
        // has nothing to do with the import.
        // A relative path is already anchored to one directory; only a
        // crate-root path needs the `src/` layout guess. `None` means the
        // path is relative but climbs past the repo root — unresolvable, so
        // it contributes no candidates rather than falling back to a
        // crate-root reading of the same text.
        let (prefixes, rest) = if let Some(rest) = path.strip_prefix("self/") {
            (dir_of(from_file, 0).map(|d| vec![d]), rest.to_string())
        } else if path.starts_with("super/") {
            let mut ups = 0usize;
            let mut rest = path.as_str();
            while let Some(next) = rest.strip_prefix("super/") {
                ups += 1;
                rest = next;
            }
            (dir_of(from_file, ups).map(|d| vec![d]), rest.to_string())
        } else {
            (
                Some(vec![String::new(), "src/".to_string()]),
                path.trim_start_matches("crate/").to_string(),
            )
        };
        if let Some(prefixes) = prefixes {
            let mut stems = vec![rest.clone()];
            if let Some((head, _)) = rest.rsplit_once('/') {
                stems.push(head.to_string());
            }
            for stem in stems {
                for prefix in &prefixes {
                    for suffix in [".rs", "/mod.rs"] {
                        candidates.push(format!("{prefix}{stem}{suffix}"));
                    }
                }
            }
        }
    }
    let matches: Vec<String> = candidates.into_iter().filter(|c| exists(c)).collect();
    if matches.len() == 1 {
        Some(matches.into_iter().next().unwrap())
    } else {
        None
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rust_use_paths_resolve_to_module_files() {
        let files: HashSet<&str> = ["src/backend.rs", "src/net/mod.rs", "src/main.rs"]
            .into_iter()
            .collect();
        // The trailing segment is the imported item, not a module.
        assert_eq!(
            resolve_module("crate::backend::Backend", "src/main.rs", &files),
            Some("src/backend.rs".to_string())
        );
        // `X/mod.rs` layout, and a path that is already the module.
        assert_eq!(
            resolve_module("crate::net", "src/main.rs", &files),
            Some("src/net/mod.rs".to_string())
        );
        // An external crate resolves to nothing rather than to something wrong.
        assert_eq!(
            resolve_module("std::collections::HashMap", "src/main.rs", &files),
            None
        );
    }

    #[test]
    fn rust_self_and_super_paths_resolve_relative_to_the_importing_file() {
        // Found by review: `self`/`super` were stripped and the remainder
        // probed from the crate root, so `super::baz::Thing` in
        // `src/foo/bar.rs` looked for `src/baz.rs` instead of `src/foo/../
        // baz.rs` — a different file entirely, or none.
        let files: HashSet<&str> = [
            "src/foo/bar.rs",
            "src/foo/sib.rs",
            "src/baz.rs",
            "src/foo/baz.rs",
        ]
        .into_iter()
        .collect();
        assert_eq!(
            resolve_module("super::baz::Thing", "src/foo/bar.rs", &files),
            Some("src/baz.rs".to_string())
        );
        assert_eq!(
            resolve_module("self::sib::Thing", "src/foo/bar.rs", &files),
            Some("src/foo/sib.rs".to_string())
        );
        // Climbing past the root resolves to nothing, not to a crate-root read.
        assert_eq!(
            resolve_module("super::super::super::baz", "src/foo/bar.rs", &files),
            None
        );
    }

    #[test]
    fn python_dotted_relative_imports_resolve_without_a_path_separator() {
        // Found in review: `resolve_module` only recognized TypeScript/Ruby-
        // style `./`/`../` relative paths. Python's own relative-import
        // syntax has no slash at all (`.store`, `..pkg.util`), so it fell
        // into the catch-all `starts_with('.') => None` arm and never
        // resolved — every `imported-definition` edge for a Python relative
        // import silently degraded to the weaker `uses` name-heuristic.
        let files: HashSet<&str> = [
            "pkg/sub/mod.py",
            "pkg/sub/sibling.py",
            "pkg/util.py",
            "pkg/__init__.py",
            "pkg/sub/__init__.py",
        ]
        .into_iter()
        .collect();
        // `from .sibling import X` — one dot is the current package.
        assert_eq!(
            resolve_module(".sibling", "pkg/sub/mod.py", &files),
            Some("pkg/sub/sibling.py".to_string())
        );
        // `from ..util import X` — each extra dot climbs one directory.
        assert_eq!(
            resolve_module("..util", "pkg/sub/mod.py", &files),
            Some("pkg/util.py".to_string())
        );
        // `from . import sibling` — bare dot names the package itself.
        assert_eq!(
            resolve_module(".", "pkg/sub/mod.py", &files),
            Some("pkg/sub/__init__.py".to_string())
        );
        // `from .. import x` — bare double dot names the parent package.
        assert_eq!(
            resolve_module("..", "pkg/sub/mod.py", &files),
            Some("pkg/__init__.py".to_string())
        );
        // A module that doesn't exist in the indexed set resolves to nothing.
        assert_eq!(resolve_module(".missing", "pkg/sub/mod.py", &files), None);
        // Climbing past the repo root resolves to nothing, matching Rust's
        // `super::super::super::` contract above.
        assert_eq!(resolve_module("....deep", "pkg/sub/mod.py", &files), None);
    }

    #[test]
    fn python_dotted_relative_import_is_ambiguous_when_both_forms_exist() {
        // A module file and a same-named package directory both matching is
        // the pre-existing "more than one candidate => None" contract every
        // other language already relies on (see `matches.len() == 1` at the
        // bottom of `resolve_module`) — this just proves the new Python arm
        // doesn't bypass it.
        let files: HashSet<&str> = ["pkg/sub/mod.py", "pkg/store.py", "pkg/store/__init__.py"]
            .into_iter()
            .collect();
        assert_eq!(resolve_module("..store", "pkg/sub/mod.py", &files), None);
    }

    #[test]
    fn bare_dot_import_resolves_at_the_repo_root() {
        // A bare `.`/`..` whose target directory is the repo root itself
        // (`from . import x` in a top-level `mod.py`, `from .. import x` one
        // level down, or TypeScript's `import x from '.'`) has an empty
        // directory prefix. Building the package candidate as
        // `format!("{joined}/__init__.py")` from that empty prefix produced
        // `/__init__.py` — a leading slash no indexed path ever carries — so
        // a root-level package could never be resolved. Non-root packages
        // were unaffected.
        let py: HashSet<&str> = ["mod.py", "__init__.py", "pkg/sub.py"]
            .into_iter()
            .collect();
        assert_eq!(
            resolve_module(".", "mod.py", &py),
            Some("__init__.py".to_string())
        );
        assert_eq!(
            resolve_module("..", "pkg/sub.py", &py),
            Some("__init__.py".to_string())
        );
        // Separate file set: a root holding both `__init__.py` and `index.ts`
        // is the pre-existing "more than one candidate => None" case, which
        // the language-agnostic candidate list has always had for `./x`
        // when both `x.py` and `x.ts` exist.
        let ts: HashSet<&str> = ["index.ts", "app.ts"].into_iter().collect();
        assert_eq!(
            resolve_module(".", "app.ts", &ts),
            Some("index.ts".to_string())
        );
    }

    #[test]
    fn python_dotted_relative_import_only_considers_python_candidates() {
        // Greptile review of the dot-relative branch: `.store` is Python
        // syntax, so a same-stem TypeScript/Ruby file next to the package
        // must neither satisfy it when `store.py` is absent (a false
        // `imported-definition` edge into the wrong language) nor make it
        // ambiguous when `store.py` is present (a false negative).
        let no_py: HashSet<&str> = ["pkg/mod.py", "pkg/store.ts", "pkg/store.rb"]
            .into_iter()
            .collect();
        assert_eq!(resolve_module(".store", "pkg/mod.py", &no_py), None);
        let both: HashSet<&str> = ["pkg/mod.py", "pkg/store.py", "pkg/store.ts"]
            .into_iter()
            .collect();
        assert_eq!(
            resolve_module(".store", "pkg/mod.py", &both),
            Some("pkg/store.py".to_string())
        );
        // A bare dot is shared with TypeScript and keeps both forms.
        let ts: HashSet<&str> = ["pkg/app.ts", "pkg/index.ts"].into_iter().collect();
        assert_eq!(
            resolve_module(".", "pkg/app.ts", &ts),
            Some("pkg/index.ts".to_string())
        );
    }

    #[test]
    fn module_resolution_probes_extensions_and_indexes() {
        let files: HashSet<&str> = ["src/utils/token.py", "pkg/api/index.ts"]
            .into_iter()
            .collect();
        assert_eq!(
            resolve_module("./token", "src/utils/auth.py", &files).as_deref(),
            Some("src/utils/token.py")
        );
        assert_eq!(
            resolve_module("pkg/api", "src/main.ts", &files).as_deref(),
            Some("pkg/api/index.ts")
        );
        assert_eq!(resolve_module("./missing", "src/main.ts", &files), None);
    }
}
