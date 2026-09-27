//! Research-only (issue #23): the CodeGraph Kernel side of the differential
//! harness — same manifest, same modes and same output discipline as OXIDE's
//! `examples/extraction_differential.rs`.
//!
//!   cgk-native dump  <manifest> <out.jsonl>
//!   cgk-native bench <manifest> <reps> <out.json>
//!   cgk-native digest <manifest>
//!   cgk-native languages
//!
//! `digest` prints `id<TAB>sha256` over the five raw buffers (meta minus its
//! trailing f64 wall-clock field) or `id<TAB>ERR <message>`; `shipped_digest.js`
//! prints the same for the npm-shipped `codegraph-kernel.node`, so the shim
//! can be checked byte-for-byte against the real artifact.

use cgk_native::decode::{decode, Endpoint};
use serde_json::{json, Value};
use std::io::{BufRead, Write};
use std::time::Instant;

struct Entry {
    id: String,
    rel: String,
    lang: String,
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
            lang: v["cg_lang"].as_str().unwrap_or("").to_string(),
            src,
        });
    }
    out
}

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

fn ep(e: &Endpoint) -> Value {
    match e {
        Endpoint::Row(r) => json!(r),
        Endpoint::Id(s) => json!(s),
    }
}

fn dump(entries: &[Entry], out: &str) {
    let mut w = std::io::BufWriter::new(std::fs::File::create(out).expect("create output"));
    for e in entries {
        let rec = if e.lang.is_empty() {
            json!({"id": e.id, "rel": e.rel, "ok": false, "error": "unsupported: no kernel language"})
        } else {
            match cgk_native::extract_file(&e.rel, &e.src, &e.lang) {
                Err(err) => json!({"id": e.id, "rel": e.rel, "lang": e.lang, "ok": false, "error": err}),
                Ok(buf) => {
                    let d = decode(&buf);
                    json!({
                        "id": e.id, "rel": e.rel, "lang": e.lang, "ok": true,
                        "errors_json": d.errors_json,
                        "nodes": d.nodes.iter().map(|n| json!({
                            "kind": n.kind, "name": n.name, "qn": n.qualified_name,
                            "start": n.start_line, "end": n.end_line,
                            "start_col": n.start_col, "end_col": n.end_col,
                            "exported": n.exported, "signature": n.signature,
                        })).collect::<Vec<_>>(),
                        "edges": d.edges.iter().map(|x| json!({
                            "src": ep(&x.source), "dst": ep(&x.target), "kind": x.kind, "line": x.line,
                        })).collect::<Vec<_>>(),
                        "refs": d.refs.iter().map(|r| json!({
                            "from": ep(&r.from), "kind": r.kind, "name": r.name,
                            "line": r.line, "col": r.column, "candidates": r.candidates,
                        })).collect::<Vec<_>>(),
                    })
                }
            }
        };
        writeln!(w, "{rec}").unwrap();
    }
}

fn bench(entries: &[Entry], reps: usize, out: &str) {
    let hwm_loaded = vm_hwm_kb();
    let mut per_file: Vec<Vec<(u64, u64, u64, bool)>> = vec![Vec::with_capacity(reps); entries.len()];
    let mut rep_total = Vec::with_capacity(reps);
    let mut sink = 0usize;
    for _ in 0..reps {
        let t_rep = Instant::now();
        for (i, e) in entries.iter().enumerate() {
            if e.lang.is_empty() {
                per_file[i].push((0, 0, 0, false));
                continue;
            }
            let t0 = Instant::now();
            let r = cgk_native::extract_file(&e.rel, &e.src, &e.lang);
            let t1 = Instant::now();
            let ok = r.is_ok();
            if let Ok(buf) = &r {
                let d = decode(buf);
                sink += d.nodes.len() + d.refs.len();
            }
            let t2 = Instant::now();
            sink += cgk_native::parse_only(&e.lang, &e.src);
            let t3 = Instant::now();
            per_file[i].push((
                (t1 - t0).as_nanos() as u64,
                (t2 - t1).as_nanos() as u64,
                (t3 - t2).as_nanos() as u64,
                ok,
            ));
        }
        rep_total.push(t_rep.elapsed().as_nanos() as u64);
    }
    let rec = json!({
        "engine": "codegraph-kernel", "reps": reps, "sink": sink,
        "vmhwm_kb_after_load": hwm_loaded, "vmhwm_kb_end": vm_hwm_kb(),
        "rep_total_ns": rep_total,
        "files": entries.iter().zip(&per_file).map(|(e, t)| json!({
            "id": e.id, "lang": e.lang, "bytes": e.src.len(),
            "ok": t.first().map(|x| x.3).unwrap_or(false),
            "extract_ns": t.iter().map(|x| x.0).collect::<Vec<_>>(),
            "decode_ns": t.iter().map(|x| x.1).collect::<Vec<_>>(),
            "parse1_ns": t.iter().map(|x| x.2).collect::<Vec<_>>(),
        })).collect::<Vec<_>>(),
    });
    std::fs::write(out, rec.to_string()).expect("write bench output");
}

fn digest(entries: &[Entry]) {
    use sha2::{Digest, Sha256};
    for e in entries {
        if e.lang.is_empty() {
            continue;
        }
        match cgk_native::extract_file(&e.rel, &e.src, &e.lang) {
            Err(err) => println!("{}\tERR {err}", e.id),
            Ok(b) => {
                let mut h = Sha256::new();
                for part in [&b.meta[..28], &b.nodes, &b.edges, &b.refs, &b.arena] {
                    h.update(part);
                }
                let hex: String = h.finalize().iter().map(|x| format!("{x:02x}")).collect();
                println!("{}\t{hex}", e.id);
            }
        }
    }
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    match args.get(1).map(String::as_str) {
        Some("dump") => dump(&load(&args[2]), &args[3]),
        Some("bench") => bench(&load(&args[2]), args[3].parse().expect("reps"), &args[4]),
        Some("digest") => digest(&load(&args[2])),
        Some("languages") => println!("{}", cgk_native::languages().join(",")),
        _ => eprintln!("usage: cgk-native dump|bench|languages ..."),
    }
}
