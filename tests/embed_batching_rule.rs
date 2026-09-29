//! Pins the embedding stage's batching rule as it stands (#34 S4): in
//! `index/embed.rs`, symbols are sent through `embed_documents` in chunks
//! of 64 when there are fewer than 8 to embed **or** `$OXIDE_EMBED_URL` is
//! set to any value, and one `embed_document` call per symbol on a thread
//! pool otherwise. The environment variable, not the provider, decides:
//! a local provider with the variable set is batched, and a remote provider
//! without it is not. That ownership is questionable (recorded in #34 D4),
//! but changing it is a behavior change for a later issue; this test exists
//! so such a change is deliberate.
//!
//! One test in this binary on purpose: it sets `OXIDE_EMBED_URL`, which is
//! process-global.

use oxide::embeddings::EmbeddingProvider;
use oxide::index::{update_index, SqliteStore};
use std::sync::atomic::{AtomicUsize, Ordering};

struct Spy {
    remote: bool,
    single: AtomicUsize,
    batches: AtomicUsize,
}

impl Spy {
    fn new(remote: bool) -> Self {
        Self {
            remote,
            single: AtomicUsize::new(0),
            batches: AtomicUsize::new(0),
        }
    }
    fn calls(&self) -> (usize, usize) {
        (
            self.single.load(Ordering::SeqCst),
            self.batches.load(Ordering::SeqCst),
        )
    }
}

impl EmbeddingProvider for Spy {
    fn name(&self) -> &str {
        "spy"
    }
    fn dim(&self) -> usize {
        4
    }
    fn embed(&self, _: &str) -> Vec<f32> {
        vec![1.0, 0.5, 0.25, 0.125]
    }
    fn is_remote(&self) -> bool {
        self.remote
    }
    fn embed_document(&self, text: &str) -> Vec<f32> {
        self.single.fetch_add(1, Ordering::SeqCst);
        self.embed(text)
    }
    fn embed_documents(&self, texts: &[String]) -> Vec<Vec<f32>> {
        self.batches.fetch_add(1, Ordering::SeqCst);
        texts.iter().map(|t| self.embed(t)).collect()
    }
}

/// `(embed_document calls, embed_documents calls)` for a first index of a
/// repo with `functions` symbols (plus the module symbol).
fn run(functions: usize, remote: bool, url: Option<&str>) -> (usize, usize) {
    let tmp = tempfile::tempdir().unwrap();
    let src: String = (0..functions)
        .map(|i| format!("def f{i}():\n    return {i}\n\n\n"))
        .collect();
    std::fs::write(tmp.path().join("m.py"), src).unwrap();
    match url {
        Some(v) => unsafe { std::env::set_var("OXIDE_EMBED_URL", v) },
        None => unsafe { std::env::remove_var("OXIDE_EMBED_URL") },
    }
    let spy = Spy::new(remote);
    let mut store = SqliteStore::open(&tmp.path().join(".oxide/index.db")).unwrap();
    let report = update_index(tmp.path(), &mut store, &spy).unwrap();
    unsafe { std::env::remove_var("OXIDE_EMBED_URL") };
    assert_eq!(report.embedded_symbols, functions + 1);
    spy.calls()
}

#[test]
fn the_env_var_not_the_provider_selects_the_batched_embedding_path() {
    // 99 symbols to embed: one 64-chunk and one 35-chunk when batched.
    // Unset, local provider: one call per symbol.
    assert_eq!(run(98, false, None), (99, 0));
    // Set (any value, even empty), local provider: batched.
    assert_eq!(run(98, false, Some("http://127.0.0.1:9/v1")), (0, 2));
    assert_eq!(run(98, false, Some("")), (0, 2));
    // Remote provider, variable unset: still one call per symbol.
    assert_eq!(run(98, true, None), (99, 0));
    // Fewer than 8 symbols: batched regardless.
    assert_eq!(run(6, false, None), (0, 1));
}
