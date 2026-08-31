//! Phase 3.4b cost evidence: bounded (files of retrieved symbols only) vs
//! unbounded (whole-repo) structural query latency on a real repo, not the
//! 7-file fixtures. `cargo run --example structural_cost --release -- <repo_dir>`

use oxide::embeddings::HashedEmbedder;
use oxide::index::{update_index, IndexBackend, SqliteStore};
use oxide::retrieval::{RetrievalEngine, RetrievalMode, SearchMode, SearchOptions};
use oxide::structural::{AstGrepProvider, FileSource, StructuralSearchProvider};
use oxide::symbols::Language;
use std::fs;
use std::path::{Path, PathBuf};
use std::time::Instant;

fn collect_all(root: &Path, ext: &str) -> Vec<(String, String)> {
    let mut out = Vec::new();
    fn rec(dir: &Path, root: &Path, ext: &str, out: &mut Vec<(String, String)>) {
        let Ok(rd) = fs::read_dir(dir) else { return };
        for entry in rd.flatten() {
            let p = entry.path();
            if p.is_dir() {
                if p.file_name().and_then(|n| n.to_str()) == Some(".oxide")
                    || p.file_name().and_then(|n| n.to_str()) == Some("node_modules")
                    || p.file_name().and_then(|n| n.to_str()) == Some(".git")
                {
                    continue;
                }
                rec(&p, root, ext, out);
            } else if p.extension().and_then(|e| e.to_str()) == Some(ext) {
                if let Ok(src) = fs::read_to_string(&p) {
                    out.push((
                        p.strip_prefix(root).unwrap().to_string_lossy().to_string(),
                        src,
                    ));
                }
            }
        }
    }
    rec(root, root, ext, &mut out);
    out
}

fn main() {
    let repo = std::env::args()
        .nth(1)
        .unwrap_or_else(|| "/tmp/darkreader_structural".into());
    let dir = PathBuf::from(&repo);

    let mut store = SqliteStore::open(Path::new(":memory:")).unwrap();
    let embedder = HashedEmbedder::default();
    let t_index = Instant::now();
    update_index(&dir, &mut store, &embedder).unwrap();
    println!(
        "indexed {repo} in {:.0}ms",
        t_index.elapsed().as_secs_f64() * 1000.0
    );

    let all_symbols = store.all_symbols().unwrap();
    println!("{} symbols total", all_symbols.len());
    let engine = RetrievalEngine::new(&store, &embedder);

    // Pick a handful of representative baseline queries — arbitrary, just
    // needs to return a realistic top-k file set to bound structural queries to.
    let queries = [
        "theme detection",
        "css style injection",
        "dark mode toggle",
        "message passing between scripts",
    ];

    let all_ts = collect_all(&dir, "ts");
    let all_tsx = collect_all(&dir, "tsx");
    println!(
        "whole-repo candidate set: {} .ts + {} .tsx files",
        all_ts.len(),
        all_tsx.len()
    );

    for q in queries {
        let hits = engine
            .search(
                q,
                &SearchOptions {
                    limit: 5,
                    mode: SearchMode::Hybrid,
                    expand: true,
                    retrieval_mode: RetrievalMode::default(),
                },
            )
            .unwrap();
        if hits.is_empty() {
            continue;
        }
        let anchor = &hits[0].symbol.name;
        let bounded_files: Vec<String> = hits.iter().map(|h| h.symbol.file.clone()).collect();
        let bounded: Vec<(String, String)> = bounded_files
            .iter()
            .filter_map(|f| {
                fs::read_to_string(dir.join(f))
                    .ok()
                    .map(|src| (f.clone(), src))
            })
            .collect();

        let bounded_sources: Vec<FileSource> = bounded
            .iter()
            .map(|(f, s)| FileSource { file: f, src: s })
            .collect();
        let t0 = Instant::now();
        let bounded_hits =
            AstGrepProvider.find_callers(Language::TypeScript, &bounded_sources, anchor);
        let bounded_ms = t0.elapsed().as_secs_f64() * 1000.0;

        let whole_sources: Vec<FileSource> = all_ts
            .iter()
            .map(|(f, s)| FileSource { file: f, src: s })
            .collect();
        let t1 = Instant::now();
        let whole_hits = AstGrepProvider.find_callers(Language::TypeScript, &whole_sources, anchor);
        let whole_ms = t1.elapsed().as_secs_f64() * 1000.0;

        println!(
            "query={q:?} anchor={anchor:?} bounded_files={} bounded_ms={bounded_ms:.2} ({} hits)  whole_files={} whole_ms={whole_ms:.2} ({} hits)",
            bounded.len(),
            bounded_hits.len(),
            all_ts.len(),
            whole_hits.len()
        );
    }
}
