//! The research overrides' parsing edges, pinned before #34 S4 moved their
//! environment reads out of the fusion and allocator bodies:
//! `$OXIDE_TERM_COVERAGE_ALPHA` values that do not parse to a positive
//! `f32` are exactly the unset no-op, and `$OXIDE_CONTEXT_MAX_PRIMARIES`
//! takes any `usize` (including 0 and caps below the shipped 5) while
//! anything else is the shipped default. Both are read per request, so a
//! change between two requests on the same engine takes effect.
//!
//! One test in this binary on purpose: the variables are process-global.

use oxide::context::{build_context, ContextOptions, Role};
use oxide::embeddings::HashedEmbedder;
use oxide::index::{update_index, SqliteStore};
use oxide::retrieval::{RetrievalEngine, RetrievalMode, SearchMode, SearchOptions};
use std::path::Path;

fn copy_dir(from: &Path, to: &Path) {
    std::fs::create_dir_all(to).unwrap();
    for e in std::fs::read_dir(from).unwrap() {
        let e = e.unwrap();
        let dst = to.join(e.file_name());
        if e.file_type().unwrap().is_dir() {
            copy_dir(&e.path(), &dst);
        } else {
            std::fs::copy(e.path(), dst).unwrap();
        }
    }
}

fn with_env<T>(key: &str, value: Option<&str>, f: impl FnOnce() -> T) -> T {
    match value {
        Some(v) => unsafe { std::env::set_var(key, v) },
        None => unsafe { std::env::remove_var(key) },
    }
    let out = f();
    unsafe { std::env::remove_var(key) };
    out
}

#[test]
fn research_override_parsing_edges_are_unchanged() {
    let tmp = tempfile::tempdir().unwrap();
    copy_dir(
        &Path::new(env!("CARGO_MANIFEST_DIR")).join("fixtures/py_repo"),
        tmp.path(),
    );
    let emb = HashedEmbedder::default();
    let mut store = SqliteStore::open(&tmp.path().join(".oxide/index.db")).unwrap();
    update_index(tmp.path(), &mut store, &emb).unwrap();

    // One engine across every value: the alpha is read per search.
    let engine = RetrievalEngine::new(&store, &emb);
    let opts = SearchOptions {
        limit: 20,
        mode: SearchMode::Hybrid,
        expand: true,
        retrieval_mode: RetrievalMode::Balanced,
    };
    let search = |alpha: Option<&str>| {
        with_env("OXIDE_TERM_COVERAGE_ALPHA", alpha, || {
            serde_json::to_string(&engine.search("retry backoff policy", &opts).unwrap()).unwrap()
        })
    };
    let unset = search(None);
    for noop in ["", "abc", "0", "-1", "-0.5", "NaN"] {
        assert_eq!(
            search(Some(noop)),
            unset,
            "alpha {noop:?} must be the unset no-op"
        );
    }
    assert_ne!(
        search(Some("0.5")),
        unset,
        "a positive alpha applies the bonus"
    );

    let primaries = |cap: Option<&str>| {
        with_env("OXIDE_CONTEXT_MAX_PRIMARIES", cap, || {
            let opts = ContextOptions {
                budget_tokens: 100_000,
                ..ContextOptions::default()
            };
            build_context(
                tmp.path(),
                &store,
                &emb,
                "retry backoff policy auth token cache",
                &opts,
            )
            .unwrap()
            .items
            .iter()
            .filter(|i| i.role == Role::Primary)
            .count()
        })
    };
    let default = primaries(None);
    assert_eq!(default, 5, "shipped cap");
    for fallback in ["", "-1", "2.5", "five"] {
        assert_eq!(
            primaries(Some(fallback)),
            default,
            "cap {fallback:?} falls back"
        );
    }
    assert_eq!(primaries(Some("0")), 0);
    assert_eq!(primaries(Some("2")), 2);
    // Above the fixture's primary supply (after floor and per-file caps):
    // raising the cap changes nothing here.
    assert_eq!(primaries(Some("9")), default);
}
