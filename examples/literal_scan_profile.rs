//! Phase-by-phase profiling of `oxide::literal::search` (the shipped
//! control) against `rg -F`, to find where the ~1.3-1.6x latency gap
//! recorded in `docs/literal-search-eval/README.md` actually comes from.
//! Diagnostic only -- no changes to `src/literal.rs`, nothing here is an
//! optimization.
//!
//! Decomposes the control's own work into the same phases it performs
//! internally but as separate, individually-timed steps (walk, then read,
//! then match), and separately measures ripgrep's engine in-process (via
//! the `grep-searcher`/`grep-regex` dev-dependencies -- ripgrep's own
//! matching code, not a reimplementation, and not a subprocess) over the
//! *same file set* `scan_repo_text` produced, so the comparison isolates
//! matcher/walk implementation differences from ignore-policy differences.
//! A real `rg -F` subprocess run (same file set, same pattern) is included
//! too, to correlate these in-process numbers with the subprocess-based
//! benchmark already in the eval doc.
//!
//! Usage: `cargo run --release --example literal_scan_profile -- [REPO] [PATTERN]`
//! (`REPS` env var controls repetitions, default 10.)

use grep_regex::RegexMatcherBuilder;
use grep_searcher::sinks::UTF8;
use grep_searcher::Searcher;
use oxide::{literal, scanner};
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::Instant;

fn ms(t: Instant) -> f64 {
    t.elapsed().as_secs_f64() * 1000.0
}

fn percentile(samples: &[f64], p: f64) -> f64 {
    let mut s = samples.to_vec();
    s.sort_by(|a, b| a.total_cmp(b));
    let idx = ((p / 100.0) * (s.len() - 1) as f64).round() as usize;
    s[idx.min(s.len() - 1)]
}

fn report(name: &str, samples: &[f64]) {
    println!(
        "{name:<28} p50={:>8.3}ms  p95={:>8.3}ms  mean={:>8.3}ms",
        percentile(samples, 50.0),
        percentile(samples, 95.0),
        samples.iter().sum::<f64>() / samples.len() as f64,
    );
}

fn main() {
    let root = std::env::args().nth(1).unwrap_or_else(|| ".".to_string());
    let root = Path::new(&root);
    let pattern = std::env::args().nth(2).unwrap_or_else(|| "fn ".to_string());
    let reps: usize = std::env::var("REPS")
        .ok()
        .and_then(|s| s.parse().ok())
        .unwrap_or(10);

    println!("root={} pattern={pattern:?} reps={reps}", root.display());

    // ---- Phase A: file discovery/walk alone (scanner::scan_repo_text) ----
    let mut walk_samples = Vec::new();
    let mut files: Vec<PathBuf> = Vec::new();
    for _ in 0..reps {
        let t = Instant::now();
        files = scanner::scan_repo_text(root).unwrap();
        walk_samples.push(ms(t));
    }
    report("A: walk (scan_repo_text)", &walk_samples);
    println!("   kept {} files", files.len());

    // ---- Phase B: reading every kept file into memory, walk excluded ----
    let mut read_samples = Vec::new();
    let mut total_bytes = 0u64;
    for _ in 0..reps {
        let t = Instant::now();
        let mut bytes = 0u64;
        for rel in &files {
            if let Ok(b) = std::fs::read(root.join(rel)) {
                bytes += b.len() as u64;
            }
        }
        read_samples.push(ms(t));
        total_bytes = bytes;
    }
    report("B: read all kept files", &read_samples);
    println!("   {total_bytes} bytes across {} files", files.len());

    // ---- Phase C: the control end-to-end (walk + read + match, as shipped) ----
    let mut control_samples = Vec::new();
    let mut control_hits = 0usize;
    for _ in 0..reps {
        let t = Instant::now();
        let r = literal::search(root, &pattern, literal::MAX_RESULTS).unwrap();
        control_samples.push(ms(t));
        control_hits = r.hits.len();
    }
    report("C: control end-to-end", &control_samples);
    println!(
        "   {control_hits} hits (capped at {}); C - (A+B) isolates match-only cost, roughly",
        literal::MAX_RESULTS
    );

    // ---- Phase D: ripgrep's own matcher, in-process, same file list ----
    // Same file set as A/B/C (not rg's own default ignore policy) so this
    // isolates matcher/walk implementation, not ignore-policy differences
    // -- the same discipline tests/literal_search.rs's parity test uses.
    let matcher = RegexMatcherBuilder::new()
        .fixed_strings(true)
        .build(&pattern)
        .expect("build fixed-string matcher");
    let mut rg_engine_samples = Vec::new();
    let mut rg_engine_hits = 0usize;
    for _ in 0..reps {
        let t = Instant::now();
        let mut hits = 0usize;
        let mut searcher = Searcher::new();
        for rel in &files {
            let full = root.join(rel);
            let _ = searcher.search_path(
                &matcher,
                &full,
                UTF8(|_line_number, _line| {
                    hits += 1;
                    Ok(true)
                }),
            );
        }
        rg_engine_samples.push(ms(t));
        rg_engine_hits = hits;
    }
    report("D: rg engine, in-process", &rg_engine_samples);
    println!("   {rg_engine_hits} matching lines (uncapped -- ripgrep has no --limit)");

    // ---- Phase E: real `rg -F` subprocess, same file list, for correlation ----
    if Command::new("rg").arg("--version").output().is_ok() {
        let mut rg_subprocess_samples = Vec::new();
        for _ in 0..reps {
            let t = Instant::now();
            let _ = Command::new("rg")
                .args(["-F", "--no-config", "-c"])
                .arg(&pattern)
                .args(&files)
                .current_dir(root)
                .output()
                .unwrap();
            rg_subprocess_samples.push(ms(t));
        }
        report("E: rg -F subprocess", &rg_subprocess_samples);
        println!(
            "   D vs E isolates process-spawn overhead; C vs D isolates walk+read+matcher implementation"
        );
    } else {
        println!("E: rg -F subprocess          skipped (rg not installed)");
    }

    println!();
    println!(
        "sum(A+B) = {:.3}ms; C (control end-to-end) = {:.3}ms -- the difference is roughly the byte-scan's own cost once walk and I/O are removed",
        percentile(&walk_samples, 50.0) + percentile(&read_samples, 50.0),
        percentile(&control_samples, 50.0),
    );
}
