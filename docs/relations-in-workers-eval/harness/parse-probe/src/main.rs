//! Research-only (issue #29), derived from the #24 probe
//! (`docs/one-parse-eval/harness/parse-probe`). Links OXIDE unmodified and
//! wraps tree-sitter's one parse entry point at link time (see Cargo.toml).
//! The identical source builds against the baseline and the challenger:
//! it only calls OXIDE's public indexing API.
//!
//!   parse-probe index    <root> <label>        update_base + embeddings (hashed)
//!   parse-probe watch    <root> <rel>          update_base_for_files + embeddings
//!   parse-probe langseam <manifest.jsonl> <dir> full pipeline, one fresh index per
//!                                              language, parses/file per language
//!   parse-probe dump     <manifest.jsonl> <out> per-file extraction dump through the
//!                                              parse worker's own seam
//!
//! The worker seam's API is the one thing that differs between the two
//! sides, so `dump` alone is feature-gated: build the challenger probe with
//! `--features challenger`.
//!
//! Every mode prints one JSON object: wall/CPU, peak RSS (VmHWM), parse
//! count and in-call time, per-stage wall and process CPU, the parse
//! workers' individual finish time / thread CPU / file count, and the gap
//! between the parse and store stages (reference resolution + hashing, on
//! the calling thread). With PROBE_ATTRIB=1 (debug build) each parse is
//! also classified by call site and by thread: `[worker]` = a parse-pool
//! thread, `[main]` = the calling thread (store loop / scan / refresh).

use oxide::embeddings::HashedEmbedder;
use oxide::index::{
    update_base_for_files, update_base_reporting, update_embeddings, update_embeddings_reporting,
    IndexOptions, ProgressSink, SqliteStore, Stage,
};
use serde_json::{json, Value};
use std::collections::BTreeMap;
use std::io::BufRead;
use std::path::Path;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Mutex;
use std::time::Instant;
use tree_sitter::ffi::{TSInput, TSParseOptions, TSParser, TSTree};

static CALLS: AtomicU64 = AtomicU64::new(0);
static NANOS: AtomicU64 = AtomicU64::new(0);
static SITES: Mutex<BTreeMap<String, (u64, u64)>> = Mutex::new(BTreeMap::new());
static LABEL: Mutex<String> = Mutex::new(String::new());

extern "C" {
    fn __real_ts_parser_parse_with_options(
        p: *mut TSParser,
        old: *const TSTree,
        input: TSInput,
        opts: TSParseOptions,
    ) -> *mut TSTree;
}

/// Classify one parse by the OXIDE frames above it (most specific first)
/// and by the thread it ran on.
fn site(bt: &str) -> String {
    let rules: [(&str, &str); 7] = [
        (
            "all_calls_and_bases_in_file",
            "relations: separate calls+bases parse",
        ),
        ("all_calls_in_file", "relations: calls query"),
        ("all_bases_in_file", "relations: bases query"),
        ("generate_tags", "extract: tree-sitter-tags"),
        ("scanner::error_nodes", "scanner: .h C/C++ disambiguation"),
        (
            "tags::TagsExtractor",
            "extract: shared OXIDE tree (tags.rs)",
        ),
        ("", "other"),
    ];
    let (_, name) = rules.iter().find(|(k, _)| bt.contains(k)).unwrap();
    let on_main = std::thread::current().name() == Some("main");
    format!("{name} [{}]", if on_main { "main" } else { "worker" })
}

#[no_mangle]
pub unsafe extern "C" fn __wrap_ts_parser_parse_with_options(
    p: *mut TSParser,
    old: *const TSTree,
    input: TSInput,
    opts: TSParseOptions,
) -> *mut TSTree {
    let attrib = std::env::var_os("PROBE_ATTRIB").is_some();
    let key = attrib.then(|| {
        let bt = std::backtrace::Backtrace::force_capture().to_string();
        let label = LABEL.lock().unwrap().clone();
        format!("{label}{}", site(&bt))
    });
    let t = Instant::now();
    let tree = __real_ts_parser_parse_with_options(p, old, input, opts);
    let ns = t.elapsed().as_nanos() as u64;
    CALLS.fetch_add(1, Ordering::Relaxed);
    NANOS.fetch_add(ns, Ordering::Relaxed);
    if let Some(k) = key {
        let mut m = SITES.lock().unwrap();
        let e = m.entry(k).or_default();
        e.0 += 1;
        e.1 += ns;
    }
    tree
}

fn rusage_ms(who: libc::c_int) -> f64 {
    let mut u: libc::rusage = unsafe { std::mem::zeroed() };
    unsafe { libc::getrusage(who, &mut u) };
    let tv = |t: libc::timeval| t.tv_sec as f64 * 1e3 + t.tv_usec as f64 / 1e3;
    tv(u.ru_utime) + tv(u.ru_stime)
}

fn cpu_ms() -> f64 {
    rusage_ms(libc::RUSAGE_SELF)
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

#[derive(Default)]
struct Worker {
    files: usize,
    last: Option<Instant>,
    thread_cpu_ms: f64,
}

#[derive(Default)]
struct Inner {
    wall: BTreeMap<String, f64>,
    cpu: BTreeMap<String, f64>,
    open: BTreeMap<String, (Instant, f64)>,
    parse_begin: Option<Instant>,
    parse_end: Option<Instant>,
    store_begin: Option<Instant>,
    workers: BTreeMap<String, Worker>,
}

/// Per-stage wall and process CPU; per parse-worker thread its file count,
/// finish time and thread CPU (sampled on the worker at every `advance`, so
/// the last sample is the worker's total).
#[derive(Default)]
struct Stages(Mutex<Inner>);

impl ProgressSink for Stages {
    fn begin(&self, stage: Stage, _: Option<usize>) {
        let now = Instant::now();
        let mut g = self.0.lock().unwrap();
        g.open.insert(format!("{stage:?}"), (now, cpu_ms()));
        match stage {
            Stage::Parse => g.parse_begin = Some(now),
            Stage::Store => g.store_begin = Some(now),
            _ => {}
        }
    }
    fn advance(&self, stage: Stage, _: usize, _: usize) {
        if !matches!(stage, Stage::Parse) {
            return;
        }
        let now = Instant::now();
        let cpu = rusage_ms(libc::RUSAGE_THREAD);
        let id = format!("{:?}", std::thread::current().id());
        let mut g = self.0.lock().unwrap();
        let w = g.workers.entry(id).or_default();
        w.files += 1;
        w.last = Some(now);
        w.thread_cpu_ms = cpu;
    }
    fn end(&self, stage: Stage, _: &str) {
        let now = Instant::now();
        let mut g = self.0.lock().unwrap();
        let k = format!("{stage:?}");
        if let Some((t, c)) = g.open.remove(&k) {
            *g.wall.entry(k.clone()).or_default() += (now - t).as_secs_f64() * 1e3;
            *g.cpu.entry(k).or_default() += cpu_ms() - c;
        }
        if matches!(stage, Stage::Parse) {
            g.parse_end = Some(now);
        }
    }
}

impl Stages {
    fn json(&self) -> Value {
        let g = self.0.lock().unwrap();
        let ms = |a: Instant, b: Instant| (b.saturating_duration_since(a)).as_secs_f64() * 1e3;
        let workers: Vec<Value> = match g.parse_begin {
            Some(b) => g
                .workers
                .values()
                .map(|w| {
                    json!({"files": w.files, "finish_ms": w.last.map(|l| ms(b, l)),
                           "thread_cpu_ms": w.thread_cpu_ms})
                })
                .collect(),
            None => Vec::new(),
        };
        let gap = match (g.parse_end, g.store_begin) {
            (Some(e), Some(s)) => Some(ms(e, s)),
            _ => None,
        };
        json!({"stages_ms": g.wall, "stages_cpu_ms": g.cpu, "parse_workers": workers,
               "resolve_gap_ms": gap})
    }
}

fn open(root: &Path) -> SqliteStore {
    SqliteStore::open(&root.join(".oxide/index.db")).expect("open index")
}

fn sites_json() -> BTreeMap<String, Value> {
    SITES
        .lock()
        .unwrap()
        .iter()
        .map(|(k, (n, ns))| (k.clone(), json!({"calls": n, "ms": *ns as f64 / 1e6})))
        .collect()
}

fn emit(mode: &str, wall: f64, cpu0: f64, extra: Value) {
    let out = json!({
        "mode": mode, "wall_ms": wall, "cpu_ms": cpu_ms() - cpu0, "vmhwm_kb": vm_hwm_kb(),
        "parse_calls": CALLS.load(Ordering::Relaxed),
        "parse_ms": NANOS.load(Ordering::Relaxed) as f64 / 1e6,
        "sites": sites_json(), "extra": extra,
    });
    println!("{out}");
}

fn report_json(r: &oxide::index::IndexReport) -> Value {
    serde_json::to_value(r).unwrap()
}

fn main() {
    let a: Vec<String> = std::env::args().collect();
    let cpu0 = cpu_ms();
    let embedder = HashedEmbedder::default();
    match a.get(1).map(String::as_str) {
        Some("index") => {
            let root = Path::new(&a[2]);
            let mut store = open(root);
            let stages = Stages::default();
            let opts = IndexOptions::default();
            let t = Instant::now();
            let mut report = update_base_reporting(root, &mut store, &opts, &stages).unwrap();
            let base_ms = t.elapsed().as_secs_f64() * 1e3;
            update_embeddings_reporting(root, &mut store, &embedder, &opts, &mut report, &stages)
                .unwrap();
            let wall = t.elapsed().as_secs_f64() * 1e3;
            let mut extra = stages.json();
            extra["base_ms"] = json!(base_ms);
            extra["report"] = report_json(&report);
            emit(&a[3], wall, cpu0, extra);
        }
        Some("watch") => {
            let root = Path::new(&a[2]);
            let mut store = open(root);
            let opts = IndexOptions::default();
            let t = Instant::now();
            let mut report =
                update_base_for_files(root, &mut store, &opts, &[a[3].clone()]).unwrap();
            let base_ms = t.elapsed().as_secs_f64() * 1e3;
            update_embeddings(root, &mut store, &embedder, &opts, &mut report).unwrap();
            let wall = t.elapsed().as_secs_f64() * 1e3;
            emit(
                "watch",
                wall,
                cpu0,
                json!({"base_ms": base_ms, "report": report_json(&report)}),
            );
        }
        Some("langseam") => {
            // Group manifest files by resolved language, copy each group into
            // its own fresh directory and index it through the real pipeline.
            // Grouping parses `.h` files (language_for_source), so counters
            // are reset before the first index run.
            let f = std::fs::File::open(&a[2]).expect("manifest");
            let out = Path::new(&a[3]);
            let mut groups: BTreeMap<&'static str, usize> = BTreeMap::new();
            for line in std::io::BufReader::new(f).lines() {
                let v: Value = serde_json::from_str(&line.unwrap()).unwrap();
                let rel = v["rel"].as_str().unwrap();
                let Ok(src) = std::fs::read_to_string(v["path"].as_str().unwrap()) else {
                    continue;
                };
                let Some(lang) = oxide::scanner::language_for_source(Path::new(rel), &src) else {
                    continue;
                };
                let dst = out.join(lang.as_str()).join(rel);
                std::fs::create_dir_all(dst.parent().unwrap()).unwrap();
                std::fs::write(&dst, &src).unwrap();
                *groups.entry(lang.as_str()).or_default() += 1;
            }
            CALLS.store(0, Ordering::Relaxed);
            NANOS.store(0, Ordering::Relaxed);
            SITES.lock().unwrap().clear();
            let mut per = serde_json::Map::new();
            let t = Instant::now();
            for (lang, n) in &groups {
                let root = out.join(lang);
                *LABEL.lock().unwrap() = format!("{lang}\t");
                let before = CALLS.load(Ordering::Relaxed);
                let mut store = open(&root);
                let stages = Stages::default();
                let report =
                    update_base_reporting(&root, &mut store, &IndexOptions::default(), &stages)
                        .unwrap();
                let parses = CALLS.load(Ordering::Relaxed) - before;
                per.insert(
                    lang.to_string(),
                    json!({"copied": n, "reparsed": report.reparsed_files, "parses": parses,
                           "parses_per_file": parses as f64 / report.reparsed_files.max(1) as f64}),
                );
            }
            LABEL.lock().unwrap().clear();
            emit(
                "langseam",
                t.elapsed().as_secs_f64() * 1e3,
                cpu0,
                json!({"languages": per}),
            );
        }
        Some("dump") => dump(&a[2], &a[3]),
        _ => eprintln!("usage: parse-probe index|watch|langseam|dump ..."),
    }
}

/// The parse worker's per-file seam, exactly as `index/pipeline.rs` runs it
/// on each side: baseline = `parse_file` in the worker, then
/// `compute_file_relations` (own parse) in the store loop; challenger =
/// `parse_file_with_structure` + `relations_from_sites`, both in the worker.
#[cfg(not(feature = "challenger"))]
fn worker_seam(
    rel: &str,
    src: &str,
    lang: oxide::symbols::Language,
) -> (Vec<oxide::symbols::Symbol>, Vec<(u64, Vec<String>, Vec<String>)>) {
    let syms = oxide::parser::parse_file(rel, src, lang);
    let rels = oxide::structural_relations::compute_file_relations(&syms, src, lang);
    (syms, rels)
}

#[cfg(feature = "challenger")]
fn worker_seam(
    rel: &str,
    src: &str,
    lang: oxide::symbols::Language,
) -> (Vec<oxide::symbols::Symbol>, Vec<(u64, Vec<String>, Vec<String>)>) {
    let (syms, sites) = oxide::parser::parse_file_with_structure(rel, src, lang);
    let rels = oxide::structural_relations::relations_from_sites(&syms, sites);
    (syms, rels)
}

/// One JSON line per file: every serialized `Symbol` field (incl. id and
/// parser `content_hash`) plus that symbol's relation row, in output order.
fn dump(manifest: &str, out: &str) {
    use std::io::Write;
    let f = std::fs::File::open(manifest).expect("manifest");
    let mut w = std::io::BufWriter::new(std::fs::File::create(out).expect("out"));
    for line in std::io::BufReader::new(f).lines() {
        let v: Value = serde_json::from_str(&line.unwrap()).unwrap();
        let rel = v["rel"].as_str().unwrap();
        let Ok(src) = std::fs::read_to_string(v["path"].as_str().unwrap()) else {
            continue;
        };
        let Some(lang) = oxide::scanner::language_for_source(Path::new(rel), &src) else {
            continue;
        };
        let (syms, rels) = worker_seam(rel, &src, lang);
        let symbols: Vec<Value> = syms
            .iter()
            .map(|s| json!({"id": s.id(), "symbol": s}))
            .collect();
        let rec = json!({"rel": rel, "lang": lang.as_str(), "symbols": symbols, "relations": rels});
        writeln!(w, "{rec}").unwrap();
    }
}
