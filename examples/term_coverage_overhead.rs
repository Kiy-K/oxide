//! CPU-overhead evidence for the term-coverage corroboration experiment
//! (docs/term-coverage-eval/README.md): in-process repeated-query timing,
//! alpha=0.0 (frozen default, no-op) vs alpha=0.3, on a real, sizeable
//! Python repo (Django, already cached locally by the ContextBench harness
//! — OXIDE has no Rust extractor, so its own `src/` can't be used) indexed
//! once — avoids per-process CLI startup noise (SQLite open, index load,
//! engine construction) that would otherwise dominate any subprocess-based
//! timing and hide the actual per-query arithmetic cost.
//! `cargo run --example term_coverage_overhead --release -- [repo_path]`

use oxide::embeddings::HashedEmbedder;
use oxide::index::{update_index, IndexBackend, SqliteStore};
use oxide::retrieval::{RetrievalEngine, RetrievalMode, SearchMode, SearchOptions};
use std::path::Path;
use std::time::Instant;

const QUERIES: &[&str] = &[
    "hybrid retrieval lexical semantic fusion",
    "structural relation expansion callers implementors",
    "context budget allocation relevance floor",
    "embedding provider identity cache invalidation",
    "symbol id content hash incremental index",
    "retrieval mode fast balanced quality",
    "term coverage corroboration distinct query terms",
    "sqlite store read only backend",
];
const REPS: usize = 200;

fn dirs_home() -> std::path::PathBuf {
    std::path::PathBuf::from(std::env::var("HOME").expect("HOME must be set"))
}

fn percentile(sorted: &[f64], p: f64) -> f64 {
    let idx = ((sorted.len() as f64 - 1.0) * p).round() as usize;
    sorted[idx]
}

fn run(engine: &RetrievalEngine, opts: &SearchOptions, alpha: &str) -> Vec<f64> {
    unsafe { std::env::set_var("OXIDE_TERM_COVERAGE_ALPHA", alpha) };
    let mut samples = Vec::with_capacity(REPS * QUERIES.len());
    // Warm-up: first calls pay one-time allocator/cache costs.
    for q in QUERIES {
        let _ = engine.search(q, opts);
    }
    for _ in 0..REPS {
        for q in QUERIES {
            let t0 = Instant::now();
            let _ = engine.search(q, opts).unwrap();
            samples.push(t0.elapsed().as_secs_f64() * 1000.0);
        }
    }
    unsafe { std::env::remove_var("OXIDE_TERM_COVERAGE_ALPHA") };
    samples.sort_by(|a, b| a.partial_cmp(b).unwrap());
    samples
}

fn main() {
    let default_root = dirs_home().join(".cache/oxide-contextbench/repos/django");
    let root = std::env::args()
        .nth(1)
        .map(std::path::PathBuf::from)
        .unwrap_or(default_root);
    let mut store = SqliteStore::open(Path::new(":memory:")).unwrap();
    let embedder = HashedEmbedder::default();
    let report = update_index(&root, &mut store, &embedder).unwrap();
    eprintln!(
        "scanned={} reparsed={} new={} errored={} store_symbols={}",
        report.scanned_files,
        report.reparsed_files,
        report.new_symbols,
        report.errored_files,
        store.all_symbols().unwrap_or_default().len()
    );

    let engine = RetrievalEngine::new(&store, &embedder);
    let opts = SearchOptions {
        limit: 10,
        mode: SearchMode::Hybrid,
        expand: true,
        retrieval_mode: RetrievalMode::Balanced,
    };

    let baseline = run(&engine, &opts, "0.0");
    let boosted = run(&engine, &opts, "0.3");

    let mean = |v: &[f64]| v.iter().sum::<f64>() / v.len() as f64;
    println!(
        "alpha=0.0  mean={:.4}ms  p50={:.4}ms  p95={:.4}ms  p99={:.4}ms  n={}",
        mean(&baseline),
        percentile(&baseline, 0.50),
        percentile(&baseline, 0.95),
        percentile(&baseline, 0.99),
        baseline.len()
    );
    println!(
        "alpha=0.3  mean={:.4}ms  p50={:.4}ms  p95={:.4}ms  p99={:.4}ms  n={}",
        mean(&boosted),
        percentile(&boosted, 0.50),
        percentile(&boosted, 0.95),
        percentile(&boosted, 0.99),
        boosted.len()
    );
    let delta_pct = (mean(&boosted) - mean(&baseline)) / mean(&baseline) * 100.0;
    println!("mean delta: {delta_pct:.2}%");
}
