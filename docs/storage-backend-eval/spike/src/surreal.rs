//! SurrealDB (embedded, RocksDB) run of the hard gates.
//!
//! Everything goes through `db.query()` + SurrealQL rather than the typed
//! builder methods: it keeps the surface we depend on small, and it's the
//! surface OXIDE would actually have to write against for FTS/KNN anyway
//! (`@@` and `<|k,ef|>` have no builder equivalent).

use crate::corpus::{cosine, Sym, DIM};
use crate::gate::{dir_bytes, rss_kb, Report};
use anyhow::{Context, Result};
use std::time::Instant;
use surrealdb::engine::any::{connect, Any};
use surrealdb::types::SurrealValue;
use surrealdb::Surreal;

/// `$SDB_ENGINE` selects the storage engine: `rocksdb` (the default here, and
/// what SurrealDB recommends for on-disk *server* deployments) or `surrealkv`
/// (what the deployment-models doc calls the preferred choice for *embedded*
/// deployments, for lower resident memory and in-process behaviour). Both are
/// reached through `engine::any::connect` with a URL, so the gate code is
/// identical for either.
pub fn engine() -> String {
    std::env::var("SDB_ENGINE").unwrap_or_else(|_| "rocksdb".into())
}

pub type Db = Any;

async fn open(path: &std::path::Path) -> Result<Surreal<Any>> {
    let db = connect(format!("{}://{}", engine(), path.to_str().unwrap())).await?;
    db.use_ns("oxide").use_db("index").await?;
    Ok(db)
}

async fn schema(db: &Surreal<Db>) -> Result<()> {
    db.query(
        r#"
        DEFINE TABLE IF NOT EXISTS symbol SCHEMALESS;
        DEFINE TABLE IF NOT EXISTS meta SCHEMALESS;
        DEFINE INDEX IF NOT EXISTS sym_file ON symbol FIELDS file;
        DEFINE INDEX IF NOT EXISTS sym_calls ON symbol FIELDS calls;
        DEFINE INDEX IF NOT EXISTS sym_bases ON symbol FIELDS bases;
        DEFINE ANALYZER IF NOT EXISTS code TOKENIZERS class FILTERS lowercase, ascii;
        DEFINE INDEX IF NOT EXISTS sym_body ON symbol FIELDS body FULLTEXT ANALYZER code BM25;
        "#,
    )
    .await?
    .check()?;
    // `SDB_INDEX_FIRST=1` defines the HNSW index on an empty table so it is
    // maintained incrementally as rows arrive, instead of being built in one
    // statement after a bulk load. The bulk-load-then-DEFINE shape is what
    // aborted at 40k, so which of the two fails is the interesting question.
    if std::env::var("SDB_INDEX_FIRST").is_ok() {
        db.query(format!(
            "DEFINE INDEX IF NOT EXISTS sym_vec ON symbol FIELDS vec HNSW DIMENSION {DIM} DIST COSINE;"
        ))
        .await?
        .check()?;
    }
    Ok(())
}

/// Written as a single explicit transaction, mirroring
/// `IndexBackend::replace_file`'s all-or-nothing contract.
async fn replace_file(db: &Surreal<Db>, file: &str, syms: &[Sym]) -> Result<()> {
    let mut q = String::from("BEGIN TRANSACTION;\nDELETE symbol WHERE file = $file;\n");
    if !syms.is_empty() {
        q.push_str("INSERT INTO symbol $rows;\n");
    }
    q.push_str("COMMIT TRANSACTION;");
    db.query(q)
        .bind(("file", file.to_string()))
        .bind(("rows", syms.to_vec()))
        .await?
        .check()
        .context("replace_file transaction")?;
    Ok(())
}

#[derive(surrealdb::types::SurrealValue)]
struct IdRow {
    id: surrealdb::types::RecordId,
}

#[derive(surrealdb::types::SurrealValue)]
struct CountRow {
    count: i64,
}

async fn count(db: &Surreal<Db>, wheres: &str) -> Result<i64> {
    let q = format!("SELECT count() AS count FROM symbol {wheres} GROUP ALL;");
    let mut r = db.query(q).await?.check()?;
    let rows: Vec<CountRow> = r.take(0)?;
    Ok(rows.first().map(|c| c.count).unwrap_or(0))
}

/// Hold the store open and park, so another process can try to open it.
pub async fn hold(path: &str, secs: u64) -> Result<()> {
    let _db = open(std::path::Path::new(path)).await?;
    println!("HELD");
    tokio::time::sleep(std::time::Duration::from_secs(secs)).await;
    Ok(())
}

/// Open the store, drop the handle, and immediately open it again in the same
/// process. RocksDB does not release its LOCK on drop, so this is expected to
/// fail — it is the reproducible form of an observation that first showed up
/// as a mid-run crash.
pub async fn reopen_same_process(path: &str) -> Result<()> {
    let p = std::path::Path::new(path);
    {
        let db = open(p).await?;
        schema(&db).await?;
        println!("opened once");
    }
    let db = open(p).await?;
    let n = count(&db, "").await?;
    println!("REOPENED in same process rows={n}");
    Ok(())
}

/// Try to open a store another process may already hold.
pub async fn try_open(path: &str) -> Result<()> {
    let db = open(std::path::Path::new(path)).await?;
    let n = count(&db, "").await?;
    println!("OPENED rows={n}");
    Ok(())
}

/// Phase 1: everything that writes. The process then exits: dropping the
/// `Surreal` handle does NOT release RocksDB's LOCK synchronously, so a
/// same-process reopen fails with "lock hold by current process". Two
/// processes is also the faithful model — OXIDE indexes in one process and
/// queries in another.
pub async fn run1(dir: &std::path::Path, files: usize, per_file: usize) -> Result<Report> {
    let mut rep = Report::new(Box::leak(format!("surrealdb-3 ({}) — phase 1: writes", engine()).into_boxed_str()));
    let syms = crate::corpus::corpus(7, files, per_file);
    let vecs = crate::corpus::vectors(&syms);
    let dbpath = dir.join("sdb");

    // ---- G2: startup (cold open of an empty store) ----
    let t = Instant::now();
    let db = open(&dbpath).await?;
    schema(&db).await?;
    rep.metric("startup.open_empty_ms", format!("{}", t.elapsed().as_millis()));

    // G1 (concurrent access) is measured out-of-process by the
    // `surreal-hold` / `surreal-try` subcommands: opening a second handle
    // inline poisons the first one's RocksDB lock for the rest of the run.

    // ---- ingest ----
    crate::gate::Report::step("ingesting symbols");
    let t = Instant::now();
    for (n, chunk) in syms.chunks(per_file).enumerate() {
        replace_file(&db, &chunk[0].file, chunk)
            .await
            .with_context(|| format!("replace_file failed on file #{n} ({})", chunk[0].file))?;
    }
    rep.metric(
        "ingest.symbols_ms",
        format!("{} for {} symbols", t.elapsed().as_millis(), syms.len()),
    );

    // vectors as a separate field update, matching OXIDE's two-phase
    // (parse-then-embed) indexing.
    // One transaction per `crate::VEC_CHUNK` rows, identical to the SQLite
    // control. The first version of this issued one autocommit `UPDATE` per
    // vector while the control batched all of them, which made the reported
    // gap a measurement of the harness rather than of the store.
    crate::gate::Report::step("ingesting vectors");
    let t = Instant::now();
    for (n, chunk) in syms
        .iter()
        .zip(&vecs)
        .collect::<Vec<_>>()
        .chunks(crate::VEC_CHUNK)
        .enumerate()
    {
        crate::gate::Report::step(&format!("  vector chunk {n}"));
        let mut q = String::from("BEGIN TRANSACTION;");
        for (i, _) in chunk.iter().enumerate() {
            q.push_str(&format!(
                "UPDATE type::record('symbol', $id{i}) SET vec = $v{i};"
            ));
        }
        q.push_str("COMMIT TRANSACTION;");
        let mut req = db.query(q);
        for (i, (s, v)) in chunk.iter().enumerate() {
            req = req
                .bind((format!("id{i}"), s.id))
                .bind((format!("v{i}"), (*v).clone()));
        }
        req.await
            .and_then(|r| r.check())
            .with_context(|| format!("vector chunk {n} failed"))?;
    }
    rep.metric(
        "ingest.vectors_ms",
        format!("{} for {} vectors", t.elapsed().as_millis(), vecs.len()),
    );

    let index_first = std::env::var("SDB_INDEX_FIRST").is_ok();
    crate::gate::Report::step(if index_first {
        "HNSW index was defined up front; nothing to build here"
    } else {
        "building HNSW index after bulk load"
    });
    let t = Instant::now();
    if !index_first {
        db.query(format!(
            "DEFINE INDEX IF NOT EXISTS sym_vec ON symbol FIELDS vec HNSW DIMENSION {DIM} DIST COSINE;"
        ))
        .await
        .and_then(|r| r.check())
        .context("HNSW index build failed")?;
    }
    rep.metric(
        "ingest.hnsw_build_ms",
        if index_first {
            "0 (index defined before ingest; cost is inside ingest.vectors_ms)".to_string()
        } else {
            format!("{}", t.elapsed().as_millis())
        },
    );

    // ---- G3b: atomic file replacement ----
    // A transaction whose second half is invalid must leave the first half's
    // DELETE unapplied: `replace_file` is all-or-nothing in OXIDE.
    let victim = syms[0].file.clone();
    let pre = file_count(&db, &victim).await?;
    let bad = db
        .query(
            "BEGIN TRANSACTION;\
             DELETE symbol WHERE file = $f;\
             THROW 'deliberate mid-transaction failure';\
             COMMIT TRANSACTION;",
        )
        .bind(("f", victim.clone()))
        .await
        .and_then(|r| r.check());
    let post = file_count(&db, &victim).await?;
    rep.gate(
        "G3b atomic replace (rollback)",
        post == pre,
        format!(
            "{pre} rows before, {post} after a failing txn (err={})",
            bad.is_err()
        ),
    );

    // ---- G3c: freshness — add / edit / rename / delete ----
    let newfile = "pkg0/added_mod.py";
    let mut added = crate::corpus::corpus(99, 1, 3);
    for (i, s) in added.iter_mut().enumerate() {
        s.id = 10_000_000 + i as i64;
        s.file = newfile.into();
    }
    replace_file(&db, newfile, &added).await?;
    let add_ok = file_count(&db, newfile).await? == 3;

    let mut edited = added.clone();
    edited[0].body = "def edited(self):\n    return zzuniquetoken\n".into();
    edited.truncate(2); // an edit that also removes a symbol
    replace_file(&db, newfile, &edited).await?;
    let edit_ok = file_count(&db, newfile).await? == 2
        && bm25_hits(&db, "zzuniquetoken").await?.len() == 1;

    // rename = delete old path + write new path (what OXIDE's scanner emits)
    let renamed_path = "pkg0/renamed_mod.py";
    let mut renamed = edited.clone();
    for s in renamed.iter_mut() {
        s.file = renamed_path.into();
    }
    replace_file(&db, newfile, &[]).await?;
    replace_file(&db, renamed_path, &renamed).await?;
    let rename_ok = file_count(&db, newfile).await? == 0 && file_count(&db, renamed_path).await? == 2;

    // Preconditions. Asserting "no stale rows after delete" proves nothing
    // unless there were rows to go stale, so measure them first and fold the
    // precondition into the gate.
    let dead_ids: std::collections::BTreeSet<i64> = renamed.iter().map(|s| s.id).collect();
    let dead_targets: Vec<(String, &str)> = renamed
        .iter()
        .flat_map(|s| {
            s.calls
                .iter()
                .map(|c| (c.clone(), "calls"))
                .chain(s.bases.iter().map(|b| (b.clone(), "bases")))
        })
        .collect();
    let rel_before = count_relations(&db, &dead_targets, &dead_ids).await?;
    let lit_before = literal_rows(&db, "zzuniqu").await?;

    replace_file(&db, renamed_path, &[]).await?;

    // Row count alone can pass while derived structures go stale: check the
    // full-text index, the literal-scan path, and every deleted symbol's
    // `calls` AND `bases` targets.
    let rel_after = count_relations(&db, &dead_targets, &dead_ids).await?;
    let lit_after = literal_rows(&db, "zzuniqu").await?;
    let delete_ok = file_count(&db, renamed_path).await? == 0
        && bm25_hits(&db, "zzuniquetoken").await?.is_empty()
        && rel_before > 0
        && rel_after == 0
        && lit_before > 0
        && lit_after == 0;

    rep.gate(
        "G3c freshness add/edit/rename/del",
        add_ok && edit_ok && rename_ok && delete_ok,
        format!(
            "add={add_ok} edit={edit_ok} rename={rename_ok} delete={delete_ok} \
             (relation rows {rel_before}->{rel_after}, literal rows {lit_before}->{lit_after}; \
             both must start nonzero or the check is vacuous)"
        ),
    );

    rep.metric("footprint.peak_rss_kb", format!("{}", rss_kb()));
    rep.metric(
        "footprint.on_disk_bytes",
        format!("{}", dir_bytes(&dbpath)),
    );
    Ok(rep)
}

/// Phase 2: a fresh process over the store phase 1 left behind — the read
/// path OXIDE pays on every CLI invocation.
pub async fn run2(dir: &std::path::Path, files: usize, per_file: usize) -> Result<Report> {
    let mut rep = Report::new(Box::leak(format!("surrealdb-3 ({}) — phase 2: fresh-process reads", engine()).into_boxed_str()));
    let syms = crate::corpus::corpus(7, files, per_file);
    let vecs = crate::corpus::vectors(&syms);
    let dbpath = dir.join("sdb");

    // ---- G3a: persistence across processes ----
    let t = Instant::now();
    let db = open(&dbpath).await?;
    let reopen_ms = t.elapsed().as_millis();
    let after = count(&db, "").await?;
    rep.gate(
        "G3a persistence/reopen",
        after == syms.len() as i64,
        format!("{after} rows after fresh-process reopen (expected {})", syms.len()),
    );
    rep.metric("startup.reopen_populated_ms", format!("{reopen_ms}"));

    // First KNN after a fresh open: an HNSW graph rebuilt in memory rather
    // than read from disk pays for itself here, once per process.
    let t = Instant::now();
    let mut r = db
        .query("SELECT id FROM symbol WHERE vec <|10,64|> $q")
        .bind(("q", vecs[0].clone()))
        .await?
        .check()?;
    let _: Vec<IdRow> = r.take(0)?;
    rep.metric("query.first_knn_after_open_ms", format!("{}", t.elapsed().as_millis()));

    // ---- G4a/b/c: several distinct probes each, so one lucky query cannot
    // carry a PASS ----
    let probes = crate::corpus::probes(&syms, crate::PROBES);

    let t = Instant::now();
    let _ = ids_where(&db, "WHERE $n IN calls", &probes.calls[0]).await?;
    let callers_ms = t.elapsed().as_millis();
    let t = Instant::now();
    let _ = ids_where(&db, "WHERE $n IN calls", &probes.calls[0]).await?;
    let callers_warm_ms = t.elapsed().as_millis();

    let mut a_ok = true;
    let mut a_detail = Vec::new();
    for (field, targets) in [("calls", &probes.calls), ("bases", &probes.bases)] {
        for target in targets {
            let expect: std::collections::BTreeSet<i64> = syms
                .iter()
                .filter(|s| {
                    if field == "calls" {
                        s.calls.contains(target)
                    } else {
                        s.bases.contains(target)
                    }
                })
                .map(|s| s.id)
                .collect();
            let got = ids_where(&db, &format!("WHERE $n IN {field}"), target).await?;
            a_ok &= !expect.is_empty() && got == expect;
            a_detail.push(format!("{}:{}/{}", target, got.len(), expect.len()));
        }
    }
    rep.gate(
        "G4a graph parity",
        a_ok,
        format!("{} probes, got/expected — {}", a_detail.len(), a_detail.join(" ")),
    );
    rep.metric(
        "query.callers_of_ms",
        format!("{callers_ms} (first call in process), {callers_warm_ms} (warm)"),
    );

    let t = Instant::now();
    let _ = bm25_hits(&db, &probes.terms[0]).await?;
    let bm25_ms = t.elapsed().as_millis();
    let mut b_ok = true;
    let (mut missing, mut spurious) = (0usize, 0usize);
    for term in &probes.terms {
        let truth: std::collections::BTreeSet<i64> = syms
            .iter()
            .filter(|s| crate::corpus::contains_token(&s.body, term))
            .map(|s| s.id)
            .collect();
        let got: std::collections::BTreeSet<i64> =
            bm25_hits(&db, term).await?.iter().map(|h| h.0).collect();
        b_ok &= !truth.is_empty() && truth == got;
        missing += truth.difference(&got).count();
        spurious += got.difference(&truth).count();
    }
    rep.gate(
        "G4b BM25 exact match vs ground truth",
        b_ok,
        format!("{} terms; total missing={missing} spurious={spurious}", probes.terms.len()),
    );
    rep.metric("query.bm25_ms", format!("{bm25_ms}"));

    // No trigram index exists in SurrealDB; the only literal-substring
    // primitive is a predicate that cannot use an index.
    let t = Instant::now();
    let _ = literal_ids(&db, &probes.literals[0]).await?;
    let lit_ms = t.elapsed().as_millis();
    let mut c_ok = true;
    let (mut lmissing, mut lspurious) = (0usize, 0usize);
    for lit in &probes.literals {
        let truth: std::collections::BTreeSet<i64> = syms
            .iter()
            .filter(|s| s.body.contains(lit))
            .map(|s| s.id)
            .collect();
        let got = literal_ids(&db, lit).await?;
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
        format!("{lit_ms} (full scan: no trigram index)"),
    );

    // ---- G4d: vector KNN correctness vs brute force ----
    let qv = vecs[0].clone();
    let mut brute: Vec<(i64, f32)> = syms
        .iter()
        .zip(&vecs)
        .map(|(s, v)| (s.id, cosine(&qv, v)))
        .collect();
    brute.sort_by(|a, b| b.1.total_cmp(&a.1));
    let brute_top: Vec<i64> = brute.iter().take(10).map(|x| x.0).collect();
    let t = Instant::now();
    let mut r = db
        .query("SELECT id FROM symbol WHERE vec <|10,64|> $q")
        .bind(("q", qv.clone()))
        .await?
        .check()?;
    let knn_rows: Vec<IdRow> = r.take(0)?;
    let knn_ms = t.elapsed().as_millis();
    let knn: Vec<i64> = knn_rows.iter().filter_map(|r| rec_id(&r.id)).collect();
    let overlap = knn.iter().filter(|i| brute_top.contains(i)).count();

    // Control A: the same top-10 computed exactly, inside SurrealDB, with no
    // ANN index involved. If this disagrees with the Rust brute force, the
    // gate is measuring a semantics mismatch rather than index recall.
    let mut r = db
        .query(
            "SELECT id, vector::similarity::cosine(vec, $q) AS sim \
             FROM symbol ORDER BY sim DESC LIMIT 10",
        )
        .bind(("q", qv.clone()))
        .await?
        .check()?;
    #[derive(SurrealValue)]
    struct SimRow {
        id: surrealdb::types::RecordId,
        #[allow(dead_code)]
        sim: f64,
    }
    let exact_rows: Vec<SimRow> = r.take(0)?;
    let exact: Vec<i64> = exact_rows.iter().filter_map(|r| rec_id(&r.id)).collect();
    let exact_overlap = exact.iter().filter(|i| brute_top.contains(i)).count();

    // Control B: the same ANN query with a much larger search list.
    let t = Instant::now();
    let mut r = db
        .query("SELECT id FROM symbol WHERE vec <|10,512|> $q")
        .bind(("q", qv.clone()))
        .await?
        .check()?;
    let hi_rows: Vec<IdRow> = r.take(0)?;
    let hi_ms = t.elapsed().as_millis();
    let hi: Vec<i64> = hi_rows.iter().filter_map(|r| rec_id(&r.id)).collect();
    let hi_overlap = hi.iter().filter(|i| brute_top.contains(i)).count();

    rep.gate(
        "G4d vector KNN recall@10 == 1.0",
        overlap == 10,
        format!("HNSW ef=64: {overlap}/10; ef=512: {hi_overlap}/10; exact-in-db control: {exact_overlap}/10"),
    );
    rep.metric("query.knn_ms", format!("{knn_ms} (ef=64), {hi_ms} (ef=512)"));

    // ---- G4e: determinism ----
    let a = bm25_hits(&db, &probes.terms[0]).await?;
    let b = bm25_hits(&db, &probes.terms[0]).await?;
    let mut r = db
        .query("SELECT id FROM symbol WHERE vec <|10,64|> $q")
        .bind(("q", qv))
        .await?
        .check()?;
    let knn2_rows: Vec<IdRow> = r.take(0)?;
    let knn2: Vec<i64> = knn2_rows.iter().filter_map(|r| rec_id(&r.id)).collect();
    rep.gate(
        "G4e determinism (repeat query)",
        a == b && knn == knn2,
        format!("bm25 stable={} knn stable={}", a == b, knn == knn2),
    );

    // ---- G2 cont: footprint ----
    rep.metric("footprint.peak_rss_kb", format!("{}", rss_kb()));
    rep.metric(
        "footprint.on_disk_bytes",
        format!("{}", dir_bytes(&dbpath)),
    );
    Ok(rep)
}


fn rec_id(id: &surrealdb::types::RecordId) -> Option<i64> {
    match &id.key {
        surrealdb::types::RecordIdKey::Number(n) => Some(*n),
        surrealdb::types::RecordIdKey::String(s) => s.parse().ok(),
        _ => None,
    }
}

/// How many of `ids` are still reachable through any of `targets` — the
/// SurrealDB equivalent of counting rows in the SQLite control's `relations`
/// table for those symbols.
async fn count_relations(
    db: &Surreal<Db>,
    targets: &[(String, &str)],
    ids: &std::collections::BTreeSet<i64>,
) -> Result<usize> {
    let mut n = 0;
    for (target, field) in targets {
        let hits = ids_where(db, &format!("WHERE $n IN {field}"), target).await?;
        n += hits.intersection(ids).count();
    }
    Ok(n)
}

/// SurrealDB has no literal-substring index, so this is a full scan over the
/// live rows. It proves the row is gone; unlike the SQLite control's trigram
/// shadow table it cannot prove an index entry was cleaned up, because there
/// is no such index to leave behind.
async fn literal_ids(db: &Surreal<Db>, lit: &str) -> Result<std::collections::BTreeSet<i64>> {
    let mut r = db
        .query("SELECT id FROM symbol WHERE string::contains(body, $lit)")
        .bind(("lit", lit.to_string()))
        .await?
        .check()?;
    let rows: Vec<IdRow> = r.take(0)?;
    Ok(rows.iter().filter_map(|x| rec_id(&x.id)).collect())
}

async fn literal_rows(db: &Surreal<Db>, lit: &str) -> Result<usize> {
    let mut r = db
        .query("SELECT id FROM symbol WHERE string::contains(body, $lit)")
        .bind(("lit", lit.to_string()))
        .await?
        .check()?;
    let rows: Vec<IdRow> = r.take(0)?;
    Ok(rows.len())
}

async fn file_count(db: &Surreal<Db>, file: &str) -> Result<i64> {
    let mut r = db
        .query("SELECT count() AS count FROM symbol WHERE file = $f GROUP ALL")
        .bind(("f", file.to_string()))
        .await?
        .check()?;
    let rows: Vec<CountRow> = r.take(0)?;
    Ok(rows.first().map(|c| c.count).unwrap_or(0))
}

async fn ids_where(
    db: &Surreal<Db>,
    wheres: &str,
    n: &str,
) -> Result<std::collections::BTreeSet<i64>> {
    let mut r = db
        .query(format!("SELECT id FROM symbol {wheres}"))
        .bind(("n", n.to_string()))
        .await?
        .check()?;
    let rows: Vec<IdRow> = r.take(0)?;
    Ok(rows.iter().filter_map(|r| rec_id(&r.id)).collect())
}

async fn bm25_hits(db: &Surreal<Db>, term: &str) -> Result<Vec<(i64, f32)>> {
    #[derive(surrealdb::types::SurrealValue)]
    struct H {
        id: surrealdb::types::RecordId,
        score: Option<f32>,
    }
    let mut r = db
        .query("SELECT id, search::score(0) AS score FROM symbol WHERE body @0@ $t ORDER BY score DESC")
        .bind(("t", term.to_string()))
        .await?
        .check()?;
    let rows: Vec<H> = r.take(0)?;
    Ok(rows
        .iter()
        .filter_map(|h| rec_id(&h.id).map(|i| (i, h.score.unwrap_or(0.0))))
        .collect())
}


/// G1a — the documented in-process concurrency model: one embedded instance,
/// `Surreal` handles cloned into concurrent Tokio tasks on a multi-thread
/// runtime. This is what the Rust SDK's concurrency reference describes (and
/// benchmarks, for reads). Concurrent *writes* are not covered by that doc, so
/// they are measured here separately rather than assumed.
pub async fn concurrency(dir: &std::path::Path, files: usize, per_file: usize) -> Result<Report> {
    let mut rep = Report::new(Box::leak(
        format!("surrealdb-3 ({}) — G1a in-process concurrency", engine()).into_boxed_str(),
    ));
    let syms = crate::corpus::corpus(7, files, per_file);
    let dbpath = dir.join("sdb");
    let db = open(&dbpath).await?;
    schema(&db).await?;
    for chunk in syms.chunks(per_file) {
        replace_file(&db, &chunk[0].file, chunk).await?;
    }

    let probes = crate::corpus::probes(&syms, crate::PROBES);
    let expect: Vec<(String, std::collections::BTreeSet<i64>)> = probes
        .calls
        .iter()
        .map(|target| {
            (
                target.clone(),
                syms.iter()
                    .filter(|s| s.calls.contains(target))
                    .map(|s| s.id)
                    .collect(),
            )
        })
        .collect();

    // Sequential baseline, same total work.
    const ROUNDS: usize = 40;
    let t = Instant::now();
    for i in 0..ROUNDS {
        let (target, _) = &expect[i % expect.len()];
        let _ = ids_where(&db, "WHERE $n IN calls", target).await?;
    }
    let seq_ms = t.elapsed().as_millis();

    // Concurrent reads across cloned handles.
    let t = Instant::now();
    let mut handles = Vec::new();
    for i in 0..ROUNDS {
        let db = db.clone();
        let (target, want) = expect[i % expect.len()].clone();
        handles.push(tokio::spawn(async move {
            let got = ids_where(&db, "WHERE $n IN calls", &target).await?;
            anyhow::Ok(got == want)
        }));
    }
    let mut all_correct = true;
    for h in handles {
        all_correct &= h.await??;
    }
    let par_ms = t.elapsed().as_millis();
    rep.gate(
        "G1a concurrent reads correct",
        all_correct,
        format!("{ROUNDS} tasks on cloned handles, every result set exact"),
    );
    rep.metric(
        "concurrency.reads_ms",
        format!("{seq_ms} sequential vs {par_ms} concurrent ({ROUNDS} queries)"),
    );

    // Concurrent writes: each task replaces its own distinct file. No two
    // tasks touch the same rows, so any failure is contention in the store,
    // not a logical conflict in the workload.
    const WRITERS: usize = 16;
    let t = Instant::now();
    let mut handles = Vec::new();
    for w in 0..WRITERS {
        let db = db.clone();
        let file = format!("pkg0/conc_{w}.py");
        let mut rows = crate::corpus::corpus(500 + w as u64, 1, 4);
        for (i, r) in rows.iter_mut().enumerate() {
            r.id = 20_000_000 + (w * 100 + i) as i64;
            r.file = file.clone();
        }
        handles.push(tokio::spawn(async move {
            replace_file(&db, &file, &rows).await.map(|_| ())
        }));
    }
    let mut write_errs = Vec::new();
    for h in handles {
        if let Err(e) = h.await? {
            write_errs.push(format!("{e}"));
        }
    }
    let write_ms = t.elapsed().as_millis();
    let mut landed = 0;
    for w in 0..WRITERS {
        landed += file_count(&db, &format!("pkg0/conc_{w}.py")).await? as usize;
    }
    rep.gate(
        "G1a concurrent writes (disjoint files)",
        write_errs.is_empty() && landed == WRITERS * 4,
        format!(
            "{WRITERS} writers x 4 rows: {landed}/{} landed, {} error(s){}",
            WRITERS * 4,
            write_errs.len(),
            write_errs
                .first()
                .map(|e| format!(" — first: {e}"))
                .unwrap_or_default()
        ),
    );
    rep.metric("concurrency.writes_ms", format!("{write_ms}"));
    rep.metric("footprint.peak_rss_kb", format!("{}", rss_kb()));
    Ok(rep)
}

/// G1c — one query from a cold process, the way `oxide context` runs today.
/// Timed by the caller around the whole process, so it includes runtime setup
/// and store open.
pub async fn one_query(dir: &std::path::Path) -> Result<()> {
    let db = open(&dir.join("sdb")).await?;
    let n = count(&db, "").await?;
    println!("rows={n}");
    Ok(())
}

/// Completeness audit: how many rows exist, and how many are missing the `vec`
/// field the vector index needs. Added because a 40k SurrealKV run reported a
/// clean ingest and then failed an exact-cosine control with "Expected
/// `array<number>` but found `NONE`", which is only possible if some vector
/// writes did not land.
pub async fn audit(dir: &std::path::Path) -> Result<()> {
    let db = open(&dir.join("sdb")).await?;
    let total = count(&db, "").await?;
    let missing = count(&db, "WHERE vec IS NONE").await?;
    let present = count(&db, "WHERE vec IS NOT NONE").await?;
    println!("engine={} rows={total} with_vec={present} missing_vec={missing}", engine());
    if missing > 0 {
        let mut r = db
            .query("SELECT id FROM symbol WHERE vec IS NONE ORDER BY id LIMIT 3")
            .await?
            .check()?;
        let lo: Vec<IdRow> = r.take(0)?;
        let mut r = db
            .query("SELECT id FROM symbol WHERE vec IS NONE ORDER BY id DESC LIMIT 3")
            .await?
            .check()?;
        let hi: Vec<IdRow> = r.take(0)?;
        let ids = |v: &Vec<IdRow>| v.iter().filter_map(|x| rec_id(&x.id)).collect::<Vec<_>>();
        println!("  lowest missing ids:  {:?}", ids(&lo));
        println!("  highest missing ids: {:?}", ids(&hi));
        println!("  (VEC_CHUNK = {}; a contiguous block of that size means one", crate::VEC_CHUNK);
        println!("   whole transaction committed without error and did not land)");
    }
    Ok(())
}


/// A context-shaped composite: one BM25 query + one vector top-10 + three
/// relation lookups, all in one warm process. This is roughly what a single
/// `oxide context` costs the store, and it is the number a daemon would have
/// to live with — a daemon buys back the cold-open cost, not this.
pub async fn context_shaped(dir: &std::path::Path, files: usize, per_file: usize) -> Result<()> {
    let syms = crate::corpus::corpus(7, files, per_file);
    let vecs = crate::corpus::vectors(&syms);
    let probes = crate::corpus::probes(&syms, crate::PROBES);
    let db = open(&dir.join("sdb")).await?;
    // Warm the process first; the cold-open cost is measured separately by G1c.
    let _ = bm25_hits(&db, &probes.terms[0]).await?;
    let mut best = u128::MAX;
    for _ in 0..3 {
        let t = Instant::now();
        let _ = bm25_hits(&db, &probes.terms[1]).await?;
        let mut r = db
            .query("SELECT id FROM symbol WHERE vec <|10,64|> $q")
            .bind(("q", vecs[0].clone()))
            .await?
            .check()?;
        let _: Vec<IdRow> = r.take(0)?;
        for target in probes.calls.iter().take(3) {
            let _ = ids_where(&db, "WHERE $n IN calls", target).await?;
        }
        best = best.min(t.elapsed().as_millis());
    }
    println!(
        "engine={} context-shaped (1 bm25 + 1 knn + 3 relation lookups), warm, best of 3: {best} ms",
        engine()
    );
    Ok(())
}
