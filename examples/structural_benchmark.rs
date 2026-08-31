//! Phase 3.4b evidence: baseline hybrid retrieval vs baseline + structural
//! enrichment, on fixtures/structural_benchmark.json's new tasks (multi-
//! implementor and cross-file-caller relationships fixtures/benchmark.json
//! has no headroom to test — 10/11 of its tasks already score recall 1.000).
//! `cargo run --example structural_benchmark --release`

use oxide::embeddings::HashedEmbedder;
use oxide::index::{update_index, IndexBackend, SqliteStore};
use oxide::retrieval::{RetrievalEngine, RetrievalMode, SearchMode, SearchOptions};
use oxide::structural::{AstGrepProvider, FileSource, StructuralSearchProvider};
use oxide::symbols::{Language, Symbol};
use serde::Deserialize;
use std::collections::{HashMap, HashSet};
use std::fs;
use std::path::{Path, PathBuf};

#[derive(Deserialize)]
struct Config {
    repos: HashMap<String, String>,
    queries: Vec<Task>,
    k: usize,
}

#[derive(Deserialize)]
struct Task {
    id: String,
    repo: String,
    text: String,
    anchor_symbol: String,
    anchor_lang: String,
    structural_intent: String,
    relevant: Vec<String>,
}

fn collect_source_files(root: &Path, ext: &str) -> Vec<(String, String)> {
    let mut out = Vec::new();
    fn rec(dir: &Path, root: &Path, ext: &str, out: &mut Vec<(String, String)>) {
        let Ok(rd) = fs::read_dir(dir) else { return };
        for entry in rd.flatten() {
            let p = entry.path();
            if p.is_dir() {
                if p.file_name().and_then(|n| n.to_str()) == Some(".oxide") {
                    continue;
                }
                rec(&p, root, ext, out);
            } else if p.extension().and_then(|e| e.to_str()) == Some(ext) {
                if let Ok(src) = fs::read_to_string(&p) {
                    let rel = p
                        .strip_prefix(root)
                        .unwrap_or(&p)
                        .to_string_lossy()
                        .to_string();
                    out.push((rel, src));
                }
            }
        }
    }
    rec(root, root, ext, &mut out);
    out
}

/// Resolve a structural hit (a byte-range match, possibly just the call
/// expression itself, not a definition) to the enclosing indexed symbol's
/// qualified name via line-range containment — the smallest symbol whose
/// span contains the hit, matching parent-attribution the same way
/// tags.rs's containment stack does for definitions.
fn enclosing_symbol<'a>(symbols: &'a [Symbol], file: &str, line: u32) -> Option<&'a Symbol> {
    symbols
        .iter()
        .filter(|s| s.file == file && s.start_line <= line && line <= s.end_line)
        .min_by_key(|s| s.end_line - s.start_line)
}

fn main() {
    let config: Config =
        serde_json::from_str(&fs::read_to_string("fixtures/structural_benchmark.json").unwrap())
            .unwrap();
    let k = config.k;

    println!(
        "{:<28} {:>16} {:>18} {:>10} {:>10}",
        "task", "baseline_recall", "+structural_recall", "base_hits", "struct_add"
    );

    for (name, path) in &config.repos {
        let dir = PathBuf::from(path);
        let mut store = SqliteStore::open(Path::new(":memory:")).unwrap();
        let embedder = HashedEmbedder::default();
        update_index(&dir, &mut store, &embedder).unwrap();
        let all_symbols = store.all_symbols().unwrap();
        let engine = RetrievalEngine::new(&store, &embedder);

        for task in config.queries.iter().filter(|t| &t.repo == name) {
            let opts = SearchOptions {
                limit: k,
                mode: SearchMode::Hybrid,
                expand: true,
                retrieval_mode: RetrievalMode::default(),
            };
            let t0 = std::time::Instant::now();
            let baseline_hits = engine.search(&task.text, &opts).unwrap();
            let baseline_ms = t0.elapsed().as_secs_f64() * 1000.0;
            let baseline_ids: Vec<String> = baseline_hits
                .iter()
                .map(|h| format!("{}#{}", h.symbol.file, h.symbol.qualified_name))
                .collect();
            let anchor_was_retrieved = baseline_hits
                .iter()
                .any(|h| h.symbol.name == task.anchor_symbol);

            let lang = match task.anchor_lang.as_str() {
                "python" => Language::Python,
                "typescript" => Language::TypeScript,
                "tsx" => Language::Tsx,
                other => panic!("unknown lang {other}"),
            };
            let ext = match lang {
                Language::Python => "py",
                Language::TypeScript => "ts",
                Language::Tsx => "tsx",
            };
            let files = collect_source_files(&dir, ext);
            let file_sources: Vec<FileSource> = files
                .iter()
                .map(|(rel, src)| FileSource { file: rel, src })
                .collect();

            let t1 = std::time::Instant::now();
            let structural_hits = match task.structural_intent.as_str() {
                "implementors" => {
                    AstGrepProvider.find_implementors(lang, &file_sources, &task.anchor_symbol)
                }
                "callers" => AstGrepProvider.find_callers(lang, &file_sources, &task.anchor_symbol),
                other => panic!("unknown intent {other}"),
            };
            let structural_ms = t1.elapsed().as_secs_f64() * 1000.0;

            let structural_ids: Vec<String> = structural_hits
                .iter()
                .filter_map(|h| enclosing_symbol(&all_symbols, &h.file, h.start_line))
                .map(|s| format!("{}#{}", s.file, s.qualified_name))
                .collect();

            let gold: HashSet<&str> = task.relevant.iter().map(|s| s.as_str()).collect();
            let baseline_set: HashSet<&str> = baseline_ids.iter().map(|s| s.as_str()).collect();
            let baseline_recall =
                baseline_set.intersection(&gold).count() as f32 / gold.len().max(1) as f32;

            let mut combined_set = baseline_set.clone();
            for id in &structural_ids {
                combined_set.insert(id.as_str());
            }
            let combined_recall =
                combined_set.intersection(&gold).count() as f32 / gold.len().max(1) as f32;
            let newly_added: Vec<&str> = structural_ids
                .iter()
                .map(|s| s.as_str())
                .filter(|id| !baseline_set.contains(id))
                .collect();

            println!(
                "{:<28} {:>16.3} {:>18.3} {:>10} {:>10}",
                task.id,
                baseline_recall,
                combined_recall,
                baseline_hits.len(),
                newly_added.len()
            );
            println!(
                "  anchor `{}` in baseline top-{}: {}  |  baseline_ms={:.2} structural_ms={:.2}",
                task.anchor_symbol, k, anchor_was_retrieved, baseline_ms, structural_ms
            );
            println!("  structural additions: {newly_added:?}");
            println!("  baseline hits: {baseline_ids:?}");
        }
    }
}
