//! Isolates the SQLite write-side improvement from batching embedding
//! writes into one transaction per chunk instead of one autocommit per
//! symbol (docs/indexing-rebuild-scopes/README.md). Uses a real,
//! file-backed store — `:memory:` databases never fsync, so they can't
//! show this effect at all — and `HashedEmbedder` so embedder latency
//! (the actual dominant cost on CPU, already profiled separately) doesn't
//! drown out the write-path signal this benchmark targets.
//! `cargo run --example embedding_write_batching_benchmark --release`

use oxide::embeddings::{EmbeddingProvider, HashedEmbedder};
use oxide::index::{IndexBackend, SqliteStore};
use oxide::symbols::{content_hash, Language, Symbol, SymbolKind};
use std::path::Path;
use std::time::Instant;

const N: usize = 4000;
const CHUNK: usize = 64;

fn make_symbols() -> Vec<Symbol> {
    (0..N)
        .map(|i| Symbol {
            qualified_name: format!("module{i}.func{i}"),
            name: format!("func{i}"),
            kind: SymbolKind::Function,
            language: Language::Python,
            file: format!("src/module{i}.py"),
            start_line: 1,
            end_line: 5,
            content_hash: content_hash(&format!("def func{i}(): pass")),
            signature: format!("def func{i}():"),
            imports: vec![],
            exported: true,
            parent: None,
            references: vec![],
            calls: vec![],
            bases: vec![],
        })
        .collect()
}

fn seed_store(path: &Path, symbols: &[Symbol]) -> SqliteStore {
    let mut store = SqliteStore::open(path).unwrap();
    for s in symbols {
        store
            .replace_file(&s.file, s.content_hash, std::slice::from_ref(s), &[])
            .unwrap();
    }
    store
}

fn main() {
    let symbols = make_symbols();
    let emb = HashedEmbedder::default();
    let vectors: Vec<(u64, Vec<f32>)> = symbols
        .iter()
        .map(|s| (s.id(), emb.embed_document(&s.signature)))
        .collect();

    let dir = tempfile::tempdir().unwrap();

    // Old pattern: one `put_embedding` call per symbol (one autocommit
    // transaction per row under SQLite's default behavior).
    let per_item_path = dir.path().join("per_item.db");
    let mut per_item_store = seed_store(&per_item_path, &symbols);
    let t0 = Instant::now();
    for (id, vec) in &vectors {
        per_item_store.put_embedding(*id, vec).unwrap();
    }
    let per_item_elapsed = t0.elapsed();

    // New pattern: one transaction per CHUNK-sized batch, matching how
    // update_embeddings actually calls it.
    let batched_path = dir.path().join("batched.db");
    let mut batched_store = seed_store(&batched_path, &symbols);
    let t0 = Instant::now();
    for chunk in vectors.chunks(CHUNK) {
        batched_store.put_embeddings_batch(chunk).unwrap();
    }
    let batched_elapsed = t0.elapsed();

    println!("N={N} symbols, chunk={CHUNK}");
    println!(
        "per-item put_embedding:    {:>8.1}ms  ({:.3}ms/symbol)",
        per_item_elapsed.as_secs_f64() * 1000.0,
        per_item_elapsed.as_secs_f64() * 1000.0 / N as f64
    );
    println!(
        "batched put_embeddings_batch: {:>6.1}ms  ({:.3}ms/symbol)",
        batched_elapsed.as_secs_f64() * 1000.0,
        batched_elapsed.as_secs_f64() * 1000.0 / N as f64
    );
    let speedup = per_item_elapsed.as_secs_f64() / batched_elapsed.as_secs_f64();
    println!("speedup: {speedup:.2}x");
}
