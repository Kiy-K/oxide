//! Semantic-retrieval research harness (docs/semantic-quality-eval): re-embed
//! a corpus under an **experimental document-text variant and/or native
//! embedding profile**, then dump the exact fusion inputs the way
//! `examples/fusion_dump.rs` does, so challengers are scored offline against
//! the frozen production pipeline (same BM25 channel, same RRF, same
//! allocator) with only the semantic channel swapped.
//!
//! Nothing here touches production: the variant is applied by a wrapper
//! `EmbeddingProvider` that rewrites `symbol_embed_text` output to the variant
//! text on the way into the real `NativeEmbedder`, and the vectors are written
//! into a *copy* of the index through the production `update_embeddings`
//! path (so the provider-switch migration, batching, and staleness logic are
//! exercised as shipped).
//!
//! Usage:
//!   semantic_variant <repo_root> <tasks.jsonl> --db <copy-of-index.db>
//!       [--variant D0..D5] [--profile arctic-embed-xs-q] [--query prefix|bare]
//!       [--no-embed] > dump.jsonl
//!
//! `--db` must be a consistent copy of `<repo_root>/.oxide/index.db`
//! (`sqlite3 index.db "VACUUM INTO 'copy.db'"`); the harness re-embeds it in
//! place. `D0` with the default profile and `--query prefix` is the
//! production pipeline and must reproduce `fusion_dump`'s `semantic` lists
//! bit-for-bit — that is the harness's self-check.
use oxide::context::{build_context_with, ContextOptions};
use oxide::embeddings::{
    symbol_embed_text, EmbeddingProvider, EmbeddingSpaceFingerprint, GemmaQueryPrompt,
    NativeEmbedder,
};
use oxide::index::{update_embeddings, IndexOptions, IndexReport};
use oxide::retrieval::{RetrievalEngine, RetrievalMode, SearchMode, SearchOptions, SymbolSnapshot};
use oxide::storage::{IndexBackend, SqliteStore};
use oxide::symbols::{Language, Symbol, SymbolKind};
use serde_json::{json, Value};
use std::collections::HashMap;
use std::io::{BufRead, Write};
use std::time::Instant;

const CANDIDATES: usize = 200;
const DOC_CHARS: usize = 600;
const BODY_LINES: usize = 10;
const BODY_CHARS: usize = 800;

fn cmp_score_id(a: &(u64, f32), b: &(u64, f32)) -> std::cmp::Ordering {
    b.1.partial_cmp(&a.1)
        .unwrap_or(std::cmp::Ordering::Equal)
        .then_with(|| a.0.cmp(&b.0))
}

// ---------------------------------------------------------------------------
// Document-text variants
// ---------------------------------------------------------------------------

/// Per-file line cache so docstring/body extraction reads each file once.
struct Sources {
    root: std::path::PathBuf,
    files: HashMap<String, Vec<String>>,
}

impl Sources {
    fn lines(&mut self, file: &str) -> &[String] {
        if !self.files.contains_key(file) {
            let text = std::fs::read_to_string(self.root.join(file)).unwrap_or_default();
            self.files
                .insert(file.to_string(), text.lines().map(str::to_string).collect());
        }
        &self.files[file]
    }
}

fn squash(s: &str, cap: usize) -> String {
    let mut out = String::with_capacity(cap.min(s.len()));
    let mut last_space = true;
    for ch in s.chars() {
        if ch.is_whitespace() {
            if !last_space {
                out.push(' ');
                last_space = true;
            }
        } else {
            out.push(ch);
            last_space = false;
        }
        if out.len() >= cap {
            break;
        }
    }
    out.trim().to_string()
}

fn strip_comment_marker(line: &str) -> &str {
    let t = line.trim();
    for m in [
        "///", "//!", "/**", "/*", "*/", "//", "#", "*", "\"\"\"", "'''",
    ] {
        if let Some(rest) = t.strip_prefix(m) {
            return rest.trim_end_matches("*/").trim();
        }
    }
    t
}

fn is_comment_line(line: &str) -> bool {
    let t = line.trim();
    t.starts_with("//") || t.starts_with('#') || t.starts_with("/*") || t.starts_with('*')
}

/// The symbol's own documentation: a Python docstring (first string literal
/// after the `def`/`class` header, or at the top of a module), else the
/// comment block immediately above the definition (`///`, `//`, `/** */`,
/// `#`). Whitespace-squashed and capped at `DOC_CHARS`.
fn doc_text(s: &Symbol, lines: &[String]) -> String {
    let start = (s.start_line.max(1) - 1) as usize;
    if start >= lines.len() {
        return String::new();
    }
    let end = (s.end_line as usize).min(lines.len());
    let mut doc = Vec::new();
    if s.language == Language::Python {
        // Skip the header (decorators + def line(s) up to the one ending in ':').
        let mut i = start;
        if s.kind != SymbolKind::Module {
            while i < end && !lines[i].trim_end().ends_with(':') {
                i += 1;
            }
            i += 1;
        }
        while i < end && lines[i].trim().is_empty() {
            i += 1;
        }
        if i < end {
            let t = lines[i].trim().trim_start_matches('r');
            if t.starts_with("\"\"\"") || t.starts_with("'''") {
                let q = &t[..3];
                let body = &t[3..];
                if let Some(close) = body.find(q) {
                    doc.push(body[..close].to_string());
                } else {
                    doc.push(body.to_string());
                    let mut j = i + 1;
                    while j < end && j < i + 12 {
                        if let Some(close) = lines[j].find(q) {
                            doc.push(lines[j][..close].to_string());
                            break;
                        }
                        doc.push(lines[j].clone());
                        j += 1;
                    }
                }
            }
        }
    }
    if doc.is_empty() && s.kind != SymbolKind::Module {
        // Leading comment block directly above the span.
        let mut j = start;
        while j > 0 && j + 12 > start && is_comment_line(&lines[j - 1]) {
            j -= 1;
        }
        for line in &lines[j..start] {
            doc.push(strip_comment_marker(line).to_string());
        }
    }
    squash(&doc.join(" "), DOC_CHARS)
}

/// The first `BODY_LINES` non-empty lines of the span (header included),
/// whitespace-squashed and capped at `BODY_CHARS`.
fn body_head(s: &Symbol, lines: &[String]) -> String {
    let start = (s.start_line.max(1) - 1) as usize;
    let end = (s.end_line as usize).min(lines.len());
    if start >= end {
        return String::new();
    }
    let picked: Vec<&str> = lines[start..end]
        .iter()
        .map(|l| l.trim())
        .filter(|l| !l.is_empty())
        .take(BODY_LINES + 1)
        .collect();
    squash(&picked.join(" "), BODY_CHARS)
}

fn path_words(file: &str) -> String {
    file.replace(['/', '_', '-', '.'], " ")
}

/// Build the variant text for one symbol. `D0` is exactly
/// `symbol_embed_text`, byte for byte.
fn variant_text(variant: &str, s: &Symbol, src: &mut Sources) -> String {
    let base = symbol_embed_text(s);
    match variant {
        "D0" => base,
        "D1" => {
            let doc = doc_text(s, src.lines(&s.file));
            format!(
                "{} {} {} {} {} {} {}",
                s.file,
                s.kind,
                s.qualified_name,
                s.signature,
                doc,
                s.imports.join(" "),
                s.references.join(" ")
            )
        }
        "D2" => {
            let lines = src.lines(&s.file);
            let doc = doc_text(s, lines);
            let body = body_head(s, lines);
            format!(
                "{} {} {} {} {} {} {} {}",
                s.file,
                s.kind,
                s.qualified_name,
                s.signature,
                doc,
                body,
                s.imports.join(" "),
                s.references.join(" ")
            )
        }
        "D3" => {
            let lines = src.lines(&s.file);
            let doc = doc_text(s, lines);
            let body = body_head(s, lines);
            format!(
                "{} {} {} {} {} {} {}",
                s.kind,
                s.name,
                s.qualified_name,
                s.signature,
                doc,
                body,
                path_words(&s.file)
            )
        }
        "D4" => {
            let doc = doc_text(s, src.lines(&s.file));
            format!("{} {} {} {}", s.kind, s.qualified_name, s.signature, doc)
        }
        "D5" => {
            let lines = src.lines(&s.file);
            let doc = doc_text(s, lines);
            let body = body_head(s, lines);
            let t = format!("{doc} {body}");
            if t.trim().is_empty() {
                base
            } else {
                t
            }
        }
        other => panic!("unknown variant {other}"),
    }
}

// ---------------------------------------------------------------------------
// Variant provider: rewrites document text on the way into the real model.
// ---------------------------------------------------------------------------

struct VariantEmbedder {
    inner: NativeEmbedder,
    name: String,
    variant: String,
    bare_query: bool,
    /// `symbol_embed_text(s)` → variant text. Keys are unique per symbol
    /// because the production text starts with `file` and `qualified_name`,
    /// the pair symbol ids are derived from.
    texts: HashMap<String, String>,
}

impl EmbeddingProvider for VariantEmbedder {
    fn name(&self) -> &str {
        &self.name
    }
    fn dim(&self) -> usize {
        self.inner.dim()
    }
    fn embed(&self, text: &str) -> Vec<f32> {
        self.inner.embed(text)
    }
    fn embed_batch(&self, texts: &[String]) -> Vec<Vec<f32>> {
        self.inner.embed_batch(texts)
    }
    fn embed_query(&self, text: &str) -> Vec<f32> {
        if self.bare_query {
            self.inner.embed(text)
        } else {
            self.inner.embed_query(text)
        }
    }
    fn embed_document(&self, text: &str) -> Vec<f32> {
        let t = self.texts.get(text).map(String::as_str).unwrap_or(text);
        self.inner.embed_document(t)
    }
    fn embed_documents(&self, texts: &[String]) -> Vec<Vec<f32>> {
        let mapped: Vec<String> = texts
            .iter()
            .map(|t| self.texts.get(t).cloned().unwrap_or_else(|| t.clone()))
            .collect();
        self.inner.embed_documents(&mapped)
    }
    fn fingerprint(&self) -> EmbeddingSpaceFingerprint {
        let mut fp = self.inner.fingerprint();
        fp.model = self.name.clone();
        fp.document_profile = format!("variant:{}", self.variant);
        if self.bare_query {
            fp.query_profile = "none".to_string();
        }
        fp
    }
}

fn arg(args: &[String], flag: &str) -> Option<String> {
    args.iter()
        .position(|a| a == flag)
        .and_then(|i| args.get(i + 1).cloned())
}

fn main() -> anyhow::Result<()> {
    let args: Vec<String> = std::env::args().collect();
    let root = std::path::PathBuf::from(&args[1]).canonicalize()?;
    let tasks = std::io::BufReader::new(std::fs::File::open(&args[2])?);
    let db = std::path::PathBuf::from(arg(&args, "--db").expect("--db <copy-of-index.db>"));
    let variant = arg(&args, "--variant").unwrap_or_else(|| "D0".to_string());
    let profile = arg(&args, "--profile")
        .unwrap_or_else(|| oxide::embeddings::DEFAULT_NATIVE_PROFILE.to_string());
    let bare_query = arg(&args, "--query").as_deref() == Some("bare");
    let no_embed = args.iter().any(|a| a == "--no-embed");

    let t0 = Instant::now();
    let inner = NativeEmbedder::new(&profile, GemmaQueryPrompt::Bare)?;
    let model_load_ms = t0.elapsed().as_secs_f64() * 1e3;

    let mut store = SqliteStore::open(&db)?;
    let symbols = store.all_symbols()?;
    let mut src = Sources {
        root: root.clone(),
        files: HashMap::new(),
    };
    let t1 = Instant::now();
    let mut texts = HashMap::with_capacity(symbols.len());
    let mut text_chars = 0usize;
    let mut with_doc = 0usize;
    for s in &symbols {
        let v = variant_text(&variant, s, &mut src);
        text_chars += v.len();
        if variant != "D0" && !doc_text(s, src.lines(&s.file)).is_empty() {
            with_doc += 1;
        }
        texts.insert(symbol_embed_text(s), v);
    }
    let text_build_ms = t1.elapsed().as_secs_f64() * 1e3;
    drop(src);

    let embedder = VariantEmbedder {
        name: format!(
            "{}+{}{}",
            inner.name(),
            variant,
            if bare_query { "+bareq" } else { "" }
        ),
        inner,
        variant: variant.clone(),
        bare_query,
        texts,
    };

    let mut embed_ms = 0.0;
    if !no_embed {
        let t2 = Instant::now();
        let mut report = IndexReport::default();
        update_embeddings(
            &root,
            &mut store,
            &embedder,
            &IndexOptions::default(),
            &mut report,
        )?;
        embed_ms = t2.elapsed().as_secs_f64() * 1e3;
    }
    let db_bytes = std::fs::metadata(&db).map(|m| m.len()).unwrap_or(0);
    eprintln!(
        "semantic_variant: profile={profile} variant={variant} query={} symbols={} with_doc={} \
         avg_chars={:.0} model_load_ms={model_load_ms:.0} text_build_ms={text_build_ms:.0} \
         embed_ms={embed_ms:.0} db_bytes={db_bytes} dim={}",
        if bare_query { "bare" } else { "prefix" },
        symbols.len(),
        with_doc,
        text_chars as f64 / symbols.len().max(1) as f64,
        embedder.dim()
    );

    // ---- dump loop: mirrors examples/fusion_dump.rs with timings added ----
    let n = store.symbol_count()?;
    let snapshot = SymbolSnapshot::load(&store)?;
    let engine = RetrievalEngine::with_snapshot(&store, &embedder, &snapshot);
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

        let lq = oxide::lexical::prepare_from_store(&store, n, &query)?;
        let (lex_scores, _) = oxide::lexical::score(&lq, 1.5, 0.75);
        let mut lex: Vec<(u64, f32)> = lex_scores.iter().map(|(id, s)| (*id, s.0)).collect();
        lex.sort_by(cmp_score_id);
        lex.truncate(CANDIDATES);

        let tq = Instant::now();
        let qv = embedder.embed_query(&query);
        let embed_query_ms = tq.elapsed().as_secs_f64() * 1e3;
        let ts = Instant::now();
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
        let scan_ms = ts.elapsed().as_secs_f64() * 1e3;

        let tf = Instant::now();
        let fused = engine.search(
            &query,
            &SearchOptions {
                limit: 2 * CANDIDATES,
                mode: SearchMode::Hybrid,
                expand: false,
                retrieval_mode: RetrievalMode::Balanced,
            },
        )?;
        let search_ms = tf.elapsed().as_secs_f64() * 1e3;
        let expanded = engine.search(
            &query,
            &SearchOptions {
                limit: 2 * CANDIDATES,
                mode: SearchMode::Hybrid,
                expand: true,
                retrieval_mode: RetrievalMode::Balanced,
            },
        )?;
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
        let tc = Instant::now();
        let pack = build_context_with(&root, &engine, &query, &ContextOptions::default())?;
        let context_ms = tc.elapsed().as_secs_f64() * 1e3;

        let mut spans = serde_json::Map::new();
        let mut ids = serde_json::Map::new();
        for id in lex.iter().chain(sem.iter()).map(|(id, _)| *id) {
            if let Some(s) = snapshot.get(id) {
                let key = format!("{}#{}", s.file, s.qualified_name);
                spans
                    .entry(key.clone())
                    .or_insert_with(|| json!([s.start_line, s.end_line]));
                ids.entry(key).or_insert_with(|| json!(id.to_string()));
            }
        }
        let rec = json!({
            "id": task["id"],
            "ids": ids,
            "spans": spans,
            "lexical": lex.iter().map(|(id, s)| json!([sym_id(*id), s])).collect::<Vec<_>>(),
            "semantic": sem.iter().map(|(id, s)| json!([sym_id(*id), s])).collect::<Vec<_>>(),
            "fused": fused.iter().map(|h| json!([format!("{}#{}", h.symbol.file, h.symbol.qualified_name), h.score, h.reasons])).collect::<Vec<_>>(),
            "expanded": expanded.iter().map(|h| json!([format!("{}#{}", h.symbol.file, h.symbol.qualified_name), h.score, h.reasons])).collect::<Vec<_>>(),
            "neighbors": neighbors,
            "snippets": {},
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
            "timing_ms": {
                "embed_query": embed_query_ms, "scan": scan_ms,
                "search": search_ms, "context": context_ms,
            },
        });
        writeln!(out, "{}", serde_json::to_string(&rec)?)?;
    }
    Ok(())
}
