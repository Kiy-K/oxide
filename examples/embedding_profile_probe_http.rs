//! HTTP-provider twin of `embedding_profile_probe` (Task D). Same
//! methodology (client-side `EmbeddingProvider` calls, same doc sample, same
//! percentile math) applied to `HttpEmbedder` so Qwen3-via-llama.cpp numbers
//! are comparable to the native ONNX profiles. Server process boundary is
//! separate: this only measures client-observed latency/RSS of the `oxide`
//! process itself, not the llama.cpp server's own RSS (measured externally
//! via `ps` against its own pid — a different process, not comparable 1:1,
//! see docs this feeds).
//!
//! Usage: `OXIDE_EMBED_URL=... OXIDE_EMBED_MODEL=... cargo run --release
//! --example embedding_profile_probe_http`

use oxide::embeddings::{EmbeddingProvider, HttpEmbedder};
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
    let url = std::env::var("OXIDE_EMBED_URL").expect("set OXIDE_EMBED_URL");
    let model = std::env::var("OXIDE_EMBED_MODEL").expect("set OXIDE_EMBED_MODEL");

    let t0 = Instant::now();
    let embedder = HttpEmbedder::new(&url, &model)?;
    let cold_init = t0.elapsed();
    println!("profile=http:{model} dim={}", embedder.dim());
    println!(
        "client_cold_init_ms={:.1}",
        cold_init.as_secs_f64() * 1000.0
    );

    let _ = embedder.embed_query("warmup");
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

    if std::env::var("OXIDE_PROBE_SKIP_THROUGHPUT").is_err() {
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
    }

    if let Some(kb) = peak_rss_kb() {
        println!("client_peak_rss_mb={:.1}", kb as f64 / 1024.0);
    }
    Ok(())
}
