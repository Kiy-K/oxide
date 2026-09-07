//! Turso Database candidate: `turso` 0.7.2, the Rust rewrite of SQLite, taken
//! with `default-features = false, features = ["fts"]`.
//!
//! Scope is deliberately four gates, not the whole list. G0/G1/G-sync/G-alloc/
//! G-compat were settled in the feasibility round (see ../README.md); G4a,
//! G4d and G4e are plain SQL and an exact scan here, identical in kind to the
//! SQLite control, so running them would only re-measure the control. What is
//! left is everything that could still move the verdict:
//!
//! - **G2** startup / footprint — paid on every one-shot CLI invocation.
//! - **G3c** freshness — Turso's own code-indexing guide tells you to
//!   `DROP TABLE` and repopulate the FTS table after removing stale data, and
//!   the FTS reference warns there is no automatic segment merging. Both
//!   claims are about exactly the incremental path OXIDE lives on, so this
//!   gate checks the delete twice: once as-is, and once after `OPTIMIZE INDEX`.
//! - **G4b** BM25 — via the pre-tokenise parity route from
//!   ../fts5-parity-notes.md (tokenise with OXIDE's own splitting rule, store
//!   the token stream, let a `whitespace` tokenizer split on the spaces it is
//!   handed), which is what a real port would do rather than hoping a built-in
//!   tokenizer happens to match.
//! - **G4c** literal substring — Turso has no `trigram` tokenizer; `ngram`
//!   ("2-3 character n-grams") is the only near-equivalent, and whether it
//!   reproduces exact substring semantics is unproven.
//!
//! Driven by a hand-rolled `block_on`, not tokio: OXIDE's CLI path has no
//! async runtime (`AGENTS.md`), so a harness that spun one up would be
//! measuring a process shape OXIDE would never run.

use crate::corpus::Sym;
use crate::gate::{rss_kb, Report};
use anyhow::Result;
use std::collections::BTreeSet;
use std::future::Future;
use std::sync::{Arc, Condvar, Mutex};
use std::task::{Context, Poll, Wake, Waker};
use std::time::Instant;
use turso::{Builder, Connection, Database};

struct Signal {
    woken: Mutex<bool>,
    cv: Condvar,
}
impl Wake for Signal {
    fn wake(self: Arc<Self>) {
        *self.woken.lock().unwrap() = true;
        self.cv.notify_one();
    }
}

/// Drives a `turso` future to completion from synchronous code with no
/// executor and no dependency. The bounded wait means a future that parks
/// waiting for a reactor nobody is running shows up as a stall rather than a
/// deadlock.
/// Instrumentation: how often a future actually parked, and how long this
/// executor spent asleep. Without these, a slow ingest number cannot be told
/// apart from a harness that naps on every `Pending`.
pub static PENDING_POLLS: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
pub static PARKED_NANOS: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);

fn block_on<F: Future>(fut: F) -> F::Output {
    let mut fut = Box::pin(fut);
    let sig = Arc::new(Signal {
        woken: Mutex::new(false),
        cv: Condvar::new(),
    });
    let waker: Waker = sig.clone().into();
    let mut cx = Context::from_waker(&waker);
    loop {
        if let Poll::Ready(v) = fut.as_mut().poll(&mut cx) {
            return v;
        }
        PENDING_POLLS.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
        let park = Instant::now();
        let mut w = sig.woken.lock().unwrap();
        while !*w {
            let (g, timed_out) = sig
                .cv
                .wait_timeout(w, std::time::Duration::from_millis(20))
                .unwrap();
            w = g;
            if timed_out.timed_out() {
                break;
            }
        }
        *w = false;
        drop(w);
        PARKED_NANOS.fetch_add(
            park.elapsed().as_nanos() as u64,
            std::sync::atomic::Ordering::Relaxed,
        );
    }
}

/// Reproduces `corpus::contains_token`'s split exactly — alphanumerics and
/// `_` are token characters, everything else is a separator — which is also
/// OXIDE's own `embeddings::tokenize_into` rule minus the stopword and
/// minimum-length filters (both would change ground truth, and neither is
/// what this gate is measuring). Lowercased, space-joined, so a `whitespace`
/// tokenizer reproduces the token stream byte for byte.
fn pretokenize(s: &str) -> String {
    s.split(|c: char| !(c.is_alphanumeric() || c == '_'))
        .filter(|w| !w.is_empty())
        .map(|w| w.to_lowercase())
        .collect::<Vec<_>>()
        .join(" ")
}

fn open(path: &std::path::Path) -> Result<(Database, Connection)> {
    // `experimental_index_method` is what gates `CREATE INDEX ... USING fts`
    // and the fts_match/fts_score functions (docs: sql-reference/experimental-features).
    let db = block_on(
        Builder::new_local(path.to_str().unwrap())
            .experimental_index_method(true)
            .build(),
    )?;
    let conn = db.connect()?;
    Ok((db, conn))
}

fn schema(c: &Connection, fts: bool, ngram: bool) -> Result<()> {
    block_on(c.execute_batch(
        r#"
        CREATE TABLE IF NOT EXISTS symbols(
          id INTEGER PRIMARY KEY, file TEXT NOT NULL, qualified_name TEXT NOT NULL,
          name TEXT NOT NULL, signature TEXT NOT NULL, body TEXT NOT NULL,
          tok_name TEXT NOT NULL, tok_body TEXT NOT NULL,
          start_line INTEGER, end_line INTEGER, content_hash INTEGER);
        CREATE INDEX IF NOT EXISTS symbols_file ON symbols(file);
        CREATE TABLE IF NOT EXISTS sym_lit(symbol_id INTEGER PRIMARY KEY, lit_body TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS relations(symbol_id INTEGER, kind TEXT, target TEXT);
        CREATE INDEX IF NOT EXISTS relations_rev ON relations(kind, target);
        CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
        "#,
    ))?;
    // Field weights are the FTS5 control's weights: names at 4, bodies at 1
    // (`AGENTS.md`: bodies are indexed because gold-context evals showed
    // bugfix targets hide behind local identifiers).
    if fts {
        if let Err(e) = block_on(c.execute_batch(
            "CREATE INDEX IF NOT EXISTS sym_fts ON symbols USING fts (tok_name, tok_body) \
             WITH (tokenizer = 'whitespace', weights = 'tok_name=4.0,tok_body=1.0');",
        )) {
            anyhow::bail!("CREATE INDEX ... USING fts failed: {e}");
        }
    }
    if ngram {
        if let Err(e) = block_on(c.execute_batch(
            "CREATE INDEX IF NOT EXISTS lit_fts ON sym_lit USING fts (lit_body) \
             WITH (tokenizer = 'ngram');",
        )) {
            anyhow::bail!("CREATE INDEX ... USING fts (ngram) failed: {e}");
        }
    }
    Ok(())
}

/// Attributes the ingest cost instead of asserting it. Four shapes over the
/// same corpus and the same per-file transactions:
///
/// 1. no FTS index at all — the floor, plain row writes;
/// 2. the weighted BM25 index only;
/// 3. both indexes — **OXIDE's real shape**, because an incremental reindex
///    rewrites one file's rows while every index is live;
/// 4. both indexes created *after* the bulk load — the shape Turso's own
///    code-indexing guide and FTS reference prescribe for bulk imports.
///
/// Shape 4 is not a shape OXIDE can run for an incremental edit; it is only
/// reachable on a full cold rebuild. It is measured anyway so the candidate is
/// judged under its documented best configuration as well as its real one.
pub fn ingest_shapes(dir: &std::path::Path, files: usize, per_file: usize) -> Result<Report> {
    let mut rep = Report::new("turso 0.7.2 — ingest cost by index shape");
    let syms = crate::corpus::corpus(7, files, per_file);
    let rep = &mut rep;
    let syms = &syms[..];
    // Discarded warm-up. The first ingest in a process measured 4,788 ms for
    // work that measured 103 ms later in the same process — a cold-cache /
    // first-touch effect, not a property of the shape. Without this, whichever
    // shape happens to run first is charged ~50x its real cost.
    // The 5th column is `optimize_every`: how many files between
    // `OPTIMIZE INDEX` runs, 0 for never. The FTS reference says there is no
    // automatic segment merging and prescribes OPTIMIZE INDEX "when query
    // performance degrades over time", so a candidate judged without it has
    // not been judged under its documented configuration.
    for (label, fts, ngram, index_first, optimize_every) in [
        ("warmup", false, false, true, 0),
        ("no_index", false, false, true, 0),
        ("fts_only", true, false, true, 0),
        ("fts+ngram_index_first", true, true, true, 0),
        ("fts+ngram_index_first_optimize_10", true, true, true, 10),
        ("fts+ngram_index_last", true, true, false, 0),
    ] {
        let p = dir.join(format!("shape_{label}.db"));
        let _ = std::fs::remove_file(&p);
        let (db, mut c) = open(&p)?;
        schema(&c, fts && index_first, ngram && index_first)?;
        let t = Instant::now();
        let mut optimizes = 0usize;
        for (n, chunk) in syms.chunks(per_file).enumerate() {
            replace_file(&mut c, &chunk[0].file, chunk)?;
            if optimize_every > 0 && (n + 1) % optimize_every == 0 {
                block_on(c.execute_batch("OPTIMIZE INDEX;"))?;
                optimizes += 1;
            }
        }
        let load_ms = t.elapsed().as_millis();
        let parked_ms = PARKED_NANOS.swap(0, std::sync::atomic::Ordering::Relaxed) / 1_000_000;
        let parks = PENDING_POLLS.swap(0, std::sync::atomic::Ordering::Relaxed);
        let t = Instant::now();
        if !index_first {
            schema(&c, fts, ngram)?;
        }
        let index_ms = t.elapsed().as_millis();
        if label == "warmup" {
            drop(c);
            drop(db);
            remove_store(&p);
            continue;
        }
        rep.metric(
            &format!("ingest.shape.{label}_ms"),
            format!(
                "{} total ({load_ms} load + {index_ms} index) for {} symbols = {:.2} ms/symbol",
                load_ms + index_ms,
                syms.len(),
                (load_ms + index_ms) as f64 / syms.len() as f64
            ) + &format!(" [executor parked {parked_ms} ms over {parks} Pending polls, {optimizes} OPTIMIZE INDEX]"),
        );
        drop(c);
        drop(db);
        remove_store(&p);
    }
    rep.metric("footprint.peak_rss_kb", format!("{}", rss_kb()));
    Ok(std::mem::replace(rep, Report::new("")))
}

/// A Turso database is a file plus sidecars (`-wal`, and `-tshm` under the
/// multiprocess flag). Deleting only the `.db` leaves the rest behind.
fn remove_store(p: &std::path::Path) {
    let _ = std::fs::remove_file(p);
    for ext in ["-wal", "-shm", "-tshm"] {
        let mut side = p.as_os_str().to_owned();
        side.push(ext);
        let _ = std::fs::remove_file(std::path::PathBuf::from(side));
    }
}

/// One transaction, exactly like `IndexBackend::replace_file` and like the
/// SQLite control: symbols, their relations and their literal-search rows
/// move together or not at all. Turso keeps the FTS index in step with DML
/// itself (docs: "FTS indexes are updated automatically"), so unlike FTS5's
/// external-content tables there is no shadow-table delete to issue by hand —
/// which is precisely why G3c has to verify it actually happened.
fn replace_file(c: &mut Connection, file: &str, syms: &[Sym]) -> Result<()> {
    let tx = block_on(c.transaction())?;
    block_on(tx.execute(
        "DELETE FROM relations WHERE symbol_id IN (SELECT id FROM symbols WHERE file = ?1)",
        (file,),
    ))?;
    block_on(tx.execute(
        "DELETE FROM sym_lit WHERE symbol_id IN (SELECT id FROM symbols WHERE file = ?1)",
        (file,),
    ))?;
    block_on(tx.execute("DELETE FROM symbols WHERE file = ?1", (file,)))?;
    for s in syms {
        block_on(tx.execute(
            "INSERT INTO symbols(id,file,qualified_name,name,signature,body,tok_name,tok_body,\
             start_line,end_line,content_hash) VALUES(?1,?2,?3,?4,?5,?6,?7,?8,?9,?10,?11)",
            turso::params![
                s.id,
                s.file.clone(),
                s.qualified_name.clone(),
                s.name.clone(),
                s.signature.clone(),
                s.body.clone(),
                pretokenize(&format!("{} {}", s.name, s.qualified_name)),
                pretokenize(&s.body),
                s.start_line as i64,
                s.end_line as i64,
                s.content_hash
            ],
        ))?;
        block_on(tx.execute(
            "INSERT INTO sym_lit VALUES(?1,?2)",
            turso::params![s.id, s.body.clone()],
        ))?;
        for t in &s.calls {
            block_on(tx.execute(
                "INSERT INTO relations VALUES(?1,'calls',?2)",
                turso::params![s.id, t.clone()],
            ))?;
        }
        for t in &s.bases {
            block_on(tx.execute(
                "INSERT INTO relations VALUES(?1,'bases',?2)",
                turso::params![s.id, t.clone()],
            ))?;
        }
    }
    block_on(tx.commit())?;
    Ok(())
}

fn scalar(c: &Connection, sql: &str) -> Result<i64> {
    let mut rows = block_on(c.query(sql, ()))?;
    match block_on(rows.next())? {
        Some(r) => Ok(r.get::<i64>(0)?),
        None => Ok(0),
    }
}

fn count_file(c: &Connection, file: &str) -> Result<i64> {
    let mut rows = block_on(c.query("SELECT count(*) FROM symbols WHERE file = ?1", (file,)))?;
    Ok(block_on(rows.next())?
        .map(|r| r.get::<i64>(0))
        .transpose()?
        .unwrap_or(0))
}

/// BM25 hits for a term. The term goes through the same pre-tokenisation the
/// stored text did, so the query side and the index side agree.
fn bm25_hits(c: &Connection, term: &str) -> Result<Vec<(i64, f64)>> {
    let q = pretokenize(term);
    let mut rows = block_on(c.query(
        "SELECT id, fts_score(tok_name, tok_body, ?1) AS score FROM symbols \
         WHERE fts_match(tok_name, tok_body, ?1) ORDER BY score DESC, id",
        (q,),
    ))?;
    let mut out = Vec::new();
    while let Some(r) = block_on(rows.next())? {
        out.push((r.get::<i64>(0)?, r.get::<f64>(1).unwrap_or(f64::NAN)));
    }
    Ok(out)
}

/// Literal substring through the ngram-tokenised index, as a phrase query —
/// the closest Turso equivalent of the control's `sym_tri MATCH '"lit"'`.
fn literal_hits(c: &Connection, lit: &str) -> Result<BTreeSet<i64>> {
    let mut rows = block_on(c.query(
        "SELECT symbol_id FROM sym_lit WHERE fts_match(lit_body, ?1)",
        (format!("\"{lit}\""),),
    ))?;
    let mut out = BTreeSet::new();
    while let Some(r) = block_on(rows.next())? {
        out.insert(r.get::<i64>(0)?);
    }
    Ok(out)
}

pub fn run(dir: &std::path::Path, files: usize, per_file: usize) -> Result<Report> {
    let mut rep = Report::new(
        "turso 0.7.2 (tantivy fts + ngram) — G2/G3c/G4b/G4c ONLY; G3a/G3b/G4a/G4d/G4e NOT RUN",
    );
    let syms = crate::corpus::corpus(7, files, per_file);
    let dbpath = dir.join("index.db");
    // The workdir is caller-supplied and may be reused, in which case timing
    // an "empty" open would really time a populated one. Start from nothing.
    remove_store(&dbpath);

    // ---- G2: startup, empty ----
    let t = Instant::now();
    let (db, mut c) = open(&dbpath)?;
    schema(&c, true, true)?;
    rep.metric(
        "startup.open_empty_ms",
        format!("{}", t.elapsed().as_millis()),
    );

    let t = Instant::now();
    for chunk in syms.chunks(per_file) {
        replace_file(&mut c, &chunk[0].file, chunk)?;
    }
    rep.metric(
        "ingest.symbols_ms",
        format!("{} for {} symbols", t.elapsed().as_millis(), syms.len()),
    );

    // ---- G2: startup, populated reopen ----
    let before = scalar(&c, "SELECT count(*) FROM symbols")?;
    drop(c);
    drop(db);
    let t = Instant::now();
    let (db, mut c) = open(&dbpath)?;
    let reopen_ms = t.elapsed().as_millis();
    let after = scalar(&c, "SELECT count(*) FROM symbols")?;
    rep.metric("startup.reopen_populated_ms", format!("{reopen_ms}"));
    rep.gate(
        "G2 reopen sees every row",
        before == after && after == syms.len() as i64,
        format!(
            "{before} before / {after} after reopen (expected {})",
            syms.len()
        ),
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

    // Preconditions: "no stale rows after delete" proves nothing unless there
    // were rows to go stale. Same discipline as the SQLite control.
    let dead_ids = renamed
        .iter()
        .map(|s| s.id.to_string())
        .collect::<Vec<_>>()
        .join(",");
    let rel_count = |c: &Connection| -> Result<i64> {
        scalar(
            c,
            &format!("SELECT count(*) FROM relations WHERE symbol_id IN ({dead_ids})"),
        )
    };
    let rel_before = rel_count(&c)?;
    let fts_before = bm25_hits(&c, "zzuniquetoken")?.len();
    let lit_before = literal_hits(&c, "zzuniquetoken")?.len();

    replace_file(&mut c, renamed_path, &[])?;

    // First check WITHOUT `OPTIMIZE INDEX`: Turso's FTS docs say deletes are
    // tombstoned and merged only on demand, and its code-indexing guide tells
    // you to DROP and repopulate the FTS table after removing stale data.
    // OXIDE deletes files continuously, so a stale hit here is a real defect,
    // not a tuning knob.
    let rel_after = rel_count(&c)?;
    let fts_after = bm25_hits(&c, "zzuniquetoken")?.len();
    let lit_after = literal_hits(&c, "zzuniquetoken")?.len();
    let rows_gone = count_file(&c, renamed_path)? == 0;
    let delete_ok = rows_gone
        && rel_before > 0
        && rel_after == 0
        && fts_before > 0
        && fts_after == 0
        && lit_before > 0
        && lit_after == 0;

    // Then check again after OPTIMIZE INDEX, so the report can say whether a
    // stale hit is permanent or merely deferred.
    let opt = block_on(c.execute_batch("OPTIMIZE INDEX;"));
    let fts_opt = bm25_hits(&c, "zzuniquetoken")?.len();
    let lit_opt = literal_hits(&c, "zzuniquetoken")?.len();

    rep.gate(
        "G3c freshness add/edit/rename/del",
        add_ok && edit_ok && rename_ok && delete_ok,
        format!(
            "add={add_ok} edit={edit_ok} rename={rename_ok} delete={delete_ok} \
             (relation rows {rel_before}->{rel_after}, fts hits {fts_before}->{fts_after}, \
             ngram hits {lit_before}->{lit_after}; after OPTIMIZE INDEX (ok={}): \
             fts={fts_opt} ngram={lit_opt})",
            opt.is_ok()
        ),
    );

    // ---- G4b: BM25 against ground truth computed independently in Rust ----
    let probes = crate::corpus::probes(&syms, crate::PROBES);
    let t = Instant::now();
    let first = bm25_hits(&c, &probes.terms[0])?;
    let bm25_ms = t.elapsed().as_micros();
    let mut b_ok = true;
    let (mut missing, mut spurious) = (0usize, 0usize);
    for term in &probes.terms {
        let truth: BTreeSet<i64> = syms
            .iter()
            .filter(|s| crate::corpus::contains_token(&s.body, term))
            .map(|s| s.id)
            .collect();
        let got: BTreeSet<i64> = bm25_hits(&c, term)?.iter().map(|h| h.0).collect();
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
    // The FTS reference says "Lower scores indicate higher relevance" and then
    // orders its own examples by score DESC. Record the range so the
    // contradiction is visible rather than assumed away.
    if let (Some(f), Some(l)) = (first.first(), first.last()) {
        rep.metric(
            "query.fts_score_range",
            format!(
                "first={:.4} last={:.4} over {} hits (ORDER BY score DESC)",
                f.1,
                l.1,
                first.len()
            ),
        );
    }

    // ---- G4c: literal substring through the ngram index ----
    let t = Instant::now();
    let _ = literal_hits(&c, &probes.literals[0])?;
    let lit_ms = t.elapsed().as_micros();
    let mut c_ok = true;
    let (mut lmissing, mut lspurious) = (0usize, 0usize);
    for lit in &probes.literals {
        let truth: BTreeSet<i64> = syms
            .iter()
            .filter(|s| s.body.contains(lit))
            .map(|s| s.id)
            .collect();
        let got = literal_hits(&c, lit)?;
        c_ok &= !truth.is_empty() && truth == got;
        lmissing += truth.difference(&got).count();
        lspurious += got.difference(&truth).count();
    }
    rep.gate(
        "G4c literal substring exact match",
        c_ok,
        format!(
            "{} substrings; total missing={lmissing} spurious={lspurious} (ngram tokenizer)",
            probes.literals.len()
        ),
    );
    rep.metric(
        "query.literal_ms",
        format!("{:.3} (ngram fts index)", lit_ms as f64 / 1000.0),
    );

    rep.metric("footprint.peak_rss_kb", format!("{}", rss_kb()));
    // The store, not the directory: `ingest_shapes` leaves throwaway databases
    // and their sidecars in `dir`, and summing the directory would charge the
    // candidate for them (found by Codex review). The SQLite control measures
    // index.db plus its own sidecars for exactly this reason.
    let mut on_disk = std::fs::metadata(&dbpath).map(|m| m.len()).unwrap_or(0);
    for ext in ["-wal", "-shm", "-tshm"] {
        let side = dbpath.with_file_name(format!("index.db{ext}"));
        on_disk += std::fs::metadata(side).map(|m| m.len()).unwrap_or(0);
    }
    rep.metric("footprint.on_disk_bytes", format!("{on_disk}"));
    drop(c);
    drop(db);
    Ok(rep)
}
