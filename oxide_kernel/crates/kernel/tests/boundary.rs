//! Kernel dependency boundary (docs/spec/SPEC.md § Kernel): the kernel links
//! no crates, has no build script or out-of-tree sources, and reaches only an
//! allowlist of side-effect-free std modules, so it cannot open connections,
//! read files or the clock, spawn processes, print, or carry DB/transport/
//! integration types. Loosening any check needs a SPEC-backed reason here.

use std::fs;
use std::path::{Path, PathBuf};

/// The only std modules kernel source may name. Everything else — `fs`, `io`,
/// `net`, `process`, `env`, `thread`, `os`, `time`, globs, `self` aliases — is
/// rejected. `env!`/`include_str!` are compile-time and unaffected.
const ALLOWED_STD: &[&str] = &[
    "any",
    "array",
    "borrow",
    "boxed",
    "cell",
    "char",
    "cmp",
    "collections",
    "convert",
    "default",
    "error",
    "fmt",
    "hash",
    "iter",
    "marker",
    "mem",
    "num",
    "ops",
    "option",
    "rc",
    "result",
    "slice",
    "str",
    "string",
    "sync",
    "vec",
];

/// Constructs that do I/O or pull in source from elsewhere without naming a
/// std module.
const FORBIDDEN: &[&str] = &[
    "print!",
    "println!",
    "eprint!",
    "eprintln!",
    "dbg!",
    "include!",
    "extern crate",
    "#[path",
];

/// Manifest keys that would add a build script, native links or sources
/// outside `src/`.
const FORBIDDEN_KEYS: &[&str] = &["build", "links", "path"];

#[test]
fn kernel_manifest_declares_no_dependencies_or_build_inputs() {
    for line in include_str!("../Cargo.toml").lines().map(str::trim) {
        let table = line.starts_with('[') && line.contains("dependencies");
        assert!(
            !table || line == "[dev-dependencies]",
            "kernel Cargo.toml declares `{line}`"
        );
        let key = line.split('=').next().unwrap_or("").trim();
        assert!(
            !line.contains('=') || !FORBIDDEN_KEYS.contains(&key),
            "kernel Cargo.toml sets `{line}`"
        );
    }
}

#[test]
fn kernel_source_does_no_io() {
    let mut files = Vec::new();
    rust_files(
        &Path::new(env!("CARGO_MANIFEST_DIR")).join("src"),
        &mut files,
    );
    assert!(!files.is_empty());
    for file in files {
        let found = violations(&fs::read_to_string(&file).unwrap());
        assert!(found.is_empty(), "{}: {found:?}", file.display());
    }
}

/// The scanner itself must catch every bypass shape found in review.
#[test]
fn scanner_catches_known_bypasses() {
    for bad in [
        "use std::fs::read;",
        "use std::fs as f;",
        "use std::{fs as f};",
        "use std::{collections::{BTreeMap}, fs};",
        "let t = std::time::SystemTime::now();",
        "::std::io::stdout();",
        "use std :: net;",
        "use std as s;",
        "use std::{self as s};",
        "use std::*;",
        "use std::os::unix::fs::FileExt;",
        "println!(\"x\");",
        "dbg!(x);",
        "include!(\"../../x.rs\");",
        "#[path = \"../x.rs\"] mod x;",
        "extern crate alloc;",
    ] {
        assert!(!violations(bad).is_empty(), "not caught: {bad}");
    }
    for good in [
        "use std::collections::BTreeMap;",
        "use std::{fmt, collections::{BTreeMap, BTreeSet}};",
        "const V: &str = env!(\"CARGO_PKG_VERSION\");",
        "fn my_eprintln_free() {} let stdout_like = 1;",
    ] {
        assert_eq!(
            violations(good),
            Vec::<String>::new(),
            "false positive: {good}"
        );
    }
}

fn violations(source: &str) -> Vec<String> {
    let mut found: Vec<String> = std_modules(source)
        .into_iter()
        .filter(|m| !ALLOWED_STD.contains(&m.as_str()))
        .map(|m| format!("std::{m}"))
        .collect();
    for token in FORBIDDEN {
        let hit = source
            .match_indices(token)
            .any(|(at, _)| !source[..at].chars().next_back().is_some_and(is_ident));
        if hit {
            found.push((*token).to_owned());
        }
    }
    found
}

/// Top-level module named after every `std` path, including each item of a
/// grouped `std::{...}` import; `std as` aliases are reported as `as`.
fn std_modules(source: &str) -> Vec<String> {
    let mut found = Vec::new();
    for (at, _) in source.match_indices("std") {
        let rest = &source[at + 3..];
        if source[..at].chars().next_back().is_some_and(is_ident) || rest.starts_with(is_ident) {
            continue; // part of a longer identifier such as `stdout`
        }
        let rest = rest.trim_start();
        if let Some(path) = rest.strip_prefix("::") {
            let path = path.trim_start();
            match path.strip_prefix('{') {
                Some(group) => group_items(group, &mut found),
                None => found.push(ident(path)),
            }
        } else if rest
            .strip_prefix("as")
            .is_some_and(|r| !r.starts_with(is_ident))
        {
            found.push("as".to_owned());
        }
    }
    found
}

fn group_items(group: &str, found: &mut Vec<String>) {
    let mut depth = 1;
    let mut expect_item = true;
    for (i, c) in group.char_indices() {
        match c {
            '{' => depth += 1,
            '}' => {
                depth -= 1;
                if depth == 0 {
                    return;
                }
            }
            ',' if depth == 1 => expect_item = true,
            c if c.is_whitespace() => {}
            _ if expect_item && depth == 1 => {
                found.push(ident(&group[i..]));
                expect_item = false;
            }
            _ => {}
        }
    }
}

/// The identifier at the start of `s`, or its first character (`*`, ...).
fn ident(s: &str) -> String {
    let end = s.find(|c: char| !is_ident(c)).unwrap_or(s.len());
    match end {
        0 => s.chars().next().map(String::from).unwrap_or_default(),
        _ => s[..end].to_owned(),
    }
}

fn is_ident(c: char) -> bool {
    c.is_alphanumeric() || c == '_'
}

fn rust_files(dir: &Path, out: &mut Vec<PathBuf>) {
    for entry in fs::read_dir(dir).unwrap() {
        let path = entry.unwrap().path();
        if path.is_dir() {
            rust_files(&path, out);
        } else if path.extension().is_some_and(|e| e == "rs") {
            out.push(path);
        }
    }
}
