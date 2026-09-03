//! Correctness gate for the native fastembed profiles used in the CPU
//! embedding survey (docs/cpu-embedding-survey/) — minilm-l6-v2, jina-code-v2,
//! arctic-embed-xs, bge-small-en-v1.5. Unlike the timed probe
//! (embedding_profile_probe), this does one embed call per check and is safe
//! to run on a contended machine: only pass/fail matters here, not latency.
//!
//! Checks: reported dimension is nonzero and stable, vectors are
//! L2-normalized, and a paraphrase pair scores above an unrelated pair.
//!
//! Usage: `cargo run --release --features native-embed --example
//! native_correctness_check -- <profile>`

use oxide::embeddings::{EmbeddingProvider, GemmaQueryPrompt, NativeEmbedder};

fn cosine(a: &[f32], b: &[f32]) -> f32 {
    let dot: f32 = a.iter().zip(b).map(|(x, y)| x * y).sum();
    let na: f32 = a.iter().map(|x| x * x).sum::<f32>().sqrt();
    let nb: f32 = b.iter().map(|x| x * x).sum::<f32>().sqrt();
    dot / (na * nb)
}

fn norm(v: &[f32]) -> f32 {
    v.iter().map(|x| x * x).sum::<f32>().sqrt()
}

fn main() -> anyhow::Result<()> {
    let profile = std::env::args()
        .nth(1)
        .expect("usage: native_correctness_check <profile>");

    eprintln!("oxide: loading native profile {profile:?} (downloads if not cached)...");
    let embedder = NativeEmbedder::new(&profile, GemmaQueryPrompt::Bare)?;

    let paraphrase_a = embedder.embed_document(
        "def retry_with_backoff(fn, max_attempts=3): retries fn with exponential backoff",
    );
    let paraphrase_b = embedder.embed_document(
        "def call_with_exponential_backoff(func, attempts=3): retries func, doubling delay each failure",
    );
    let unrelated = embedder.embed_document(
        "class InvoiceLineItem: represents a single billed line item on a customer invoice",
    );

    for (name, v) in [
        ("paraphrase_a", &paraphrase_a),
        ("paraphrase_b", &paraphrase_b),
        ("unrelated", &unrelated),
    ] {
        anyhow::ensure!(!v.is_empty(), "{name} embedding is empty");
        anyhow::ensure!(v.len() == embedder.dim(), "{name} length != reported dim()");
        let n = norm(v);
        anyhow::ensure!(
            (n - 1.0).abs() < 0.05,
            "{name} is not L2-normalized: norm={n:.4}"
        );
    }

    let sim_paraphrase = cosine(&paraphrase_a, &paraphrase_b);
    let sim_unrelated = cosine(&paraphrase_a, &unrelated);
    println!(
        "profile={profile} dim={} sim_paraphrase={sim_paraphrase:.4} sim_unrelated={sim_unrelated:.4}",
        embedder.dim()
    );
    anyhow::ensure!(
        sim_paraphrase > sim_unrelated,
        "FAIL: paraphrase similarity ({sim_paraphrase:.4}) did not beat unrelated ({sim_unrelated:.4})"
    );
    println!("PASS: paraphrase pair scores above unrelated pair, vectors are unit-normalized");
    Ok(())
}
