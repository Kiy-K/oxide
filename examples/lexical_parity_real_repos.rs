//! Generalize the persisted-BM25 parity gate from the synthetic fixture to
//! real repositories at real scale, and measure what the migration actually
//! costs on them.
//!
//! `tests/lexical_persistence.rs` proves bit-exact parity on a four-file
//! fixture. That is the right unit gate but it is a small corpus with a tiny
//! vocabulary. The ContextBench repos cached under
//! `~/.cache/oxide-contextbench/repos` are the opposite: Django, pylint,
//! astropy, matplotlib — tens of thousands of symbols, real identifier
//! distributions, and indexes built by a *previous* OXIDE binary, so running
//! against them also exercises the upgrade-and-backfill path on databases
//! that genuinely predate the feature.
//!
//! # Why this never touches the real index
//!
//! Those cached indexes hold qwen3 embeddings that cost hours to build, and
//! `oxide index` on them would run the embedding stage: with the configured
//! provider now differing from the stored one, that triggers a provider
//! migration which *clears every vector*. So this probe snapshots each
//! database with `VACUUM INTO` (a consistent copy that leaves the source
//! byte-identical) and runs only `update_base` — the parse/symbol/relations/
//! postings stage — on the copy. No embedder is constructed and no vector is
//! read or written.
//!
//! Usage: `cargo run --release --example lexical_parity_real_repos -- [repo...]`
//! With no arguments, sweeps every repo under
//! `~/.cache/oxide-contextbench/repos` that has an `.oxide/index.db`.

use oxide::index::{update_base, IndexBackend, IndexOptions, SqliteStore, LEXICAL_INDEX_KEY};
use oxide::lexical::LexicalIndex;
use std::path::{Path, PathBuf};
use std::time::Instant;

fn ms(t: Instant) -> f64 {
    t.elapsed().as_secs_f64() * 1000.0
}

fn size(p: &Path) -> u64 {
    std::fs::metadata(p).map(|m| m.len()).unwrap_or(0)
}

/// Queries drawn from the repo's own symbols, so the term distribution is the
/// one this corpus actually has rather than words invented for a fixture.
/// Deterministic: symbols arrive from `all_symbols` in `(file, start_line)`
/// order and are sampled at a fixed stride.
fn queries_from(symbols: &[oxide::symbols::Symbol]) -> Vec<String> {
    let mut out = Vec::new();
    if symbols.is_empty() {
        return out;
    }
    let stride = (symbols.len() / 40).max(1);
    for s in symbols.iter().step_by(stride).take(30) {
        out.push(s.name.clone());
    }
    // Multi-term queries: the shape `oxide context --task` actually sends.
    for pair in symbols
        .iter()
        .step_by(stride)
        .take(10)
        .collect::<Vec<_>>()
        .windows(2)
    {
        out.push(format!("{} {}", pair[0].name, pair[1].qualified_name));
    }
    // A repeated token, which term coverage must count once and BM25 every time.
    if let Some(s) = symbols.first() {
        out.push(format!("{} {} handler", s.name, s.name));
    }
    out
}

struct Outcome {
    repo: String,
    symbols: usize,
    before_bytes: u64,
    after_bytes: u64,
    backfill_ms: f64,
    queries: usize,
    scored_docs: usize,
    mismatches: usize,
}

fn check(repo: &Path, workdir: &Path) -> anyhow::Result<Option<Outcome>> {
    let live = repo.join(".oxide/index.db");
    if !live.exists() {
        return Ok(None);
    }
    let name = repo
        .file_name()
        .map(|s| s.to_string_lossy().into_owned())
        .unwrap_or_default();

    // Consistent snapshot; leaves the source untouched.
    let snapshot = workdir.join(format!("{name}.db"));
    {
        let src = rusqlite::Connection::open_with_flags(
            &live,
            rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY,
        )?;
        src.execute("VACUUM INTO ?1", [snapshot.to_string_lossy()])?;
    }
    let before_bytes = size(&snapshot);

    let root = {
        let store = SqliteStore::open_read_only(&snapshot)?;
        store.get_meta("root")?.map(PathBuf::from)
    };
    let Some(root) = root else {
        println!("  {name}: no root in meta, skipped");
        return Ok(None);
    };
    if !root.exists() {
        println!(
            "  {name}: recorded root {} is gone, skipped",
            root.display()
        );
        return Ok(None);
    }

    // Base stage only — no embedder is constructed, so the embedding stage
    // and its provider-migration logic never run.
    let mut store = SqliteStore::open(&snapshot)?;
    let had_key = store.get_meta(LEXICAL_INDEX_KEY)?.is_some();
    let t = Instant::now();
    update_base(&root, &mut store, &IndexOptions::default())?;
    let backfill_ms = ms(t);
    let published = store.get_meta(LEXICAL_INDEX_KEY)?;
    drop(store);
    let after_bytes = size(&snapshot);

    let store = SqliteStore::open_read_only(&snapshot)?;
    let symbols = store.all_symbols()?;
    anyhow::ensure!(
        published.as_deref() == Some(oxide::index::LEXICAL_INDEX_VERSION.to_string().as_str()),
        "{name}: update_base did not publish the lexical generation key (had_key={had_key})"
    );
    let (docs, _) = store.lexical_totals()?;
    anyhow::ensure!(
        docs == symbols.len(),
        "{name}: lexical_docs has {docs} rows for {} symbols",
        symbols.len()
    );

    let memory = LexicalIndex::build(&symbols, Some(&root));
    let queries = queries_from(&symbols);
    let mut mismatches = 0usize;
    let mut scored_docs = 0usize;
    for q in &queries {
        let mem = memory.search(q, 1.5, 0.75).0;
        let per = oxide::lexical::score(
            &oxide::lexical::prepare_from_store(&store, symbols.len(), q)?,
            1.5,
            0.75,
        )
        .0;
        scored_docs += mem.len();
        if mem.len() != per.len() {
            mismatches += 1;
            println!(
                "  {name}: query {q:?} scored {} docs in memory, {} persisted",
                mem.len(),
                per.len()
            );
            continue;
        }
        let mut bad: Option<(u64, f32, f32)> = None;
        for (id, m) in &mem {
            match per.get(id) {
                Some(p)
                    if m.0.to_bits() == p.0.to_bits()
                        && m.1 == p.1
                        && m.2.to_bits() == p.2.to_bits() => {}
                Some(p) => bad = Some((*id, m.0, p.0)),
                None => bad = Some((*id, m.0, f32::NAN)),
            }
        }
        if let Some((id, a, b)) = bad {
            mismatches += 1;
            println!("  {name}: query {q:?} doc {id}: memory {a} vs persisted {b}");
        }
    }
    let _ = std::fs::remove_file(&snapshot);

    Ok(Some(Outcome {
        repo: name,
        symbols: symbols.len(),
        before_bytes,
        after_bytes,
        backfill_ms,
        queries: queries.len(),
        scored_docs,
        mismatches,
    }))
}

fn main() -> anyhow::Result<()> {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let repos: Vec<PathBuf> = if args.is_empty() {
        let base = PathBuf::from(std::env::var("HOME")?).join(".cache/oxide-contextbench/repos");
        let mut v: Vec<PathBuf> = std::fs::read_dir(&base)?
            .filter_map(|e| e.ok().map(|e| e.path()))
            .filter(|p| p.join(".oxide/index.db").exists())
            .collect();
        v.sort();
        v
    } else {
        args.iter().map(PathBuf::from).collect()
    };
    // A repo named on the command line is evidence the caller asked for. If
    // it cannot be compared — snapshot failed, no `root` in meta, root gone —
    // the run is incomplete and must not report a clean parity result. In
    // sweep mode (no arguments) a skip is ordinary: the cache holds indexes
    // this probe has no business judging.
    let explicit = !args.is_empty();
    println!("checking {} repos", repos.len());

    let workdir = tempfile::tempdir()?;
    let mut results = Vec::new();
    let mut unusable = 0usize;
    for repo in &repos {
        match check(repo, workdir.path()) {
            Ok(Some(o)) => {
                println!(
                    "{:<28} {:>7} symbols  db {:>6.1} -> {:>6.1} MB (+{:>5.1}%)  backfill {:>8.0} ms  {:>3} queries  {:>7} scored  {}",
                    o.repo,
                    o.symbols,
                    o.before_bytes as f64 / 1e6,
                    o.after_bytes as f64 / 1e6,
                    (o.after_bytes as f64 / o.before_bytes.max(1) as f64 - 1.0) * 100.0,
                    o.backfill_ms,
                    o.queries,
                    o.scored_docs,
                    if o.mismatches == 0 { "EXACT" } else { "MISMATCH" }
                );
                results.push(o);
            }
            Ok(None) => {
                if explicit {
                    unusable += 1;
                }
            }
            Err(e) => {
                println!("{}: ERROR {e}", repo.display());
                unusable += 1;
            }
        }
    }

    let bad: usize = results.iter().map(|o| o.mismatches).sum();
    let sym: usize = results.iter().map(|o| o.symbols).sum();
    let before: u64 = results.iter().map(|o| o.before_bytes).sum();
    let after: u64 = results.iter().map(|o| o.after_bytes).sum();
    let scored: usize = results.iter().map(|o| o.scored_docs).sum();
    let qs: usize = results.iter().map(|o| o.queries).sum();
    println!(
        "\n{} repos, {sym} symbols, {qs} queries, {scored} scored documents compared\n\
         db total {:.1} -> {:.1} MB (+{:.1}%)\n{}",
        results.len(),
        before as f64 / 1e6,
        after as f64 / 1e6,
        (after as f64 / before.max(1) as f64 - 1.0) * 100.0,
        match (bad, unusable) {
            (0, 0) => "PARITY EXACT on every query".to_string(),
            (0, n) => format!(
                "PARITY EXACT on what ran, but {n} requested repo(s) could not be compared — this run is not complete evidence"
            ),
            _ => "PARITY BROKEN".to_string(),
        }
    );
    anyhow::ensure!(bad == 0, "{bad} queries diverged");
    anyhow::ensure!(
        unusable == 0,
        "{unusable} requested repo(s) could not be compared"
    );
    Ok(())
}
