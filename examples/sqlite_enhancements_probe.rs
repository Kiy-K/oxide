//! Measurement for the bundled-SQLite enhancements that are *candidates*
//! rather than shipped code: FTS5 trigram literal search (Task 3), the
//! structural-graph query plans (Task 4), and the native tuning knobs
//! (Task 5). Nothing here is production surface — the point is to get
//! numbers before deciding whether any of it earns a place in `storage.rs`.
//!
//! Usage: `cargo run --release --example sqlite_enhancements_probe -- <repo>`
//! (the repo must already have a `.oxide/index.db`).

use oxide::index::{IndexBackend, SqliteStore};
use rusqlite::Connection;
use std::path::{Path, PathBuf};
use std::time::Instant;

fn ms(t: Instant) -> f64 {
    t.elapsed().as_secs_f64() * 1000.0
}

fn file_size(p: &Path) -> u64 {
    std::fs::metadata(p).map(|m| m.len()).unwrap_or(0)
}

fn explain(conn: &Connection, label: &str, sql: &str) {
    println!("\n-- {label}\n   {}", sql.replace('\n', "\n   "));
    let mut stmt = match conn.prepare(&format!("EXPLAIN QUERY PLAN {sql}")) {
        Ok(s) => s,
        Err(e) => {
            println!("   !! {e}");
            return;
        }
    };
    let rows = stmt
        .query_map([], |r| r.get::<_, String>(3))
        .unwrap()
        .collect::<Result<Vec<_>, _>>()
        .unwrap();
    for r in rows {
        println!("   => {r}");
    }
}

fn main() -> anyhow::Result<()> {
    let repo = PathBuf::from(std::env::args().nth(1).expect("usage: <repo-path>"));
    let dbpath = repo.join(".oxide/index.db");
    let store = SqliteStore::open_read_only(&dbpath)?;
    let symbols = store.all_symbols()?;
    let root = store
        .get_meta("root")?
        .map(PathBuf::from)
        .unwrap_or_else(|| repo.clone());
    println!("repo={} symbols={}", repo.display(), symbols.len());
    println!("index.db={} bytes", file_size(&dbpath));
    drop(store);

    // ---------- Task 4: query plans on a COPY of the real index ----------
    // Never on the live one: this creates a candidate index and runs
    // `ANALYZE`, which persists planner statistics into `sqlite_stat1`, and
    // an interruption before the `DROP INDEX` would leave the probe's index
    // installed in a user's repository. A measurement must not be able to
    // change what it measures.
    let workdir = tempfile::tempdir()?;
    let probe_db = workdir.path().join("probe-index.db");
    std::fs::copy(&dbpath, &probe_db)?;
    let conn = Connection::open(&probe_db)?;
    println!("\n=== EXPLAIN QUERY PLAN (as shipped) ===");
    explain(
        &conn,
        "all_symbol_relations: the only read the relation table gets",
        "SELECT symbol_id, kind, target FROM symbol_relations",
    );
    explain(
        &conn,
        "reverse lookup pushed into SQL (candidate: callers_of in SQL)",
        "SELECT symbol_id FROM symbol_relations WHERE kind = 'calls' AND target = 'notify'",
    );
    explain(
        &conn,
        "cascade delete path for one symbol's relations",
        "DELETE FROM symbol_relations WHERE symbol_id = 1",
    );
    explain(
        &conn,
        "persisted lexical query term lookup",
        "SELECT p.symbol_id, p.tf, d.len FROM lexical_postings p
         JOIN lexical_docs d ON d.symbol_id = p.symbol_id WHERE p.term = 'retry'",
    );
    explain(
        &conn,
        "cascade delete path for one symbol's postings",
        "DELETE FROM lexical_postings WHERE symbol_id = 1",
    );

    // Candidate composite/covering index for a SQL-side reverse lookup.
    let t = Instant::now();
    conn.execute_batch(
        "CREATE INDEX IF NOT EXISTS probe_idx_relations_target
         ON symbol_relations(kind, target, symbol_id);",
    )?;
    let build_covering = ms(t);
    println!("\ncovering index build: {build_covering:.1} ms");
    explain(
        &conn,
        "reverse lookup WITH the covering index",
        "SELECT symbol_id FROM symbol_relations WHERE kind = 'calls' AND target = 'notify'",
    );

    // Reverse lookup cost: whole-table load + in-memory index (what
    // RelationGraph does today) vs a single indexed SQL lookup.
    let t = Instant::now();
    let store = SqliteStore::open_read_only(&probe_db)?;
    let all = store.all_symbol_relations()?;
    let load_ms = ms(t);
    let mut targets: Vec<String> = all
        .values()
        .flat_map(|(c, _)| c.iter().cloned())
        .take(200)
        .collect();
    targets.sort();
    targets.dedup();
    let probe: Vec<&String> = targets.iter().take(20).collect();
    drop(store);

    let t = Instant::now();
    let mut hits = 0usize;
    {
        let mut stmt = conn.prepare(
            "SELECT symbol_id FROM symbol_relations WHERE kind = 'calls' AND target = ?1",
        )?;
        for name in &probe {
            hits += stmt
                .query_map([name.as_str()], |r| r.get::<_, i64>(0))?
                .count();
        }
    }
    let sql_lookup_ms = ms(t);
    println!(
        "reverse lookup: all_symbol_relations load {load_ms:.1} ms (whole table, {} symbols) \
         vs {} indexed SQL lookups {sql_lookup_ms:.2} ms ({hits} rows)",
        all.len(),
        probe.len()
    );

    // ---------- Task 3: FTS5 trigram literal substring ----------
    println!("\n=== FTS5 trigram ===");
    let tri_path = workdir.path().join("trigram_probe.db");
    let tri = Connection::open(&tri_path)?;
    tri.execute_batch(
        "PRAGMA journal_mode = WAL;
         CREATE VIRTUAL TABLE sym_tri USING fts5(body, tokenize = 'trigram');",
    )?;
    let mut bodies: std::collections::HashMap<&str, String> = std::collections::HashMap::new();
    let t = Instant::now();
    {
        let txn = tri.unchecked_transaction()?;
        let mut ins = txn.prepare("INSERT INTO sym_tri(rowid, body) VALUES(?1,?2)")?;
        for s in &symbols {
            let src = bodies
                .entry(&s.file)
                .or_insert_with(|| std::fs::read_to_string(root.join(&s.file)).unwrap_or_default());
            let body = oxide::lexical::body_slice(s, src);
            ins.execute(rusqlite::params![s.id() as i64, body])?;
        }
        drop(ins);
        txn.commit()?;
    }
    let tri_build = ms(t);
    println!(
        "trigram build: {tri_build:.0} ms for {} symbols",
        symbols.len()
    );
    println!(
        "trigram db: {} bytes (index.db is {})",
        file_size(&tri_path),
        file_size(&dbpath)
    );

    for needle in ["should_retry", "_retry(", "def compute", "zzz_absent"] {
        let t = Instant::now();
        let n: i64 = tri.query_row(
            "SELECT count(*) FROM sym_tri WHERE body MATCH ?1",
            [format!("\"{needle}\"")],
            |r| r.get(0),
        )?;
        let fts_ms = ms(t);
        // Ground truth: the scan it would replace.
        let t = Instant::now();
        let mut scan = 0i64;
        for s in &symbols {
            if let Some(src) = bodies.get(s.file.as_str()) {
                if oxide::lexical::body_slice(s, src).contains(needle) {
                    scan += 1;
                }
            }
        }
        let scan_ms = ms(t);
        println!(
            "  {needle:>14}: fts5 {n:>5} hits in {fts_ms:6.2} ms | scan {scan:>5} hits in {scan_ms:7.1} ms | {}",
            if n == scan { "exact" } else { "MISMATCH" }
        );
    }
    drop(tri);

    // ---------- Task 5: native tuning knobs ----------
    println!("\n=== PRAGMA sweep ===");
    let t = Instant::now();
    conn.execute_batch("PRAGMA optimize;")?;
    println!("PRAGMA optimize:        {:.1} ms", ms(t));
    let t = Instant::now();
    conn.execute_batch("ANALYZE;")?;
    println!("ANALYZE (full):         {:.1} ms", ms(t));
    let t = Instant::now();
    let ic: String = conn.query_row("PRAGMA integrity_check", [], |r| r.get(0))?;
    println!("PRAGMA integrity_check: {:.1} ms -> {ic}", ms(t));
    let t = Instant::now();
    let fk: usize = conn
        .prepare("PRAGMA foreign_key_check")?
        .query_map([], |_| Ok(()))?
        .count();
    println!(
        "PRAGMA foreign_key_check: {:.1} ms -> {fk} violations",
        ms(t)
    );

    // Post-ANALYZE plans: does the optimizer choose differently?
    println!("\n=== EXPLAIN QUERY PLAN (after ANALYZE) ===");
    explain(
        &conn,
        "persisted lexical query term lookup",
        "SELECT p.symbol_id, p.tf, d.len FROM lexical_postings p
         JOIN lexical_docs d ON d.symbol_id = p.symbol_id WHERE p.term = 'retry'",
    );
    Ok(())
}
