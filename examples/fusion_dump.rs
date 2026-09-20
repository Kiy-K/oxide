//! Ranking-research harness (docs/ranking-fusion-eval): for each task in a
//! JSONL file, dump the **exact fusion inputs** — the lexical and semantic
//! top-200 candidate lists with their raw scores, computed the way
//! `RetrievalEngine::search` computes them — plus the production fused
//! list, the structural neighbors of the top seeds, and the production
//! context pack, so fusion/reranking challengers can be evaluated offline
//! without touching production ranking.
//!
//! Usage: fusion_dump <repo_root> <tasks.jsonl> > dump.jsonl
//! Each input line: {"id": "...", "query": "...", ...}; each output line
//! echoes `id` and adds `lexical`, `semantic`, `fused`, `neighbors`, `pack`.
use oxide::context::{build_context_with, ContextOptions};
use oxide::embeddings::open_embedder;
use oxide::retrieval::{RetrievalEngine, RetrievalMode, SearchMode, SearchOptions, SymbolSnapshot};
use oxide::storage::{IndexBackend, SqliteStore};
use serde_json::{json, Value};
use std::io::{BufRead, Write};

const CANDIDATES: usize = 200;

fn cmp_score_id(a: &(u64, f32), b: &(u64, f32)) -> std::cmp::Ordering {
    b.1.partial_cmp(&a.1)
        .unwrap_or(std::cmp::Ordering::Equal)
        .then_with(|| a.0.cmp(&b.0))
}

fn main() -> anyhow::Result<()> {
    let args: Vec<String> = std::env::args().collect();
    let root = std::path::PathBuf::from(&args[1]).canonicalize()?;
    let tasks = std::io::BufReader::new(std::fs::File::open(&args[2])?);
    let embedder = open_embedder(None)?;
    let store = SqliteStore::open_read_only(&root.join(".oxide/index.db"))?;
    let n = store.symbol_count()?;
    let snapshot = SymbolSnapshot::load(&store)?;
    let engine = RetrievalEngine::with_snapshot(&store, embedder.as_ref(), &snapshot);
    let graph = engine.relation_graph()?;
    let out = std::io::stdout();
    let mut out = out.lock();
    let sym_id = |id: u64| -> Value {
        snapshot
            .get(id)
            .map(|s| json!(format!("{}#{}", s.file, s.qualified_name)))
            .unwrap_or(Value::Null)
    };

    for line in tasks.lines() {
        let line = line?;
        if line.trim().is_empty() {
            continue;
        }
        let task: Value = serde_json::from_str(&line)?;
        let query = task["query"].as_str().unwrap_or_default().to_string();

        // Lexical: same BM25 parameters and top-K selection as the engine.
        let lq = oxide::lexical::prepare_from_store(&store, n, &query)?;
        let (lex_scores, _) = oxide::lexical::score(&lq, 1.5, 0.75);
        let mut lex: Vec<(u64, f32)> = lex_scores.iter().map(|(id, s)| (*id, s.0)).collect();
        lex.sort_by(cmp_score_id);
        lex.truncate(CANDIDATES);

        // Semantic: exhaustive dot product, same sequential f32 sum.
        let qv = embedder.embed_query(&query);
        let mut sem: Vec<(u64, f32)> = Vec::new();
        store.for_each_embedding(&mut |id, dim, bytes| {
            let len = dim.min(bytes.len() / 4);
            if len != qv.len() {
                return;
            }
            let mut dot = 0.0f32;
            for (a, c) in qv.iter().zip(bytes.as_chunks::<4>().0.iter().take(len)) {
                dot += a * f32::from_le_bytes(*c);
            }
            sem.push((id, dot));
        })?;
        sem.sort_by(cmp_score_id);
        sem.truncate(CANDIDATES);

        // Production fusion (hybrid, no expansion, full fused list).
        let fused = engine.search(
            &query,
            &SearchOptions {
                limit: 2 * CANDIDATES,
                mode: SearchMode::Hybrid,
                expand: false,
                retrieval_mode: RetrievalMode::Balanced,
            },
        )?;
        // Production hybrid with expansion, as `oxide search` returns it.
        let expanded = engine.search(
            &query,
            &SearchOptions {
                limit: 2 * CANDIDATES,
                mode: SearchMode::Hybrid,
                expand: true,
                retrieval_mode: RetrievalMode::Balanced,
            },
        )?;
        // Structural neighbors of the top-5 fused seeds: the evidence a
        // bounded evidence-aware reranker may use.
        let neighbors: Vec<Value> = fused
            .iter()
            .take(5)
            .map(|h| {
                json!({
                    "seed": format!("{}#{}", h.symbol.file, h.symbol.qualified_name),
                    "neighbors": graph.neighbors(&h.symbol).iter()
                        .map(|(rel, s)| json!([rel, format!("{}#{}", s.file, s.qualified_name)]))
                        .collect::<Vec<_>>(),
                })
            })
            .collect();
        let pack = build_context_with(&root, &engine, &query, &ContextOptions::default())?;
        // Snippets for the candidates an external judge may be asked about:
        // the top-10 of each channel and of the fused list.
        let mut judged: Vec<u64> = Vec::new();
        judged.extend(lex.iter().take(10).map(|(id, _)| *id));
        judged.extend(sem.iter().take(10).map(|(id, _)| *id));
        judged.extend(fused.iter().take(10).map(|h| h.symbol.id()));
        let mut snippets = serde_json::Map::new();
        for id in judged {
            if let Some(s) = snapshot.get(id) {
                let key = format!("{}#{}", s.file, s.qualified_name);
                if !snippets.contains_key(&key) {
                    let text = oxide::retrieval::read_snippet(
                        &root.join(&s.file),
                        s.start_line,
                        s.end_line,
                        30,
                    );
                    snippets.insert(key, json!({"signature": s.signature, "kind": format!("{:?}", s.kind), "snippet": text}));
                }
            }
        }

        let rec = json!({
            "id": task["id"],
            "lexical": lex.iter().map(|(id, s)| json!([sym_id(*id), s])).collect::<Vec<_>>(),
            "semantic": sem.iter().map(|(id, s)| json!([sym_id(*id), s])).collect::<Vec<_>>(),
            "fused": fused.iter().map(|h| json!([format!("{}#{}", h.symbol.file, h.symbol.qualified_name), h.score, h.reasons])).collect::<Vec<_>>(),
            "expanded": expanded.iter().map(|h| json!([format!("{}#{}", h.symbol.file, h.symbol.qualified_name), h.score, h.reasons])).collect::<Vec<_>>(),
            "neighbors": neighbors,
            "snippets": snippets,
            "pack": {
                "used_tokens": pack.used_tokens,
                "budget_tokens": pack.budget_tokens,
                "items": pack.items.iter().map(|i| json!({
                    "id": format!("{}#{}", i.symbol.file, i.symbol.qualified_name),
                    "role": format!("{:?}", i.role), "score": i.score,
                    "est_tokens": i.est_tokens, "reasons": i.reasons,
                })).collect::<Vec<_>>(),
                "omitted": pack.omitted.iter().map(|o| json!([o.id, o.why])).collect::<Vec<_>>(),
            },
        });
        writeln!(out, "{}", serde_json::to_string(&rec)?)?;
    }
    Ok(())
}
