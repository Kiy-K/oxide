//! `update_base_for_files` (the auto-indexing watcher's scoped base-update
//! primitive, `docs/auto-indexing-watcher-constraints/README.md` seam #3).
//! The one property that matters most: deletions come ONLY from a confirmed
//! `NotFound` for a path already in `changed_paths` — never from a file's
//! mere absence from that set. A file the caller doesn't mention is
//! untouched, full stop.

use oxide::index::{update_base, update_base_for_files, IndexBackend, IndexOptions, SqliteStore};
use std::path::Path;

fn write(path: &Path, content: &str) {
    std::fs::create_dir_all(path.parent().unwrap()).unwrap();
    std::fs::write(path, content).unwrap();
}

fn seeded_two_file_repo() -> (tempfile::TempDir, SqliteStore) {
    let tmp = tempfile::tempdir().unwrap();
    write(&tmp.path().join("src/a.py"), "def a():\n    return 1\n");
    write(&tmp.path().join("src/b.py"), "def b():\n    return 2\n");
    let mut store = SqliteStore::open(Path::new(":memory:")).unwrap();
    update_base(tmp.path(), &mut store, &IndexOptions::default()).unwrap();
    (tmp, store)
}

#[test]
fn untouched_files_survive_even_when_absent_from_the_changed_set() {
    // The single most important invariant: a.py is never mentioned in this
    // batch. It must not be deleted, reparsed, or otherwise touched, even
    // though it's absent — absence from `changed_paths` is not evidence.
    let (tmp, mut store) = seeded_two_file_repo();
    write(&tmp.path().join("src/b.py"), "def b():\n    return 3\n");
    let r = update_base_for_files(
        tmp.path(),
        &mut store,
        &IndexOptions::default(),
        &["src/b.py".to_string()],
    )
    .unwrap();
    assert_eq!(r.reparsed_files, 1);
    assert_eq!(r.removed_files, 0);
    let symbols = store.all_symbols().unwrap();
    assert!(
        symbols.iter().any(|s| s.file == "src/a.py"),
        "a.py must survive untouched: {symbols:?}"
    );
    assert!(symbols.iter().any(|s| s.name == "b"));
}

#[test]
fn deletion_requires_both_notfound_and_prior_tracking() {
    let (tmp, mut store) = seeded_two_file_repo();
    std::fs::remove_file(tmp.path().join("src/b.py")).unwrap();
    let r = update_base_for_files(
        tmp.path(),
        &mut store,
        &IndexOptions::default(),
        &["src/b.py".to_string()],
    )
    .unwrap();
    assert_eq!(r.removed_files, 1);
    // 2, not 1: each file gets an implicit Module symbol alongside its
    // concrete ones (`def b()` + the module symbol for `src/b.py`).
    assert_eq!(r.deleted_symbols, 2);
    let symbols = store.all_symbols().unwrap();
    assert!(!symbols.iter().any(|s| s.file == "src/b.py"));
    assert!(
        symbols.iter().any(|s| s.file == "src/a.py"),
        "unrelated file must survive"
    );
}

#[test]
fn a_never_tracked_path_that_does_not_exist_is_a_silent_no_op() {
    // A transient path (e.g. an editor temp file that slipped past the
    // caller's ignore-filter) that doesn't exist and was never indexed:
    // nothing to account, not an error, not a phantom deletion.
    let (tmp, mut store) = seeded_two_file_repo();
    let r = update_base_for_files(
        tmp.path(),
        &mut store,
        &IndexOptions::default(),
        &["src/never_existed.py".to_string()],
    )
    .unwrap();
    assert_eq!(r.removed_files, 0);
    assert_eq!(r.reparsed_files, 0);
    assert_eq!(r.scanned_files, 0);
}

#[test]
fn new_file_in_the_changed_set_is_indexed() {
    let (tmp, mut store) = seeded_two_file_repo();
    write(&tmp.path().join("src/c.py"), "def c():\n    return 3\n");
    let r = update_base_for_files(
        tmp.path(),
        &mut store,
        &IndexOptions::default(),
        &["src/c.py".to_string()],
    )
    .unwrap();
    assert_eq!(r.reparsed_files, 1);
    // 2, not 1: `def c()` plus the implicit Module symbol for `src/c.py`.
    assert_eq!(r.new_symbols, 2);
    let symbols = store.all_symbols().unwrap();
    assert!(symbols.iter().any(|s| s.name == "c"));
}

#[test]
fn rename_is_a_delete_plus_a_create_in_the_same_batch() {
    // Simulates what a debounced fs watcher typically reports for a rename:
    // both the old and new paths appear as "changed" in one batch.
    let (tmp, mut store) = seeded_two_file_repo();
    std::fs::rename(
        tmp.path().join("src/b.py"),
        tmp.path().join("src/renamed.py"),
    )
    .unwrap();
    let r = update_base_for_files(
        tmp.path(),
        &mut store,
        &IndexOptions::default(),
        &["src/b.py".to_string(), "src/renamed.py".to_string()],
    )
    .unwrap();
    assert_eq!(r.removed_files, 1);
    assert_eq!(r.reparsed_files, 1);
    let symbols = store.all_symbols().unwrap();
    assert!(!symbols.iter().any(|s| s.file == "src/b.py"));
    assert!(symbols
        .iter()
        .any(|s| s.file == "src/renamed.py" && s.name == "b"));
}

#[test]
fn duplicate_paths_in_one_batch_are_deduped_not_double_counted() {
    let (tmp, mut store) = seeded_two_file_repo();
    write(&tmp.path().join("src/b.py"), "def b():\n    return 9\n");
    let r = update_base_for_files(
        tmp.path(),
        &mut store,
        &IndexOptions::default(),
        &["src/b.py".to_string(), "src/b.py".to_string()],
    )
    .unwrap();
    assert_eq!(r.reparsed_files, 1);
    assert_eq!(r.scanned_files, 1);
}

#[test]
fn force_reparse_bypasses_the_content_hash_shortcut_in_scoped_mode_too() {
    let (tmp, mut store) = seeded_two_file_repo();
    // No edit — content is unchanged — but force_reparse must still reparse.
    let opts = IndexOptions {
        force_reparse: true,
        ..IndexOptions::default()
    };
    let r =
        update_base_for_files(tmp.path(), &mut store, &opts, &["src/a.py".to_string()]).unwrap();
    assert_eq!(r.reparsed_files, 1);
    assert_eq!(r.unchanged_files, 0);
}

#[test]
fn unreadable_existing_file_is_errored_not_deleted() {
    // A file that exists but fails to read as UTF-8 must never be treated
    // as a deletion — only `NotFound` is deletion evidence.
    let (tmp, mut store) = seeded_two_file_repo();
    std::fs::write(tmp.path().join("src/a.py"), [0xFF, 0xFE, 0x00, 0x01]).unwrap();
    let r = update_base_for_files(
        tmp.path(),
        &mut store,
        &IndexOptions::default(),
        &["src/a.py".to_string()],
    )
    .unwrap();
    assert_eq!(r.removed_files, 0, "unreadable != deleted");
    assert_eq!(r.errored_files, 1);
    let symbols = store.all_symbols().unwrap();
    assert!(
        symbols.iter().any(|s| s.file == "src/a.py"),
        "a.py's prior (still valid) symbols must survive a transient unreadable state"
    );
}

#[test]
fn empty_changed_set_is_a_harmless_no_op() {
    let (tmp, mut store) = seeded_two_file_repo();
    let r = update_base_for_files(tmp.path(), &mut store, &IndexOptions::default(), &[]).unwrap();
    assert_eq!(r.scanned_files, 0);
    assert_eq!(r.reparsed_files, 0);
    assert_eq!(r.removed_files, 0);
    let symbols = store.all_symbols().unwrap();
    // 4: 2 files × (1 concrete symbol + 1 implicit Module symbol each).
    assert_eq!(symbols.len(), 4, "nothing should have changed");
}

#[test]
fn converges_to_the_same_state_as_full_update_base() {
    // Apply the same edits via update_base_for_files (scoped) and via
    // update_base (full scan) starting from identical seed state; both
    // must land on the same final symbol set.
    let (tmp_a, mut store_a) = seeded_two_file_repo();
    write(&tmp_a.path().join("src/b.py"), "def b():\n    return 42\n");
    write(&tmp_a.path().join("src/c.py"), "def c():\n    return 1\n");
    update_base_for_files(
        tmp_a.path(),
        &mut store_a,
        &IndexOptions::default(),
        &["src/b.py".to_string(), "src/c.py".to_string()],
    )
    .unwrap();

    let (tmp_b, mut store_b) = seeded_two_file_repo();
    write(&tmp_b.path().join("src/b.py"), "def b():\n    return 42\n");
    write(&tmp_b.path().join("src/c.py"), "def c():\n    return 1\n");
    update_base(tmp_b.path(), &mut store_b, &IndexOptions::default()).unwrap();

    let mut names_a: Vec<String> = store_a
        .all_symbols()
        .unwrap()
        .iter()
        .map(|s| format!("{}#{}", s.file, s.qualified_name))
        .collect();
    let mut names_b: Vec<String> = store_b
        .all_symbols()
        .unwrap()
        .iter()
        .map(|s| format!("{}#{}", s.file, s.qualified_name))
        .collect();
    names_a.sort();
    names_b.sort();
    assert_eq!(names_a, names_b);
}
