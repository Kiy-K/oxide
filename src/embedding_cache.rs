//! Shared, content-addressed embedding cache + fail-closed commit
//! verification for the term-coverage-eval harness's commit-keyed
//! worktrees (docs/term-coverage-eval/README.md's harness-reuse
//! follow-up). See `examples/term_coverage_index.rs` for the CLI that
//! wires this in.
//!
//! Each commit-keyed worktree's own `.oxide/index.db` stays fully
//! commit-exclusive — its symbols, structural relations, and embeddings
//! table are never shared with any other commit's worktree. What IS
//! shared, in a *separate* cache database, is the embedding vector for a
//! given piece of text under a given embedding provider — keyed by
//! `(embedding_fingerprint, content_hash(text))`, the same content-hash
//! invalidation `update_index` already uses within one index.db, just
//! extended across index.db boundaries. Nothing about symbol or
//! structural-relation state ever crosses this boundary: the cache is
//! never read from or written to by anything except the embedding call
//! path itself, so it cannot leak a stale symbol or relation by
//! construction — only a content-addressed vector that a different commit
//! happens to also want, under the exact provider that produced it.

use crate::embeddings::{EmbeddingProvider, EmbeddingSpaceFingerprint};
use crate::symbols::content_hash;
use anyhow::{Context, Result};
use rusqlite::Connection;
use std::path::Path;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Mutex;

/// Fails closed: errors unless `repo_dir`'s checked-out HEAD is EXACTLY
/// `expected_commit`. Defense in depth on top of the Python harness's own
/// checkout verification (`ensure_repo_checkout`, `term_coverage_eval.py::
/// sweep`), at the point that actually writes index state.
pub fn verify_commit(repo_dir: &Path, expected_commit: &str) -> Result<()> {
    let out = std::process::Command::new("git")
        .args(["rev-parse", "HEAD"])
        .current_dir(repo_dir)
        .output()
        .with_context(|| format!("git rev-parse HEAD in {}", repo_dir.display()))?;
    if !out.status.success() {
        anyhow::bail!(
            "git rev-parse HEAD failed in {}: {}",
            repo_dir.display(),
            String::from_utf8_lossy(&out.stderr)
        );
    }
    let head = String::from_utf8_lossy(&out.stdout).trim().to_string();
    if head != expected_commit {
        anyhow::bail!(
            "commit verification failed: {} is at {head:?}, expected {expected_commit:?} — \
             refusing to proceed against the wrong commit",
            repo_dir.display()
        );
    }
    Ok(())
}

pub struct SharedEmbeddingCache {
    inner: Box<dyn EmbeddingProvider>,
    conn: Mutex<Connection>,
    fingerprint_key: String,
    hits: AtomicUsize,
    misses: AtomicUsize,
}

impl SharedEmbeddingCache {
    pub fn open(inner: Box<dyn EmbeddingProvider>, cache_db_path: &Path) -> Result<Self> {
        if let Some(parent) = cache_db_path.parent() {
            std::fs::create_dir_all(parent)?;
        }
        let conn = Connection::open(cache_db_path)?;
        conn.busy_timeout(std::time::Duration::from_secs(5))?;
        conn.execute_batch(
            "PRAGMA journal_mode=WAL;
             CREATE TABLE IF NOT EXISTS embedding_cache (
               fingerprint TEXT NOT NULL,
               content_hash INTEGER NOT NULL,
               dim INTEGER NOT NULL,
               vec BLOB NOT NULL,
               PRIMARY KEY (fingerprint, content_hash)
             );",
        )?;
        // Namespacing by the provider's own fingerprint (not just `name()`)
        // is what satisfies "incompatible embedding fingerprints never
        // reuse vectors": a same-labeled endpoint that started returning a
        // different dimension or pooling strategy gets a different
        // `fingerprint()` and therefore a disjoint cache namespace — the
        // same fingerprint-first contract `RepositoryService::
        // validate_index` and `term_coverage_sweep.rs`'s provenance gate
        // already use.
        let fingerprint_key = serde_json::to_string(&inner.fingerprint())?;
        Ok(Self {
            inner,
            conn: Mutex::new(conn),
            fingerprint_key,
            hits: AtomicUsize::new(0),
            misses: AtomicUsize::new(0),
        })
    }

    pub fn hits(&self) -> usize {
        self.hits.load(Ordering::Relaxed)
    }
    pub fn misses(&self) -> usize {
        self.misses.load(Ordering::Relaxed)
    }

    fn get(&self, ch: u64) -> Option<Vec<f32>> {
        let conn = self.conn.lock().unwrap();
        let mut stmt = conn
            .prepare_cached(
                "SELECT dim, vec FROM embedding_cache WHERE fingerprint = ?1 AND content_hash = ?2",
            )
            .ok()?;
        let row: Option<(i32, Vec<u8>)> = stmt
            .query_row(rusqlite::params![self.fingerprint_key, ch as i64], |r| {
                Ok((r.get(0)?, r.get(1)?))
            })
            .ok();
        let (dim, bytes) = row?;
        // Defense in depth alongside `put`'s own dimension check: a row
        // whose stored `dim` doesn't match this provider's current
        // dimension can only exist from a schema/data anomaly (manual
        // tampering, a cache file shared across an incompatible OXIDE
        // version) since `put` never writes a mismatched one — but
        // serving it anyway would silently hand `RetrievalEngine::search`
        // a malformed vector under a provider identity that claims it's
        // fine. Treat it as a miss instead.
        if dim as usize != self.inner.dim() {
            return None;
        }
        Some(
            bytes
                .as_chunks::<4>()
                .0
                .iter()
                .map(|c| f32::from_le_bytes(*c))
                .take(dim as usize)
                .collect(),
        )
    }

    fn put(&self, ch: u64, v: &[f32]) {
        // A failure/empty vector, or one whose length doesn't match this
        // provider's own declared dimension, must never be cached. Empty
        // is `HttpEmbedder`'s documented transient-failure signal
        // (`CachingEmbedder`, examples/term_coverage_sweep.rs, applies the
        // same rule to query embeddings) — caching it would silently
        // poison every future commit that happens to share this content,
        // long after the provider recovers. A non-empty but wrong-length
        // vector is a different failure shape `HttpEmbedder` doesn't
        // itself guard against (a malformed/truncated HTTP response is
        // not required to come back empty) — caching *that* would let a
        // transient bad response persist as this content's permanent
        // embedding across every future commit that reuses it, silently
        // dropped from semantic search wherever `RetrievalEngine::search`'s
        // own length check (`v.len() != qv.len()`) then skips it, with no
        // path back to a healthy vector once the provider recovers, since
        // nothing else would ever invalidate this cache entry.
        if v.is_empty() || v.len() != self.inner.dim() {
            return;
        }
        let bytes: Vec<u8> = v.iter().flat_map(|f| f.to_le_bytes()).collect();
        let conn = self.conn.lock().unwrap();
        let _ = conn.execute(
            "INSERT OR REPLACE INTO embedding_cache(fingerprint, content_hash, dim, vec) \
             VALUES(?1,?2,?3,?4)",
            rusqlite::params![self.fingerprint_key, ch as i64, v.len() as i32, bytes],
        );
    }
}

impl EmbeddingProvider for SharedEmbeddingCache {
    fn name(&self) -> &str {
        self.inner.name()
    }
    fn dim(&self) -> usize {
        self.inner.dim()
    }
    fn embed(&self, text: &str) -> Vec<f32> {
        self.inner.embed(text)
    }
    fn is_available(&self) -> bool {
        self.inner.is_available()
    }
    fn embed_query(&self, text: &str) -> Vec<f32> {
        // Deliberately NOT cached: this wrapper only caches the indexing
        // path (`embed_document`/`embed_documents`, what `update_index`
        // calls) — the same document/query split `term_coverage_sweep.rs`'s
        // `CachingEmbedder` already documents, a different cache key space
        // this wrapper does not touch.
        self.inner.embed_query(text)
    }
    fn fingerprint(&self) -> EmbeddingSpaceFingerprint {
        self.inner.fingerprint()
    }

    fn embed_document(&self, text: &str) -> Vec<f32> {
        let ch = content_hash(text);
        if let Some(v) = self.get(ch) {
            self.hits.fetch_add(1, Ordering::Relaxed);
            return v;
        }
        self.misses.fetch_add(1, Ordering::Relaxed);
        let v = self.inner.embed_document(text);
        self.put(ch, &v);
        v
    }

    fn embed_documents(&self, texts: &[String]) -> Vec<Vec<f32>> {
        let mut out: Vec<Option<Vec<f32>>> = Vec::with_capacity(texts.len());
        let mut miss_idx = Vec::new();
        let mut miss_texts = Vec::new();
        for (i, t) in texts.iter().enumerate() {
            let ch = content_hash(t);
            if let Some(v) = self.get(ch) {
                self.hits.fetch_add(1, Ordering::Relaxed);
                out.push(Some(v));
            } else {
                out.push(None);
                miss_idx.push(i);
                miss_texts.push(t.clone());
            }
        }
        if !miss_texts.is_empty() {
            self.misses.fetch_add(miss_texts.len(), Ordering::Relaxed);
            let computed = self.inner.embed_documents(&miss_texts);
            for (j, v) in miss_idx.into_iter().zip(computed) {
                let ch = content_hash(&texts[j]);
                self.put(ch, &v);
                out[j] = Some(v);
            }
        }
        out.into_iter().map(|v| v.unwrap_or_default()).collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::embeddings::HashedEmbedder;
    use std::process::Command;
    use std::sync::atomic::AtomicUsize as StdAtomicUsize;

    fn git(dir: &Path, args: &[&str]) {
        let st = Command::new("git")
            .args(args)
            .current_dir(dir)
            .status()
            .unwrap();
        assert!(st.success(), "git {args:?}");
    }

    fn init_repo_at_head(dir: &Path) -> String {
        std::fs::write(dir.join("a.py"), "def one():\n    pass\n").unwrap();
        git(dir, &["init", "-q"]);
        git(
            dir,
            &["-c", "user.email=t@t", "-c", "user.name=t", "add", "."],
        );
        git(
            dir,
            &[
                "-c",
                "user.email=t@t",
                "-c",
                "user.name=t",
                "commit",
                "-qm",
                "init",
            ],
        );
        String::from_utf8(
            Command::new("git")
                .args(["rev-parse", "HEAD"])
                .current_dir(dir)
                .output()
                .unwrap()
                .stdout,
        )
        .unwrap()
        .trim()
        .to_string()
    }

    // ---- verify_commit: fails closed ----

    #[test]
    fn verify_commit_succeeds_when_head_matches() {
        let tmp = tempfile::tempdir().unwrap();
        let head = init_repo_at_head(tmp.path());
        assert!(verify_commit(tmp.path(), &head).is_ok());
    }

    #[test]
    fn verify_commit_fails_closed_on_wrong_commit() {
        let tmp = tempfile::tempdir().unwrap();
        let _head = init_repo_at_head(tmp.path());
        let err = verify_commit(tmp.path(), "0000000000000000000000000000000000dead")
            .expect_err("must refuse a HEAD that doesn't match");
        assert!(err.to_string().contains("commit verification failed"));
    }

    #[test]
    fn verify_commit_fails_closed_when_not_a_git_repo() {
        let tmp = tempfile::tempdir().unwrap();
        // No `git init` at all — not a repository.
        assert!(verify_commit(tmp.path(), "deadbeef").is_err());
    }

    // ---- SharedEmbeddingCache: a counting wrapper proves reuse is real,
    // not just "the same deterministic output happened twice" ----

    struct CountingEmbedder {
        inner: HashedEmbedder,
        calls: std::sync::Arc<StdAtomicUsize>,
    }
    impl CountingEmbedder {
        fn new() -> (Self, std::sync::Arc<StdAtomicUsize>) {
            let calls = std::sync::Arc::new(StdAtomicUsize::new(0));
            (
                Self {
                    inner: HashedEmbedder::default(),
                    calls: calls.clone(),
                },
                calls,
            )
        }
    }
    impl EmbeddingProvider for CountingEmbedder {
        fn name(&self) -> &str {
            "counting-test-embedder"
        }
        fn dim(&self) -> usize {
            self.inner.dim()
        }
        fn embed(&self, text: &str) -> Vec<f32> {
            self.calls.fetch_add(1, Ordering::Relaxed);
            self.inner.embed(text)
        }
    }

    #[test]
    fn identical_content_reuses_cached_embedding_without_a_second_provider_call() {
        let cache_db = tempfile::NamedTempFile::new().unwrap();
        let (embedder1, calls1) = CountingEmbedder::new();
        let cache1 = SharedEmbeddingCache::open(Box::new(embedder1), cache_db.path()).unwrap();
        let v1 = cache1.embed_document("def payment(): pass");
        assert_eq!(cache1.misses(), 1);
        assert_eq!(cache1.hits(), 0);
        assert_eq!(
            calls1.load(Ordering::Relaxed),
            1,
            "a real miss must call the provider"
        );

        // A brand-new cache instance (simulating a different commit's
        // worktree/process) pointed at the SAME cache db must reuse the
        // vector for identical text without calling its own provider.
        let (embedder2, calls2) = CountingEmbedder::new();
        let cache2 = SharedEmbeddingCache::open(Box::new(embedder2), cache_db.path()).unwrap();
        let v2 = cache2.embed_document("def payment(): pass");
        assert_eq!(
            cache2.hits(),
            1,
            "identical content must hit the shared cache"
        );
        assert_eq!(cache2.misses(), 0);
        assert_eq!(
            calls2.load(Ordering::Relaxed),
            0,
            "a cache hit must never call the underlying provider at all"
        );
        assert_eq!(
            v1, v2,
            "reused vector must be byte-identical to the original"
        );
    }

    #[test]
    fn changed_content_is_re_embedded_not_served_stale() {
        let cache_db = tempfile::NamedTempFile::new().unwrap();
        let (embedder1, _calls1) = CountingEmbedder::new();
        let cache1 = SharedEmbeddingCache::open(Box::new(embedder1), cache_db.path()).unwrap();
        cache1.embed_document("def payment(): pass");

        let (embedder2, _calls2) = CountingEmbedder::new();
        let cache2 = SharedEmbeddingCache::open(Box::new(embedder2), cache_db.path()).unwrap();
        // Different text -> different content_hash -> must NOT hit the
        // entry cached for the old content.
        cache2.embed_document("def payment(amount): return amount");
        assert_eq!(
            cache2.misses(),
            1,
            "changed content must be freshly embedded"
        );
        assert_eq!(
            cache2.hits(),
            0,
            "changed content must never reuse a stale vector"
        );
    }

    #[test]
    fn different_fingerprints_never_share_cached_vectors() {
        struct NamedEmbedder(&'static str, HashedEmbedder);
        impl EmbeddingProvider for NamedEmbedder {
            fn name(&self) -> &str {
                self.0
            }
            fn dim(&self) -> usize {
                self.1.dim()
            }
            fn embed(&self, text: &str) -> Vec<f32> {
                self.1.embed(text)
            }
        }

        let cache_db = tempfile::NamedTempFile::new().unwrap();
        let cache_a = SharedEmbeddingCache::open(
            Box::new(NamedEmbedder("provider-a", HashedEmbedder::default())),
            cache_db.path(),
        )
        .unwrap();
        cache_a.embed_document("def payment(): pass");

        // Same text, same cache db, but a DIFFERENT provider identity
        // (fingerprint) — must not see provider-a's cached vector at all.
        let cache_b = SharedEmbeddingCache::open(
            Box::new(NamedEmbedder("provider-b", HashedEmbedder::default())),
            cache_db.path(),
        )
        .unwrap();
        cache_b.embed_document("def payment(): pass");
        assert_eq!(
            cache_b.misses(),
            1,
            "a different embedding fingerprint must never reuse another provider's vectors"
        );
        assert_eq!(cache_b.hits(), 0);
    }

    #[test]
    fn failed_or_empty_embeddings_are_never_cached() {
        struct FlakyEmbedder {
            inner: HashedEmbedder,
            fail_next: StdAtomicUsize,
        }
        impl EmbeddingProvider for FlakyEmbedder {
            fn name(&self) -> &str {
                "flaky-test-embedder"
            }
            fn dim(&self) -> usize {
                self.inner.dim()
            }
            fn embed(&self, text: &str) -> Vec<f32> {
                if self.fail_next.swap(0, Ordering::Relaxed) == 1 {
                    return Vec::new(); // simulates a transient HTTP failure
                }
                self.inner.embed(text)
            }
        }

        let cache_db = tempfile::NamedTempFile::new().unwrap();
        let cache1 = SharedEmbeddingCache::open(
            Box::new(FlakyEmbedder {
                inner: HashedEmbedder::default(),
                fail_next: StdAtomicUsize::new(1),
            }),
            cache_db.path(),
        )
        .unwrap();
        let failed = cache1.embed_document("def payment(): pass");
        assert!(
            failed.is_empty(),
            "the failure itself should surface, not be masked here"
        );

        // A later, healthy provider must NOT see a poisoned empty-vector
        // cache entry for this content — it must recompute for real.
        let cache2 = SharedEmbeddingCache::open(
            Box::new(FlakyEmbedder {
                inner: HashedEmbedder::default(),
                fail_next: StdAtomicUsize::new(0),
            }),
            cache_db.path(),
        )
        .unwrap();
        let recovered = cache2.embed_document("def payment(): pass");
        assert_eq!(cache2.misses(), 1, "a failed embed must never be cached");
        assert!(
            !recovered.is_empty(),
            "the healthy provider must be consulted for real"
        );
    }

    /// A malformed HTTP response doesn't have to come back *empty* the way
    /// `HashedEmbedder`/`HttpEmbedder`'s own documented failure path does —
    /// a truncated or garbled response can come back non-empty but the
    /// wrong length. `put`'s empty-only check alone would silently cache
    /// that as this content's permanent embedding across every future
    /// commit that reuses it, with no path back to a healthy vector once
    /// the provider recovers (nothing else would ever invalidate the
    /// entry). Both `put` (never writes it) and `get` (never serves a
    /// wrong-dimension row even if one somehow exists) must refuse it.
    #[test]
    fn malformed_wrong_dimension_embeddings_are_never_cached_or_served() {
        struct WrongDimEmbedder {
            inner: HashedEmbedder,
            return_wrong_dim_next: StdAtomicUsize,
        }
        impl EmbeddingProvider for WrongDimEmbedder {
            fn name(&self) -> &str {
                "wrong-dim-test-embedder"
            }
            fn dim(&self) -> usize {
                self.inner.dim()
            }
            fn embed(&self, text: &str) -> Vec<f32> {
                let v = self.inner.embed(text);
                if self.return_wrong_dim_next.swap(0, Ordering::Relaxed) == 1 {
                    // Simulates a truncated/garbled HTTP response: non-empty,
                    // but not this provider's declared dimension.
                    return v[..v.len() / 2].to_vec();
                }
                v
            }
        }

        let cache_db = tempfile::NamedTempFile::new().unwrap();
        let cache1 = SharedEmbeddingCache::open(
            Box::new(WrongDimEmbedder {
                inner: HashedEmbedder::default(),
                return_wrong_dim_next: StdAtomicUsize::new(1),
            }),
            cache_db.path(),
        )
        .unwrap();
        let malformed = cache1.embed_document("def payment(): pass");
        assert_ne!(
            malformed.len(),
            cache1.inner.dim(),
            "sanity: the fixture must actually return a wrong-dimension vector"
        );

        // A later, healthy provider must NOT see a poisoned wrong-dimension
        // cache entry for this content — it must recompute for real.
        let cache2 = SharedEmbeddingCache::open(
            Box::new(WrongDimEmbedder {
                inner: HashedEmbedder::default(),
                return_wrong_dim_next: StdAtomicUsize::new(0),
            }),
            cache_db.path(),
        )
        .unwrap();
        let recovered = cache2.embed_document("def payment(): pass");
        assert_eq!(
            cache2.misses(),
            1,
            "a wrong-dimension embed must never be cached"
        );
        assert_eq!(
            recovered.len(),
            cache2.inner.dim(),
            "the healthy provider must be consulted for real, not served the malformed vector"
        );
    }
}
