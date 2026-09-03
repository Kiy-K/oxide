//! Thin CLI wrapper around `oxide::embedding_cache` — commit-correct
//! indexing with a shared, content-addressed embedding cache (the
//! harness-reuse follow-up to docs/term-coverage-eval/README.md). See
//! `src/embedding_cache.rs` for the actual mechanism and its regression
//! tests.
//!
//! Usage: `term_coverage_index <repo_dir> <expected_commit_sha> <cache_db_path>`

use oxide::embedding_cache::{verify_commit, SharedEmbeddingCache};
use oxide::embeddings::open_embedder;
use oxide::index::{update_index, SqliteStore};

fn main() -> anyhow::Result<()> {
    let mut args = std::env::args().skip(1);
    let repo_dir = std::path::PathBuf::from(
        args.next()
            .expect("usage: term_coverage_index <repo_dir> <expected_commit_sha> <cache_db_path>"),
    );
    let expected_commit = args.next().expect("expected_commit_sha required");
    let cache_db_path = std::path::PathBuf::from(args.next().expect("cache_db_path required"));

    verify_commit(&repo_dir, &expected_commit)?;

    let db_path = repo_dir.join(".oxide/index.db");
    let mut store = SqliteStore::open(&db_path)?;
    let inner = open_embedder(None)?;
    let embedder = SharedEmbeddingCache::open(inner, &cache_db_path)?;

    let report = update_index(&repo_dir, &mut store, &embedder)?;
    eprintln!(
        "indexed {}: scanned={} reparsed={} unchanged={} errored={} \
         embedded={} reused_in_index={} embed_cache_hits={} embed_cache_misses={}",
        repo_dir.display(),
        report.scanned_files,
        report.reparsed_files,
        report.unchanged_files,
        report.errored_files,
        report.embedded_symbols,
        report.reused_embeddings,
        embedder.hits(),
        embedder.misses(),
    );
    Ok(())
}
