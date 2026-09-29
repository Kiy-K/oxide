//! Developer-intent evidence research harness (issue #30). Research-only:
//! nothing here is wired into production, and nothing is written to the
//! corpus index (it is opened read-only).
//!
//! Per task it dumps two things side by side:
//!   1. the **frozen production** symbol pipeline, exactly as shipped —
//!      BM25 top-200, semantic top-200, `RetrievalEngine::search` RRF
//!      fusion (no expansion) and the `build_context_with` pack;
//!   2. a **separate, isolated intent channel** over developer-intent
//!      evidence records produced by `extract_intent.py` (only `keep:true`
//!      records — secret-like/generated/vendored/noise records are never
//!      embedded): its own BM25 (same tokenizer, k1=1.5, b=0.75, same IDF
//!      formula as `lexical::score`) and its own exact dot-product scan with
//!      the production embedder (document vectors from the evidence text
//!      alone, query vector identical to the production query vector).
//!
//! Fusion of the two channels is left to the offline scorer so every
//! representation (separate nodes / symbol-attached / hybrid) is computed
//! from one dump and the production lists are never altered here.
//!
//! Usage:
//!   intent_dump <repo_root> <tasks.jsonl> --evidence <ev.jsonl>
//!       [--cache <emb-cache.db>] [--no-evidence] > dump.jsonl
use oxide::context::{build_context_with, ContextOptions};
use oxide::embedding_cache::SharedEmbeddingCache;
use oxide::embeddings::{
    tokenize, EmbeddingProvider, EmbeddingSpaceFingerprint, GemmaQueryPrompt, NativeEmbedder,
};
use oxide::index::{EmbeddingSpace, SpaceRead};
use oxide::retrieval::{RetrievalEngine, RetrievalMode, SearchMode, SearchOptions, SymbolSnapshot};
use oxide::storage::{IndexRead, SqliteStore};
use serde_json::{json, Value};
use std::collections::HashMap;
use std::io::{BufRead, Write};
use std::time::Instant;

const CANDIDATES: usize = 200;
const K1: f32 = 1.5;
const B: f32 = 0.75;

fn cmp_score(a: &(usize, f32), b: &(usize, f32)) -> std::cmp::Ordering {
    b.1.partial_cmp(&a.1)
        .unwrap_or(std::cmp::Ordering::Equal)
        .then_with(|| a.0.cmp(&b.0))
}

fn cmp_score_id(a: &(u64, f32), b: &(u64, f32)) -> std::cmp::Ordering {
    b.1.partial_cmp(&a.1)
        .unwrap_or(std::cmp::Ordering::Equal)
        .then_with(|| a.0.cmp(&b.0))
}

fn arg(args: &[String], flag: &str) -> Option<String> {
    args.iter()
        .position(|a| a == flag)
        .and_then(|i| args.get(i + 1).cloned())
}

fn peak_rss_kb() -> u64 {
    std::fs::read_to_string("/proc/self/status")
        .ok()
        .and_then(|s| {
            s.lines()
                .find(|l| l.starts_with("VmHWM:"))
                .and_then(|l| l.split_whitespace().nth(1)?.parse().ok())
        })
        .unwrap_or(0)
}

/// Delegates to a shared cache (queries are never cached by it).
struct Shared(&'static SharedEmbeddingCache);

impl EmbeddingProvider for Shared {
    fn name(&self) -> &str {
        self.0.name()
    }
    fn dim(&self) -> usize {
        self.0.dim()
    }
    fn embed(&self, text: &str) -> Vec<f32> {
        self.0.embed(text)
    }
    fn embed_batch(&self, texts: &[String]) -> Vec<Vec<f32>> {
        self.0.embed_batch(texts)
    }
    fn embed_query(&self, text: &str) -> Vec<f32> {
        self.0.embed_query(text)
    }
    fn embed_document(&self, text: &str) -> Vec<f32> {
        self.0.embed_document(text)
    }
    fn embed_documents(&self, texts: &[String]) -> Vec<Vec<f32>> {
        self.0.embed_documents(texts)
    }
    fn fingerprint(&self) -> EmbeddingSpaceFingerprint {
        self.0.fingerprint()
    }
}

/// In-memory BM25 over evidence records; same formula as `lexical::score`
/// (every query-token occurrence scores, as production does).
struct EvidenceBm25 {
    postings: HashMap<String, Vec<(usize, u32)>>,
    len: Vec<f32>,
    avg: f32,
}

impl EvidenceBm25 {
    fn build(texts: &[String]) -> Self {
        let mut postings: HashMap<String, Vec<(usize, u32)>> = HashMap::new();
        let mut len = Vec::with_capacity(texts.len());
        for (i, t) in texts.iter().enumerate() {
            let mut tf: HashMap<String, u32> = HashMap::new();
            let toks = tokenize(t);
            len.push(toks.len() as f32);
            for tok in toks {
                *tf.entry(tok).or_default() += 1;
            }
            for (tok, n) in tf {
                postings.entry(tok).or_default().push((i, n));
            }
        }
        let avg = len.iter().sum::<f32>() / len.len().max(1) as f32;
        Self { postings, len, avg }
    }

    fn search(&self, query: &str) -> Vec<(usize, f32)> {
        let n = self.len.len().max(1) as f32;
        let mut scores: HashMap<usize, f32> = HashMap::new();
        for tok in tokenize(query) {
            let Some(docs) = self.postings.get(&tok) else {
                continue;
            };
            let df = docs.len() as f32;
            let idf = ((n - df + 0.5) / (df + 0.5)).max(0.0).ln_1p();
            for &(doc, tf) in docs {
                let dl = self.len[doc];
                let tf_norm = tf as f32 * (K1 + 1.0)
                    / (tf as f32 + K1 * (1.0 - B + B * dl / self.avg.max(1.0)));
                *scores.entry(doc).or_default() += idf * tf_norm;
            }
        }
        let mut out: Vec<(usize, f32)> = scores.into_iter().collect();
        out.sort_by(cmp_score);
        out.truncate(CANDIDATES);
        out
    }
}

fn main() -> anyhow::Result<()> {
    let args: Vec<String> = std::env::args().collect();
    let root = std::path::PathBuf::from(&args[1]).canonicalize()?;
    let tasks = std::io::BufReader::new(std::fs::File::open(&args[2])?);
    let db = arg(&args, "--db")
        .map(std::path::PathBuf::from)
        .unwrap_or_else(|| root.join(".oxide/index.db"));
    let no_evidence = args.iter().any(|a| a == "--no-evidence");

    let t0 = Instant::now();
    let native: Box<dyn EmbeddingProvider> = Box::new(NativeEmbedder::new(
        oxide::embeddings::DEFAULT_NATIVE_PROFILE,
        GemmaQueryPrompt::Bare,
    )?);
    let model_load_ms = t0.elapsed().as_secs_f64() * 1e3;
    let (embedder, cache): (
        Box<dyn EmbeddingProvider>,
        Option<&'static SharedEmbeddingCache>,
    ) = match arg(&args, "--cache") {
        Some(p) => {
            let c: &'static SharedEmbeddingCache = Box::leak(Box::new(SharedEmbeddingCache::open(
                native,
                std::path::Path::new(&p),
            )?));
            (Box::new(Shared(c)), Some(c))
        }
        None => (native, None),
    };

    // Before the (slow) evidence embedding, so a bad index fails fast.
    {
        let store = SqliteStore::open_read_only(&db)?;
        let space = EmbeddingSpace::read(&store)?.readable_by(&embedder.fingerprint());
        anyhow::ensure!(
            space == SpaceRead::Compatible,
            "refusing to search: stored embedding space is {space:?} for provider {:?}; run `oxide index` with it first",
            embedder.name()
        );
    }

    // ---- evidence: load kept records, BM25, embed ----
    let mut ev_ids: Vec<String> = Vec::new();
    let mut ev_texts: Vec<String> = Vec::new();
    if !no_evidence {
        let path = arg(&args, "--evidence").expect("--evidence <ev.jsonl> (or --no-evidence)");
        for line in std::io::BufReader::new(std::fs::File::open(path)?).lines() {
            let v: Value = serde_json::from_str(&line?)?;
            if v["keep"] != Value::Bool(true) {
                continue;
            }
            let text = v["text"].as_str().unwrap_or_default();
            let heading = v["heading"].as_str().unwrap_or_default();
            // Doc chunks carry their heading path; code comments are text only.
            let t = if heading.is_empty() {
                text.to_string()
            } else {
                format!("{heading}\n{text}")
            };
            ev_ids.push(v["eid"].as_str().unwrap_or_default().to_string());
            ev_texts.push(t);
        }
    }
    let t1 = Instant::now();
    let bm25 = EvidenceBm25::build(&ev_texts);
    let bm25_build_ms = t1.elapsed().as_secs_f64() * 1e3;
    let t2 = Instant::now();
    let ev_vecs: Vec<Vec<f32>> = if ev_texts.is_empty() {
        Vec::new()
    } else {
        embedder.embed_documents(&ev_texts)
    };
    let embed_ms = t2.elapsed().as_secs_f64() * 1e3;
    let ev_chars: usize = ev_texts.iter().map(String::len).sum();
    let rss_after_evidence = peak_rss_kb();

    // ---- production pipeline, read-only ----
    let store = SqliteStore::open_read_only(&db)?;
    let n = store.symbol_count()?;
    let snapshot = SymbolSnapshot::load(&store)?;
    let engine = RetrievalEngine::with_snapshot(&store, embedder.as_ref(), &snapshot);
    let out = std::io::stdout();
    let mut out = out.lock();
    let sym_key = |id: u64| -> Value {
        snapshot
            .get(id)
            .map(|s| json!(format!("{}#{}", s.file, s.qualified_name)))
            .unwrap_or(Value::Null)
    };
    let mut intent_ms_total = 0.0;
    let mut tasks_n = 0usize;

    for line in tasks.lines() {
        let line = line?;
        if line.trim().is_empty() {
            continue;
        }
        let task: Value = serde_json::from_str(&line)?;
        let query = task["query"].as_str().unwrap_or_default().to_string();

        let lq = oxide::lexical::prepare_from_store(&store, n, &query)?;
        let (lex_scores, _) = oxide::lexical::score(&lq, K1, B);
        let mut lex: Vec<(u64, f32)> = lex_scores.iter().map(|(id, s)| (*id, s.0)).collect();
        lex.sort_by(cmp_score_id);
        lex.truncate(CANDIDATES);

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

        let ts = Instant::now();
        let fused = engine.search(
            &query,
            &SearchOptions {
                limit: 2 * CANDIDATES,
                mode: SearchMode::Hybrid,
                expand: false,
                retrieval_mode: RetrievalMode::Balanced,
            },
        )?;
        let search_ms = ts.elapsed().as_secs_f64() * 1e3;
        let tc = Instant::now();
        let pack = build_context_with(&root, &engine, &query, &ContextOptions::default())?;
        let context_ms = tc.elapsed().as_secs_f64() * 1e3;

        // Intent channel: reuses the production query vector (same model,
        // same prefix), so its extra query-time cost is BM25 + one scan.
        let ti = Instant::now();
        let ilex = bm25.search(&query);
        let mut isem: Vec<(usize, f32)> = ev_vecs
            .iter()
            .enumerate()
            .filter(|(_, v)| v.len() == qv.len())
            .map(|(i, v)| {
                let mut dot = 0.0f32;
                for (a, b) in qv.iter().zip(v) {
                    dot += a * b;
                }
                (i, dot)
            })
            .collect();
        isem.sort_by(cmp_score);
        isem.truncate(CANDIDATES);
        let intent_ms = ti.elapsed().as_secs_f64() * 1e3;
        intent_ms_total += intent_ms;
        tasks_n += 1;

        let mut spans = serde_json::Map::new();
        for id in lex.iter().chain(sem.iter()).map(|(id, _)| *id) {
            if let Some(s) = snapshot.get(id) {
                spans
                    .entry(format!("{}#{}", s.file, s.qualified_name))
                    .or_insert_with(|| json!([s.start_line, s.end_line]));
            }
        }
        for h in &fused {
            spans
                .entry(format!("{}#{}", h.symbol.file, h.symbol.qualified_name))
                .or_insert_with(|| json!([h.symbol.start_line, h.symbol.end_line]));
        }
        for i in &pack.items {
            spans
                .entry(format!("{}#{}", i.symbol.file, i.symbol.qualified_name))
                .or_insert_with(|| json!([i.symbol.start_line, i.symbol.end_line]));
        }
        let rec = json!({
            "id": task["id"],
            "spans": spans,
            "lexical": lex.iter().map(|(id, s)| json!([sym_key(*id), s])).collect::<Vec<_>>(),
            "semantic": sem.iter().map(|(id, s)| json!([sym_key(*id), s])).collect::<Vec<_>>(),
            "fused": fused.iter().map(|h| json!([format!("{}#{}", h.symbol.file, h.symbol.qualified_name), h.score])).collect::<Vec<_>>(),
            "pack": {
                "used_tokens": pack.used_tokens,
                "budget_tokens": pack.budget_tokens,
                "items": pack.items.iter().map(|i| json!({
                    "id": format!("{}#{}", i.symbol.file, i.symbol.qualified_name),
                    "role": format!("{:?}", i.role), "score": i.score,
                    "est_tokens": i.est_tokens,
                })).collect::<Vec<_>>(),
            },
            "intent_lex": ilex.iter().map(|(i, s)| json!([ev_ids[*i], s])).collect::<Vec<_>>(),
            "intent_sem": isem.iter().map(|(i, s)| json!([ev_ids[*i], s])).collect::<Vec<_>>(),
            "timing_ms": {"search": search_ms, "context": context_ms, "intent": intent_ms},
        });
        writeln!(out, "{}", serde_json::to_string(&rec)?)?;
    }
    eprintln!(
        "{}",
        json!({
            "root": root.display().to_string(), "symbols": n, "evidence": ev_ids.len(),
            "evidence_chars": ev_chars, "model_load_ms": model_load_ms,
            "bm25_build_ms": bm25_build_ms, "embed_ms": embed_ms,
            "cache_hits": cache.map_or(0, |c| c.hits()), "cache_misses": cache.map_or(0, |c| c.misses()),
            "intent_query_ms_mean": intent_ms_total / tasks_n.max(1) as f64,
            "peak_rss_kb_after_evidence": rss_after_evidence, "peak_rss_kb": peak_rss_kb(),
            "tasks": tasks_n,
        })
    );
    Ok(())
}
