//! Where does `LexicalIndex::build` actually spend its time?
//!
//! `docs/storage-backend-eval/baseline.md` measured `oxide search` scaling
//! linearly at ~11 µs/symbol and attributed it to the per-process lexical
//! rebuild, but never split that cost. The persisted-lexical design depends
//! on the split: if it is dominated by reading symbol bodies off disk, a
//! store that keeps the token stream is enough; if it is dominated by
//! building the posting map, only a persisted *inverted* index helps.
//!
//! Usage: `cargo run --release --example lexical_build_probe -- <repo-path>`
//! (the repo must already have a `.oxide/index.db`).

use oxide::index::{IndexBackend, SqliteStore};
use oxide::lexical::LexicalIndex;
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::time::Instant;

fn main() -> anyhow::Result<()> {
    let repo = PathBuf::from(std::env::args().nth(1).expect("usage: <repo-path>"));
    let store = SqliteStore::open_read_only(&repo.join(".oxide/index.db"))?;

    let t = Instant::now();
    let symbols = store.all_symbols()?;
    let load_ms = t.elapsed().as_secs_f64() * 1000.0;

    let root = store.get_meta("root")?.map(PathBuf::from);
    let root = root.as_deref().unwrap_or(Path::new("."));

    // Body reading alone, same per-file caching `LexicalIndex::build` uses.
    let t = Instant::now();
    let mut bodies: HashMap<&str, String> = HashMap::new();
    let mut body_bytes = 0usize;
    for s in &symbols {
        let b = bodies
            .entry(&s.file)
            .or_insert_with(|| std::fs::read_to_string(root.join(&s.file)).unwrap_or_default());
        body_bytes += b.len().min(1);
    }
    let read_ms = t.elapsed().as_secs_f64() * 1000.0;

    // Tokenization alone: every field the builder tokenizes, emitting into a
    // counter instead of a posting map, so this excludes map maintenance.
    let t = Instant::now();
    let mut tokens = 0usize;
    for s in &symbols {
        let mut count = |text: &str| {
            oxide::embeddings::tokenize_into(text, &mut |_| tokens += 1);
        };
        count(&s.qualified_name);
        count(&s.name);
        count(&s.signature);
        count(&s.file.replace(['/', '.', ':'], " "));
        for r in &s.references {
            count(r);
        }
        for i in &s.imports {
            count(i);
        }
        if let Some(body) = bodies.get(s.file.as_str()) {
            let start = (s.start_line as usize).saturating_sub(1);
            let lines: Vec<&str> = body.lines().collect();
            if start < lines.len() {
                let end = (s.end_line as usize).min(lines.len());
                count(&lines[start..end].join("\n"));
            }
        }
    }
    let tokenize_ms = t.elapsed().as_secs_f64() * 1000.0;

    // The real thing, warm page cache (bodies were just read above).
    let t = Instant::now();
    let idx = LexicalIndex::build(&symbols, Some(root));
    let build_ms = t.elapsed().as_secs_f64() * 1000.0;
    std::hint::black_box(&idx);

    println!(
        "symbols={} files={} distinct_tokens_emitted={tokens}",
        symbols.len(),
        bodies.len()
    );
    println!("all_symbols      {load_ms:8.1} ms");
    println!("read bodies      {read_ms:8.1} ms  ({body_bytes} files touched)");
    println!("tokenize only    {tokenize_ms:8.1} ms");
    println!("LexicalIndex     {build_ms:8.1} ms  (read + tokenize + posting map)");
    println!(
        "posting map      {:8.1} ms  (build - read - tokenize)",
        build_ms - read_ms - tokenize_ms
    );
    Ok(())
}
