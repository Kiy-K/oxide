//! Research-only (issue #23): the OXIDE side of the CodeGraph Kernel
//! differential harness (`docs/codegraph-kernel-eval/`). Never linked into
//! the `oxide` binary and never called by production indexing.
//!
//! Runs exactly the per-file seam `index/pipeline.rs` runs —
//! `parser::parse_file` then `structural_relations::compute_file_relations`
//! — over a manifest of files, and either dumps the raw output (`dump`) or
//! times it in-process (`bench`). Cross-file enrichment (`references`,
//! `content_hash` folding), storage and embeddings are deliberately out of
//! scope, matching the issue's comparison boundary.
//!
//! Manifest: JSONL, one `{"id", "path", "rel", "oxide_lang"}` per file; `rel`
//! is the repo-relative path both extractors are given as the file name.
//!
//!   cargo run --release --no-default-features --example extraction_differential -- dump  <manifest> <out.jsonl>
//!   cargo run --release --no-default-features --example extraction_differential -- bench <manifest> <reps> <out.json>
//!   cargo run --release --no-default-features --example extraction_differential -- classify <paths.txt>
//!
//! `classify` prints `path<TAB>language` using the scanner's own
//! `language_for_source`, so the corpus builder never re-implements OXIDE's
//! language policy (including the content-sniffed `.h` C/C++ split).

use oxide::parser::{extractor_for, parse_file};
use oxide::structural_relations::compute_file_relations;
use oxide::symbols::{Language, Symbol};
use serde_json::{json, Value};
use std::collections::HashMap;
use std::io::{BufRead, Write};
use std::str::FromStr;
use std::time::Instant;

struct Entry {
    id: String,
    rel: String,
    lang: Language,
    src: String,
}

fn load(manifest: &str) -> Vec<Entry> {
    let f = std::fs::File::open(manifest).expect("open manifest");
    let mut out = Vec::new();
    for line in std::io::BufReader::new(f).lines() {
        let line = line.expect("read manifest");
        if line.trim().is_empty() {
            continue;
        }
        let v: Value = serde_json::from_str(&line).expect("manifest json");
        let src = std::fs::read_to_string(v["path"].as_str().unwrap()).expect("read source");
        out.push(Entry {
            id: v["id"].as_str().unwrap().to_string(),
            rel: v["rel"].as_str().unwrap().to_string(),
            lang: Language::from_str(v["oxide_lang"].as_str().unwrap()).expect("language"),
            src,
        });
    }
    out
}

/// Peak resident set size so far (`VmHWM`, kB) — Linux only; 0 elsewhere.
fn vm_hwm_kb() -> u64 {
    std::fs::read_to_string("/proc/self/status")
        .ok()
        .and_then(|s| {
            s.lines()
                .find(|l| l.starts_with("VmHWM:"))
                .and_then(|l| l.split_whitespace().nth(1)?.parse().ok())
        })
        .unwrap_or(0)
}

fn has_parse_error(lang: Language, src: &str) -> bool {
    if !lang.has_structural_queries() {
        return false;
    }
    let mut p = tree_sitter::Parser::new();
    p.set_language(&extractor_for(lang).ts_language()).unwrap();
    p.parse(src, None).is_none_or(|t| t.root_node().has_error())
}

/// `compute_file_relations`' output: `(symbol id, calls, bases)` per symbol.
type Relations = Vec<(u64, Vec<String>, Vec<String>)>;

fn extract(e: &Entry) -> (Vec<Symbol>, Relations) {
    let symbols = parse_file(&e.rel, &e.src, e.lang);
    let relations = compute_file_relations(&symbols, &e.src, e.lang);
    (symbols, relations)
}

fn dump(entries: &[Entry], out: &str) {
    let mut w = std::io::BufWriter::new(std::fs::File::create(out).expect("create output"));
    for e in entries {
        let (symbols, relations) = extract(e);
        let rel_by_id: HashMap<u64, (&Vec<String>, &Vec<String>)> =
            relations.iter().map(|(id, c, b)| (*id, (c, b))).collect();
        let imports = symbols
            .last()
            .map(|m| m.imports.clone())
            .unwrap_or_default();
        let syms: Vec<Value> = symbols
            .iter()
            .map(|s| {
                // `compute_file_relations` returns an entry for every symbol.
                let (calls, bases) = rel_by_id[&s.id()];
                json!({
                    "qn": s.qualified_name, "name": s.name, "kind": s.kind.to_string(),
                    "start": s.start_line, "end": s.end_line, "parent": s.parent,
                    "exported": s.exported, "calls": calls, "bases": bases,
                })
            })
            .collect();
        let rec = json!({
            "id": e.id, "rel": e.rel, "lang": e.lang.as_str(), "ok": true,
            "has_parse_error": has_parse_error(e.lang, &e.src),
            "imports": imports, "symbols": syms,
        });
        writeln!(w, "{rec}").unwrap();
    }
}

fn bench(entries: &[Entry], reps: usize, out: &str) {
    let hwm_loaded = vm_hwm_kb();
    // per_file[f][r] = (parse_file ns, relations ns, one bare parse ns)
    let mut per_file = vec![Vec::with_capacity(reps); entries.len()];
    let mut parser = tree_sitter::Parser::new();
    let mut rep_total = Vec::with_capacity(reps);
    let mut sink = 0usize;
    for _ in 0..reps {
        let t_rep = Instant::now();
        for (i, e) in entries.iter().enumerate() {
            let t0 = Instant::now();
            let symbols = parse_file(&e.rel, &e.src, e.lang);
            let t1 = Instant::now();
            let relations = compute_file_relations(&symbols, &e.src, e.lang);
            let t2 = Instant::now();
            // One bare tree-sitter parse with the same grammar, for
            // attributing the seam's cost to repeated parsing (issue #24).
            if e.lang.has_structural_queries() {
                parser
                    .set_language(&extractor_for(e.lang).ts_language())
                    .unwrap();
                sink += parser
                    .parse(&e.src, None)
                    .map_or(0, |t| t.root_node().child_count() as usize);
            }
            let t3 = Instant::now();
            sink += symbols.len() + relations.len();
            per_file[i].push((
                (t1 - t0).as_nanos() as u64,
                (t2 - t1).as_nanos() as u64,
                (t3 - t2).as_nanos() as u64,
            ));
        }
        rep_total.push(t_rep.elapsed().as_nanos() as u64);
    }
    let rec = json!({
        "engine": "oxide", "reps": reps, "sink": sink,
        "vmhwm_kb_after_load": hwm_loaded, "vmhwm_kb_end": vm_hwm_kb(),
        "rep_total_ns": rep_total,
        "files": entries.iter().zip(&per_file).map(|(e, t)| json!({
            "id": e.id, "lang": e.lang.as_str(), "bytes": e.src.len(),
            "parse_ns": t.iter().map(|x| x.0).collect::<Vec<_>>(),
            "relations_ns": t.iter().map(|x| x.1).collect::<Vec<_>>(),
            "parse1_ns": t.iter().map(|x| x.2).collect::<Vec<_>>(),
        })).collect::<Vec<_>>(),
    });
    std::fs::write(out, rec.to_string()).expect("write bench output");
}

fn classify(paths: &str) {
    for p in std::fs::read_to_string(paths).expect("read paths").lines() {
        let lang = std::fs::read_to_string(p)
            .ok()
            .and_then(|src| oxide::scanner::language_for_source(std::path::Path::new(p), &src));
        println!("{p}\t{}", lang.map(|l| l.as_str()).unwrap_or("-"));
    }
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    match args.get(1).map(String::as_str) {
        Some("dump") => dump(&load(&args[2]), &args[3]),
        Some("bench") => bench(&load(&args[2]), args[3].parse().expect("reps"), &args[4]),
        Some("classify") => classify(&args[2]),
        _ => eprintln!("usage: extraction_differential dump|bench <manifest> ..."),
    }
}
