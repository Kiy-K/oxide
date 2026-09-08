//! Issue #5: crash-safe embedding-provider migration, plus the compatibility
//! gaps found alongside it.
//!
//! The reported sequence: index under provider A, switch to provider B, and
//! get killed after B's replacement vectors commit but before the closing
//! `set_meta_all` publishes B's identity. Every observable signal then said
//! "healthy" — row counts matched, metadata named A — while the rows were
//! B's. A same-dimension switch is the dangerous shape: nothing about the
//! vectors' width gives the mismatch away, so retrieval scored queries
//! against the wrong space instead of failing.
//!
//! These tests reach that state through the real code path rather than by
//! hand-writing meta rows: a SQLite trigger aborts the publishing write, so
//! `update_embeddings` fails exactly where a `kill -9` would land.

use oxide::embeddings::{EmbeddingProvider, EmbeddingSpaceFingerprint, HashedEmbedder};
use oxide::index::{
    embed_text, update_embeddings, update_index, IndexBackend, IndexOptions, IndexReport,
    SqliteStore, EMBEDDING_MIGRATION_KEY,
};
use oxide::retrieval::{RetrievalMode, SearchMode};
use oxide::service::{RepositoryService, SearchRequest};
use std::path::{Path, PathBuf};

/// Pin the offline hashed embedder, and clear any endpoint override, for
/// this whole test binary.
///
/// `open_embedder`'s default is a real ONNX model (`DEFAULT_NATIVE_PROFILE`)
/// that downloads weights and needs network; these tests are about migration
/// bookkeeping, not embedding quality. `OXIDE_EMBED_URL` is removed as well
/// because it outranks the native profile *and* switches `update_embeddings`
/// onto its batched path — one test here is specifically about the threaded
/// path, which only runs when no endpoint is configured.
///
/// Safe despite both variables being process-global: the writes happen
/// exactly once inside a `Once`, and every test calls this as its first
/// statement, so `Once` blocks every other test thread until they complete.
/// No `getenv` in this process can run concurrently with them, and the values
/// never change afterwards.
fn pin_offline_embedder() {
    static PIN: std::sync::Once = std::sync::Once::new();
    PIN.call_once(|| {
        // SAFETY: the only writes to these variables in this process,
        // serialized by `Once` ahead of every read. See the doc comment.
        unsafe {
            std::env::set_var("OXIDE_EMBED_NATIVE", "hashed");
            std::env::remove_var("OXIDE_EMBED_URL");
        }
    });
}

fn write(path: &Path, content: &str) {
    std::fs::create_dir_all(path.parent().unwrap()).unwrap();
    std::fs::write(path, content).unwrap();
}

fn index_path(root: &Path) -> PathBuf {
    root.join(".oxide").join("index.db")
}

fn search_request(mode: SearchMode) -> SearchRequest {
    SearchRequest {
        limit: 5,
        mode,
        expand: false,
        retrieval_mode: RetrievalMode::default(),
    }
}

/// A second embedding space at a caller-chosen width.
///
/// At 256 it is the dangerous same-dimension case: a distinct vector space
/// wearing the same shape as `HashedEmbedder::default()`, so no length check
/// anywhere in retrieval can tell the two apart. The transform only has to
/// move vectors somewhere else in the space, not be a good embedder.
struct RotatedEmbedder {
    dim: usize,
}

impl EmbeddingProvider for RotatedEmbedder {
    fn name(&self) -> &str {
        "rotated-bow"
    }
    fn dim(&self) -> usize {
        self.dim
    }
    fn embed(&self, text: &str) -> Vec<f32> {
        let mut v = HashedEmbedder::new(self.dim).embed(text);
        v.rotate_left(1);
        v
    }
}

/// A provider that always fails, the way `HttpEmbedder` reports an
/// unreachable endpoint: empty vectors rather than an error.
struct FailingEmbedder;

impl EmbeddingProvider for FailingEmbedder {
    fn name(&self) -> &str {
        "always-fails"
    }
    fn dim(&self) -> usize {
        256
    }
    fn embed(&self, _text: &str) -> Vec<f32> {
        Vec::new()
    }
    fn is_available(&self) -> bool {
        false
    }
}

/// Make the closing identity publication fail, exactly where an interrupted
/// process would stop. `set_meta_all` writes `embedding_fingerprint` in the
/// same transaction as the rest, so aborting on it rolls the whole
/// publication back — which is the point: the vectors are already committed
/// and durable, the identity describing them is not.
fn block_identity_publication(root: &Path) {
    let conn = rusqlite::Connection::open(index_path(root)).unwrap();
    conn.execute_batch(
        "CREATE TRIGGER block_publish_insert BEFORE INSERT ON meta
             WHEN NEW.key = 'embedding_fingerprint'
             BEGIN SELECT RAISE(ABORT, 'simulated interruption'); END;
         CREATE TRIGGER block_publish_update BEFORE UPDATE ON meta
             WHEN NEW.key = 'embedding_fingerprint'
             BEGIN SELECT RAISE(ABORT, 'simulated interruption'); END;",
    )
    .unwrap();
}

fn unblock_identity_publication(root: &Path) {
    let conn = rusqlite::Connection::open(index_path(root)).unwrap();
    conn.execute_batch("DROP TRIGGER block_publish_insert; DROP TRIGGER block_publish_update;")
        .unwrap();
}

fn set_meta_directly(root: &Path, key: &str, value: &str) {
    let conn = rusqlite::Connection::open(index_path(root)).unwrap();
    conn.execute(
        "INSERT INTO meta(key,value) VALUES(?1,?2) ON CONFLICT(key) DO UPDATE SET value=?2",
        [key, value],
    )
    .unwrap();
}

fn delete_meta_directly(root: &Path, key: &str) {
    let conn = rusqlite::Connection::open(index_path(root)).unwrap();
    conn.execute("DELETE FROM meta WHERE key = ?1", [key])
        .unwrap();
}

fn sample_repo(root: &Path) {
    write(
        &root.join("src/auth.py"),
        "class AuthService:\n    def refresh_token(self, token):\n        return validate_refresh_token(token)\n\ndef validate_refresh_token(token):\n    return token\n",
    );
}

/// Index `root` under `HashedEmbedder::default()` and publish it cleanly.
fn index_under_hashed(root: &Path) {
    sample_repo(root);
    let mut store = SqliteStore::open(&index_path(root)).unwrap();
    update_index(root, &mut store, &HashedEmbedder::default()).unwrap();
}

/// Take a cleanly-published hashed index into the interrupted-migration
/// state: replacement vectors from `next` are committed, its identity is not.
fn interrupt_migration_to(root: &Path, next: &dyn EmbeddingProvider) {
    block_identity_publication(root);
    let mut store = SqliteStore::open(&index_path(root)).unwrap();
    let mut report = IndexReport::default();
    let err = update_embeddings(
        root,
        &mut store,
        next,
        &IndexOptions::default(),
        &mut report,
    )
    .expect_err("the trigger must abort the closing identity write");
    assert!(
        err.to_string().contains("simulated interruption")
            || err
                .chain()
                .any(|c| c.to_string().contains("simulated interruption")),
        "expected the injected abort, got: {err:#}"
    );
    drop(store);
    unblock_identity_publication(root);
}

fn migration_marker(root: &Path) -> Option<String> {
    let store = SqliteStore::open_read_only(&index_path(root)).unwrap();
    store
        .get_meta(EMBEDDING_MIGRATION_KEY)
        .unwrap()
        .filter(|s| !s.is_empty())
}

/// The interrupted state is exactly the one the issue describes: every
/// replacement vector committed and belonging to `next`, and the published
/// identity still naming the provider that no longer owns them.
///
/// Asserting the *vectors* is the load-bearing part. Row counts and stale
/// metadata alone would also hold if `begin_embedding_migration` had never
/// cleared anything and the old provider's rows had simply survived — which
/// is a different (and much less dangerous) state than the one under test.
fn assert_interrupted_state_reached(root: &Path, next: &dyn EmbeddingProvider) {
    let store = SqliteStore::open_read_only(&index_path(root)).unwrap();
    let stats = store.stats().unwrap();
    assert!(stats.symbols > 0);
    assert_eq!(
        stats.embeddings, stats.symbols,
        "the row-count completeness check must still pass — that is what made this silent"
    );
    assert_eq!(
        store.get_meta("embedder").unwrap().as_deref(),
        Some("hashed-bow-256"),
        "the published identity must still name the pre-migration provider"
    );
    assert_eq!(
        store.get_meta("embedding_fingerprint").unwrap(),
        Some(serde_json::to_string(&HashedEmbedder::default().fingerprint()).unwrap()),
        "the published fingerprint must still be the pre-migration provider's"
    );
    let stored = store.all_embeddings().unwrap();
    for s in store.all_symbols().unwrap() {
        let (_, vec) = stored.get(&s.id()).expect("every symbol has a row");
        assert_eq!(
            vec,
            &next.embed_document(&embed_text(&s)),
            "{} does not hold the incoming provider's vector — the state under \
             test was never reached",
            s.qualified_name
        );
    }
}

fn assert_semantic_search_rejected(root: &Path) {
    let service = RepositoryService::discover(Some(root.to_str().unwrap())).unwrap();
    let err = service
        .search("refresh token", search_request(SearchMode::Hybrid))
        .expect_err("semantic search must refuse an index left mid-migration");
    assert_eq!(err.code(), "index_stale", "{err:?}");
    assert!(
        err.message().contains("oxide index"),
        "the error must name the fix: {}",
        err.message()
    );

    // Lexical-only never consults the vector space, so the read-only service
    // contract is preserved: a mid-migration index stays usable, degraded.
    let hits = service
        .search("refresh token", search_request(SearchMode::LexicalOnly))
        .expect("lexical search must stay available");
    assert!(!hits.is_empty());
}

#[test]
fn same_dimension_interrupted_migration_is_not_served_as_healthy() {
    pin_offline_embedder();
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path();
    index_under_hashed(root);

    let rotated = RotatedEmbedder { dim: 256 };
    interrupt_migration_to(root, &rotated);

    assert_interrupted_state_reached(root, &rotated);
    assert!(
        migration_marker(root).is_some(),
        "the in-flight marker is what makes the torn state detectable"
    );
    assert_semantic_search_rejected(root);

    let service = RepositoryService::discover(Some(root.to_str().unwrap())).unwrap();
    let status = service.status().unwrap();
    assert!(!status.is_current, "{status:?}");
    assert!(!status.embedder_current, "{status:?}");
}

#[test]
fn different_dimension_interrupted_migration_is_not_served_as_healthy() {
    pin_offline_embedder();
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path();
    index_under_hashed(root);

    // Retrieval already ignores length-mismatched vectors, so this case was
    // never actively wrong — but it silently answered from lexical signal
    // alone while reporting a healthy hybrid index. It must fail loudly too.
    let rotated = RotatedEmbedder { dim: 384 };
    interrupt_migration_to(root, &rotated);

    assert_interrupted_state_reached(root, &rotated);
    assert!(migration_marker(root).is_some());
    assert_semantic_search_rejected(root);
}

#[test]
fn reverting_to_the_previous_provider_re_embeds_instead_of_reusing() {
    pin_offline_embedder();
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path();
    index_under_hashed(root);
    interrupt_migration_to(root, &RotatedEmbedder { dim: 256 });

    // The reported opt-out: give up on the new provider and go back. Content
    // hashes are unchanged, so nothing content-based would trigger a rebuild
    // — only the marker knows the rows are the wrong provider's.
    let hashed = HashedEmbedder::default();
    let mut store = SqliteStore::open(&index_path(root)).unwrap();
    let mut report = IndexReport::default();
    update_embeddings(
        root,
        &mut store,
        &hashed,
        &IndexOptions::default(),
        &mut report,
    )
    .unwrap();
    assert_eq!(
        report.reused_embeddings, 0,
        "no vector from the abandoned provider may be reused"
    );
    assert!(report.embedded_symbols > 0);

    // Sharpest available check: every stored vector must equal what the
    // restored provider computes right now, not the rotation of it.
    let symbols = store.all_symbols().unwrap();
    let stored = store.all_embeddings().unwrap();
    for s in &symbols {
        let (_, vec) = stored.get(&s.id()).expect("every symbol re-embedded");
        assert_eq!(
            vec,
            &hashed.embed_document(&embed_text(s)),
            "{} still holds the abandoned provider's vector",
            s.qualified_name
        );
    }
    drop(store);

    assert!(migration_marker(root).is_none(), "marker must be retired");
    let service = RepositoryService::discover(Some(root.to_str().unwrap())).unwrap();
    assert!(service
        .search("refresh token", search_request(SearchMode::Hybrid))
        .is_ok());
    assert!(service.status().unwrap().is_current);
}

#[test]
fn continuing_with_the_new_provider_resumes_instead_of_re_embedding() {
    pin_offline_embedder();
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path();
    index_under_hashed(root);
    let rotated = RotatedEmbedder { dim: 256 };
    interrupt_migration_to(root, &rotated);

    // The other half of recovery: the marker proves the committed rows are
    // already this provider's, so a resumed run must publish and reuse them
    // rather than pay for a full re-embed.
    let mut store = SqliteStore::open(&index_path(root)).unwrap();
    let mut report = IndexReport::default();
    update_embeddings(
        root,
        &mut store,
        &rotated,
        &IndexOptions::default(),
        &mut report,
    )
    .unwrap();
    assert_eq!(
        report.embedded_symbols, 0,
        "nothing should need recomputing"
    );
    assert!(report.reused_embeddings > 0);

    let published: EmbeddingSpaceFingerprint =
        serde_json::from_str(&store.get_meta("embedding_fingerprint").unwrap().unwrap()).unwrap();
    assert_eq!(published, rotated.fingerprint());
    drop(store);
    assert!(migration_marker(root).is_none());
}

#[test]
fn unreadable_stored_fingerprint_does_not_fail_open_to_the_name_check() {
    pin_offline_embedder();
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path();
    index_under_hashed(root);

    // Present but corrupt. `embedder`/`dim` still name the current provider,
    // so the legacy fallback would happily approve this index — which is the
    // fingerprint contract failing open into the check it exists to replace.
    set_meta_directly(root, "embedding_fingerprint", "{not json");

    let service = RepositoryService::discover(Some(root.to_str().unwrap())).unwrap();
    let err = service
        .search("refresh token", search_request(SearchMode::Hybrid))
        .expect_err("an unreadable fingerprint must never be waved through");
    assert_eq!(err.code(), "provider_mismatch", "{err:?}");
}

#[test]
fn legacy_index_dimension_change_under_one_provider_name_clears_vectors() {
    pin_offline_embedder();
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path();
    sample_repo(root);

    let mut store = SqliteStore::open(&index_path(root)).unwrap();
    update_index(root, &mut store, &HashedEmbedder::new(128)).unwrap();
    drop(store);

    // Pre-fingerprint index shape: identity is the `embedder` name alone.
    // `HashedEmbedder` reports "hashed-bow-256" at every width, so the name
    // is unchanged across a 128 -> 256 switch and only `dim` gives it away.
    delete_meta_directly(root, "embedding_fingerprint");

    let mut store = SqliteStore::open(&index_path(root)).unwrap();
    let mut report = IndexReport::default();
    update_embeddings(
        root,
        &mut store,
        &HashedEmbedder::new(256),
        &IndexOptions::default(),
        &mut report,
    )
    .unwrap();
    assert_eq!(report.reused_embeddings, 0);
    for (_, (_, vec)) in store.all_embeddings().unwrap() {
        assert_eq!(vec.len(), 256, "a 128-wide row survived the widening");
    }
}

#[test]
fn threaded_embedding_path_counts_empty_vectors_as_failures() {
    pin_offline_embedder();
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path();
    // Over the 8-symbol threshold that selects the threaded path, which used
    // to skip the empty/all-zero rejection the batched path applies.
    let body: String = (0..12)
        .map(|i| format!("def handler_{i}(request):\n    return request\n\n"))
        .collect();
    write(&root.join("src/handlers.py"), &body);

    let mut store = SqliteStore::open(&index_path(root)).unwrap();
    let mut report = IndexReport::default();
    update_index(root, &mut store, &FailingEmbedder).unwrap();
    update_embeddings(
        root,
        &mut store,
        &FailingEmbedder,
        &IndexOptions::default(),
        &mut report,
    )
    .unwrap();

    assert!(
        report.embed_failures >= 8,
        "every empty vector must be counted as a failure, got {report:?}"
    );
    assert_eq!(report.embedded_symbols, 0, "{report:?}");
    assert_eq!(
        store.stats().unwrap().embeddings,
        0,
        "an empty vector must never be stored: it satisfies the completeness \
         check while carrying no signal"
    );
}

#[test]
fn embedder_initialization_failure_leaves_an_existing_index_byte_identical() {
    pin_offline_embedder();
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path();
    index_under_hashed(root);

    let before = std::fs::read(index_path(root)).unwrap();
    let service = RepositoryService::discover(Some(root.to_str().unwrap())).unwrap();
    // Port 1 refuses connections, so `HttpEmbedder::new`'s dimension probe
    // fails and provider construction errors out. `index_staged` builds the
    // provider before opening the writer specifically so a failure here
    // cannot touch the index; this pins that ordering.
    let err = service
        .index_staged(
            Some("http://127.0.0.1:1/v1/embeddings"),
            &IndexOptions::default(),
            |_| {},
        )
        .expect_err("an unreachable endpoint must fail the run");
    assert_eq!(err.code(), "embedder_unavailable", "{err:?}");
    assert_eq!(
        std::fs::read(index_path(root)).unwrap(),
        before,
        "a failed provider handshake must not touch the existing index"
    );
}

/// Provider precedence, checked in genuinely isolated environments.
///
/// `open_embedder` and `configured_provider_name` read process-global env
/// vars, so an in-process test can only ever check one arrangement of them
/// per binary. Subprocesses give each arrangement its own environment. Every
/// probe below is network-free: `oxide status` compares the *name* the
/// current configuration resolves to against the one recorded in the index,
/// and never constructs a live provider.
#[test]
fn provider_precedence_resolves_the_same_way_the_indexer_does() {
    pin_offline_embedder();
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path();
    index_under_hashed(root);

    let status = |env: &[(&str, &str)], unset: &[&str]| -> serde_json::Value {
        let mut cmd = std::process::Command::new(env!("CARGO_BIN_EXE_oxide"));
        cmd.args(["status", "--json"]).current_dir(root);
        for key in unset {
            cmd.env_remove(key);
        }
        for (key, value) in env {
            cmd.env(key, value);
        }
        let out = cmd.output().unwrap();
        assert!(
            out.status.success(),
            "stderr: {}",
            String::from_utf8_lossy(&out.stderr)
        );
        serde_json::from_slice(&out.stdout).unwrap()
    };

    // Baseline: the offline profile with no endpoint set resolves to the
    // provider that built this index.
    let offline = status(
        &[("OXIDE_EMBED_NATIVE", "hashed")],
        &["OXIDE_EMBED_URL", "OXIDE_EMBED_MODEL"],
    );
    assert_eq!(offline["embedder"], "hashed-bow-256");
    assert_eq!(offline["embedder_current"], true, "{offline}");

    // An endpoint outranks the offline profile. This is the nuance the docs
    // now spell out: `OXIDE_EMBED_NATIVE=hashed` picks a provider, it does
    // not forbid network — so it does *not* make a run offline on its own.
    let with_url = status(
        &[
            ("OXIDE_EMBED_NATIVE", "hashed"),
            ("OXIDE_EMBED_URL", "http://127.0.0.1:1/v1/embeddings"),
            ("OXIDE_EMBED_MODEL", "some-model"),
        ],
        &[],
    );
    assert_eq!(
        with_url["embedder_current"], false,
        "a configured endpoint must win over the offline profile: {with_url}"
    );

    // Unconfigured resolves to the native default, not to hashed — the whole
    // point of the Arctic rollout, and still true without any model present
    // because this comparison is by name.
    #[cfg(feature = "native-embed")]
    {
        let unconfigured = status(
            &[],
            &["OXIDE_EMBED_NATIVE", "OXIDE_EMBED_URL", "OXIDE_EMBED_MODEL"],
        );
        assert_eq!(
            unconfigured["embedder_current"], false,
            "the unconfigured default must not resolve to the hashed embedder: {unconfigured}"
        );
    }
}

/// End-to-end under the *real* default provider: acquire `arctic-embed-xs-q`
/// and index, search and report status with it.
///
/// Opt-in and excluded from the required suite on purpose. It downloads
/// ~23MB on a cold cache and needs network — exactly the behaviour the rest
/// of this file pins the hashed embedder to avoid, and exactly why the
/// resolver assertions above (which are by name, never by loading a model)
/// cannot stand in for it. Run it deliberately:
///
/// ```sh
/// cargo test -j 2 --test provider_migration_recovery -- --ignored --nocapture
/// ```
///
/// A second run against a warm cache is the offline/cached check: it must
/// reuse the stored vectors and do no work.
#[test]
#[ignore = "downloads real model weights; run deliberately with --ignored"]
#[cfg(feature = "native-embed")]
fn real_default_provider_indexes_searches_and_stays_current() {
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path();
    sample_repo(root);

    let run = |args: &[&str]| -> serde_json::Value {
        let out = std::process::Command::new(env!("CARGO_BIN_EXE_oxide"))
            .args(args)
            .current_dir(root)
            .env_remove("OXIDE_EMBED_NATIVE")
            .env_remove("OXIDE_EMBED_URL")
            .env_remove("OXIDE_EMBED_MODEL")
            .output()
            .unwrap();
        assert!(
            out.status.success(),
            "`oxide {}` failed: {}",
            args.join(" "),
            String::from_utf8_lossy(&out.stderr)
        );
        serde_json::from_slice(&out.stdout).unwrap()
    };

    let indexed = run(&["index", ".", "--json"]);
    assert!(
        indexed["embedded_symbols"].as_u64().unwrap() > 0,
        "{indexed}"
    );
    assert_eq!(indexed["embed_failures"], 0, "{indexed}");

    let status = run(&["status", "--json"]);
    assert_eq!(status["is_current"], true, "{status}");
    assert_eq!(status["pending_embeddings"], 0, "{status}");

    let hits = run(&["search", "refresh token", "--json"]);
    assert!(
        hits.as_array().is_some_and(|h| !h.is_empty()),
        "semantic search returned nothing: {hits}"
    );

    // Warm cache: nothing to recompute, so this is also the cached-model path.
    let again = run(&["index", ".", "--json"]);
    assert_eq!(again["embedded_symbols"], 0, "{again}");
    assert!(again["reused_embeddings"].as_u64().unwrap() > 0, "{again}");
}

/// A second indexer that starts its own migration mid-run must not be able
/// to leave a mixed table behind a single published identity.
///
/// `oxide index` can run from any process at any time and its embedding loop
/// takes minutes, so a second run genuinely can take the table over between
/// the first run's compatibility check and its next batch write. Before the
/// guard, the loser kept appending its vectors into the table the winner had
/// just emptied, and whichever finished last published one identity over the
/// mix — the same silent corruption as the interrupted case, with no
/// interruption. The window is inside `update_embeddings`'s loop and cannot
/// be reached by calling it again from outside (a fresh call re-checks
/// compatibility and legitimately re-migrates), so this pins the two store
/// primitives that loop uses.
#[test]
fn a_concurrent_migration_makes_the_losing_indexer_fail_loudly() {
    pin_offline_embedder();
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path();
    index_under_hashed(root);

    let mine = RotatedEmbedder { dim: 256 };
    let theirs = RotatedEmbedder { dim: 384 };
    let my_space = serde_json::to_string(&mine.fingerprint()).unwrap();
    let their_space = serde_json::to_string(&theirs.fingerprint()).unwrap();

    // My run claims the table...
    let mut store = SqliteStore::open(&index_path(root)).unwrap();
    store.begin_embedding_migration(&my_space).unwrap();
    let symbols = store.all_symbols().unwrap();
    let ids: Vec<u64> = symbols.iter().map(|s| s.id()).collect();

    // ...and one batch lands while it is still mine.
    store
        .put_embeddings_batch(&my_space, &[(ids[0], mine.embed_document("x"))])
        .unwrap();

    // ...then another process takes it over.
    store.begin_embedding_migration(&their_space).unwrap();

    // Every remaining write of mine must now fail, so the table can only
    // ever hold the winner's rows.
    let err = store
        .put_embeddings_batch(&my_space, &[(ids[0], mine.embed_document("y"))])
        .expect_err("a write into someone else's migration must fail");
    assert!(
        err.to_string()
            .contains("took over this index's embeddings"),
        "{err:#}"
    );

    // And so must my identity publication, which would otherwise stamp my
    // provider onto the winner's vectors and retire their marker.
    let err = store
        .set_meta_all(&my_space, &[("embedder", mine.name())])
        .expect_err("publishing over someone else's migration must fail");
    assert!(
        err.to_string()
            .contains("took over this index's embeddings"),
        "{err:#}"
    );

    assert_eq!(
        store.get_meta("embedder").unwrap().as_deref(),
        Some("hashed-bow-256"),
        "nothing of the losing run may have landed"
    );
    assert_eq!(migration_marker(root), Some(their_space));
}
