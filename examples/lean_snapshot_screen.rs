//! Screen for the compact-reference follow-up to issue #14
//! (docs/retrieval-profile/corpus-load-baseline/structural-probe/README.md,
//! "One next action"). Diagnostic only: never routed into production, and
//! the side table in `--variant lean_side` is built on a disposable copy.
//!
//! Question: how much of the one-shot corpus load (`SqliteStore::all_symbols`)
//! is the per-row `imports_json` / `references_json` payload that
//! `RelationGraph` never reads for a non-seed symbol? `neighbors(seed)` reads
//! `imports`/`references` of the *seed* only; the one non-seed reader is
//! `related_tests`, which reads `references` of test symbols only.
//!
//! Variants (each one statement over `symbols`, same `ORDER BY` as
//! production, same decode for every column it keeps):
//!   full       production `all_symbols()` through `SqliteStore`
//!   lean_noimp no `imports_json` column; `references` decoded for every row
//!   lean_test  no `imports_json`; `references` decoded for test rows only
//!              (no schema change: bytes still read, decode skipped)
//!   lean_side  neither column; test references read from a narrow side
//!              table (`screen_test_refs`, `--build-side` on a copy only)
//!
//! `--oracle` checks, for every symbol of the corpus as a (fully hydrated)
//! seed, that `RelationGraph::neighbors` over the `lean_test` corpus yields
//! the same `(relation, id)` sequence as over the full corpus.
//!
//! Usage: lean_snapshot_screen <index.db> --variant <v> [--reps N] --json
//!        lean_snapshot_screen <index.db> --oracle
//!        lean_snapshot_screen <index.db> --build-side      (copy only!)

use std::alloc::{GlobalAlloc, Layout, System};
use std::path::Path;
use std::sync::atomic::{AtomicU64, Ordering::Relaxed};
use std::time::Instant;

use oxide::relations::RelationGraph;
use oxide::storage::{IndexBackend, SqliteStore};
use oxide::symbols::{Language, Symbol, SymbolKind};
use rusqlite::{Connection, OpenFlags};

struct Counting;
static ALLOCS: AtomicU64 = AtomicU64::new(0);
static BYTES: AtomicU64 = AtomicU64::new(0);
unsafe impl GlobalAlloc for Counting {
    unsafe fn alloc(&self, l: Layout) -> *mut u8 {
        ALLOCS.fetch_add(1, Relaxed);
        BYTES.fetch_add(l.size() as u64, Relaxed);
        unsafe { System.alloc(l) }
    }
    unsafe fn dealloc(&self, p: *mut u8, l: Layout) {
        unsafe { System.dealloc(p, l) }
    }
    unsafe fn realloc(&self, p: *mut u8, l: Layout, n: usize) -> *mut u8 {
        ALLOCS.fetch_add(1, Relaxed);
        BYTES.fetch_add(n as u64, Relaxed);
        unsafe { System.realloc(p, l, n) }
    }
}
#[global_allocator]
static GLOBAL: Counting = Counting;

/// `relations.rs::is_test_symbol`, same mapping (ASCII byte-wise lowercase,
/// `to_lowercase` otherwise; last clause Function|Method only).
fn is_test(file: &str, name: &str, kind: SymbolKind) -> bool {
    let lower = |s: &str| {
        if s.is_ascii() {
            s.to_ascii_lowercase()
        } else {
            s.to_lowercase()
        }
    };
    let (f, n) = (lower(file), lower(name));
    f.starts_with("test_")
        || f.contains("_test.")
        || f.contains(".test.")
        || f.contains(".spec.")
        || f.contains("/tests/")
        || f.contains("\\tests\\")
        || n.starts_with("test_")
        || n.ends_with("_test")
        || n.ends_with("test") && matches!(kind, SymbolKind::Function | SymbolKind::Method)
}

#[derive(Clone, Copy, PartialEq)]
enum Refs {
    All,
    TestOnly,
    None,
}

/// `storage.rs::row_to_symbol_without_imports` over a projection without
/// `imports_json` (and, for `Refs::None`, without `references_json`).
/// `imports = true` (with `Refs::All`) is the `full_raw` control: the full
/// production projection and `all_symbols`' per-file parse-once-then-clone
/// `imports`, through this same decoder and connection.
fn lean_load(conn: &Connection, refs: Refs, imports: bool) -> rusqlite::Result<Vec<Symbol>> {
    let cols = "file, qualified_name, name, kind, language, start_line, end_line,
                content_hash, signature, exported, parent";
    let sql = match (refs, imports) {
        (Refs::None, _) => format!("SELECT {cols} FROM symbols ORDER BY file, start_line"),
        (_, false) => {
            format!("SELECT {cols}, references_json FROM symbols ORDER BY file, start_line")
        }
        (_, true) => format!(
            "SELECT {cols}, references_json, imports_json FROM symbols ORDER BY file, start_line"
        ),
    };
    let mut stmt = conn.prepare(&sql)?;
    let mut rows = stmt.query([])?;
    let mut out = Vec::new();
    let mut last_imports: (String, Vec<String>) = (String::new(), Vec::new());
    while let Some(r) = rows.next()? {
        let file_imports = if imports {
            let raw = r.get_ref(12)?.as_str()?;
            if raw != last_imports.0 {
                last_imports = (
                    raw.to_string(),
                    serde_json::from_str(raw).unwrap_or_default(),
                );
            }
            last_imports.1.clone()
        } else {
            Vec::new()
        };
        let file: String = r.get(0)?;
        let name: String = r.get(2)?;
        let kind: SymbolKind = r
            .get_ref(3)?
            .as_str()?
            .parse()
            .unwrap_or(SymbolKind::Function);
        let decode = match refs {
            Refs::All => true,
            Refs::TestOnly => is_test(&file, &name, kind),
            Refs::None => false,
        };
        let references = if decode {
            serde_json::from_str(r.get_ref(11)?.as_str()?).unwrap_or_default()
        } else {
            Vec::new()
        };
        out.push(Symbol {
            file,
            qualified_name: r.get(1)?,
            name,
            kind,
            language: r
                .get_ref(4)?
                .as_str()?
                .parse()
                .unwrap_or(Language::TypeScript),
            start_line: r.get(5)?,
            end_line: r.get(6)?,
            content_hash: r.get::<_, i64>(7)? as u64,
            signature: r.get(8)?,
            imports: file_imports,
            exported: r.get::<_, i64>(9)? != 0,
            parent: r.get(10)?,
            references,
            calls: Vec::new(),
            bases: Vec::new(),
            completeness: Default::default(),
        });
    }
    Ok(out)
}

/// Completion-data variants for the production design: the full projection,
/// `imports` parsed once per distinct file list into a per-file map (never
/// cloned into rows), `references` decoded for test rows only. Non-test
/// rows either drop their references (`lean_map`: completed later from the
/// DB) or keep the raw JSON text (`lean_raw`: completed later in memory).
type Completion = (
    rustc_hash::FxHashMap<String, Vec<String>>,
    Vec<Option<Box<str>>>,
);
fn lean_keep(conn: &Connection, keep_raw: bool) -> rusqlite::Result<(Vec<Symbol>, Completion)> {
    let mut stmt = conn.prepare(
        "SELECT file, qualified_name, name, kind, language, start_line, end_line,
                content_hash, signature, exported, parent, references_json, imports_json
         FROM symbols ORDER BY file, start_line",
    )?;
    let mut rows = stmt.query([])?;
    let mut out = Vec::new();
    let mut file_imports: rustc_hash::FxHashMap<String, Vec<String>> = Default::default();
    let mut raw_refs = Vec::new();
    let mut last_file = String::new();
    while let Some(r) = rows.next()? {
        let file: String = r.get(0)?;
        if file != last_file {
            file_imports.insert(
                file.clone(),
                serde_json::from_str(r.get_ref(12)?.as_str()?).unwrap_or_default(),
            );
            last_file.clone_from(&file);
        }
        let name: String = r.get(2)?;
        let kind: SymbolKind = r
            .get_ref(3)?
            .as_str()?
            .parse()
            .unwrap_or(SymbolKind::Function);
        let refs_json = r.get_ref(11)?.as_str()?;
        let (references, raw) = if is_test(&file, &name, kind) {
            (serde_json::from_str(refs_json).unwrap_or_default(), None)
        } else if keep_raw {
            (Vec::new(), Some(Box::<str>::from(refs_json)))
        } else {
            (Vec::new(), None)
        };
        raw_refs.push(raw);
        out.push(Symbol {
            file,
            qualified_name: r.get(1)?,
            name,
            kind,
            language: r
                .get_ref(4)?
                .as_str()?
                .parse()
                .unwrap_or(Language::TypeScript),
            start_line: r.get(5)?,
            end_line: r.get(6)?,
            content_hash: r.get::<_, i64>(7)? as u64,
            signature: r.get(8)?,
            imports: Vec::new(),
            exported: r.get::<_, i64>(9)? != 0,
            parent: r.get(10)?,
            references,
            calls: Vec::new(),
            bases: Vec::new(),
            completeness: Default::default(),
        });
    }
    Ok((out, (file_imports, raw_refs)))
}

/// Narrow side table: one row per (test symbol, reference). Built on a
/// disposable copy only.
fn build_side(conn: &Connection) -> rusqlite::Result<(usize, f64)> {
    let t = Instant::now();
    let tx = conn.unchecked_transaction()?;
    tx.execute_batch(
        "DROP TABLE IF EXISTS screen_test_refs;
         CREATE TABLE screen_test_refs(symbol_id INTEGER NOT NULL, ref TEXT NOT NULL,
             PRIMARY KEY(symbol_id, ref)) WITHOUT ROWID;",
    )?;
    let mut n = 0;
    {
        let mut read = tx.prepare("SELECT id, file, name, kind, references_json FROM symbols")?;
        let mut ins = tx.prepare("INSERT OR IGNORE INTO screen_test_refs VALUES(?1, ?2)")?;
        let mut pending: Vec<(i64, String)> = Vec::new();
        let mut rows = read.query([])?;
        while let Some(r) = rows.next()? {
            let kind = r
                .get_ref(3)?
                .as_str()?
                .parse()
                .unwrap_or(SymbolKind::Function);
            if !is_test(r.get_ref(1)?.as_str()?, r.get_ref(2)?.as_str()?, kind) {
                continue;
            }
            let id: i64 = r.get(0)?;
            let refs: Vec<String> =
                serde_json::from_str(r.get_ref(4)?.as_str()?).unwrap_or_default();
            pending.extend(refs.into_iter().map(|x| (id, x)));
        }
        drop(rows);
        for (id, x) in pending {
            n += ins.execute(rusqlite::params![id, x])?;
        }
    }
    tx.commit()?;
    Ok((n, t.elapsed().as_secs_f64() * 1e3))
}

/// `lean_side`: the refs-free load plus the side table merged back onto the
/// test rows (one scan of the side table, keyed by symbol id).
fn side_load(conn: &Connection) -> rusqlite::Result<Vec<Symbol>> {
    let mut syms = lean_load(conn, Refs::None, false)?;
    let mut by_id: rustc_hash::FxHashMap<u64, usize> = Default::default();
    for (i, s) in syms.iter().enumerate() {
        by_id.insert(s.id(), i);
    }
    let mut stmt = conn.prepare("SELECT symbol_id, ref FROM screen_test_refs")?;
    let mut rows = stmt.query([])?;
    while let Some(r) = rows.next()? {
        if let Some(&i) = by_id.get(&(r.get::<_, i64>(0)? as u64)) {
            syms[i].references.push(r.get(1)?);
        }
    }
    Ok(syms)
}

fn median(v: &mut [f64]) -> f64 {
    v.sort_by(|a, b| a.partial_cmp(b).unwrap());
    v[v.len() / 2]
}

fn main() -> anyhow::Result<()> {
    let args: Vec<String> = std::env::args().collect();
    let db = Path::new(&args[1]);
    let flag = |f: &str| args.iter().position(|a| a == f);
    // Same connection setup as `SqliteStore::open_read_only`: `query_only`
    // and one deferred read transaction held for the connection's life.
    let conn = || -> rusqlite::Result<Connection> {
        let c = Connection::open_with_flags(db, OpenFlags::SQLITE_OPEN_READ_ONLY)?;
        c.execute_batch("PRAGMA query_only = ON; BEGIN DEFERRED;")?;
        Ok(c)
    };

    if flag("--build-side").is_some() {
        let c = Connection::open(db)?;
        let before = std::fs::metadata(db)?.len();
        let (rows, ms) = build_side(&c)?;
        drop(c);
        let after = std::fs::metadata(db)?.len();
        println!(
            "{}",
            serde_json::json!({"side_rows": rows, "build_ms": ms, "db_bytes_before": before, "db_bytes_after": after})
        );
        return Ok(());
    }

    if flag("--oracle").is_some() {
        let store = SqliteStore::open_read_only(db)?;
        let full = store.all_symbols()?;
        let lean = lean_load(&conn()?, Refs::TestOnly, false)?;
        let ids = |v: &[Symbol]| v.iter().map(Symbol::id).collect::<Vec<_>>();
        anyhow::ensure!(ids(&full) == ids(&lean), "corpus order differs");
        // The `full_raw` control must decode exactly what production does.
        let raw = lean_load(&conn()?, Refs::All, true)?;
        let json = |v: &[Symbol]| serde_json::to_string(v).expect("serialize");
        anyhow::ensure!(
            json(&full) == json(&raw),
            "full_raw decode differs from all_symbols"
        );
        let (gf, gl) = (RelationGraph::build(&full), RelationGraph::build(&lean));
        let mut mismatches = 0usize;
        let mut nonempty = 0usize;
        for seed in &full {
            let a: Vec<(String, u64)> = gf
                .neighbors(seed)
                .into_iter()
                .map(|(r, s)| (r, s.id()))
                .collect();
            let b: Vec<(String, u64)> = gl
                .neighbors(seed)
                .into_iter()
                .map(|(r, s)| (r, s.id()))
                .collect();
            nonempty += usize::from(!a.is_empty());
            mismatches += usize::from(a != b);
        }
        println!(
            "{}",
            serde_json::json!({"seeds": full.len(), "nonempty": nonempty, "mismatches": mismatches})
        );
        anyhow::ensure!(mismatches == 0, "{mismatches} neighbor mismatches");
        return Ok(());
    }

    let variant = args[flag("--variant").expect("--variant") + 1].clone();
    let reps: usize = flag("--reps").map_or(3, |i| args[i + 1].parse().unwrap());
    // Opened untimed, as the production store is before its load.
    let store = SqliteStore::open_read_only(db)?;
    let c = conn()?;
    let mut ms = Vec::new();
    let mut allocs = Vec::new();
    let mut bytes = Vec::new();
    let mut n = 0;
    for _ in 0..reps {
        let (a0, b0) = (ALLOCS.load(Relaxed), BYTES.load(Relaxed));
        let t = Instant::now();
        let syms = match variant.as_str() {
            "full" => store.all_symbols()?,
            "full_raw" => lean_load(&c, Refs::All, true)?,
            "lean_noimp" => lean_load(&c, Refs::All, false)?,
            "lean_test" => lean_load(&c, Refs::TestOnly, false)?,
            "lean_side" => side_load(&c)?,
            "lean_map" => lean_keep(&c, false)?.0,
            "lean_raw" => lean_keep(&c, true)?.0,
            v => anyhow::bail!("unknown variant {v}"),
        };
        ms.push(t.elapsed().as_secs_f64() * 1e3);
        allocs.push(ALLOCS.load(Relaxed) - a0);
        bytes.push(BYTES.load(Relaxed) - b0);
        n = syms.len();
        drop(syms);
    }
    let cold = ms[0];
    let mut warm = ms[1..].to_vec();
    println!(
        "{}",
        serde_json::json!({
            "variant": variant, "symbols": n, "cold_ms": cold,
            "warm_ms": if warm.is_empty() { cold } else { median(&mut warm) },
            "allocs": allocs[0], "alloc_bytes": bytes[0],
        })
    );
    Ok(())
}
