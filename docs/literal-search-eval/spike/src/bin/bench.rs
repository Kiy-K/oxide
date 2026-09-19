//! Benchmarks the experimental, rejected FTS5 trigram challenger
//! (`literal_trigram_spike`) against the shipped native-scan control
//! (`oxide::literal`) on real repositories, for issue #6's Pareto gate.
//! See `../../../README.md` for the committed verdict this feeds.
//!
//! This never touches OXIDE's CLI/MCP/service code and never builds an
//! `.oxide` index: both implementations are called directly as library
//! functions, and the trigram database is written to a throwaway path
//! this binary owns, never `.oxide/`.
//!
//! Usage (from this directory): `cargo run --release --bin bench -- REPO...`

use literal_trigram_spike as trigram;
use oxide::literal;
use std::path::{Path, PathBuf};
use std::time::Instant;

fn ms(t: Instant) -> f64 {
    t.elapsed().as_secs_f64() * 1000.0
}

/// A fixed, deliberately varied pattern set: identifiers likely present in
/// any real codebase, a phrase-shaped one, a too-short one (below the
/// trigram floor), one with regex metacharacters, and one guaranteed
/// absent. Deterministic and corpus-independent, so results are
/// comparable across repos rather than being an artifact of "what queries
/// happened to hit."
fn fixed_patterns() -> Vec<&'static str> {
    vec![
        "fn ",
        "impl ",
        "return",
        "TODO",
        "config",
        "a.b*c(d)",
        "ab",
        "zzz_definitely_absent_xyz",
    ]
}

/// Patterns drawn from the repo's own content via a quick control scan for
/// common short tokens, so at least some queries are guaranteed to hit
/// real, repo-specific text rather than only the fixed set above.
fn patterns_from_repo(root: &Path) -> Vec<String> {
    let mut out = Vec::new();
    for candidate in ["struct", "class", "import", "function", "error"] {
        if let Ok(r) = literal::search(root, candidate, 1) {
            if !r.hits.is_empty() {
                out.push(candidate.to_string());
            }
        }
    }
    out
}

struct Correctness {
    pattern: String,
    verdict: &'static str,
    detail: String,
}

fn check_correctness(root: &Path, db: &Path, pattern: &str) -> Correctness {
    let control = literal::search(root, pattern, literal::MAX_RESULTS);
    let challenger = trigram::search(db, pattern, literal::MAX_RESULTS);
    match (control, challenger) {
        (Ok(c), Ok(t)) if c == t => Correctness {
            pattern: pattern.to_string(),
            verdict: "EXACT",
            detail: format!("{} hits, byte-identical", c.hits.len()),
        },
        (Ok(c), Ok(t)) => {
            let c_only = c.hits.iter().filter(|h| !t.hits.contains(h)).count();
            let t_only = t.hits.iter().filter(|h| !c.hits.contains(h)).count();
            Correctness {
                pattern: pattern.to_string(),
                verdict: "MISMATCH",
                detail: format!(
                    "control={} trigram={} control_only={c_only} trigram_only={t_only}",
                    c.hits.len(),
                    t.hits.len()
                ),
            }
        }
        (Ok(c), Err(e)) => Correctness {
            pattern: pattern.to_string(),
            verdict: "TRIGRAM_ERROR",
            detail: format!("control found {} hits; trigram: {e}", c.hits.len()),
        },
        (Err(e), _) => Correctness {
            pattern: pattern.to_string(),
            verdict: "CONTROL_ERROR",
            detail: format!("{e}"),
        },
    }
}

fn percentile(samples: &[f64], p: f64) -> f64 {
    let mut s = samples.to_vec();
    s.sort_by(|a, b| a.total_cmp(b));
    let idx = ((p / 100.0) * (s.len() - 1) as f64).round() as usize;
    s[idx.min(s.len() - 1)]
}

/// `$SKIP_TRIGRAM=1` runs only the control's repeated scan (no trigram
/// build, no trigram queries) — used to capture the control's own peak
/// RSS in isolation, since a combined session's peak is always dominated
/// by whichever phase allocates most and would tell you nothing about the
/// control on its own.
fn skip_trigram() -> bool {
    std::env::var("SKIP_TRIGRAM").as_deref() == Ok("1")
}

fn bench_repo(root: &Path, reps: usize) {
    println!("== {} ==", root.display());
    if skip_trigram() {
        let mut samples = Vec::new();
        for _ in 0..reps {
            let t = Instant::now();
            let _ = literal::search(root, "fn ", 100).unwrap();
            samples.push(ms(t));
        }
        println!(
            "control-only ({reps} reps): p50={:.3}ms p95={:.3}ms",
            percentile(&samples, 50.0),
            percentile(&samples, 95.0),
        );
        return;
    }
    let tmp = tempfile::tempdir().unwrap();
    let db = tmp.path().join("trigram.db");

    // ---- cold build ----
    let stats = trigram::build(root, &db).unwrap();
    println!(
        "build: {} files ({} skipped non-utf8), {} lines, {}ms, {} bytes db",
        stats.files_indexed,
        stats.files_skipped_non_utf8,
        stats.lines_indexed,
        stats.build_ms,
        stats.db_bytes
    );
    println!("control: 0 bytes, 0ms (no index to build)");

    // ---- correctness ----
    let mut patterns: Vec<String> = fixed_patterns().into_iter().map(String::from).collect();
    patterns.extend(patterns_from_repo(root));
    println!("-- correctness ({} patterns) --", patterns.len());
    let mut exact = 0;
    let mut mismatch = 0;
    let mut trigram_errors = 0;
    for p in &patterns {
        let r = check_correctness(root, &db, p);
        println!("  {:>14} {:?}: {}", r.verdict, r.pattern, r.detail);
        match r.verdict {
            "EXACT" => exact += 1,
            "MISMATCH" => mismatch += 1,
            "TRIGRAM_ERROR" => trigram_errors += 1,
            _ => {}
        }
    }
    println!(
        "correctness: exact={exact} mismatch={mismatch} trigram_error={trigram_errors} / {}",
        patterns.len()
    );

    // ---- latency ----
    let latency_pattern = patterns
        .iter()
        .find(|p| p.len() >= trigram::MIN_PATTERN_LEN && p.as_str() != "zzz_definitely_absent_xyz")
        .cloned()
        .unwrap_or_else(|| "fn ".to_string());
    let mut control_samples = Vec::new();
    let mut trigram_samples = Vec::new();
    for _ in 0..reps {
        let t = Instant::now();
        let _ = literal::search(root, &latency_pattern, 100).unwrap();
        control_samples.push(ms(t));
        let t = Instant::now();
        let _ = trigram::search(&db, &latency_pattern, 100).unwrap();
        trigram_samples.push(ms(t));
    }
    println!(
        "latency ({:?}, {reps} reps): control p50={:.3}ms p95={:.3}ms | trigram p50={:.3}ms p95={:.3}ms",
        latency_pattern,
        percentile(&control_samples, 50.0),
        percentile(&control_samples, 95.0),
        percentile(&trigram_samples, 50.0),
        percentile(&trigram_samples, 95.0),
    );

    // ---- incremental update cost ----
    if let Some(rel) = oxide::scanner::scan_repo_text(root)
        .unwrap()
        .into_iter()
        .next()
    {
        let mut conn = rusqlite::Connection::open(&db).unwrap();
        let t = Instant::now();
        trigram::update_file(&mut conn, root, &rel).unwrap();
        let update_ms = ms(t);
        println!(
            "incremental update (touch {}): trigram={update_ms:.3}ms | control=0.000ms (no index to update)",
            rel.display(),
        );
    }

    println!();
}

/// Peak resident set size in KB for this process so far, from
/// `/proc/self/status`'s `VmHWM` (high-water mark) — Linux-specific. No
/// external `time -v` dependency: not every environment this has run on
/// has one.
fn peak_rss_kb() -> Option<u64> {
    let status = std::fs::read_to_string("/proc/self/status").ok()?;
    status.lines().find_map(|line| {
        line.strip_prefix("VmHWM:")
            .and_then(|rest| rest.split_whitespace().next())
            .and_then(|kb| kb.parse().ok())
    })
}

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let repos: Vec<PathBuf> = if args.is_empty() {
        vec![PathBuf::from(".")]
    } else {
        args.iter().map(PathBuf::from).collect()
    };
    let reps = std::env::var("REPS")
        .ok()
        .and_then(|s| s.parse().ok())
        .unwrap_or(20);
    for repo in &repos {
        bench_repo(repo, reps);
    }
    if let Some(kb) = peak_rss_kb() {
        println!("process peak RSS (VmHWM, whole run so far): {kb} KB");
    }
}
