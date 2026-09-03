//! Correctness gate for the Nomic v2 MoE HTTP path — run this and confirm it
//! passes BEFORE trusting any timing from `embedding_profile_probe_http_nomic`.
//! llama.cpp's MoE-embedding support had known bugs (a tokenizer mismatch and
//! a GGML_ASSERT crash, both filed against this exact model upstream); a
//! silently-wrong-but-non-crashing load is the more dangerous failure mode
//! for a benchmark, since a bad embedding still produces a number.
//!
//! Checks, for both the full 768d output and (if `OXIDE_TRUNCATE_DIM` is set)
//! the truncated variant:
//!   1. Reported dimension matches expectation.
//!   2. Every vector is L2-normalized (‖v‖ ≈ 1.0).
//!   3. A paraphrase pair scores higher (cosine) than an unrelated pair —
//!      the minimum bar for "this is a semantically meaningful embedding
//!      space", not a quality benchmark.
//!
//! Usage: `OXIDE_EMBED_URL=... OXIDE_EMBED_MODEL=... [OXIDE_TRUNCATE_DIM=256]
//! cargo run --release --example nomic_correctness_check`

use oxide::embeddings::{EmbeddingProvider, HttpEmbedder, HttpPromptProtocol};

fn cosine(a: &[f32], b: &[f32]) -> f32 {
    assert_eq!(a.len(), b.len(), "dimension mismatch in cosine()");
    let dot: f32 = a.iter().zip(b).map(|(x, y)| x * y).sum();
    let na: f32 = a.iter().map(|x| x * x).sum::<f32>().sqrt();
    let nb: f32 = b.iter().map(|x| x * x).sum::<f32>().sqrt();
    dot / (na * nb)
}

fn norm(v: &[f32]) -> f32 {
    v.iter().map(|x| x * x).sum::<f32>().sqrt()
}

fn check(embedder: &dyn EmbeddingProvider, expected_dim: usize) -> anyhow::Result<()> {
    anyhow::ensure!(
        embedder.dim() == expected_dim,
        "expected dim={expected_dim}, got {}",
        embedder.dim()
    );

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
        anyhow::ensure!(v.len() == expected_dim, "{name} has wrong length");
        let n = norm(v);
        anyhow::ensure!(
            (n - 1.0).abs() < 0.05,
            "{name} is not L2-normalized: norm={n:.4}"
        );
    }

    let sim_paraphrase = cosine(&paraphrase_a, &paraphrase_b);
    let sim_unrelated = cosine(&paraphrase_a, &unrelated);
    println!(
        "dim={expected_dim} sim_paraphrase={sim_paraphrase:.4} sim_unrelated={sim_unrelated:.4}"
    );
    anyhow::ensure!(
        sim_paraphrase > sim_unrelated,
        "FAIL: paraphrase similarity ({sim_paraphrase:.4}) did not beat unrelated ({sim_unrelated:.4}) \
         — embedding space looks broken, do not trust timing from this endpoint"
    );
    println!("PASS: paraphrase pair scores above unrelated pair, vectors are unit-normalized");
    Ok(())
}

fn main() -> anyhow::Result<()> {
    let url = std::env::var("OXIDE_EMBED_URL").expect("set OXIDE_EMBED_URL");
    let model = std::env::var("OXIDE_EMBED_MODEL").expect("set OXIDE_EMBED_MODEL");
    let truncate_dim = std::env::var("OXIDE_TRUNCATE_DIM").ok().map(|s| {
        s.parse::<usize>()
            .expect("OXIDE_TRUNCATE_DIM must be an integer")
    });

    let protocol = HttpPromptProtocol::Prefixed {
        query_prefix: "search_query: ",
        document_prefix: "search_document: ",
        label: "nomic-v2",
    };

    println!("== full-dimension check ==");
    let full = HttpEmbedder::new_with_protocol(&url, &model, protocol.clone(), None)?;
    check(&full, full.dim())?;

    if let Some(d) = truncate_dim {
        println!("\n== truncated ({d}d) check ==");
        let truncated = HttpEmbedder::new_with_protocol(&url, &model, protocol, Some(d))?;
        check(&truncated, d)?;
    }

    Ok(())
}
