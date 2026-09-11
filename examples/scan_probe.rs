//! Semantic-scan control measurement: median in-process latency of a
//! `VectorOnly`, no-expansion search (the exhaustive streaming dot-product
//! scan plus candidate hydration) against an existing index, and the
//! process RSS afterwards. This is the number sqlite-vec / Zvec must beat.
//! Usage: scan_probe <repo_root> "<query>" [iterations]
use oxide::embeddings::open_embedder;
use oxide::retrieval::{RetrievalEngine, RetrievalMode, SearchMode, SearchOptions};
use oxide::storage::{IndexBackend, SqliteStore};
use std::time::Instant;

fn rss_kb() -> u64 {
    std::fs::read_to_string("/proc/self/status")
        .ok()
        .and_then(|s| {
            s.lines()
                .find(|l| l.starts_with("VmRSS:"))
                .and_then(|l| l.split_whitespace().nth(1)?.parse().ok())
        })
        .unwrap_or(0)
}

fn main() -> anyhow::Result<()> {
    let root = std::path::PathBuf::from(std::env::args().nth(1).unwrap()).canonicalize()?;
    let query = std::env::args().nth(2).unwrap();
    let iters: usize = std::env::args()
        .nth(3)
        .and_then(|s| s.parse().ok())
        .unwrap_or(15);
    // Raw mode: `<root> <query> <iters> <dim>` scans with a synthetic query
    // vector of `dim`, for indexes whose vectors no local embedder can
    // query (e.g. a 1024-dim copy) — read + decode + dot, no hydration.
    if let Some(dim) = std::env::args()
        .nth(4)
        .and_then(|s| s.parse::<usize>().ok())
    {
        let qv: Vec<f32> = (0..dim).map(|i| (i as f32 * 0.37).sin()).collect();
        let mut times = Vec::new();
        let mut rows = 0usize;
        for _ in 0..iters {
            let store = SqliteStore::open_read_only(&root.join(".oxide/index.db"))?;
            let t = Instant::now();
            let mut best = (0u64, f32::MIN);
            rows = 0;
            store.for_each_embedding(&mut |id, d, bytes| {
                rows += 1;
                if d.min(bytes.len() / 4) != qv.len() {
                    return;
                }
                let mut dot = 0.0f32;
                for (a, c) in qv.iter().zip(bytes.as_chunks::<4>().0.iter()) {
                    dot += a * f32::from_le_bytes(*c);
                }
                if dot > best.1 {
                    best = (id, dot);
                }
            })?;
            times.push(t.elapsed().as_secs_f64() * 1e3);
        }
        times.sort_by(|a, b| a.partial_cmp(b).unwrap());
        println!(
            "rows={rows} dim={dim} raw_scan median={:.2}ms min={:.2}ms rss={}KB",
            times[times.len() / 2],
            times[0],
            rss_kb()
        );
        return Ok(());
    }
    let embedder = open_embedder(None)?;
    let opts = SearchOptions {
        limit: 10,
        mode: SearchMode::VectorOnly,
        expand: false,
        retrieval_mode: RetrievalMode::default(),
    };
    let mut fresh = Vec::new();
    let mut warm = Vec::new();
    let mut rows = 0usize;
    for _ in 0..iters {
        // Fresh connection per request, as every CLI/MCP request does.
        let t = Instant::now();
        let store = SqliteStore::open_read_only(&root.join(".oxide/index.db"))?;
        let engine = RetrievalEngine::new(&store, embedder.as_ref());
        engine.search(&query, &opts)?;
        fresh.push(t.elapsed().as_secs_f64() * 1e3);
        let t = Instant::now();
        engine.search(&query, &opts)?;
        warm.push(t.elapsed().as_secs_f64() * 1e3);
        if rows == 0 {
            store.for_each_embedding(&mut |_, _, _| rows += 1)?;
        }
    }
    fresh.sort_by(|a, b| a.partial_cmp(b).unwrap());
    warm.sort_by(|a, b| a.partial_cmp(b).unwrap());
    println!(
        "rows={rows} search_vector_only fresh_conn_median={:.2}ms same_conn_median={:.2}ms min={:.2}ms rss={}KB",
        fresh[fresh.len() / 2],
        warm[warm.len() / 2],
        warm[0],
        rss_kb()
    );
    Ok(())
}
