//! Per-stage wall-clock **and allocation** breakdown of one request against
//! an existing index, finer-grained than `request_profile.rs`: the corpus
//! load is split into raw row iteration / decode / relations / merge, and
//! every evidence source (literal, lexical, semantic, structural, git,
//! blast radius) plus fusion, hydration and snippet rendering is timed on
//! its own. Allocation counts come from a counting global allocator, so a
//! stage's number is what that stage allocated, not what it retained.
//!
//! Usage: retrieval_profile <repo_root> "<query>" [--git-range <A..B>]
//!
//! Run twice in the same process (see `repeat` below) to separate a cold
//! first request (page cache, prepared statements, embedder load) from a
//! warm one — the difference is what a long-lived `oxide mcp` amortizes.
use oxide::context::{build_context_with, ContextOptions};
use oxide::embeddings::open_embedder;
use oxide::relations::RelationGraph;
use oxide::retrieval::{RetrievalEngine, RetrievalMode, SearchMode, SearchOptions, SymbolSnapshot};
use oxide::storage::{IndexBackend, SqliteStore};
use oxide::symbols::Symbol;
use std::alloc::{GlobalAlloc, Layout, System};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::time::Instant;

struct Counting;
static ALLOCS: AtomicUsize = AtomicUsize::new(0);
static BYTES: AtomicUsize = AtomicUsize::new(0);

unsafe impl GlobalAlloc for Counting {
    unsafe fn alloc(&self, layout: Layout) -> *mut u8 {
        ALLOCS.fetch_add(1, Ordering::Relaxed);
        BYTES.fetch_add(layout.size(), Ordering::Relaxed);
        System.alloc(layout)
    }
    unsafe fn dealloc(&self, ptr: *mut u8, layout: Layout) {
        System.dealloc(ptr, layout)
    }
    unsafe fn realloc(&self, ptr: *mut u8, layout: Layout, new_size: usize) -> *mut u8 {
        ALLOCS.fetch_add(1, Ordering::Relaxed);
        BYTES.fetch_add(new_size, Ordering::Relaxed);
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
