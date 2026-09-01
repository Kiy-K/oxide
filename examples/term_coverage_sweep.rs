//! Eval-harness optimization for the term-coverage corroboration experiment
//! (docs/term-coverage-eval/README.md): runs one (repo, query) task's full
//! alpha sweep in a single process instead of 10 separate `oxide` CLI
//! subprocess invocations (5 alphas × {search, context}).
//!
//! Eliminates two real, measured redundancies without touching any
//! retrieval/ranking/embedding-model/index/context-allocation code:
//!   1. Subprocess + engine-rebuild overhead: one process opens the store
//!      and builds one `RetrievalEngine` (which loads the document-vector
//!      cache and the `LexicalIndex` exactly once, per their own existing
//!      "cached for the engine's lifetime" contracts — see retrieval.rs),
//!      reused across all 5 `engine.search()` calls in the sweep.
//!   2. Redundant query embedding: `CachingEmbedder` below memoizes
//!      `embed_query` by exact query text, so the 10 calls that would
//!      otherwise each hit the embedder over HTTP (`search`/`context` use
//!      the identical, untransformed query text — see `build_context`'s own
//!      comment on this) collapse to exactly 1 real network round trip per
//!      unique query. `build_context` still builds its own internal
//!      `RetrievalEngine` per call (its signature isn't changed here), so
//!      its `LexicalIndex`/vector-cache rebuild per alpha is NOT eliminated
//!      — only its embedding call is, via the shared cache. That residual
//!      cost is pure in-memory CPU work (no network, no subprocess) and
//!      the CPU-overhead benchmark in the same experiment (`cargo run
//!      --example term_coverage_overhead --release`) already shows it is
//!      within measurement noise even on a 35k-symbol repo.
//!
//! Refuses to run if the index's recorded embedder identity doesn't match
//! the configured provider — silently mixing embedding spaces is exactly
//! the failure this experiment's own RET-005 finding warns about.
//!
//! Usage: `cargo run --release --example term_coverage_sweep -- <repo_dir> <task_file> [alphas_csv]`
//! Prints one JSON object per line (one per alpha) to stdout.

use oxide::context::{build_context, ContextOptions};
use oxide::embeddings::{open_embedder, EmbeddingProvider, EmbeddingSpaceFingerprint};
use oxide::index::{IndexBackend, SqliteStore};
use oxide::retrieval::{RetrievalEngine, RetrievalMode, SearchMode, SearchOptions};
use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Mutex;
use std::time::Instant;

/// Wraps any `EmbeddingProvider` and memoizes `embed_query` by exact input
/// text. Every other method forwards unchanged — this only ever touches the
/// query-embedding path `RetrievalEngine::search` calls (see
/// `search_calls_embed_query_not_embed_for_the_query_text` in retrieval.rs's
/// own test suite for why that distinction matters: `embed`/`embed_document`
/// are for indexing-time document text, a different cache key space this
/// wrapper deliberately does not touch).
struct CachingEmbedder {
    inner: Box<dyn EmbeddingProvider>,
    query_cache: Mutex<HashMap<String, Vec<f32>>>,
    calls: AtomicUsize,
    hits: AtomicUsize,
}

impl CachingEmbedder {
    fn new(inner: Box<dyn EmbeddingProvider>) -> Self {
        Self {
            inner,
            query_cache: Mutex::new(HashMap::new()),
            calls: AtomicUsize::new(0),
            hits: AtomicUsize::new(0),
        }
    }
    fn calls(&self) -> usize {
        self.calls.load(Ordering::Relaxed)
    }
    fn hits(&self) -> usize {
        self.hits.load(Ordering::Relaxed)
    }
}

impl EmbeddingProvider for CachingEmbedder {
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
    fn embed_batch(&self, texts: &[String]) -> Vec<Vec<f32>> {
        self.inner.embed_batch(texts)
    }
    fn embed_query(&self, text: &str) -> Vec<f32> {
        if let Some(v) = self.query_cache.lock().unwrap().get(text) {
            self.hits.fetch_add(1, Ordering::Relaxed);
            return v.clone();
        }
        self.calls.fetch_add(1, Ordering::Relaxed);
        let v = self.inner.embed_query(text);
        // `HttpEmbedder` returns an empty vector on a transient failure
        // (embeddings.rs's documented-by-design degrade-gracefully
        // contract) — caching that would silently poison every later alpha
        // arm with a "successful" empty-vector result even after the
        // provider recovers, indistinguishable from a real lexical-only
        // finding. Only a genuine embedding is memoized; a failure is left
        // uncached so the next call retries against the live provider.
        if !v.is_empty() {
            self.query_cache
                .lock()
                .unwrap()
                .insert(text.to_string(), v.clone());
        }
        v
    }
    fn embed_document(&self, text: &str) -> Vec<f32> {
        self.inner.embed_document(text)
    }
    fn embed_documents(&self, texts: &[String]) -> Vec<Vec<f32>> {
        self.inner.embed_documents(texts)
    }
    fn fingerprint(&self) -> EmbeddingSpaceFingerprint {
        self.inner.fingerprint()
    }
}

fn main() -> anyhow::Result<()> {
    let mut args = std::env::args().skip(1);
    let repo = PathBuf::from(args.next().expect(
        "usage: term_coverage_sweep <repo_dir> <task_file> [alphas_csv=0.0,0.1,0.2,0.3,0.5]",
    ));
    let task_file = args.next().expect("task_file argument required");
    let alphas: Vec<String> = args
        .next()
        .unwrap_or_else(|| "0.0,0.1,0.2,0.3,0.5".to_string())
        .split(',')
        .map(|s| s.trim().to_string())
        .collect();
    let task = std::fs::read_to_string(&task_file)?;

    let db_path = repo.join(".oxide/index.db");
    let store = SqliteStore::open_read_only(&db_path)?;
    let inner = open_embedder(None)?;

    // Provenance gate: refuse to mix embedding spaces rather than silently
    // scoring a query vector from provider A against document vectors from
    // provider B. Same fingerprint-first, name+dim-fallback contract as
    // `RepositoryService::validate_index` (service.rs) — comparing only the
    // provider *name* would pass a provider that kept the same URL/model
    // label but started returning a different dimension, silently
    // degrading every result to lexical-only once every stored vector
    // fails `RetrievalEngine::search`'s length check.
    let stored_fp: Option<EmbeddingSpaceFingerprint> = store
        .get_meta("embedding_fingerprint")?
        .filter(|s| !s.is_empty())
        .and_then(|s| serde_json::from_str(&s).ok());
    let compatible = match &stored_fp {
        Some(prev) => *prev == inner.fingerprint(),
        None => {
            let stored_embedder = store.get_meta("embedder")?;
            let stored_dim = store.get_meta("dim")?;
            stored_embedder.as_deref() == Some(inner.name())
                && stored_dim.as_deref() == Some(inner.dim().to_string().as_str())
        }
    };
    if !compatible {
        anyhow::bail!(
            "embedder mismatch: index was built with a different embedding provider or \
             dimension than the configured provider {:?} (dim {}) — refusing to run (would \
             silently mix embedding spaces); reindex with the intended embedder first",
            inner.name(),
            inner.dim()
        );
    }
    let embedder = CachingEmbedder::new(inner);

    let engine = RetrievalEngine::new(&store, &embedder);
    let search_opts = SearchOptions {
        limit: 20,
        mode: SearchMode::Hybrid,
        expand: true,
        retrieval_mode: RetrievalMode::Balanced,
    };
    let ctx_opts = ContextOptions {
        retrieval_mode: RetrievalMode::Balanced,
        ..ContextOptions::default()
    };

    for alpha in &alphas {
        unsafe { std::env::set_var("OXIDE_TERM_COVERAGE_ALPHA", alpha) };

        let t0 = Instant::now();
        let hits = engine.search(&task, &search_opts)?;
        let search_seconds = t0.elapsed().as_secs_f64();

        let t1 = Instant::now();
        let pack = build_context(&repo, &store, &embedder, &task, &ctx_opts)?;
        let context_seconds = t1.elapsed().as_secs_f64();

        // A provider that goes unavailable mid-sweep must fail loudly, not
        // emit a "successful" result that is silently lexical-only — the
        // caller (term_coverage_eval.py) has no other way to distinguish
        // degraded evidence from a real ranking outcome.
        if !embedder.is_available() {
            anyhow::bail!(
                "embedding provider became unavailable during alpha={alpha}'s sweep arm; \
                 refusing to emit a result that would silently look like a real hybrid ranking"
            );
        }

        let out = serde_json::json!({
            "alpha": alpha,
            "search_seconds": search_seconds,
            "context_seconds": context_seconds,
            "hits": hits,
            "pack": pack,
            "embedder": embedder.name(),
            "embed_query_calls_total": embedder.calls(),
            "embed_query_cache_hits_total": embedder.hits(),
        });
        println!("{}", serde_json::to_string(&out)?);
    }
    unsafe { std::env::remove_var("OXIDE_TERM_COVERAGE_ALPHA") };
    Ok(())
}
