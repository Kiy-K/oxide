//! "Enhanced SQLite" control: OXIDE's current authoritative store plus the
//! three things the comparison hypothesises — FTS5/BM25 over bodies, an FTS5
//! trigram index for literal substring search, and a vector path. The vector
//! path here is OXIDE's existing brute-force cosine over a blob column, which
//! is the honest baseline any "viable vector extension" has to beat.

use crate::corpus::{cosine, Sym, DIM};
use crate::gate::{rss_kb, Report};
use anyhow::Result;
use rusqlite::{params, Connection};
use std::time::Instant;

fn open(path: &std::path::Path) -> Result<Connection> {
    let c = Connection::open(path)?;
    c.execute_batch("PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;")?;
    Ok(c)
}

fn schema(c: &Connection) -> Result<()> {
    c.execute_batch(
        r#"
        CREATE TABLE IF NOT EXISTS symbols(
          id INTEGER PRIMARY KEY, file TEXT NOT NULL, qualified_name TEXT NOT NULL,
          name TEXT NOT NULL, signature TEXT NOT NULL, body TEXT NOT NULL,
          start_line INTEGER, end_line INTEGER, content_hash INTEGER);
        CREATE INDEX IF NOT EXISTS symbols_file ON symbols(file);
        CREATE TABLE IF NOT EXISTS embeddings(symbol_id INTEGER PRIMARY KEY, vec BLOB NOT NULL);
        CREATE TABLE IF NOT EXISTS relations(symbol_id INTEGER, kind TEXT, target TEXT);
        CREATE INDEX IF NOT EXISTS relations_rev ON relations(kind, target);
        CREATE INDEX IF NOT EXISTS relations_fwd ON relations(symbol_id);
        CREATE VIRTUAL TABLE IF NOT EXISTS sym_fts USING fts5(
          body, name, content='symbols', content_rowid='id');
        CREATE VIRTUAL TABLE IF NOT EXISTS sym_tri USING fts5(
          body, content='symbols', content_rowid='id', tokenize='trigram');
        CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
        "#,
    )?;
    Ok(())
}

/// One transaction, exactly like `IndexBackend::replace_file`: symbols, their
/// relations, and both FTS shadow tables move together or not at all.
fn replace_file(c: &mut Connection, file: &str, syms: &[Sym]) -> Result<()> {
    let tx = c.transaction()?;
    {
        let mut stale = tx.prepare("SELECT id, body, name FROM symbols WHERE file = ?1")?;
        let old: Vec<(i64, String, String)> = stale
            .query_map([file], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)))?
            .collect::<rusqlite::Result<_>>()?;
        for (id, body, name) in &old {
            tx.execute(
                "INSERT INTO sym_fts(sym_fts, rowid, body, name) VALUES('delete', ?1, ?2, ?3)",
                params![id, body, name],
            )?;
            tx.execute(
                "INSERT INTO sym_tri(sym_tri, rowid, body) VALUES('delete', ?1, ?2)",
                params![id, body],
            )?;
        }
        tx.execute("DELETE FROM relations WHERE symbol_id IN (SELECT id FROM symbols WHERE file = ?1)", [file])?;
        tx.execute("DELETE FROM symbols WHERE file = ?1", [file])?;
        for s in syms {
            tx.execute(
                "INSERT INTO symbols(id,file,qualified_name,name,signature,body,start_line,end_line,content_hash)
                 VALUES(?1,?2,?3,?4,?5,?6,?7,?8,?9)",
                params![s.id, s.file, s.qualified_name, s.name, s.signature, s.body,
                        s.start_line, s.end_line, s.content_hash],
            )?;
            tx.execute(
                "INSERT INTO sym_fts(rowid, body, name) VALUES(?1,?2,?3)",
                params![s.id, s.body, s.name],
            )?;
            tx.execute("INSERT INTO sym_tri(rowid, body) VALUES(?1,?2)", params![s.id, s.body])?;
            for t in &s.calls {
                tx.execute("INSERT INTO relations VALUES(?1,'calls',?2)", params![s.id, t])?;
            }
            for t in &s.bases {
                tx.execute("INSERT INTO relations VALUES(?1,'bases',?2)", params![s.id, t])?;
            }
        }
    }
    tx.commit()?;
    Ok(())
}

fn count_file(c: &Connection, file: &str) -> Result<i64> {
    Ok(c.query_row("SELECT count(*) FROM symbols WHERE file=?1", [file], |r| r.get(0))?)
}

fn bm25_hits(c: &Connection, term: &str) -> Result<Vec<(i64, f64)>> {
    let mut st = c.prepare(
        "SELECT rowid, bm25(sym_fts) FROM sym_fts WHERE sym_fts MATCH ?1 ORDER BY 2, 1",
    )?;
    let v = st
        .query_map([term], |r| Ok((r.get(0)?, r.get(1)?)))?
        .collect::<rusqlite::Result<Vec<(i64, f64)>>>()?;
    Ok(v)
}

fn ids_rel(c: &Connection, kind: &str, target: &str) -> Result<std::collections::BTreeSet<i64>> {
    let mut st = c.prepare("SELECT symbol_id FROM relations WHERE kind=?1 AND target=?2")?;
    let v = st
        .query_map(params![kind, target], |r| r.get(0))?
        .collect::<rusqlite::Result<std::collections::BTreeSet<i64>>>()?;
    Ok(v)
}

/// G1 control: hold an open write transaction and park, so another process
/// can try to read the same database file.
pub fn hold(path: &str, secs: u64) -> Result<()> {
    let mut c = open(std::path::Path::new(path))?;
    schema(&c)?;
    let tx = c.transaction()?;
    tx.execute("INSERT INTO meta VALUES('held', '1')", [])?;
    println!("HELD (uncommitted write transaction open)");
    std::thread::sleep(std::time::Duration::from_secs(secs));
    drop(tx);
    Ok(())
}

/// G1 control: read a database another process may be writing, exactly the way
/// `SqliteStore::open_read_only` does — a plain read-only connection, never
/// `immutable=1` (`AGENTS.md`).
pub fn try_open(path: &str) -> Result<()> {
    let c = Connection::open_with_flags(
        path,
        rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY | rusqlite::OpenFlags::SQLITE_OPEN_URI,
    )?;
    let n: i64 = c.query_row("SELECT count(*) FROM meta", [], |r| r.get(0))?;
    println!("OPENED meta_rows={n}");
    Ok(())
}

pub fn run(dir: &std::path::Path, files: usize, per_file: usize) -> Result<Report> {
    let mut rep = Report::new("enhanced sqlite (fts5 + trigram + brute-force cosine)");
    let syms = crate::corpus::corpus(7, files, per_file);
    let vecs = crate::corpus::vectors(&syms);
    let dbpath = dir.join("index.db");

    let t = Instant::now();
    let mut c = open(&dbpath)?;
    schema(&c)?;
    rep.metric("startup.open_empty_ms", format!("{}", t.elapsed().as_millis()));

    let t = Instant::now();
    for chunk in syms.chunks(per_file) {
        replace_file(&mut c, &chunk[0].file, chunk)?;
    }
    rep.metric(
        "ingest.symbols_ms",
        format!("{} for {} symbols", t.elapsed().as_millis(), syms.len()),
    );

    // Chunked at `crate::VEC_CHUNK`, identical to the SurrealDB side, so the
    // number of commits is the same on both and the comparison is of the store
    // rather than of how the harness happened to batch.
    let t = Instant::now();
    for chunk in syms.iter().zip(&vecs).collect::<Vec<_>>().chunks(crate::VEC_CHUNK) {
        let tx = c.transaction()?;
        for (s, v) in chunk {
            let blob: Vec<u8> = v.iter().flat_map(|f| f.to_le_bytes()).collect();
            tx.execute("INSERT OR REPLACE INTO embeddings VALUES(?1,?2)", params![s.id, blob])?;
        }
        tx.commit()?;
    }
    rep.metric(
        "ingest.vectors_ms",
        format!("{} for {} vectors", t.elapsed().as_millis(), vecs.len()),
    );
    rep.metric("ingest.hnsw_build_ms", "0 (no ANN index; brute force)");

    // ---- G3a: persistence / reopen ----
    let before: i64 = c.query_row("SELECT count(*) FROM symbols", [], |r| r.get(0))?;
    drop(c);
    let t = Instant::now();
    let mut c = open(&dbpath)?;
    let reopen_ms = t.elapsed().as_millis();
    let after: i64 = c.query_row("SELECT count(*) FROM symbols", [], |r| r.get(0))?;
    rep.gate(
        "G3a persistence/reopen",
        before == after && after == syms.len() as i64,
        format!("{before} before / {after} after reopen (expected {})", syms.len()),
    );
    rep.metric("startup.reopen_populated_ms", format!("{reopen_ms}"));

    // ---- G3b: atomic file replacement ----
    let victim = syms[0].file.clone();
    let pre = count_file(&c, &victim)?;
    let bad = (|| -> Result<()> {
        let tx = c.transaction()?;
        tx.execute("DELETE FROM symbols WHERE file=?1", [victim.as_str()])?;
        tx.execute("INSERT INTO symbols(id,file) VALUES(?1,?2)", params![syms[0].id, 1])?;
        tx.execute("INSERT INTO nonexistent_table VALUES(1)", [])?;
        tx.commit()?;
        Ok(())
    })();
    let post = count_file(&c, &victim)?;
    rep.gate(
        "G3b atomic replace (rollback)",
        post == pre,
        format!("{pre} rows before, {post} after a failing txn (err={})", bad.is_err()),
    );

    // ---- G3c: freshness ----
    let newfile = "pkg0/added_mod.py";
    let mut added = crate::corpus::corpus(99, 1, 3);
    for (i, s) in added.iter_mut().enumerate() {
        s.id = 10_000_000 + i as i64;
        s.file = newfile.into();
    }
    replace_file(&mut c, newfile, &added)?;
    let add_ok = count_file(&c, newfile)? == 3;

    let mut edited = added.clone();
    edited[0].body = "def edited(self):\n    return zzuniquetoken\n".into();
    edited.truncate(2);
    replace_file(&mut c, newfile, &edited)?;
    let edit_ok = count_file(&c, newfile)? == 2 && bm25_hits(&c, "zzuniquetoken")?.len() == 1;

    let renamed_path = "pkg0/renamed_mod.py";
    let mut renamed = edited.clone();
    for s in renamed.iter_mut() {
        s.file = renamed_path.into();
    }
    replace_file(&mut c, newfile, &[])?;
    replace_file(&mut c, renamed_path, &renamed)?;
    let rename_ok = count_file(&c, newfile)? == 0 && count_file(&c, renamed_path)? == 2;

    // Preconditions. "No stale rows after delete" proves nothing unless there
    // were rows to go stale, so measure first and fold that into the gate.
    // Covers every deleted symbol and both relation kinds, not one hard-coded
    // call target.
    let dead_ids = renamed
        .iter()
        .map(|s| s.id.to_string())
        .collect::<Vec<_>>()
        .join(",");
    let rel_count = |c: &Connection| -> Result<i64> {
        Ok(c.query_row(
            &format!("SELECT count(*) FROM relations WHERE symbol_id IN ({dead_ids})"),
            [],
            |r| r.get(0),
        )?)
    };
    let tri_count = |c: &Connection| -> Result<i64> {
        Ok(c.query_row(
            "SELECT count(*) FROM sym_tri WHERE sym_tri MATCH '\"zzuniqu\"'",
            [],
            |r| r.get(0),
        )?)
    };
    let rel_before = rel_count(&c)?;
    let tri_before = tri_count(&c)?;

    replace_file(&mut c, renamed_path, &[])?;

    // Every derived structure must follow the delete, not just the row count:
    // the FTS shadow table, the trigram shadow table, and the relation rows.
    let rel_after = rel_count(&c)?;
    let tri_after = tri_count(&c)?;
    let delete_ok = count_file(&c, renamed_path)? == 0
        && bm25_hits(&c, "zzuniquetoken")?.is_empty()
        && rel_before > 0
        && rel_after == 0
        && tri_before > 0
        && tri_after == 0;
    rep.gate(
        "G3c freshness add/edit/rename/del",
        add_ok && edit_ok && rename_ok && delete_ok,
        format!(
            "add={add_ok} edit={edit_ok} rename={rename_ok} delete={delete_ok} \
             (relation rows {rel_before}->{rel_after}, trigram rows {tri_before}->{tri_after}; \
             both must start nonzero or the check is vacuous)"
        ),
    );

    // ---- G4a: graph parity, over several distinct targets ----
    let probes = crate::corpus::probes(&syms, crate::PROBES);
    let mut a_ok = true;
    let mut a_detail = Vec::new();
    let t = Instant::now();
    let _ = ids_rel(&c, "calls", &probes.calls[0])?;
    let callers_ms = t.elapsed().as_micros();
    for target in &probes.calls {
        let expect: std::collections::BTreeSet<i64> = syms
            .iter()
            .filter(|s| s.calls.contains(target))
            .map(|s| s.id)
            .collect();
        let got = ids_rel(&c, "calls", target)?;
        a_ok &= !expect.is_empty() && got == expect;
        a_detail.push(format!("{}:{}/{}", target, got.len(), expect.len()));
    }
    for target in &probes.bases {
        let expect: std::collections::BTreeSet<i64> = syms
            .iter()
            .filter(|s| s.bases.contains(target))
            .map(|s| s.id)
            .collect();
        let got = ids_rel(&c, "bases", target)?;
        a_ok &= !expect.is_empty() && got == expect;
        a_detail.push(format!("{}:{}/{}", target, got.len(), expect.len()));
    }
    rep.gate(
        "G4a graph parity",
        a_ok,
        format!("{} probes, got/expected — {}", a_detail.len(), a_detail.join(" ")),
    );
    rep.metric("query.callers_of_ms", format!("{:.3}", callers_ms as f64 / 1000.0));

    // ---- G4b: BM25, over several distinct terms ----
    let t = Instant::now();
    let _ = bm25_hits(&c, &probes.terms[0])?;
    let bm25_ms = t.elapsed().as_micros();
    let mut b_ok = true;
    let (mut missing, mut spurious) = (0usize, 0usize);
    for term in &probes.terms {
        let truth: std::collections::BTreeSet<i64> = syms
            .iter()
            .filter(|s| crate::corpus::contains_token(&s.body, term))
            .map(|s| s.id)
            .collect();
        let got: std::collections::BTreeSet<i64> =
            bm25_hits(&c, term)?.iter().map(|h| h.0).collect();
        b_ok &= !truth.is_empty() && truth == got;
        missing += truth.difference(&got).count();
        spurious += got.difference(&truth).count();
    }
    rep.gate(
        "G4b BM25 exact match vs ground truth",
        b_ok,
        format!(
            "{} terms; total missing={missing} spurious={spurious}",
            probes.terms.len()
        ),
    );
    rep.metric("query.bm25_ms", format!("{:.3}", bm25_ms as f64 / 1000.0));

    // ---- G4c: literal substring via trigram index, several substrings ----
    let trigram = |c: &Connection, lit: &str| -> Result<std::collections::BTreeSet<i64>> {
        let mut st = c.prepare("SELECT rowid FROM sym_tri WHERE sym_tri MATCH ?1")?;
        let v = st
            .query_map([format!("\"{lit}\"")], |r| r.get(0))?
            .collect::<rusqlite::Result<std::collections::BTreeSet<i64>>>()?;
        Ok(v)
    };
    let t = Instant::now();
    let _ = trigram(&c, &probes.literals[0])?;
    let lit_ms = t.elapsed().as_micros();
    let mut c_ok = true;
    let (mut lmissing, mut lspurious) = (0usize, 0usize);
    for lit in &probes.literals {
        let truth: std::collections::BTreeSet<i64> =
            syms.iter().filter(|s| s.body.contains(lit)).map(|s| s.id).collect();
        let got = trigram(&c, lit)?;
        c_ok &= !truth.is_empty() && truth == got;
        lmissing += truth.difference(&got).count();
        lspurious += got.difference(&truth).count();
    }
    rep.gate(
        "G4c literal substring exact match",
        c_ok,
        format!(
            "{} substrings; total missing={lmissing} spurious={lspurious}",
            probes.literals.len()
        ),
    );
    rep.metric(
        "query.literal_ms",
        format!("{:.3} (fts5 trigram index)", lit_ms as f64 / 1000.0),
    );

    // ---- G4d: vector top-10 (brute force is exact by construction) ----
    let qv = vecs[0].clone();
    let mut brute: Vec<(i64, f32)> =
        syms.iter().zip(&vecs).map(|(s, v)| (s.id, cosine(&qv, v))).collect();
    brute.sort_by(|a, b| b.1.total_cmp(&a.1).then(a.0.cmp(&b.0)));
    let brute_top: Vec<i64> = brute.iter().take(10).map(|x| x.0).collect();

    let t = Instant::now();
    let mut scored: Vec<(i64, f32)> = {
        let mut st = c.prepare("SELECT symbol_id, vec FROM embeddings")?;
        let v = st.query_map([], |r| {
            let id: i64 = r.get(0)?;
            let b: Vec<u8> = r.get(1)?;
            let v: Vec<f32> = b
                .chunks_exact(4)
                .map(|c| f32::from_le_bytes([c[0], c[1], c[2], c[3]]))
                .collect();
            Ok((id, if v.len() == DIM { cosine(&qv, &v) } else { f32::MIN }))
        })?
        .collect::<rusqlite::Result<Vec<(i64, f32)>>>()?;
        v
    };
    scored.sort_by(|a, b| b.1.total_cmp(&a.1).then(a.0.cmp(&b.0)));
    let knn: Vec<i64> = scored.iter().take(10).map(|x| x.0).collect();
    let knn_ms = t.elapsed().as_micros();
    let overlap = knn.iter().filter(|i| brute_top.contains(i)).count();
    rep.gate(
        "G4d vector KNN recall@10 == 1.0",
        overlap == 10,
        format!("{overlap}/10 overlap with brute-force cosine"),
    );
    // The SurrealDB side reports a separate first-query-in-process number for
    // its HNSW index; measure the same cold/warm split here so neither backend
    // is credited with a warm cache the other paid for.
    let t = Instant::now();
    {
        let mut st = c.prepare("SELECT symbol_id, vec FROM embeddings")?;
        let _: Vec<(i64, f32)> = st
            .query_map([], |r| {
                let id: i64 = r.get(0)?;
                let b: Vec<u8> = r.get(1)?;
                let v: Vec<f32> = b
                    .chunks_exact(4)
                    .map(|c| f32::from_le_bytes([c[0], c[1], c[2], c[3]]))
                    .collect();
                Ok((id, if v.len() == DIM { cosine(&qv, &v) } else { f32::MIN }))
            })?
            .collect::<rusqlite::Result<Vec<(i64, f32)>>>()?;
    }
    let knn_warm_ms = t.elapsed().as_micros();
    rep.metric(
        "query.knn_ms",
        format!(
            "{:.3} (first in process), {:.3} (warm); full scan of {} vectors",
            knn_ms as f64 / 1000.0,
            knn_warm_ms as f64 / 1000.0,
            scored.len()
        ),
    );

    // ---- G4e: determinism ----
    let a = bm25_hits(&c, &probes.terms[0])?;
    let b = bm25_hits(&c, &probes.terms[0])?;
    rep.gate(
        "G4e determinism (repeat query)",
        a == b,
        format!("bm25 stable={} knn exact-by-construction", a == b),
    );

    rep.metric("footprint.peak_rss_kb", format!("{}", rss_kb()));
    let mut on_disk = std::fs::metadata(&dbpath).map(|m| m.len()).unwrap_or(0);
    for ext in ["-wal", "-shm"] {
        let p = dbpath.with_file_name(format!("index.db{ext}"));
        on_disk += std::fs::metadata(p).map(|m| m.len()).unwrap_or(0);
    }
    rep.metric("footprint.on_disk_bytes", format!("{on_disk}"));
    Ok(rep)
}
