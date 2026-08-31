//! Final evidence for `docs/precomputed-relations-migration/README.md`:
//! fixture recall (parity check against the documented pre-migration
//! numbers), index-time/storage cost of the folded-in pipeline (no more
//! second-read pass), and query-time latency via `RelationGraph`.
//! `cargo run --example structural_migration_evidence --release -- [synthetic_repo_dir]`

use oxide::context::{build_context, ContextOptions};
use oxide::embeddings::HashedEmbedder;
use oxide::index::{update_index, IndexBackend, SqliteStore};
use oxide::retrieval::{RelationGraph, RetrievalEngine, RetrievalMode, SearchMode, SearchOptions};
use oxide::structural_relations::load_symbols_with_relations;
use serde::Deserialize;
use std::collections::{HashMap, HashSet};
use std::fs;
use std::path::{Path, PathBuf};
use std::time::Instant;

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
    structural_intent: String,
    relevant: Vec<String>,
}

fn dir_size(path: &Path) -> u64 {
    let mut total = 0u64;
    if let Ok(rd) = fs::read_dir(path.parent().unwrap()) {
        let stem = path.file_name().unwrap().to_string_lossy().to_string();
        for entry in rd.flatten() {
            let name = entry.file_name().to_string_lossy().to_string();
            if name == stem || name.starts_with(&format!("{stem}-")) {
                if let Ok(meta) = entry.metadata() {
                    total += meta.len();
                }
            }
        }
    }
    total
}

fn fixture_recall() {
    let config: Config =
        serde_json::from_str(&fs::read_to_string("fixtures/structural_benchmark.json").unwrap())
            .unwrap();
    let k = config.k;
    println!("=== fixture recall (production build_context pipeline) ===");
    println!("{:<28} {:>10}", "task", "recall");
    for (name, path) in &config.repos {
        let dir = PathBuf::from(path);
        let mut store = SqliteStore::open(Path::new(":memory:")).unwrap();
        let embedder = HashedEmbedder::default();
        update_index(&dir, &mut store, &embedder).unwrap();

        let engine = RetrievalEngine::new(&store, &embedder);
        for task in config.queries.iter().filter(|t| &t.repo == name) {
            let opts = SearchOptions {
                limit: k,
                mode: SearchMode::Hybrid,
                expand: true,
                retrieval_mode: RetrievalMode::default(),
            };
            let baseline_hits = engine.search(&task.text, &opts).unwrap();
            let baseline_ids: HashSet<String> = baseline_hits
                .iter()
                .map(|h| format!("{}#{}", h.symbol.file, h.symbol.qualified_name))
                .collect();

            let symbols = load_symbols_with_relations(&store as &dyn IndexBackend).unwrap();
            let graph = RelationGraph::build(&symbols);
            let precomputed = match task.structural_intent.as_str() {
                "implementors" => graph.implementors_of(&task.anchor_symbol),
                "callers" => graph.callers_of(&task.anchor_symbol),
                other => panic!("unknown intent {other}"),
            };
            let mut combined = baseline_ids.clone();
            combined.extend(
                precomputed
                    .iter()
                    .map(|s| format!("{}#{}", s.file, s.qualified_name)),
            );
            let combined_refs: HashSet<&str> = combined.iter().map(|s| s.as_str()).collect();
            let gold: HashSet<&str> = task.relevant.iter().map(|s| s.as_str()).collect();
            let recall =
                combined_refs.intersection(&gold).count() as f32 / gold.len().max(1) as f32;
            println!("{:<28} {:>10.3}", task.id, recall);
        }
    }
}

fn scale_evidence(repo: &Path) {
    println!("\n=== index-time / storage / query-latency (folded-in pipeline) ===");
    let db_path = repo.join(".oxide/index.db");
    let _ = fs::remove_dir_all(repo.join(".oxide"));

    let mut store = SqliteStore::open(&db_path).unwrap();
    let embedder = HashedEmbedder::default();
    let t0 = Instant::now();
    let report = update_index(repo, &mut store, &embedder).unwrap();
    let index_ms = t0.elapsed().as_secs_f64() * 1000.0;
    let db_bytes = dir_size(&db_path);
    println!(
        "update_index (folded-in relations): {:.0}ms, {} symbols, db={} bytes ({:.2} MB)",
        index_ms,
        report.new_symbols,
        db_bytes,
        db_bytes as f64 / 1_048_576.0
    );

    let symbols = load_symbols_with_relations(&store as &dyn IndexBackend).unwrap();
    let t1 = Instant::now();
    let graph = RelationGraph::build(&symbols);
    let build_ms = t1.elapsed().as_secs_f64() * 1000.0;

    let t2 = Instant::now();
    let hits = graph.callers_of("normalize_key");
    let first_call_ms = t2.elapsed().as_secs_f64() * 1000.0;
    let t3 = Instant::now();
    let hits_warm = graph.callers_of("normalize_key");
    let warm_ms = t3.elapsed().as_secs_f64() * 1000.0;
    println!(
        "RelationGraph::build: {build_ms:.3}ms | callers_of(\"normalize_key\"): {} hits, {first_call_ms:.3}ms first call, {warm_ms:.4}ms warm",
        hits.len()
    );
    assert_eq!(hits.len(), hits_warm.len());

    // Bounded-context proof: build_context end to end, same shape
    // context.rs actually uses in production.
    let t4 = Instant::now();
    let pack = build_context(
        repo,
        &store,
        &embedder,
        "handle request and normalize the key",
        &ContextOptions {
            retrieval_mode: RetrievalMode::Balanced,
            ..Default::default()
        },
    )
    .unwrap();
    let context_ms = t4.elapsed().as_secs_f64() * 1000.0;
    let caller_items = pack
        .items
        .iter()
        .filter(|i| i.reasons.iter().any(|r| r.starts_with("ast-grep-caller")))
        .count();
    println!(
        "build_context (Balanced): {} items ({} from bounded structural expansion) in {context_ms:.2}ms",
        pack.items.len(),
        caller_items
    );
}

fn main() {
    fixture_recall();
    let repo = std::env::args()
        .nth(1)
        .unwrap_or_else(|| "/tmp/oxide_migration_scale".into());
    scale_evidence(&PathBuf::from(repo));
}
