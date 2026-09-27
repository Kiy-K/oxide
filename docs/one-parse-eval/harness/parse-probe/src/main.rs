//! Research-only (issue #24). See Cargo.toml for how parses are intercepted.
//!
//!   parse-probe index <root> <full|noop|edit>   update_index_scoped (hashed embedder)
//!   parse-probe watch <root> <rel>              update_base_for_files + update_embeddings
//!   parse-probe seam  <manifest.jsonl>          per-file seam (language_for_source +
//!                                               parse_file + compute_file_relations)
//!
//! Every mode prints one JSON object: wall/CPU time, peak RSS, and the count
//! and summed in-call time of Tree-sitter parses, split by call site when
//! PROBE_ATTRIB=1 (backtrace per parse — use a debug build for that, where
//! nothing is inlined away; timings come from a release build without it).

use oxide::embeddings::HashedEmbedder;
use oxide::index::{
    update_base_for_files, update_base_reporting, update_embeddings,
    update_embeddings_reporting, IndexOptions, ProgressSink, SqliteStore, Stage,
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

/// Classify one parse by the OXIDE frames above it. Order matters: the most
/// specific caller wins.
fn site(bt: &str) -> String {
    let rules: [(&str, &str); 8] = [
        ("all_calls_and_bases_in_file", "relations: shared calls+bases parse"),
        ("all_calls_in_file", "relations: calls query"),
        ("all_bases_in_file", "relations: bases query"),
        ("generate_tags", "extract: tree-sitter-tags"),
        ("collect_imports", "imports: collect_meta walk"),
        ("LanguageExtractor>::extract", "extract: collect_meta walk"),
        ("scanner::error_nodes", "scanner: .h C/C++ disambiguation"),
        ("", "other"),
    ];
    let (_, name) = rules.iter().find(|(k, _)| bt.contains(k)).unwrap();
    let mut s = name.to_string();
    if name.starts_with("scanner") {
        // Which caller asked for the language: parse worker, store loop or
        // the unchanged-file relations refresh.
        // The parse workers are `std::thread::scope` threads; the store
        // loop runs on the calling (main) thread.
        let on_main = std::thread::current().name() == Some("main");
        s += if bt.contains("parse_and_persist_changed_files") && !on_main {
            " [parse worker]"
        } else if bt.contains("parse_and_persist_changed_files") {
            " [store loop]"
        } else if bt.contains("update_base_inner") {
            " [relations refresh]"
        } else {
            " [other]"
        };
    }
    s
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

fn cpu_ms() -> f64 {
    let mut u: libc::rusage = unsafe { std::mem::zeroed() };
    unsafe { libc::getrusage(libc::RUSAGE_SELF, &mut u) };
    let tv = |t: libc::timeval| t.tv_sec as f64 * 1e3 + t.tv_usec as f64 / 1e3;
    tv(u.ru_utime) + tv(u.ru_stime)
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

/// Wall time between each stage's begin and end.
#[derive(Default)]
struct Stages(Mutex<(BTreeMap<String, f64>, BTreeMap<String, Instant>)>);

impl ProgressSink for Stages {
    fn begin(&self, stage: Stage, _: Option<usize>) {
        self.0.lock().unwrap().1.insert(format!("{stage:?}"), Instant::now());
    }
    fn advance(&self, _: Stage, _: usize, _: usize) {}
    fn end(&self, stage: Stage, _: &str) {
        let mut g = self.0.lock().unwrap();
        if let Some(t) = g.1.remove(&format!("{stage:?}")) {
            *g.0.entry(format!("{stage:?}")).or_default() += t.elapsed().as_secs_f64() * 1e3;
        }
    }
}

fn open(root: &Path) -> SqliteStore {
    SqliteStore::open(&root.join(".oxide/index.db")).expect("open index")
}

fn emit(mode: &str, wall: f64, cpu0: f64, extra: Value) {
    let sites: BTreeMap<String, Value> = SITES
        .lock()
        .unwrap()
        .iter()
        .map(|(k, (n, ns))| (k.clone(), json!({"calls": n, "ms": *ns as f64 / 1e6})))
        .collect();
    let out = json!({
        "mode": mode, "wall_ms": wall, "cpu_ms": cpu_ms() - cpu0, "vmhwm_kb": vm_hwm_kb(),
        "parse_calls": CALLS.load(Ordering::Relaxed),
        "parse_ms": NANOS.load(Ordering::Relaxed) as f64 / 1e6,
        "sites": sites, "extra": extra,
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
            let st = stages.0.lock().unwrap().0.clone();
            emit(&a[3], wall, cpu0, json!({"base_ms": base_ms, "stages_ms": st, "report": report_json(&report)}));
        }
        Some("watch") => {
            let root = Path::new(&a[2]);
            let mut store = open(root);
            let opts = IndexOptions::default();
            let t = Instant::now();
            let mut report = update_base_for_files(root, &mut store, &opts, &[a[3].clone()]).unwrap();
            let base_ms = t.elapsed().as_secs_f64() * 1e3;
            update_embeddings(root, &mut store, &embedder, &opts, &mut report).unwrap();
            let wall = t.elapsed().as_secs_f64() * 1e3;
            emit("watch", wall, cpu0, json!({"base_ms": base_ms, "report": report_json(&report)}));
        }
        Some("seam") => {
            let f = std::fs::File::open(&a[2]).expect("manifest");
            let mut files = 0usize;
            let t = Instant::now();
            for line in std::io::BufReader::new(f).lines() {
                let v: Value = serde_json::from_str(&line.unwrap()).unwrap();
                let rel = v["rel"].as_str().unwrap();
                let Ok(src) = std::fs::read_to_string(v["path"].as_str().unwrap()) else {
                    continue; // binary / non-UTF-8: the scanner never indexes these
                };
                // Mirror index/pipeline.rs exactly: language resolved in the
                // parse worker, parse_file, then resolved again in the store
                // loop before compute_file_relations. Sites are recorded
                // under a placeholder label and re-keyed by the resolved
                // language once it is known.
                *LABEL.lock().unwrap() = "~\t".to_string();
                let Some(lang) = oxide::scanner::language_for_source(Path::new(rel), &src) else {
                    SITES.lock().unwrap().retain(|k, _| !k.starts_with("~\t"));
                    continue;
                };
                let syms = oxide::parser::parse_file(rel, &src, lang);
                if let Some(lang) = oxide::scanner::language_for_source(Path::new(rel), &src) {
                    let _ = oxide::structural_relations::compute_file_relations(&syms, &src, lang);
                }
                LABEL.lock().unwrap().clear();
                let mut m = SITES.lock().unwrap();
                let pending: Vec<_> = m.keys().filter(|k| k.starts_with("~\t")).cloned().collect();
                for k in pending {
                    let (n, ns) = m.remove(&k).unwrap();
                    let e = m.entry(format!("{}\t{}", lang.as_str(), &k[2..])).or_default();
                    e.0 += n;
                    e.1 += ns;
                }
                *m.entry(format!("{}\t#files", lang.as_str())).or_default() = {
                    let prev = m.get(&format!("{}\t#files", lang.as_str())).copied().unwrap_or_default();
                    (prev.0 + 1, 0)
                };
                drop(m);
                files += 1;
            }
            emit("seam", t.elapsed().as_secs_f64() * 1e3, cpu0, json!({"files": files}));
        }
        _ => eprintln!("usage: parse-probe index|watch|seam ..."),
    }
}
