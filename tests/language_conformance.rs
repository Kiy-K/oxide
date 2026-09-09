//! Language conformance suite: one committed golden snapshot per language,
//! covering every dimension OXIDE's extraction contract is supposed to hold.
//!
//! The snapshot is the audit artifact. It deliberately records *current*
//! behavior, gaps included — a `KNOWN GAP` note next to a golden line is how
//! a gap stays visible until a later change flips it, instead of living in
//! a doc nobody diffs. Regenerate with `UPDATE_GOLDEN=1 cargo test -j 2
//! --test language_conformance` and read the diff.
//!
//! Dimensions and where each one is asserted:
//!
//! | Dimension | Where |
//! |---|---|
//! | definitions + stable identity | golden `id` (FNV of file + qualified_name) |
//! | kinds + qualified names | golden `kind` / `qualified_name` |
//! | parent/child containment | golden `parent` |
//! | imports / re-exports | golden `imports` |
//! | references | golden `references` |
//! | calls / callers | golden `calls` |
//! | inheritance / implementations | golden `bases` |
//! | nested symbols | `Record.Meta`, `build.inner`, `Derived.area.helper` |
//! | broken / incomplete files | `broken.*` fixtures + `broken_files_do_not_abort_indexing` |
//! | determinism | `cold_index_is_deterministic` |
//! | incremental single-file edits | `single_file_edit_touches_only_that_file` |
//!
//! Fixtures live in `fixtures/conformance/<lang>/` and are copied to a temp
//! dir before indexing so no `.oxide/` is ever written into the repo.
//!
//! KNOWN GAPS the committed goldens currently pin (each one is a line a
//! later change is expected to flip — if one of these silently "fixes"
//! itself, the golden diff is the alarm):
//!
//! - **Decorator lines are outside the span.** `Record` starts at the
//!   `class` line, not `@dataclass`; same for `@property`/`@register`. The
//!   second-order effect is visible too: `@register("svc")` above `run`
//!   attributes the `register` call to `pkg/service.py:__module__`, not to
//!   `run`, because the decorator line isn't inside any symbol's span.
//! - **Qualified bases are dropped.** `class Record(abc.ABC)` and
//!   `class Derived extends ns.Base` both record no base from the qualified
//!   name; `Derived` keeps only `Shape` from its implements clause.
//! - **Generic bases are dropped.** `class Panel extends
//!   React.Component<ButtonProps>` records nothing at all.
//! - **JSX element usage is not a call.** Rendering `<Button />` leaves
//!   `calls` empty everywhere in the tsx golden, so `callers_of("Button")`
//!   finds nothing; only the token-intersection `references` sees it.
//! - **Import bindings are not stored.** `import { Base, Derived } from
//!   './service'` records the module string only; the bound names survive
//!   nowhere structured (`SymbolKind::Import` is never produced).
//! - **Python symbols are unconditionally `exported: true`** — the flag has
//!   no Python meaning today.

use oxide::embeddings::HashedEmbedder;
use oxide::index::update_index;
use oxide::storage::SqliteStore;
use oxide::structural_relations::load_symbols_with_relations;
use oxide::symbols::Symbol;
use serde::{Deserialize, Serialize};
use std::fs;
use std::path::{Path, PathBuf};

const LANGUAGES: &[&str] = &["python", "typescript", "tsx"];

#[derive(Serialize, Deserialize, PartialEq, Debug, Clone)]
struct SnapshotSymbol {
    file: String,
    qualified_name: String,
    kind: String,
    parent: Option<String>,
    start_line: u32,
    end_line: u32,
    exported: bool,
    id: u64,
    content_hash: u64,
    imports: Vec<String>,
    references: Vec<String>,
    calls: Vec<String>,
    bases: Vec<String>,
}

fn fixture_root(lang: &str) -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("fixtures/conformance")
        .join(lang)
}

fn golden_path(lang: &str) -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("fixtures/conformance")
        .join(format!("{lang}.golden.json"))
}

fn copy_tree(src: &Path, dst: &Path) {
    fs::create_dir_all(dst).unwrap();
    for entry in fs::read_dir(src).unwrap() {
        let entry = entry.unwrap();
        let to = dst.join(entry.file_name());
        if entry.file_type().unwrap().is_dir() {
            copy_tree(&entry.path(), &to);
        } else {
            fs::copy(entry.path(), to).unwrap();
        }
    }
}

/// Fresh temp copy of a language's fixture tree, ready to index.
fn staged(lang: &str) -> tempfile::TempDir {
    let tmp = tempfile::tempdir().unwrap();
    copy_tree(&fixture_root(lang), tmp.path());
    tmp
}

fn index_at(root: &Path) -> Vec<Symbol> {
    let mut store = SqliteStore::open(&root.join(".oxide/index.db")).unwrap();
    update_index(root, &mut store, &HashedEmbedder::default()).unwrap();
    load_symbols_with_relations(&store).unwrap()
}

fn snapshot(symbols: &[Symbol]) -> Vec<SnapshotSymbol> {
    let mut out: Vec<SnapshotSymbol> = symbols
        .iter()
        .map(|s| SnapshotSymbol {
            file: s.file.clone(),
            qualified_name: s.qualified_name.clone(),
            kind: s.kind.to_string(),
            parent: s.parent.clone(),
            start_line: s.start_line,
            end_line: s.end_line,
            exported: s.exported,
            id: s.id(),
            content_hash: s.content_hash,
            imports: s.imports.clone(),
            references: s.references.clone(),
            calls: s.calls.clone(),
            bases: s.bases.clone(),
        })
        .collect();
    out.sort_by(|a, b| {
        (&a.file, a.start_line, &a.qualified_name).cmp(&(&b.file, b.start_line, &b.qualified_name))
    });
    out
}

fn snapshot_of(lang: &str) -> Vec<SnapshotSymbol> {
    let tmp = staged(lang);
    snapshot(&index_at(tmp.path()))
}

/// Compare against the committed golden, or rewrite it under `UPDATE_GOLDEN=1`.
fn check_golden(lang: &str, actual: &[SnapshotSymbol]) {
    let path = golden_path(lang);
    let rendered = serde_json::to_string_pretty(actual).unwrap() + "\n";
    if std::env::var("UPDATE_GOLDEN").is_ok() {
        fs::write(&path, &rendered).unwrap();
        return;
    }
    let expected_raw = fs::read_to_string(&path).unwrap_or_else(|e| {
        panic!("missing golden {path:?} ({e}) — regenerate with UPDATE_GOLDEN=1")
    });
    let expected: Vec<SnapshotSymbol> = serde_json::from_str(&expected_raw).unwrap();
    if expected == actual {
        return;
    }
    // Point at the first divergence rather than dumping two whole files.
    for (i, (want, got)) in expected.iter().zip(actual.iter()).enumerate() {
        assert_eq!(
            want, got,
            "{lang} golden diverges at index {i} — rerun with UPDATE_GOLDEN=1 and review the diff"
        );
    }
    panic!(
        "{lang} golden has {} symbols, extraction produced {} — rerun with UPDATE_GOLDEN=1",
        expected.len(),
        actual.len()
    );
}

#[test]
fn python_conformance() {
    check_golden("python", &snapshot_of("python"));
}

#[test]
fn typescript_conformance() {
    check_golden("typescript", &snapshot_of("typescript"));
}

#[test]
fn tsx_conformance() {
    check_golden("tsx", &snapshot_of("tsx"));
}

#[test]
fn cold_index_is_deterministic() {
    // Two independent cold indexes of identical sources must agree
    // symbol-for-symbol and field-for-field, including list ordering —
    // HashMap iteration leaking into any of `references`/`calls`/`bases`
    // shows up here (tests/determinism_stress.rs covers the retrieval side).
    for lang in LANGUAGES {
        assert_eq!(
            snapshot_of(lang),
            snapshot_of(lang),
            "{lang}: cold index is not deterministic"
        );
    }
}

#[test]
fn no_change_reindex_is_a_no_op() {
    for lang in LANGUAGES {
        let tmp = staged(lang);
        let first = snapshot(&index_at(tmp.path()));
        let second = snapshot(&index_at(tmp.path()));
        assert_eq!(
            first, second,
            "{lang}: reindex without edits changed symbols"
        );
    }
}

#[test]
fn single_file_edit_touches_only_that_file() {
    // Per language: append a definition to one file, reindex, and assert
    // every *other* file's symbols are byte-identical. This is the contract
    // incremental re-embedding rests on — a symbol whose content_hash moves
    // without its source moving is a wasted re-embed.
    let cases = [
        (
            "python",
            "pkg/service.py",
            "\n\ndef appended():\n    return build()\n",
            "appended",
        ),
        (
            "typescript",
            "src/service.ts",
            "\nexport function appended(): number {\n  return compute();\n}\n",
            "appended",
        ),
        (
            "tsx",
            "src/App.tsx",
            "\nexport function Appended() {\n  return <App />;\n}\n",
            "Appended",
        ),
    ];
    for (lang, rel, addition, added_name) in cases {
        let tmp = staged(lang);
        let before = snapshot(&index_at(tmp.path()));

        let target = tmp.path().join(rel);
        let mut src = fs::read_to_string(&target).unwrap();
        src.push_str(addition);
        fs::write(&target, src).unwrap();

        let after = snapshot(&index_at(tmp.path()));

        let untouched = |snap: &[SnapshotSymbol]| -> Vec<SnapshotSymbol> {
            snap.iter().filter(|s| s.file != rel).cloned().collect()
        };
        assert_eq!(
            untouched(&before),
            untouched(&after),
            "{lang}: editing {rel} disturbed symbols in other files"
        );
        assert!(
            after.iter().any(|s| s.file == rel && s.name_is(added_name)),
            "{lang}: appended definition was not indexed"
        );
    }
}

#[test]
fn broken_files_do_not_abort_indexing() {
    // A file tree-sitter cannot fully parse must degrade to (at worst) the
    // module fallback symbol and must never take its siblings down with it.
    let cases = [
        ("python", "pkg/broken.py", "pkg/models.py"),
        ("typescript", "src/broken.ts", "src/service.ts"),
        ("tsx", "src/broken.tsx", "src/Button.tsx"),
    ];
    for (lang, broken, sibling) in cases {
        let snap = snapshot_of(lang);
        assert!(
            snap.iter().any(|s| s.file == broken),
            "{lang}: {broken} produced no symbols at all, not even the module fallback"
        );
        assert!(
            snap.iter().filter(|s| s.file == sibling).count() > 1,
            "{lang}: {sibling} lost symbols alongside a broken file"
        );
    }
}

impl SnapshotSymbol {
    /// Bare last segment of the qualified name — the golden stores only the
    /// qualified form, and tests above only ever need the tail.
    fn name_is(&self, name: &str) -> bool {
        self.qualified_name
            .rsplit(['.', ':'])
            .next()
            .is_some_and(|n| n == name)
    }
}
