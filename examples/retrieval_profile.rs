//! Per-stage wall-clock **and allocation** breakdown of one request against
//! an existing index, finer-grained than `request_profile.rs`: the corpus
//! load is split into raw row iteration / decode / relations / merge, and
//! every evidence source (literal, lexical, semantic, structural, git,
//! blast radius) plus fusion, hydration and snippet rendering is timed on
//! its own. Allocation counts come from a counting global allocator, so a
//! stage's number is what that stage allocated, not what it retained.
//!
//! Usage: retrieval_profile <repo_root> "<query>" [--git-range <A..B>]
//!        retrieval_profile <repo_root> "<query>" --stage <name> [--json]
//!                          [--hydrate <N>]
//!
//! Run twice in the same process (see `repeat` below) to separate a cold
//! first request (page cache, prepared statements, embedder load) from a
//! warm one — the difference is what a long-lived `oxide mcp` amortizes.
//!
//! `--stage <name>` is the **isolated** mode used by
//! `scripts/corpus_load_baseline.py` (docs/retrieval-profile/corpus-load-
//! baseline/): the process runs only that stage's untimed prerequisites
//! (open the store, and for the stages that need one, load a snapshot),
//! then times the stage itself `PROFILE_REPEAT` times (default 1). The
//! sequential mode above calls `all_symbols` and `all_symbol_relations`
//! before `SymbolSnapshot::load`, so its first round is a process-cold
//! measurement of the *first* stage only; this mode makes repetition 0 of
//! every stage process-cold (page cache and allocator state are whatever
//! the previous process left — see the report for what "cold" means here).
//! With `--json`, one JSON object per repetition goes to stdout; `--hydrate
//! N` sets the `hydrate` stage's candidate count (default 400 = both
//! channels' `FUSION_CANDIDATE_LIMIT`).
//!
//! Stages: `all_symbols`, `all_symbols_rows_floor` (the same statement with
//! every column's bytes read off the row and nothing decoded or allocated —
//! SQLite's share of `all_symbols`, including overflow-page reads, which a
//! step-only probe would defer), `all_symbol_relations`, `snapshot_load`,
//! `relation_index`, `callers_index`, `hydrate`, `search_noexpand`,
//! `search_expand`, `context`, `context_cached` (snapshot + index supplied,
//! the `oxide mcp` cache-hit shape), `payload` (a diagnostic read of
//! `references_json`/`imports_json`/`symbol_relations` sizes over raw
//! SQL, never timed and never part of a request) and `order_digest` (the
//! exact `all_symbols` order as data: an FNV-1a digest over the id
//! sequence plus the count of `(file, start_line)` ties, so two builds'
//! corpus orders can be compared byte for byte).
#[path = "support/probe_graph.rs"]
mod probe_graph;

use oxide::context::{build_context_with, ContextOptions};
use oxide::embeddings::open_embedder;
use oxide::relations::{RelationGraph, RelationIndex};
use oxide::retrieval::{RetrievalEngine, RetrievalMode, SearchMode, SearchOptions, SymbolSnapshot};
use oxide::storage::{IndexBackend, SqliteStore};
use oxide::symbols::Symbol;
use std::alloc::{GlobalAlloc, Layout, System};
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::time::Instant;

struct Counting;
static ALLOCS: AtomicUsize = AtomicUsize::new(0);
static BYTES: AtomicUsize = AtomicUsize::new(0);
static COUNTING: AtomicBool = AtomicBool::new(true);

unsafe impl GlobalAlloc for Counting {
    unsafe fn alloc(&self, layout: Layout) -> *mut u8 {
        if COUNTING.load(Ordering::Relaxed) {
            ALLOCS.fetch_add(1, Ordering::Relaxed);
            BYTES.fetch_add(layout.size(), Ordering::Relaxed);
        }
        System.alloc(layout)
    }
    unsafe fn dealloc(&self, ptr: *mut u8, layout: Layout) {
        System.dealloc(ptr, layout)
    }
    unsafe fn realloc(&self, ptr: *mut u8, layout: Layout, new_size: usize) -> *mut u8 {
        if COUNTING.load(Ordering::Relaxed) {
            ALLOCS.fetch_add(1, Ordering::Relaxed);
            BYTES.fetch_add(new_size, Ordering::Relaxed);
        }
        System.realloc(ptr, layout, new_size)
    }
}

#[global_allocator]
static GLOBAL: Counting = Counting;

struct Stage {
    t: Instant,
    a: usize,
    b: usize,
}

fn begin() -> Stage {
    Stage {
        t: Instant::now(),
        a: ALLOCS.load(Ordering::Relaxed),
        b: BYTES.load(Ordering::Relaxed),
    }
}

fn end(s: Stage, name: &str, note: impl std::fmt::Display) {
    let ms = s.t.elapsed().as_secs_f64() * 1e3;
    let allocs = ALLOCS.load(Ordering::Relaxed) - s.a;
    let kb = (BYTES.load(Ordering::Relaxed) - s.b) / 1024;
    println!("{name:<32} {ms:>9.2} ms {allocs:>9} allocs {kb:>9} KB  {note}");
}

fn main() -> anyhow::Result<()> {
    if std::env::var("PROFILE_COUNT_ALLOCS").as_deref() == Ok("0") {
        COUNTING.store(false, Ordering::Relaxed);
    }
    let args: Vec<String> = std::env::args().collect();
    let root = std::path::PathBuf::from(&args[1]).canonicalize()?;
    let query = args[2].clone();
    let git_range = args
        .iter()
        .position(|a| a == "--git-range")
        .and_then(|i| args.get(i + 1))
        .cloned()
        .unwrap_or_else(|| "HEAD~1".to_string());
    let repeat: usize = std::env::var("PROFILE_REPEAT")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(2);
    if let Some(stage) = args
        .iter()
        .position(|a| a == "--stage")
        .and_then(|i| args.get(i + 1))
    {
        let json = args.iter().any(|a| a == "--json");
        let hydrate_n: usize = args
            .iter()
            .position(|a| a == "--hydrate")
            .and_then(|i| args.get(i + 1))
            .and_then(|v| v.parse().ok())
            .unwrap_or(400);
        return run_stage(&root, &query, stage, repeat, json, hydrate_n);
    }

    let s = begin();
    let embedder = open_embedder(None)?;
    end(s, "open_embedder", embedder.name());

    for round in 0..repeat {
        println!(
            "---- round {round} ({}) ----",
            if round == 0 { "cold" } else { "warm" }
        );
        let s = begin();
        let store = SqliteStore::open_read_only(&root.join(".oxide/index.db"))?;
        end(s, "open_read_only", "");

        let s = begin();
        let stats = store.stats()?;
        end(s, "stats (3 COUNT)", format!("{} symbols", stats.symbols));

        let s = begin();
        let n = store.symbol_count()?;
        end(s, "symbol_count", n);

        // ---- corpus load, split ----
        let s = begin();
        let all = store.all_symbols()?;
        end(
            s,
            "all_symbols (decode+json)",
            format!("{} rows", all.len()),
        );
        let refs: usize = all.iter().map(|s| s.references.len()).sum();
        let imps: usize = all.iter().map(|s| s.imports.len()).sum();
        println!(
            "{:<32} {:>9} refs {:>9} imports  (avg {:.1} refs/symbol)",
            "  (payload)",
            refs,
            imps,
            refs as f64 / all.len().max(1) as f64
        );
        drop(all);

        let s = begin();
        let rel = store.all_symbol_relations()?;
        let rel_rows: usize = rel.values().map(|(c, b)| c.len() + b.len()).sum();
        end(
            s,
            "all_symbol_relations",
            format!("{} symbols, {rel_rows} rows", rel.len()),
        );
        drop(rel);

        let s = begin();
        let snapshot = SymbolSnapshot::load(&store)?;
        end(
            s,
            "SymbolSnapshot::load (total)",
            format!("{} symbols", snapshot.symbols.len()),
        );

        let s = begin();
        let graph = RelationGraph::build(&snapshot.symbols);
        end(s, "RelationGraph::build", "");

        // ---- lexical ----
        let s = begin();
        let lq = oxide::lexical::prepare_from_store(&store, n, &query)?;
        let postings: usize = lq.terms.iter().map(|t| t.postings.len()).sum();
        end(
            s,
            "lexical prepare",
            format!("{} terms, {postings} postings", lq.terms.len()),
        );
        let s = begin();
        let (scores, _) = oxide::lexical::score(&lq, 1.5, 0.75);
        end(s, "lexical score", format!("{} docs", scores.len()));

        // ---- semantic ----
        let s = begin();
        let qv = embedder.embed_query(&query);
        end(s, "embed_query (inference)", format!("dim {}", qv.len()));
        let s = begin();
        let mut rows = 0usize;
        store.for_each_embedding(&mut |_, _, _| rows += 1)?;
        end(s, "embedding scan (rows only)", format!("{rows} rows"));

        // ---- hydration ----
        let ids: Vec<u64> = snapshot.symbols.iter().take(400).map(|s| s.id()).collect();
        let s = begin();
        let hydrated = store.symbols_by_ids(&ids)?;
        end(s, "symbols_by_ids x400", format!("{} rows", hydrated.len()));

        // ---- search surfaces (engine without snapshot = one-shot CLI) ----
        let engine = RetrievalEngine::new(&store, embedder.as_ref());
        for (label, mode) in [
            ("search lexical --no-expand", SearchMode::LexicalOnly),
            ("search semantic", SearchMode::VectorOnly),
            ("search hybrid --no-expand", SearchMode::Hybrid),
        ] {
            let s = begin();
            let hits = engine.search(
                &query,
                &SearchOptions {
                    limit: 16,
                    mode,
                    expand: false,
                    retrieval_mode: RetrievalMode::Balanced,
                },
            )?;
            end(s, label, format!("{} hits", hits.len()));
        }
        // Expansion on a fresh engine so the corpus load is attributed to it.
        let engine2 = RetrievalEngine::new(&store, embedder.as_ref());
        let s = begin();
        let hits = engine2.search(
            &query,
            &SearchOptions {
                limit: 16,
                mode: SearchMode::Hybrid,
                expand: true,
                retrieval_mode: RetrievalMode::Balanced,
            },
        )?;
        end(
            s,
            "search hybrid (expand, cold eng)",
            format!("{} hits", hits.len()),
        );

        // ---- structural / git / blast on the shared graph ----
        let s = begin();
        let mut nb = 0;
        for h in hits.iter().take(5) {
            nb += graph.neighbors(&h.symbol).len();
        }
        end(s, "neighbors x5", format!("{nb} neighbors"));

        let s = begin();
        let mut c = 0;
        for h in hits.iter().take(3) {
            c += graph.callers_of(&h.symbol.name).len();
        }
        end(s, "callers_of x3 (+index build)", format!("{c} callers"));

        let seeds: Vec<&Symbol> = hits.iter().take(3).map(|h| &h.symbol).collect();
        let s = begin();
        let blast = oxide::blast_radius::compute(&graph, &seeds, true);
        end(
            s,
            "blast_radius::compute",
            format!("{} members", blast.len()),
        );

        let s = begin();
        let git = oxide::gitctx::build_git_context(&root, &snapshot.symbols, &git_range)?;
        end(
            s,
            "gitctx::build_git_context",
            format!("{} changed symbols", git.changed_symbols.len()),
        );

        let s = begin();
        let first = query.split_whitespace().next().unwrap_or("x");
        let lit = oxide::literal::search(&root, first, 200)?;
        end(
            s,
            "literal::search (1st term)",
            format!("{} hits, truncated={}", lit.hits.len(), lit.truncated),
        );

        // ---- context surfaces (engine with the already-loaded snapshot) ----
        let engine3 = RetrievalEngine::with_snapshot(&store, embedder.as_ref(), &snapshot);
        for (label, git, blast) in [
            ("context balanced (warm snap)", false, false),
            ("context --git (warm snap)", true, false),
            ("context --blast (warm snap)", false, true),
        ] {
            let s = begin();
            let pack = build_context_with(
                &root,
                &engine3,
                &query,
                &ContextOptions {
                    git,
                    blast_radius: blast,
                    ..ContextOptions::default()
                },
            )?;
            end(s, label, format!("{} items", pack.items.len()));
        }
        let engine4 = RetrievalEngine::new(&store, embedder.as_ref());
        let s = begin();
        let pack = build_context_with(&root, &engine4, &query, &ContextOptions::default())?;
        end(
            s,
            "context balanced (cold eng)",
            format!("{} items", pack.items.len()),
        );
    }
    Ok(())
}

/// Peak resident set of this process so far (`VmHWM`, KB), the number the
/// report keeps apart from the allocator's cumulative byte count: the two
/// answer different questions (what the OS had to give the process vs.
/// how much churn a stage produced). Linux only; `None` elsewhere.
fn vm_hwm_kb() -> Option<u64> {
    let status = std::fs::read_to_string("/proc/self/status").ok()?;
    status
        .lines()
        .find(|l| l.starts_with("VmHWM:"))
        .and_then(|l| l.split_whitespace().nth(1))
        .and_then(|v| v.parse().ok())
}

/// One timed repetition of one stage: what `end` prints, as data.
struct Sample {
    ms: f64,
    allocs: usize,
    bytes: usize,
    n: usize,
    note: String,
}

fn sample(s: Stage, n: usize, note: impl Into<String>) -> Sample {
    Sample {
        ms: s.t.elapsed().as_secs_f64() * 1e3,
        allocs: ALLOCS.load(Ordering::Relaxed) - s.a,
        bytes: BYTES.load(Ordering::Relaxed) - s.b,
        n,
        note: note.into(),
    }
}

fn emit(stage: &str, rep: usize, r: &Sample, json: bool) {
    let hwm = vm_hwm_kb();
    if json {
        println!(
            "{}",
            serde_json::json!({
                "stage": stage,
                "rep": rep,
                "ms": r.ms,
                "allocs": r.allocs,
                "bytes": r.bytes,
                "n": r.n,
                "note": r.note,
                "vm_hwm_kb": hwm,
            })
        );
    } else {
        println!(
            "{stage:<24} rep {rep} {:>9.2} ms {:>9} allocs {:>9} KB  n={} hwm={}KB  {}",
            r.ms,
            r.allocs,
            r.bytes / 1024,
            r.n,
            hwm.unwrap_or(0),
            r.note
        );
    }
}

/// Spread `n` candidate ids across the corpus in its stored order, so a
/// hydration sample touches pages throughout the table the way a fused
/// candidate set does, rather than one run of adjacent rows.
fn strided_ids(symbols: &[Symbol], n: usize) -> Vec<u64> {
    if symbols.is_empty() {
        return Vec::new();
    }
    let step = (symbols.len() / n.max(1)).max(1);
    symbols
        .iter()
        .step_by(step)
        .take(n)
        .map(|s| s.id())
        .collect()
}

fn run_stage(
    root: &std::path::Path,
    query: &str,
    stage: &str,
    repeat: usize,
    json: bool,
    hydrate_n: usize,
) -> anyhow::Result<()> {
    let db = root.join(".oxide/index.db");
    if stage == "payload" {
        return payload_diagnostic(&db, json);
    }
    if stage == "order_digest" {
        return order_digest(&db, json);
    }
    if stage == "probe_plans" {
        return probe_plans(&db, json);
    }
    if stage.starts_with("probe_oracle")
        || stage == "probe_diag_build"
        || stage.starts_with("struct_")
        || stage.starts_with("search_expand_probe")
        || stage.starts_with("context_probe")
    {
        return structural_probe_stage(root, query, stage, repeat, json);
    }
    let store = SqliteStore::open_read_only(&db)?;
    // The rows-floor stages read through a plain rusqlite connection
    // (opened once per process, untimed, like `store`) so they can run the
    // production statement without decoding rows into `Symbol`s.
    let raw_conn = if stage.starts_with("all_symbols_rows_floor") {
        Some(rusqlite::Connection::open_with_flags(
            &db,
            rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY | rusqlite::OpenFlags::SQLITE_OPEN_NO_MUTEX,
        )?)
    } else {
        None
    };
    let needs_embedder = matches!(
        stage,
        "search_noexpand" | "search_expand" | "context" | "context_cached"
    );
    // Untimed: the embedder is per-process state every surface amortizes
    // (its own cost is in §3 of the profile), never part of a stage.
    let embedder = if needs_embedder {
        Some(open_embedder(None)?)
    } else {
        None
    };
    // Untimed prerequisite for the stages that operate on a loaded corpus.
    let snapshot = if matches!(
        stage,
        "relation_index" | "callers_index" | "hydrate" | "context_cached"
    ) {
        Some(SymbolSnapshot::load(&store)?)
    } else {
        None
    };
    let cached_index = if stage == "context_cached" {
        snapshot.as_ref().map(|s| RelationIndex::build(&s.symbols))
    } else {
        None
    };
    let opts_noexpand = SearchOptions {
        limit: 10,
        mode: SearchMode::Hybrid,
        expand: false,
        retrieval_mode: RetrievalMode::Balanced,
    };
    let opts_expand = SearchOptions {
        limit: 10,
        mode: SearchMode::Hybrid,
        expand: true,
        retrieval_mode: RetrievalMode::Balanced,
    };

    for rep in 0..repeat {
        let r = match stage {
            "all_symbols" => {
                let s = begin();
                let all = store.all_symbols()?;
                let refs: usize = all.iter().map(|s| s.references.len()).sum();
                let imps: usize = all.iter().map(|s| s.imports.len()).sum();
                let r = sample(s, all.len(), format!("refs={refs} imports={imps}"));
                drop(all);
                r
            }
            "all_symbols_rows_floor" | "all_symbols_rows_floor_unordered" => {
                let conn = raw_conn.as_ref().expect("opened above");
                // Same statement and order as `SqliteStore::all_symbols`;
                // the `_unordered` twin drops the ORDER BY so the sorter's
                // share is visible *with* every column materialized (the
                // control the earlier step-only probe lacked). Diagnostic
                // only: production keeps the ORDER BY (AGENTS.md). The
                // `prepare` sits inside the timed window exactly as it does
                // in `all_symbols`, and the connection is the per-process
                // one opened untimed above, so the floor and the production
                // stage differ only in what they do with each row.
                let order = if stage.ends_with("_unordered") {
                    ""
                } else {
                    " ORDER BY file, start_line"
                };
                let s = begin();
                let mut stmt = conn.prepare(&format!(
                    "SELECT file, qualified_name, name, kind, language, start_line, end_line,
                            content_hash, signature, imports_json, exported, parent, references_json
                     FROM symbols{order}"
                ))?;
                let mut rows = stmt.query([])?;
                let (mut n, mut bytes) = (0usize, 0usize);
                while let Some(r) = rows.next()? {
                    for i in 0..13 {
                        // Every column materialized (text bytes touched,
                        // integers read), nothing copied.
                        bytes += match r.get_ref(i)? {
                            rusqlite::types::ValueRef::Text(t) => t.len(),
                            rusqlite::types::ValueRef::Integer(_) => 8,
                            rusqlite::types::ValueRef::Null => 0,
                            other => other.as_bytes().map(<[u8]>::len).unwrap_or(0),
                        };
                    }
                    n += 1;
                }
                sample(s, n, format!("column_bytes={bytes}"))
            }
            "all_symbol_relations" => {
                let s = begin();
                let rel = store.all_symbol_relations()?;
                let rows: usize = rel.values().map(|(c, b)| c.len() + b.len()).sum();
                let r = sample(s, rel.len(), format!("rows={rows}"));
                drop(rel);
                r
            }
            "snapshot_load" => {
                let s = begin();
                let snap = SymbolSnapshot::load(&store)?;
                let r = sample(s, snap.symbols.len(), "with_relations");
                drop(snap);
                r
            }
            "relation_index" => {
                let snap = snapshot.as_ref().expect("loaded above");
                let s = begin();
                let index = RelationIndex::build(&snap.symbols);
                let r = sample(s, snap.symbols.len(), "");
                drop(index);
                r
            }
            "callers_index" => {
                // What `context` pays on its first `callers_of` after the
                // index is built: the lazy reverse index over every
                // symbol's `calls`. A fresh graph per repetition so each
                // one builds it again.
                let snap = snapshot.as_ref().expect("loaded above");
                let graph = RelationGraph::build(&snap.symbols);
                let first = query.split_whitespace().next().unwrap_or("x");
                let s = begin();
                let c = graph.callers_of(first).len();
                sample(s, snap.symbols.len(), format!("callers={c}"))
            }
            "hydrate" => {
                let snap = snapshot.as_ref().expect("loaded above");
                let ids = strided_ids(&snap.symbols, hydrate_n);
                let s = begin();
                let rows = store.symbols_by_ids(&ids)?;
                let r = sample(s, rows.len(), format!("requested={}", ids.len()));
                drop(rows);
                r
            }
            "search_noexpand" | "search_expand" => {
                let embedder = embedder.as_ref().expect("opened above");
                let engine = RetrievalEngine::new(&store, embedder.as_ref());
                let opts = if stage == "search_expand" {
                    &opts_expand
                } else {
                    &opts_noexpand
                };
                let s = begin();
                let hits = engine.search(query, opts)?;
                let expanded = hits
                    .iter()
                    .filter(|h| h.reasons.iter().any(|r| r.contains('←')))
                    .count();
                sample(s, hits.len(), format!("expanded_hits={expanded}"))
            }
            "context" => {
                let embedder = embedder.as_ref().expect("opened above");
                let engine = RetrievalEngine::new(&store, embedder.as_ref());
                let s = begin();
                let pack = build_context_with(root, &engine, query, &ContextOptions::default())?;
                sample(s, pack.items.len(), "cold engine")
            }
            "context_cached" => {
                let embedder = embedder.as_ref().expect("opened above");
                let snap = snapshot.as_ref().expect("loaded above");
                let index = cached_index.as_ref().expect("built above");
                let engine = RetrievalEngine::with_snapshot_and_index(
                    &store,
                    embedder.as_ref(),
                    snap,
                    index,
                );
                let s = begin();
                let pack = build_context_with(root, &engine, query, &ContextOptions::default())?;
                sample(s, pack.items.len(), "snapshot+index supplied")
            }
            other => anyhow::bail!("unknown stage {other:?}"),
        };
        emit(stage, rep, &r, json);
    }
    Ok(())
}

/// What the corpus load has to read, measured over raw SQL rather than
/// through `Symbol`: per-row JSON payload bytes and item counts for
/// `references_json`/`imports_json`, the relations side table, and the
/// database's page geometry. Never timed — the point is to know the shape
/// of the input, not to add another stage.
fn payload_diagnostic(db: &std::path::Path, json: bool) -> anyhow::Result<()> {
    let conn = rusqlite::Connection::open_with_flags(
        db,
        rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY | rusqlite::OpenFlags::SQLITE_OPEN_NO_MUTEX,
    )?;
    let one = |sql: &str| -> anyhow::Result<i64> {
        Ok(conn
            .query_row(sql, [], |r| r.get::<_, Option<i64>>(0))?
            .unwrap_or(0))
    };
    let symbols = one("SELECT COUNT(*) FROM symbols")?;
    let files = one("SELECT COUNT(*) FROM files")?;
    let embeddings = one("SELECT COUNT(*) FROM embeddings")?;
    // Index identity as the writer published it: what a reader would
    // validate against, recorded so a baseline can be tied to one exact
    // index state.
    let mut meta = serde_json::Map::new();
    {
        let mut stmt = conn.prepare("SELECT key, value FROM meta ORDER BY key")?;
        let rows = stmt.query_map([], |r| Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?)))?;
        for row in rows {
            let (k, v) = row?;
            if k != "root" {
                meta.insert(k, serde_json::Value::String(v));
            }
        }
    }
    let refs_bytes = one("SELECT SUM(LENGTH(CAST(references_json AS BLOB))) FROM symbols")?;
    let imports_bytes = one("SELECT SUM(LENGTH(CAST(imports_json AS BLOB))) FROM symbols")?;
    let refs_items = one("SELECT SUM(json_array_length(references_json)) FROM symbols")?;
    let imports_items = one("SELECT SUM(json_array_length(imports_json)) FROM symbols")?;
    let distinct_imports = one("SELECT COUNT(DISTINCT imports_json) FROM symbols")?;
    let sig_bytes = one("SELECT SUM(LENGTH(CAST(signature AS BLOB))) FROM symbols")?;
    let text_bytes = one(
        "SELECT SUM(LENGTH(CAST(file AS BLOB)) + LENGTH(CAST(qualified_name AS BLOB)) \
         + LENGTH(CAST(name AS BLOB)) + LENGTH(CAST(COALESCE(parent, '') AS BLOB))) FROM symbols",
    )?;
    let relations_rows = one("SELECT COUNT(*) FROM symbol_relations")?;
    let relations_bytes = one("SELECT SUM(LENGTH(CAST(target AS BLOB))) FROM symbol_relations")?;
    let relations_symbols = one("SELECT COUNT(DISTINCT symbol_id) FROM symbol_relations")?;
    let page_size = one("PRAGMA page_size")?;
    let page_count = one("PRAGMA page_count")?;
    let freelist = one("PRAGMA freelist_count")?;
    // Row-size distribution of the JSON payload, the column the earlier
    // profile blamed for overflow-page reads.
    let pct = |p: f64| -> anyhow::Result<i64> {
        let sql = format!(
            "SELECT LENGTH(CAST(references_json AS BLOB)) FROM symbols \
             ORDER BY 1 LIMIT 1 OFFSET CAST(({p} * (SELECT COUNT(*) FROM symbols)) AS INTEGER)"
        );
        Ok(conn
            .query_row(&sql, [], |r| r.get::<_, Option<i64>>(0))
            .unwrap_or(Some(0))
            .unwrap_or(0))
    };
    let refs_p50 = pct(0.5)?;
    let refs_p90 = pct(0.9)?;
    let refs_p99 = pct(0.99)?;
    let refs_max = one("SELECT MAX(LENGTH(CAST(references_json AS BLOB))) FROM symbols")?;
    let sqlite_version: String = conn.query_row("SELECT sqlite_version()", [], |r| r.get(0))?;
    // The corpus load's plan, as this database's planner chooses it —
    // the report's decomposition of `all_symbols` hangs off this.
    let plan: Vec<String> = {
        let mut stmt = conn.prepare(
            "EXPLAIN QUERY PLAN SELECT file, qualified_name, name, kind, language, start_line,
                    end_line, content_hash, signature, imports_json, exported, parent, references_json
             FROM symbols ORDER BY file, start_line",
        )?;
        let rows = stmt.query_map([], |r| r.get::<_, String>(3))?;
        rows.collect::<Result<_, _>>()?
    };
    let symbols_pages = table_pages(&conn, "symbols");
    let v = serde_json::json!({
        "stage": "payload",
        "symbols": symbols,
        "files": files,
        "embeddings": embeddings,
        "meta": meta,
        "references_json_bytes": refs_bytes,
        "references_items": refs_items,
        "references_json_bytes_p50": refs_p50,
        "references_json_bytes_p90": refs_p90,
        "references_json_bytes_p99": refs_p99,
        "references_json_bytes_max": refs_max,
        "imports_json_bytes": imports_bytes,
        "imports_items": imports_items,
        "imports_json_distinct": distinct_imports,
        "signature_bytes": sig_bytes,
        "identity_text_bytes": text_bytes,
        "symbol_relations_rows": relations_rows,
        "symbol_relations_target_bytes": relations_bytes,
        "symbol_relations_symbols": relations_symbols,
        "page_size": page_size,
        "page_count": page_count,
        "freelist_count": freelist,
        "sqlite_version": sqlite_version,
        "all_symbols_plan": plan,
        "symbols_table_pages": symbols_pages,
    });
    if json {
        println!("{v}");
    } else {
        println!("{}", serde_json::to_string_pretty(&v)?);
    }
    Ok(())
}

/// Pages of one table's b-tree via `dbstat`, when the bundled SQLite has
/// the virtual table compiled in; `None` otherwise (diagnostic only).
fn table_pages(conn: &rusqlite::Connection, table: &str) -> Option<i64> {
    conn.query_row(
        "SELECT COUNT(*) FROM dbstat WHERE name = ?1",
        [table],
        |r| r.get::<_, i64>(0),
    )
    .ok()
}

/// The corpus order `all_symbols` returns, reduced to comparable data:
/// an FNV-1a digest of the id sequence (so two builds can be diffed with
/// one line each), the number of adjacent rows tied on
/// `(file, start_line)` — the rows whose relative order the SQL
/// `ORDER BY` leaves to the planner — and a check that the sequence is
/// sorted by `(file, start_line)` at all.
fn order_digest(db: &std::path::Path, json: bool) -> anyhow::Result<()> {
    let store = SqliteStore::open_read_only(db)?;
    let all = store.all_symbols()?;
    let mut h: u64 = 0xcbf2_9ce4_8422_2325;
    let mut ties = 0usize;
    let mut sorted = true;
    for (i, s) in all.iter().enumerate() {
        for b in s.id().to_le_bytes() {
            h ^= b as u64;
            h = h.wrapping_mul(0x0000_0100_0000_01b3);
        }
        if i > 0 {
            let prev = &all[i - 1];
            match prev
                .file
                .cmp(&s.file)
                .then(prev.start_line.cmp(&s.start_line))
            {
                std::cmp::Ordering::Equal => ties += 1,
                std::cmp::Ordering::Greater => sorted = false,
                std::cmp::Ordering::Less => {}
            }
        }
    }
    let v = serde_json::json!({
        "stage": "order_digest",
        "n": all.len(),
        "id_sequence_fnv1a": format!("{h:016x}"),
        "adjacent_ties": ties,
        "sorted_by_file_start_line": sorted,
    });
    println!(
        "{}",
        if json {
            v.to_string()
        } else {
            serde_json::to_string_pretty(&v)?
        }
    );
    Ok(())
}

/// Issue #14 diagnostic only. All seed selection happens before the timer.
fn structural_probe_stage(
    root: &std::path::Path,
    query: &str,
    stage: &str,
    repeat: usize,
    json: bool,
) -> anyhow::Result<()> {
    use probe_graph::{Access, ProbeGraph};
    let db = root.join(".oxide/index.db");
    if stage == "probe_diag_build" {
        let before = std::fs::metadata(&db)?.len();
        let conn = rusqlite::Connection::open(&db)?;
        let s = begin();
        let ddl = probe_graph::build_diag(&conn)?;
        let r = sample(s, ddl.len(), "diagnostic indexes and test references");
        drop(conn);
        let v = serde_json::json!({"stage":stage,"ms":r.ms,"allocs":r.allocs,"bytes":r.bytes,"db_bytes_before":before,"db_bytes_after":std::fs::metadata(&db)?.len(),"wal_bytes_final":std::fs::metadata(db.with_extension("db-wal")).map(|m|m.len()).unwrap_or(0)});
        println!(
            "{}",
            if json {
                v.to_string()
            } else {
                serde_json::to_string_pretty(&v)?
            }
        );
        return Ok(());
    }
    let store = SqliteStore::open_read_only(&db)?;
    let conn =
        rusqlite::Connection::open_with_flags(&db, rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY)?;
    let access = if stage.ends_with("_diag") {
        Access::Diag
    } else {
        Access::Scan
    };
    let probe = ProbeGraph::new(&conn, access);
    if stage.starts_with("probe_oracle") {
        let snap = SymbolSnapshot::load(&store)?;
        let graph = RelationGraph::build(&snap.symbols);
        let mut mismatches = Vec::new();
        let mut checked = 0usize;
        for chunk in snap.symbols.chunks(32) {
            let refs: Vec<&Symbol> = chunk.iter().collect();
            let (neighbors, tests) = probe.neighbors_and_tests(&refs, true)?;
            for ((seed, got), got_tests) in chunk.iter().zip(neighbors).zip(tests) {
                checked += 1;
                let want: Vec<_> = graph
                    .neighbors(seed)
                    .into_iter()
                    .map(|(r, s)| (r, s.id()))
                    .collect();
                let actual: Vec<_> = got.into_iter().map(|(r, id)| (r.to_string(), id)).collect();
                if actual != want {
                    mismatches.push(format!("neighbors:{}#{}", seed.file, seed.qualified_name));
                }
                let want_tests: Vec<_> = graph
                    .related_tests(seed)
                    .into_iter()
                    .map(Symbol::id)
                    .collect();
                if got_tests != want_tests {
                    mismatches.push(format!("tests:{}#{}", seed.file, seed.qualified_name));
                }
                let want_imports: Vec<Vec<u64>> = seed
                    .imports
                    .iter()
                    .map(|m| {
                        graph
                            .resolve_import(&seed.file, m)
                            .into_iter()
                            .map(Symbol::id)
                            .collect()
                    })
                    .collect();
                if probe.resolve_import_ids(seed)? != want_imports {
                    mismatches.push(format!("imports:{}#{}", seed.file, seed.qualified_name));
                }
                let scope = vec![seed.file.clone()];
                let want_callers: Vec<u64> = graph
                    .callers_of(&seed.name)
                    .into_iter()
                    .filter(|s| s.file == seed.file)
                    .map(Symbol::id)
                    .collect();
                if probe.scoped_callers(&seed.name, &scope)? != want_callers {
                    mismatches.push(format!("callers:{}#{}", seed.file, seed.qualified_name));
                }
            }
        }
        for chunk in snap.symbols.chunks(400) {
            let ids: Vec<u64> = chunk.iter().map(Symbol::id).collect();
            let hydrated = probe.hydrate(&store, &ids, true)?;
            for seed in chunk {
                if hydrated
                    .get(&seed.id())
                    .map(serde_json::to_value)
                    .transpose()?
                    != Some(serde_json::to_value(seed)?)
                {
                    mismatches.push(format!("hydrate:{}#{}", seed.file, seed.qualified_name));
                }
            }
        }
        let v = serde_json::json!({"stage":stage,"checked":checked,"mismatch_count":mismatches.len(),"mismatches":mismatches.iter().take(20).collect::<Vec<_>>(),"statements":probe.stats().statements,"rows":probe.stats().rows,"read_bytes":probe.stats().bytes});
        println!(
            "{}",
            if json {
                v.to_string()
            } else {
                serde_json::to_string_pretty(&v)?
            }
        );
        return Ok(());
    }
    let embedder = open_embedder(None)?;
    if stage == "context_probe_check" {
        let got = context_probe(root, &store, embedder.as_ref(), &probe, &conn, query)?;
        let want = build_context_with(
            root,
            &RetrievalEngine::new(&store, embedder.as_ref()),
            query,
            &ContextOptions::default(),
        )?;
        if serde_json::to_vec(&got)? != serde_json::to_vec(&want)? {
            std::fs::write(
                "/tmp/oxide-issue14-context-probe.json",
                serde_json::to_vec_pretty(&got)?,
            )?;
            std::fs::write(
                "/tmp/oxide-issue14-context-oracle.json",
                serde_json::to_vec_pretty(&want)?,
            )?;
            anyhow::bail!("context probe differs from production for query {query:?}");
        }
        println!(
            "{}",
            serde_json::json!({"stage":stage,"equal":true,"items":got.items.len()})
        );
        return Ok(());
    }
    if stage == "context_probe" {
        for rep in 0..repeat {
            let s = begin();
            let pack = context_probe(root, &store, embedder.as_ref(), &probe, &conn, query)?;
            let r = sample(
                s,
                pack.items.len(),
                format!(
                    "probe_statements={} probe_rows={}",
                    probe.stats().statements,
                    probe.stats().rows
                ),
            );
            emit(stage, rep, &r, json);
        }
        return Ok(());
    }
    if stage == "search_expand_probe_check" {
        let got = search_expand_probe(&store, embedder.as_ref(), &probe, query)?;
        let want = RetrievalEngine::new(&store, embedder.as_ref()).search(
            query,
            &SearchOptions {
                limit: 10,
                mode: SearchMode::Hybrid,
                expand: true,
                retrieval_mode: RetrievalMode::Balanced,
            },
        )?;
        anyhow::ensure!(
            serde_json::to_vec(&got)? == serde_json::to_vec(&want)?,
            "search probe differs from production for query {query:?}"
        );
        println!(
            "{}",
            serde_json::json!({"stage":stage,"equal":true,"hits":got.len()})
        );
        return Ok(());
    }
    if stage == "search_expand_probe" {
        for rep in 0..repeat {
            let s = begin();
            let hits = search_expand_probe(&store, embedder.as_ref(), &probe, query)?;
            let r = sample(
                s,
                hits.len(),
                format!(
                    "probe_statements={} probe_rows={}",
                    probe.stats().statements,
                    probe.stats().rows
                ),
            );
            emit(stage, rep, &r, json);
        }
        return Ok(());
    }
    let engine = RetrievalEngine::new(&store, embedder.as_ref());
    let seeds: Vec<Symbol> = if stage.contains("search") {
        let direct = engine.search(
            query,
            &SearchOptions {
                limit: 400,
                mode: SearchMode::Hybrid,
                expand: false,
                retrieval_mode: RetrievalMode::Balanced,
            },
        )?;
        let lex = oxide::lexical::prepare_from_store(&store, store.symbol_count()?, query)?;
        let (scores, _) = oxide::lexical::score(&lex, 1.5, 0.75);
        let max_lex = scores.values().map(|s| s.0).fold(0.0f32, f32::max);
        direct
            .into_iter()
            .filter(|h| h.reasons.iter().any(|r| r.starts_with("lexical")))
            .filter(|h| {
                scores
                    .get(&h.symbol.id())
                    .is_some_and(|(score, _, _)| *score >= max_lex * 0.55)
                    && max_lex > 0.0
            })
            .take(3)
            .map(|h| h.symbol)
            .collect()
    } else {
        engine
            .search(
                query,
                &SearchOptions {
                    limit: 16,
                    mode: SearchMode::Hybrid,
                    expand: false,
                    retrieval_mode: RetrievalMode::Balanced,
                },
            )?
            .into_iter()
            .take(5)
            .map(|h| h.symbol)
            .collect()
    };
    let seed_refs: Vec<&Symbol> = seeds.iter().collect();
    for rep in 0..repeat {
        let s = begin();
        let n = if stage.starts_with("struct_base") {
            let snap = if stage.contains("search") {
                SymbolSnapshot::from_symbols(store.all_symbols()?)
            } else {
                SymbolSnapshot::load(&store)?
            };
            let index = RelationIndex::build(&snap.symbols);
            let graph = RelationGraph::with_index(&snap.symbols, &index);
            seed_refs
                .iter()
                .map(|seed| graph.neighbors(seed).len())
                .sum::<usize>()
        } else {
            probe
                .neighbors_ids(&seed_refs)?
                .iter()
                .map(Vec::len)
                .sum::<usize>()
        };
        let stats = probe.stats();
        let r = sample(
            s,
            n,
            format!(
                "seeds={} statements={} rows={} read_bytes={}",
                seeds.len(),
                stats.statements,
                stats.rows,
                stats.bytes
            ),
        );
        emit(stage, rep, &r, json);
    }
    Ok(())
}

fn search_expand_probe(
    store: &SqliteStore,
    embedder: &dyn oxide::embeddings::EmbeddingProvider,
    probe: &probe_graph::ProbeGraph<'_>,
    query: &str,
) -> anyhow::Result<Vec<oxide::retrieval::SearchHit>> {
    use oxide::retrieval::SearchHit;
    use std::collections::{HashMap, HashSet};
    let engine = RetrievalEngine::new(store, embedder);
    let mut direct = engine.search(
        query,
        &SearchOptions {
            limit: 400,
            mode: SearchMode::Hybrid,
            expand: false,
            retrieval_mode: RetrievalMode::Balanced,
        },
    )?;
    let lex = oxide::lexical::prepare_from_store(store, store.symbol_count()?, query)?;
    let (scores, _) = oxide::lexical::score(&lex, 1.5, 0.75);
    let max_lex = scores.values().map(|s| s.0).fold(0.0f32, f32::max);
    let strong: Vec<&Symbol> = direct
        .iter()
        .filter(|h| h.reasons.iter().any(|r| r.starts_with("lexical")))
        .filter(|h| {
            scores
                .get(&h.symbol.id())
                .is_some_and(|(score, _, _)| *score >= max_lex * 0.55)
                && max_lex > 0.0
        })
        .map(|h| &h.symbol)
        .take(3)
        .collect();
    let neighbors = probe.neighbors_ids(&strong)?;
    let direct_ids: HashSet<u64> = direct.iter().map(|h| h.symbol.id()).collect();
    let mut expansions: HashMap<u64, (f32, Vec<String>)> = HashMap::new();
    for (seed, edges) in strong.iter().zip(neighbors) {
        for (rel, id) in edges {
            if id == seed.id() {
                continue;
            }
            let e = expansions.entry(id).or_insert((0.0, Vec::new()));
            e.0 += seed_score(seed.id(), &direct) * 0.5;
            let reason = format!("{}←{}", rel, seed.qualified_name);
            if !e.1.contains(&reason) {
                e.1.push(reason);
            }
        }
    }
    let mut extra = Vec::new();
    for (id, (score, reasons)) in expansions {
        if direct_ids.contains(&id) {
            if let Some(h) = direct.iter_mut().find(|h| h.symbol.id() == id) {
                h.reasons.extend(reasons);
            }
        } else {
            extra.push((id, score, reasons));
        }
    }
    extra.sort_by(|a, b| {
        b.1.partial_cmp(&a.1)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then(a.0.cmp(&b.0))
    });
    let remaining = 10usize.saturating_sub(direct.len());
    let ids: Vec<u64> = extra.iter().take(remaining).map(|x| x.0).collect();
    let mut hydrated = probe.hydrate(store, &ids, false)?;
    for (id, score, reasons) in extra.into_iter().take(remaining) {
        if let Some(symbol) = hydrated.remove(&id) {
            direct.push(SearchHit {
                symbol,
                score,
                reasons,
                snippet: String::new(),
            });
        }
    }
    direct.truncate(10);
    Ok(direct)
}

fn seed_score(id: u64, direct: &[oxide::retrieval::SearchHit]) -> f32 {
    direct
        .iter()
        .find(|h| h.symbol.id() == id)
        .map_or(0.001, |h| h.score)
}

fn probe_plans(db: &std::path::Path, json: bool) -> anyhow::Result<()> {
    let conn =
        rusqlite::Connection::open_with_flags(db, rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY)?;
    let version: String = conn.query_row("SELECT sqlite_version()", [], |r| r.get(0))?;
    let mut plans = serde_json::Map::new();
    let statements = [
        ("global_scan", "SELECT id, file, start_line, qualified_name, name, kind, parent, references_json FROM symbols"),
        ("qualified", "SELECT id, file, start_line, name, kind, qualified_name FROM symbols WHERE qualified_name IN (SELECT value FROM json_each('[\"foo\"]'))"),
        ("children", "SELECT id, file, start_line, name, kind, parent FROM symbols WHERE parent IN (SELECT value FROM json_each('[\"foo\"]'))"),
        ("defs", "SELECT id, file, start_line, name, kind FROM symbols WHERE name IN (SELECT value FROM json_each('[\"foo\"]')) AND parent IS NULL"),
        ("by_file", "SELECT id, file, start_line, name, kind, file FROM symbols WHERE file IN (SELECT value FROM json_each('[\"foo.py\"]'))"),
        ("test_name_scan", "SELECT id, name FROM symbols"),
        ("callers", "SELECT s.id, s.file, s.start_line, r.rowid FROM symbols s JOIN symbol_relations r ON r.symbol_id = s.id WHERE s.file IN (SELECT value FROM json_each('[\"foo.py\"]')) AND r.kind = 'calls' AND r.target = 'foo'"),
        ("hydrate", "SELECT file FROM symbols WHERE id IN (SELECT value FROM json_each('[1,2]'))"),
        ("vector", "SELECT symbol_id, dim, vec FROM embeddings"),
    ];
    for (name, sql) in statements {
        let mut stmt = conn.prepare(&format!("EXPLAIN QUERY PLAN {sql}"))?;
        let rows: Vec<String> = stmt
            .query_map([], |r| r.get(3))?
            .collect::<Result<_, _>>()?;
        plans.insert(name.into(), serde_json::json!(rows));
    }
    let has_diag: bool = conn.query_row(
        "SELECT COUNT(*) > 0 FROM sqlite_master WHERE name = 'diag_test_refs'",
        [],
        |r| r.get(0),
    )?;
    if has_diag {
        let mut stmt = conn.prepare("EXPLAIN QUERY PLAN SELECT ref, symbol_id FROM diag_test_refs WHERE ref IN (SELECT value FROM json_each('[\"foo\"]'))")?;
        let rows: Vec<String> = stmt
            .query_map([], |r| r.get(3))?
            .collect::<Result<_, _>>()?;
        plans.insert("test_refs".into(), serde_json::json!(rows));
    }
    let v = serde_json::json!({"stage":"probe_plans","sqlite_version":version,"has_diag":has_diag,"plans":plans});
    println!(
        "{}",
        if json {
            v.to_string()
        } else {
            serde_json::to_string_pretty(&v)?
        }
    );
    Ok(())
}

fn context_probe(
    root: &std::path::Path,
    store: &SqliteStore,
    embedder: &dyn oxide::embeddings::EmbeddingProvider,
    probe: &probe_graph::ProbeGraph<'_>,
    conn: &rusqlite::Connection,
    query: &str,
) -> anyhow::Result<oxide::context::ContextPack> {
    use std::collections::HashSet;
    let seeds = RetrievalEngine::new(store, embedder).search(
        query,
        &SearchOptions {
            limit: 16,
            mode: SearchMode::Hybrid,
            expand: false,
            retrieval_mode: RetrievalMode::Balanced,
        },
    )?;
    let focus: Vec<&Symbol> = seeds.iter().take(5).map(|h| &h.symbol).collect();
    let (neighbors, tests) = probe.neighbors_and_tests(&focus, true)?;
    let mut ids: HashSet<u64> = seeds.iter().map(|h| h.symbol.id()).collect();
    ids.extend(neighbors.into_iter().flatten().map(|(_, id)| id));
    ids.extend(tests.into_iter().flatten());
    for s in &focus {
        ids.extend(probe.resolve_import_ids(s)?.into_iter().flatten());
    }
    // Keep every candidate of the graph classes that precede test edges,
    // including members beyond the 24-edge truncation boundary.
    let refs: Vec<&str> = focus
        .iter()
        .flat_map(|s| s.references.iter().map(String::as_str))
        .collect();
    let parents: Vec<&str> = focus.iter().filter_map(|s| s.parent.as_deref()).collect();
    let child_keys: Vec<&str> = parents
        .iter()
        .copied()
        .chain(focus.iter().map(|s| s.qualified_name.as_str()))
        .collect();
    for (col, values) in [
        ("name", refs),
        ("qualified_name", parents),
        ("parent", child_keys),
    ] {
        if values.is_empty() {
            continue;
        }
        let sql =
            format!("SELECT id FROM symbols WHERE {col} IN (SELECT value FROM json_each(?1))");
        let json = serde_json::to_string(&values)?;
        let mut stmt = conn.prepare(&sql)?;
        let rows = stmt.query_map([json], |r| r.get::<_, i64>(0))?;
        for row in rows {
            ids.insert(row? as u64);
        }
    }
    // RelationGraph's file-existence check must see every indexed file,
    // including files outside the bounded result set.
    let mut stmt = conn.prepare("SELECT id FROM symbols GROUP BY file")?;
    for row in stmt.query_map([], |r| r.get::<_, i64>(0))? {
        ids.insert(row? as u64);
    }
    let mut scope = Vec::new();
    for h in &seeds {
        if scope.len() >= 3 {
            break;
        }
        if !scope.contains(&h.symbol.file) {
            scope.push(h.symbol.file.clone());
        }
    }
    for h in seeds.iter().take(2) {
        ids.extend(probe.scoped_callers(&h.symbol.name, &scope)?);
    }
    let ids: Vec<u64> = ids.into_iter().collect();
    let mut symbols = Vec::with_capacity(ids.len());
    for chunk in ids.chunks(400) {
        symbols.extend(probe.hydrate(store, chunk, true)?.into_values());
    }
    symbols.sort_by(|a, b| {
        a.file
            .cmp(&b.file)
            .then(a.start_line.cmp(&b.start_line))
            .then((a.id() as i64).cmp(&(b.id() as i64)))
    });
    let mut snapshot = SymbolSnapshot::from_symbols(symbols);
    snapshot.with_relations = true;
    let index = RelationIndex::build(&snapshot.symbols);
    let engine = RetrievalEngine::with_snapshot_and_index(store, embedder, &snapshot, &index);
    let mut pack = build_context_with(root, &engine, query, &ContextOptions::default())?;
    // A one-shot CLI engine hydrates its direct seeds before loading the
    // relations snapshot, so those seed rows have empty calls/bases. The
    // supplied-snapshot path (MCP) hydrates them from that snapshot instead.
    // Preserve the one-shot output shape in this diagnostic comparison.
    let seed_ids: HashSet<u64> = seeds.iter().map(|h| h.symbol.id()).collect();
    for item in &mut pack.items {
        if seed_ids.contains(&item.symbol.id()) {
            item.symbol.calls.clear();
            item.symbol.bases.clear();
        }
    }
    Ok(pack)
}
