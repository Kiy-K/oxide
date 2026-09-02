//! Runtime probe for comparing local embedding profiles (Task D: choose the
//! v0.1 embedding profile by OXIDE's actual Pareto frontier, not assumption).
//! Measures what `eval-agent/results/native_screen/indexing_timings.tsv`
//! never actually captured (its `index_seconds` column was left blank).
//!
//! Usage: `cargo run --release --features native-embed --example
//! embedding_profile_probe -- <profile> [query-prompt]`
//! profile: embeddinggemma-300m | embeddinggemma-300m-q4 | minilm-l6-v2
//! query-prompt (Gemma only): bare | search-result | code-retrieval
//!
//! Reports cold init time, warm embed_query p50/p95 (50 reps), 1/10/50/100
//! symbol-doc batch latency, full-batch throughput, peak RSS (VmHWM), and the
//! model's on-disk cache footprint. Never touches retrieval/allocation code.

use oxide::embeddings::{EmbeddingProvider, GemmaQueryPrompt, NativeEmbedder};
use std::time::Instant;

fn peak_rss_kb() -> Option<u64> {
    let status = std::fs::read_to_string("/proc/self/status").ok()?;
    status.lines().find_map(|l| {
        l.strip_prefix("VmHWM:")
            .and_then(|rest| rest.split_whitespace().next())
            .and_then(|n| n.parse().ok())
    })
}

fn percentile(sorted: &[f64], p: f64) -> f64 {
    let idx = ((sorted.len() - 1) as f64 * p).round() as usize;
    sorted[idx]
}

fn sample_doc(n: usize) -> String {
    format!(
        "def handle_request_{n}(self, request, context):\n    \
         \"\"\"Process an incoming request and dispatch to the right handler.\"\"\"\n    \
         if not request.is_valid():\n        raise ValueError(\"invalid request\")\n    \
         return self.dispatcher.route(request, context)"
    )
}

fn main() -> anyhow::Result<()> {
    let mut args = std::env::args().skip(1);
    let profile = args.next().unwrap_or_else(|| "embeddinggemma-300m".into());
    let prompt = match args.next().as_deref() {
        Some("bare") | None => GemmaQueryPrompt::Bare,
        Some("search-result") => GemmaQueryPrompt::SearchResult,
        Some("code-retrieval") => GemmaQueryPrompt::CodeRetrieval,
        Some(other) => anyhow::bail!("unknown query prompt {other:?}"),
    };

    eprintln!("oxide: loading native profile {profile:?} (downloads if not cached)...");
    let t0 = Instant::now();
    let embedder = NativeEmbedder::new(&profile, prompt)?;
    let cold_init = t0.elapsed();
    println!("profile={profile} dim={}", embedder.dim());
    println!("cold_init_ms={:.1}", cold_init.as_secs_f64() * 1000.0);

    // Warm embed_query p50/p95.
    let _ = embedder.embed_query("warmup"); // discard first call (lazy graph init)
    let mut lat = Vec::new();
    for i in 0..50 {
        let q = format!("how does request routing work in module {i}");
        let t = Instant::now();
        let v = embedder.embed_query(&q);
        anyhow::ensure!(!v.is_empty(), "embed_query returned empty vector");
        lat.push(t.elapsed().as_secs_f64() * 1000.0);
    }
    lat.sort_by(|a, b| a.partial_cmp(b).unwrap());
    println!("embed_query_p50_ms={:.2}", percentile(&lat, 0.50));
    println!("embed_query_p95_ms={:.2}", percentile(&lat, 0.95));

    // 1/10/50/100-symbol incremental embedding latency (embed_documents).
    for n in [1usize, 10, 50, 100] {
        let docs: Vec<String> = (0..n).map(sample_doc).collect();
        let t = Instant::now();
        let out = embedder.embed_documents(&docs);
        let elapsed = t.elapsed();
        anyhow::ensure!(
            out.iter().all(|v| !v.is_empty()),
            "empty vector in batch n={n}"
        );
        println!(
            "incremental_n{n}_total_ms={:.1} per_item_ms={:.2}",
            elapsed.as_secs_f64() * 1000.0,
            elapsed.as_secs_f64() * 1000.0 / n as f64
        );
    }

    // Full-document throughput: one large batch.
    let big: Vec<String> = (0..500).map(sample_doc).collect();
    let t = Instant::now();
    let out = embedder.embed_documents(&big);
    let elapsed = t.elapsed();
    anyhow::ensure!(out.len() == big.len());
    println!(
        "throughput_500_total_ms={:.1} items_per_sec={:.1}",
        elapsed.as_secs_f64() * 1000.0,
        500.0 / elapsed.as_secs_f64()
    );

    if let Some(kb) = peak_rss_kb() {
        println!("peak_rss_mb={:.1}", kb as f64 / 1024.0);
    }

    Ok(())
}
