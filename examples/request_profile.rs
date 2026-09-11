//! Per-stage wall-clock breakdown of one search/context request against an
//! existing index, for finding what is still O(N) per request.
//! Usage: request_profile <repo_root> "<query>"
use oxide::context::{build_context_with, ContextOptions};
use oxide::embeddings::open_embedder;
use oxide::relations::RelationGraph;
use oxide::retrieval::{RetrievalEngine, RetrievalMode, SearchMode, SearchOptions, SymbolSnapshot};
use oxide::storage::{IndexBackend, SqliteStore};
use std::time::Instant;

fn main() -> anyhow::Result<()> {
    let root = std::path::PathBuf::from(std::env::args().nth(1).unwrap()).canonicalize()?;
    let query = std::env::args().nth(2).unwrap();
    let t = Instant::now();
    let embedder = open_embedder(None)?;
    println!(
        "open_embedder            {:>8.2} ms",
        t.elapsed().as_secs_f64() * 1e3
    );
    let t = Instant::now();
    let store = SqliteStore::open_read_only(&root.join(".oxide/index.db"))?;
    println!(
        "open_read_only           {:>8.2} ms",
        t.elapsed().as_secs_f64() * 1e3
    );
    let t = Instant::now();
    let stats = store.stats()?;
    println!(
        "stats (3 COUNT)          {:>8.2} ms  ({} symbols)",
        t.elapsed().as_secs_f64() * 1e3,
        stats.symbols
    );
    let t = Instant::now();
    let n = store.symbol_count()?;
    println!(
        "symbol_count             {:>8.2} ms  ({n})",
        t.elapsed().as_secs_f64() * 1e3
    );
    let t = Instant::now();
    let lq = oxide::lexical::prepare_from_store(&store, n, &query)?;
    let postings: usize = lq.terms.iter().map(|t| t.postings.len()).sum();
    println!(
        "lexical prepare          {:>8.2} ms  ({} terms, {postings} postings)",
        t.elapsed().as_secs_f64() * 1e3,
        lq.terms.len()
    );
    let t = Instant::now();
    let (scores, _) = oxide::lexical::score(&lq, 1.5, 0.75);
    println!(
        "lexical score            {:>8.2} ms  ({} docs)",
        t.elapsed().as_secs_f64() * 1e3,
        scores.len()
    );
    let t = Instant::now();
    let qv = embedder.embed_query(&query);
    println!(
        "embed_query              {:>8.2} ms  (dim {})",
        t.elapsed().as_secs_f64() * 1e3,
        qv.len()
    );
    let t = Instant::now();
    let mut rows = 0usize;
    store.for_each_embedding(&mut |_, _, _| rows += 1)?;
    println!(
        "embedding scan (no dot)  {:>8.2} ms  ({rows} rows)",
        t.elapsed().as_secs_f64() * 1e3
    );
    let t = Instant::now();
    let snapshot = SymbolSnapshot::load(&store)?;
    println!(
        "SymbolSnapshot::load     {:>8.2} ms",
        t.elapsed().as_secs_f64() * 1e3
    );
    let t = Instant::now();
    let engine = RetrievalEngine::with_snapshot(&store, embedder.as_ref(), &snapshot);
    let hits = engine.search(
        &query,
        &SearchOptions {
            limit: 16,
            mode: SearchMode::Hybrid,
            expand: false,
            retrieval_mode: RetrievalMode::default(),
        },
    )?;
    println!(
        "search (no expand)       {:>8.2} ms  ({} hits)",
        t.elapsed().as_secs_f64() * 1e3,
        hits.len()
    );
    let t = Instant::now();
    let hits = engine.search(
        &query,
        &SearchOptions {
            limit: 16,
            mode: SearchMode::Hybrid,
            expand: true,
            retrieval_mode: RetrievalMode::default(),
        },
    )?;
    println!(
        "search (expand)          {:>8.2} ms  ({} hits)",
        t.elapsed().as_secs_f64() * 1e3,
        hits.len()
    );
    let t = Instant::now();
    let graph = RelationGraph::build(&snapshot.symbols);
    println!(
        "RelationGraph::build     {:>8.2} ms",
        t.elapsed().as_secs_f64() * 1e3
    );
    let t = Instant::now();
    let mut nb = 0;
    for h in hits.iter().take(5) {
        nb += graph.neighbors(&h.symbol).len();
    }
    println!(
        "neighbors x5             {:>8.2} ms  ({nb} neighbors)",
        t.elapsed().as_secs_f64() * 1e3
    );
    let t = Instant::now();
    let mut c = 0;
    for h in hits.iter().take(2) {
        c += graph.callers_of(&h.symbol.name).len();
    }
    println!(
        "callers_of x2 (+index)   {:>8.2} ms  ({c} callers)",
        t.elapsed().as_secs_f64() * 1e3
    );
    let t = Instant::now();
    let pack = build_context_with(&root, &engine, &query, &ContextOptions::default())?;
    println!(
        "build_context_with       {:>8.2} ms  ({} items)",
        t.elapsed().as_secs_f64() * 1e3,
        pack.items.len()
    );
    let t = Instant::now();
    let pack = build_context_with(&root, &engine, &query, &ContextOptions::default())?;
    println!(
        "build_context_with again {:>8.2} ms  ({} items)",
        t.elapsed().as_secs_f64() * 1e3,
        pack.items.len()
    );
    Ok(())
}
