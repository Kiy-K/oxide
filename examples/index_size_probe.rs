//! Disk/vector-size probe for the CPU embedding survey
//! (docs/cpu-embedding-survey/): indexes one fixed fixture repo under a given
//! embedder and reports the resulting `index.db` size. Exists because the
//! shipped `oxide index` CLI only drives `open_embedder` (Qwen3-only HTTP,
//! or a native-embed profile) and has no path for
//! `HttpEmbedder::new_with_protocol` (Nomic) — this bypasses the CLI and
//! calls `update_index` directly, exactly like `eval.rs` already does, so no
//! CLI/production surface needs to change for this measurement to exist.
//!
//! Usage: `cargo run --release [--features native-embed] --example
//! index_size_probe -- <repo-path> <profile>`
//! profile: hashed | native:<fastembed-profile-name> | nomic:768 | nomic:256
//! (nomic:* reads OXIDE_EMBED_URL/OXIDE_EMBED_MODEL, defaulting to the
//! survey's own local server convention if unset)

use oxide::embeddings::{EmbeddingProvider, HashedEmbedder, HttpEmbedder, HttpPromptProtocol};
use oxide::index::{update_index, SqliteStore};
use std::path::Path;

fn nomic_protocol() -> HttpPromptProtocol {
    HttpPromptProtocol::Prefixed {
        query_prefix: "search_query: ",
        document_prefix: "search_document: ",
        label: "nomic-v2",
    }
}

fn run(repo: &Path, embedder: &dyn EmbeddingProvider, db_path: &Path) -> anyhow::Result<()> {
    let mut store = SqliteStore::open(db_path)?;
    update_index(repo, &mut store, embedder)?;
    drop(store);
    let size = std::fs::metadata(db_path)?.len();
    println!(
        "profile={} dim={} index_db_bytes={size}",
        embedder.name(),
        embedder.dim()
    );
    Ok(())
}

fn main() -> anyhow::Result<()> {
    let mut args = std::env::args().skip(1);
    let repo = args
        .next()
        .expect("usage: index_size_probe <repo> <profile>");
    let profile = args
        .next()
        .expect("usage: index_size_probe <repo> <profile>");
    let repo = Path::new(&repo);

    let tmp = tempfile::tempdir()?;
    let db_path = tmp.path().join("index.db");

    match profile.as_str() {
        "hashed" => run(repo, &HashedEmbedder::default(), &db_path)?,
        "nomic:768" => {
            let url = std::env::var("OXIDE_EMBED_URL")
                .unwrap_or_else(|_| "http://127.0.0.1:8192/v1/embeddings".to_string());
            let model = std::env::var("OXIDE_EMBED_MODEL")
                .unwrap_or_else(|_| "nomic-v2-moe-Q8_0".to_string());
            let e = HttpEmbedder::new_with_protocol(&url, &model, nomic_protocol(), None)?;
            run(repo, &e, &db_path)?
        }
        "nomic:256" => {
            let url = std::env::var("OXIDE_EMBED_URL")
                .unwrap_or_else(|_| "http://127.0.0.1:8192/v1/embeddings".to_string());
            let model = std::env::var("OXIDE_EMBED_MODEL")
                .unwrap_or_else(|_| "nomic-v2-moe-Q8_0".to_string());
            let e = HttpEmbedder::new_with_protocol(&url, &model, nomic_protocol(), Some(256))?;
            run(repo, &e, &db_path)?
        }
        other => {
            #[cfg(feature = "native-embed")]
            if let Some(native_profile) = other.strip_prefix("native:") {
                let e = oxide::embeddings::NativeEmbedder::new(
                    native_profile,
                    oxide::embeddings::GemmaQueryPrompt::Bare,
                )?;
                run(repo, &e, &db_path)?;
                return Ok(());
            }
            anyhow::bail!("unknown profile {other:?}; expected hashed | native:<name> | nomic:768 | nomic:256")
        }
    }
    Ok(())
}
