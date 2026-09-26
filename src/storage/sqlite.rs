//! [`SqliteStore`]: connection lifecycle (writer open with foreign keys, WAL
//! and bounded schema-init retry; the read-only snapshot connection), every
//! write transaction (each bumps the generation; the identity-publishing and
//! batch-embedding writes also re-check the migration marker), and the
//! complete `IndexBackend` implementation. Transactions are kept whole here
//! on purpose: each one's atomicity is auditable in one place.

use super::backend::{IndexBackend, IndexStats, SymbolRelations};
use super::row::row_to_symbol;
use super::schema::{EMBEDDING_MIGRATION_KEY, INDEX_GENERATION_KEY, INDEX_ID_KEY, SCHEMA_SQL};
use crate::symbols::Symbol;
use anyhow::{Context, Result};
use rusqlite::{Connection, OptionalExtension};
use std::collections::{HashMap, HashSet};
use std::path::Path;

/// `PRAGMA wal_autocheckpoint` (in pages) while a full-corpus base pass is
/// writing — 16 MB at the default 4 KB page, against SQLite's 1000-page
/// (4 MB) default. Measured on the 13k-symbol pylint corpus
/// (docs/sqlite-request-path/README.md): cold index −21%, p95 commit
/// latency 13 → 3.5 ms (fewer checkpoints landing inside commits), WAL
/// peak 17 → ≤32 MB. Disabling checkpoints outright was faster still but
/// let the WAL reach 1.16 GB for a 46 MB database, and 16000 pages bought
/// only noise-level time for a 71 MB WAL; this is the bounded point.
pub const BULK_WAL_AUTOCHECKPOINT_PAGES: u32 = 4000;
/// SQLite's own default, restored by [`IndexBackend::end_bulk_writes`].
const DEFAULT_WAL_AUTOCHECKPOINT_PAGES: u32 = 1000;

pub struct SqliteStore {
    pub(super) conn: Connection,
}

/// Fail unless [`EMBEDDING_MIGRATION_KEY`] still holds `expected_space`,
/// read inside `tx` so the check and the write it guards commit or roll back
/// together. A guard read outside the transaction would be a plain TOCTOU.
fn ensure_migration_marker(tx: &rusqlite::Transaction<'_>, expected_space: &str) -> Result<()> {
    let current: String = tx
        .query_row(
            "SELECT value FROM meta WHERE key = ?1",
            [EMBEDDING_MIGRATION_KEY],
            |r| r.get(0),
        )
        .optional()?
        .unwrap_or_default();
    anyhow::ensure!(
        current == expected_space,
        "another process took over this index's embeddings mid-run \
         (migration marker changed); re-run `oxide index`"
    );
    Ok(())
}

/// Cold-start schema init retry: bounded to give a losing concurrent
/// first-time indexer a real chance to proceed once the winner finishes
/// initializing, without ever waiting indefinitely (see `open`'s doc
/// comment for why `busy_timeout` alone is not enough here).
const SCHEMA_INIT_MAX_RETRIES: u32 = 10;
const SCHEMA_INIT_RETRY_DELAY: std::time::Duration = std::time::Duration::from_millis(50);

/// Insert one file's postings.
///
/// Deliberately the plain shape. Three faster-looking variants were measured
/// on the 15k-symbol perf corpus and none beat it: a 32 MB `cache_size` cost
/// 31 MB of RSS for no time change; 128-row multi-row `INSERT` statements
/// were *slower* (13.3 s vs 11.8 s cold index); and pre-sorting rows by term
/// for b-tree locality came back inside noise (11.6 s). The write cost is
/// b-tree and WAL work proportional to row count, roughly 9 µs per posting,
/// and none of the usual levers move it. See
/// `docs/storage-backend-eval/enhanced-sqlite.md`.
fn insert_postings(
    tx: &rusqlite::Transaction<'_>,
    postings: &[crate::lexical::DocPostings],
) -> Result<()> {
    let mut stmt =
        tx.prepare("INSERT INTO lexical_postings(term, symbol_id, tf) VALUES(?1,?2,?3)")?;
    for d in postings {
        for (term, tf) in &d.terms {
            stmt.execute(rusqlite::params![term, d.symbol_id as i64, tf])?;
        }
    }
    Ok(())
}

fn is_locked(e: &rusqlite::Error) -> bool {
    matches!(
        e,
        rusqlite::Error::SqliteFailure(inner, _)
            if matches!(
                inner.code,
                rusqlite::ErrorCode::DatabaseBusy | rusqlite::ErrorCode::DatabaseLocked
            )
    )
}

/// Whether an error returned by [`SqliteStore::open`] is transient lock
/// contention (the bounded cold-start retry above was exhausted, or the
/// underlying `Connection::open` itself hit a lock) rather than genuine
/// corruption. Callers use this to pick a retryable error code instead of
/// telling the caller to delete and rebuild the index over a condition that
/// resolves itself once the other writer finishes.
pub fn is_locked_error(e: &anyhow::Error) -> bool {
    e.chain()
        .filter_map(|c| c.downcast_ref::<rusqlite::Error>())
        .any(is_locked)
}

/// Advance [`INDEX_GENERATION_KEY`] inside `tx`. Every write path calls
/// this so the counter is a complete record of "something changed"; a
/// write path that forgot would let a cached snapshot outlive the content
/// it was built from (`tests/index_generation.rs` pins each path).
fn bump_generation(tx: &rusqlite::Transaction<'_>) -> Result<()> {
    tx.execute(
        "INSERT INTO meta(key,value) VALUES(?1,'1')
         ON CONFLICT(key) DO UPDATE SET value = CAST(value AS INTEGER) + 1",
        [INDEX_GENERATION_KEY],
    )?;
    Ok(())
}

impl SqliteStore {
    /// `(index_id, index_generation)` if both are present — absent on an
    /// index no writer has opened since these keys existed, in which case a
    /// caller must not cache anything derived from it.
    pub fn generation(&self) -> Result<Option<(String, u64)>> {
        let id = self.get_meta(INDEX_ID_KEY)?;
        let generation = self.get_meta(INDEX_GENERATION_KEY)?;
        Ok(match (id, generation) {
            (Some(id), Some(g)) => g.parse::<u64>().ok().map(|g| (id, g)),
            _ => None,
        })
    }

    /// `EXPLAIN QUERY PLAN` rows for `sql`, one detail string per row —
    /// diagnostic only, used by `tests/query_plans.rs` to pin that the
    /// request-path statements stay index-driven whatever `ANALYZE`
    /// statistics an index happens to carry.
    pub fn explain_query_plan(&self, sql: &str) -> Result<Vec<String>> {
        let mut stmt = self.conn.prepare(&format!("EXPLAIN QUERY PLAN {sql}"))?;
        let rows = stmt.query_map([], |r| r.get::<_, String>(3))?;
        Ok(rows.collect::<std::result::Result<Vec<_>, _>>()?)
    }

    /// Run `ANALYZE` (experiment support for `tests/query_plans.rs` and
    /// `docs/sqlite-request-path/`): persists `sqlite_stat1`, which is
    /// exactly what a `PRAGMA optimize` would feed the planner.
    pub fn analyze(&self) -> Result<()> {
        self.conn.execute_batch("ANALYZE;")?;
        Ok(())
    }

    pub fn open(path: &Path) -> Result<Self> {
        if let Some(dir) = path.parent() {
            std::fs::create_dir_all(dir)?;
        }
        let conn =
            Connection::open(path).with_context(|| format!("open index at {}", path.display()))?;
        conn.busy_timeout(std::time::Duration::from_secs(5))?;
        // Foreign keys, declared rather than inherited. Both dependent
        // tables carry `ON DELETE CASCADE` and OXIDE genuinely relies on
        // those cascades — `replace_file` deletes a file's symbols and lets
        // the embeddings and relations go with them, which is exactly why it
        // snapshots the embeddings it wants to keep first.
        //
        // Nothing in OXIDE ever asked for that. Enforcement was on only
        // because `libsqlite3-sys` compiles its bundled SQLite with
        // `-DSQLITE_DEFAULT_FOREIGN_KEYS=1` (0.30.1's `build.rs`, the
        // version pinned by `rusqlite 0.32`), inverting SQLite's own
        // documented default of OFF. A rusqlite bump, or ever linking a
        // system SQLite, would have silently switched cascades off and
        // started stranding a row per deleted symbol, in a codebase whose
        // comments asserted the cascades were decorative. Setting it
        // explicitly costs one statement per open and makes the dependency
        // real; `tests/foreign_key_audit.rs` fails if it stops holding.
        //
        // Must precede any transaction: `PRAGMA foreign_keys` is a silent
        // no-op inside one.
        conn.execute_batch("PRAGMA foreign_keys = ON;")
            .with_context(|| format!("enable foreign keys on {}", path.display()))?;
        // Cold-start race: two processes creating the very first index.db
        // concurrently can still hit `database is locked` while switching
        // journal_mode / creating the schema, even with busy_timeout set —
        // SQLite's busy handler does not retry every SQLITE_BUSY/LOCKED
        // variant (the same underlying class of gotcha that motivated the
        // IMMEDIATE-transaction fix in `replace_file`/`remove_files`, here
        // hit during first-ever schema setup instead of a read->write lock
        // upgrade). Retry with a short bounded backoff so a loser waits for
        // the winner to finish initializing instead of failing outright;
        // this is not infinite — a persistent lock still surfaces as a
        // real, structured, retryable error after ~1.5s of total backoff.
        let mut attempt = 0u32;
        loop {
            match conn.execute_batch(SCHEMA_SQL) {
                Ok(()) => break,
                Err(e) if is_locked(&e) && attempt < SCHEMA_INIT_MAX_RETRIES => {
                    attempt += 1;
                    std::thread::sleep(SCHEMA_INIT_RETRY_DELAY * attempt);
                }
                Err(e) => {
                    return Err(e)
                        .with_context(|| format!("initialize schema at {}", path.display()))
                }
            }
        }
        // Identity for the generation counter (see `INDEX_ID_KEY`). Not a
        // cryptographic need — just distinct across rebuilds of the same
        // path, which time + pid + path give. `OR IGNORE`: set once, kept.
        let path_str = path.to_string_lossy();
        let pid = std::process::id().to_le_bytes();
        let nanos = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_nanos())
            .unwrap_or(0)
            .to_le_bytes();
        let seed =
            crate::symbols::fnv1a64_iter([path_str.as_bytes(), pid.as_slice(), nanos.as_slice()]);
        conn.execute(
            "INSERT OR IGNORE INTO meta(key,value) VALUES(?1,?2)",
            rusqlite::params![INDEX_ID_KEY, format!("{seed:016x}")],
        )?;
        // Generation 0 = "keyable, nothing written by this counter yet".
        // An index that predates the counter becomes keyable on its first
        // writer open (any `oxide index` run), with no reindex needed.
        conn.execute(
            "INSERT OR IGNORE INTO meta(key,value) VALUES(?1,'0')",
            [INDEX_GENERATION_KEY],
        )?;
        Ok(Self { conn })
    }

    /// Open an existing index without mutating the database file's own
    /// content. Schema setup is skipped; an incompatible index surfaces as
    /// `index_unreadable`.
    ///
    /// # Concurrency contract
    ///
    /// This is a plain `SQLITE_OPEN_READ_ONLY` connection — deliberately
    /// *not* opened with the `immutable=1` URI parameter. `immutable=1`
    /// tells SQLite the file will never change for the life of the
    /// connection, which disables WAL/locking consistency checks entirely.
    /// SQLite's own docs are explicit that this is unsafe when the file
    /// *can* change: "can result in incorrect query results and/or
    /// SQLITE_CORRUPT errors if the database file is changed by another
    /// process." OXIDE cannot promise that — `oxide index` can start from
    /// another process at any time — so `immutable=1` must not be used here.
    ///
    /// A plain read-only WAL connection is the mode WAL was designed for:
    /// one writer plus any number of concurrent readers, none of which
    /// block each other, each reader seeing a consistent snapshot as of
    /// when its read transaction started. The tradeoff versus `immutable=1`
    /// is that this connection may need to create (or read) the writer's
    /// `-wal`/`-shm` coordination files if they do not already exist — a
    /// normal, harmless side effect of participating correctly in WAL, not
    /// a modification of `index.db`'s own indexed content. The accepted
    /// contract is: **read-only OXIDE commands are safe during concurrent
    /// indexing and never modify the database themselves, but SQLite may
    /// create/touch normal WAL/SHM state as any WAL reader does.** See
    /// `tests/cli_e2e.rs` for the tests proving this (index.db content is
    /// untouched by reads; reads succeed correctly against a live writer).
    pub fn open_read_only(path: &Path) -> Result<Self> {
        let conn = Connection::open_with_flags(path, rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY)
            .with_context(|| format!("open index at {}", path.display()))?;
        // WAL readers essentially never block on a writer, but the very
        // first reader to observe a given database creates the -shm
        // wal-index; a busy_timeout is a cheap safety net for that narrow
        // window, matching the writer's own connection setup.
        conn.busy_timeout(std::time::Duration::from_secs(5))?;
        conn.execute_batch("PRAGMA query_only = ON;")
            .with_context(|| format!("set query_only on {}", path.display()))?;
        // One WAL snapshot for the whole read session. Every read command
        // validates the index's identity metadata and *then* loads vectors;
        // in autocommit those are two independent snapshots, so a concurrent
        // `oxide index` finishing a provider switch in between let a request
        // approve the old provider's metadata and go on to score against the
        // new provider's rows. A deferred transaction takes its snapshot at
        // the first read and holds it, so the two agree by construction.
        //
        // Read-only in the strict sense the CLI contract requires: with
        // `query_only` set this can never become a write transaction, it
        // takes no locks a writer waits on under WAL, and the connection's
        // `Drop` ends it. Safe to hold open only because every reader here
        // is request-scoped (`RepositoryService::open_index_for_read`); a
        // long-lived one would pin the WAL against checkpointing.
        conn.execute_batch("BEGIN DEFERRED;")
            .with_context(|| format!("begin read snapshot on {}", path.display()))?;
        Ok(Self { conn })
    }
}

impl IndexBackend for SqliteStore {
    fn get_meta(&self, key: &str) -> Result<Option<String>> {
        let mut stmt = self.conn.prepare("SELECT value FROM meta WHERE key = ?1")?;
        let mut rows = stmt.query([key])?;
        Ok(rows.next()?.map(|r| r.get(0)).transpose()?)
    }

    fn set_meta(&mut self, key: &str, value: &str) -> Result<()> {
        let tx = self
            .conn
            .transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)?;
        tx.execute(
            "INSERT INTO meta(key,value) VALUES(?1,?2) ON CONFLICT(key) DO UPDATE SET value=?2",
            [key, value],
        )?;
        bump_generation(&tx)?;
        tx.commit()?;
        Ok(())
    }

    fn set_meta_all(&mut self, expected_space: &str, pairs: &[(&str, &str)]) -> Result<()> {
        let tx = self
            .conn
            .transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)?;
        ensure_migration_marker(&tx, expected_space)?;
        for (key, value) in pairs {
            tx.execute(
                "INSERT INTO meta(key,value) VALUES(?1,?2) ON CONFLICT(key) DO UPDATE SET value=?2",
                [*key, *value],
            )?;
        }
        bump_generation(&tx)?;
        tx.commit()?;
        Ok(())
    }

    fn file_hashes(&self) -> Result<HashMap<String, u64>> {
        let mut stmt = self.conn.prepare("SELECT path, content_hash FROM files")?;
        let rows = stmt.query_map([], |r| {
            Ok((r.get::<_, String>(0)?, r.get::<_, i64>(1)? as u64))
        })?;
        Ok(rows.collect::<std::result::Result<HashMap<_, _>, _>>()?)
    }

    fn replace_file(
        &mut self,
        file: &str,
        hash: u64,
        symbols: &[Symbol],
        relations: &[(u64, Vec<String>, Vec<String>)],
        postings: &[crate::lexical::DocPostings],
    ) -> Result<()> {
        // IMMEDIATE: acquire the write lock up front. A deferred
        // transaction that reads before it writes can hit SQLITE_BUSY on
        // the read->write lock upgrade, which busy_timeout does NOT retry
        // (observed directly by racing indexers against the same repo).
        let tx = self
            .conn
            .transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)?;
        // Embeddings cascade-delete with their symbols; unchanged symbols are
        // re-inserted below and their embeddings restored by the indexer only
        // when needed. To preserve them across a rewrite we snapshot first.
        let mut kept: Vec<(u64, u64, i32, Vec<u8>)> = Vec::new();
        {
            let mut stmt = tx.prepare(
                "SELECT e.symbol_id, e.content_hash, e.dim, e.vec FROM embeddings e
                 JOIN symbols s ON s.id = e.symbol_id WHERE s.file = ?1",
            )?;
            let rows = stmt.query_map([file], |r| {
                Ok((
                    r.get::<_, i64>(0)? as u64,
                    r.get::<_, i64>(1)? as u64,
                    r.get::<_, i32>(2)?,
                    r.get::<_, Vec<u8>>(3)?,
                ))
            })?;
            for row in rows {
                kept.push(row?);
            }
        }
        // This cascades to `embeddings` and `symbol_relations` — foreign keys
        // ARE enforced here (see `open`), which is why the snapshot above
        // exists and why nothing deletes those two tables by hand.
        tx.execute("DELETE FROM symbols WHERE file = ?1", [file])?;
        tx.execute("DELETE FROM files WHERE path = ?1", [file])?;
        tx.execute(
            "INSERT OR REPLACE INTO files(path, content_hash) VALUES(?1, ?2)",
            rusqlite::params![file, hash as i64],
        )?;
        {
            let mut stmt = tx.prepare(
                "INSERT INTO symbols(id, file, qualified_name, name, kind, language,
                     start_line, end_line, content_hash, signature, imports_json,
                     exported, parent, references_json)
                 VALUES(?1,?2,?3,?4,?5,?6,?7,?8,?9,?10,?11,?12,?13,?14)",
            )?;
            for s in symbols {
                stmt.execute(rusqlite::params![
                    s.id() as i64,
                    file,
                    s.qualified_name,
                    s.name,
                    s.kind.to_string(),
                    s.language.as_str(),
                    s.start_line,
                    s.end_line,
                    s.content_hash as i64,
                    s.signature,
                    serde_json::to_string(&s.imports)?,
                    s.exported as i64,
                    s.parent,
                    serde_json::to_string(&s.references)?,
                ])?;
            }
        }
        // Restore embeddings that still have a live symbol with the same content.
        let live: HashSet<u64> = symbols.iter().map(|s| s.id()).collect();
        {
            let mut stmt = tx.prepare(
                "INSERT OR REPLACE INTO embeddings(symbol_id, content_hash, dim, vec)
                 VALUES(?1,?2,?3,?4)",
            )?;
            for (id, chash, dim, vec) in kept {
                if live.contains(&id) {
                    stmt.execute(rusqlite::params![id as i64, chash as i64, dim, vec])?;
                }
            }
        }
        // Same transaction as the symbol rewrite above — see this method's
        // doc comment for why relations must land atomically with
        // symbols/content_hash, not as a follow-up call. No per-symbol
        // delete here: the file-scoped delete above already cleared every
        // relation row belonging to this file.
        {
            let mut ins = tx.prepare(
                "INSERT INTO symbol_relations (symbol_id, kind, target) VALUES (?1, ?2, ?3)",
            )?;
            for (symbol_id, calls, bases) in relations {
                for target in calls {
                    ins.execute(rusqlite::params![*symbol_id as i64, "calls", target])?;
                }
                for target in bases {
                    ins.execute(rusqlite::params![*symbol_id as i64, "bases", target])?;
                }
            }
        }
        // Lexical postings, same transaction and same reason: a file whose
        // symbols are current but whose postings are not is a silently
        // wrong BM25 corpus that no later incremental run would revisit,
        // because the file's content_hash already matches. The cascade from
        // the symbol delete above already removed the old rows.
        {
            let mut doc = tx.prepare("INSERT INTO lexical_docs(symbol_id, len) VALUES(?1,?2)")?;
            for d in postings {
                doc.execute(rusqlite::params![d.symbol_id as i64, d.len])?;
            }
        }
        insert_postings(&tx, postings)?;
        bump_generation(&tx)?;
        tx.commit()?;
        Ok(())
    }

    fn put_file_lexical(
        &mut self,
        file: &str,
        expected_hash: u64,
        postings: &[crate::lexical::DocPostings],
    ) -> Result<bool> {
        let tx = self
            .conn
            .transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)?;
        // Re-read the file's hash inside this transaction, the same way
        // `ensure_migration_marker` re-reads the embedding marker inside
        // each write. The backfill's postings were derived from a snapshot
        // taken earlier in the run; if `oxide watch` (or a second `oxide
        // index`) replaced that file meanwhile, those symbols already have
        // correct, newer postings and writing ours would overwrite them
        // with stale ones — permanently, since the file's content_hash now
        // matches and no incremental run would revisit it.
        let stored: Option<i64> = tx
            .query_row(
                "SELECT content_hash FROM files WHERE path = ?1",
                [file],
                |r| r.get(0),
            )
            .optional()?;
        if stored != Some(expected_hash as i64) {
            return Ok(false);
        }
        {
            // Delete first: a symbol's term set can shrink, and a stale row
            // for a term the symbol no longer has would keep matching it.
            let mut del = tx.prepare("DELETE FROM lexical_postings WHERE symbol_id = ?1")?;
            let mut doc = tx.prepare(
                "INSERT INTO lexical_docs(symbol_id, len) VALUES(?1,?2)
                 ON CONFLICT(symbol_id) DO UPDATE SET len = ?2",
            )?;
            for d in postings {
                del.execute([d.symbol_id as i64])?;
                doc.execute(rusqlite::params![d.symbol_id as i64, d.len])?;
            }
        }
        insert_postings(&tx, postings)?;
        bump_generation(&tx)?;
        tx.commit()?;
        Ok(true)
    }

    fn lexical_totals(&self) -> Result<(usize, i64)> {
        let (count, total): (i64, Option<i64>) =
            self.conn
                .query_row("SELECT COUNT(*), SUM(len) FROM lexical_docs", [], |r| {
                    Ok((r.get(0)?, r.get(1)?))
                })?;
        Ok((count as usize, total.unwrap_or(0)))
    }

    fn lexical_postings(&self, term: &str) -> Result<Vec<(u64, u32, u32)>> {
        let mut stmt = self.conn.prepare_cached(
            "SELECT p.symbol_id, p.tf, d.len
             FROM lexical_postings p JOIN lexical_docs d ON d.symbol_id = p.symbol_id
             WHERE p.term = ?1",
        )?;
        let rows = stmt.query_map([term], |r| {
            Ok((r.get::<_, i64>(0)? as u64, r.get(1)?, r.get(2)?))
        })?;
        Ok(rows.collect::<std::result::Result<Vec<_>, _>>()?)
    }

    fn remove_files(&mut self, files: &[String]) -> Result<()> {
        // IMMEDIATE: acquire the write lock up front. A deferred
        // transaction that reads before it writes can hit SQLITE_BUSY on
        // the read->write lock upgrade, which busy_timeout does NOT retry
        // (observed directly by racing indexers against the same repo).
        let tx = self
            .conn
            .transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)?;
        // Cascades to `embeddings` and `symbol_relations`, in this
        // transaction. Two repo-wide anti-join sweeps used to run here
        // *after* the commit, on the documented belief that foreign keys
        // were unenforced; they were no-ops in every run OXIDE has ever
        // made, and they scanned both tables in full to achieve it.
        for f in files {
            tx.execute("DELETE FROM symbols WHERE file = ?1", [f])?;
            tx.execute("DELETE FROM files WHERE path = ?1", [f])?;
        }
        bump_generation(&tx)?;
        tx.commit()?;
        Ok(())
    }

    fn all_symbols(&self) -> Result<Vec<Symbol>> {
        self.load_corpus(false)
    }

    fn all_symbols_lean(&self) -> Result<Vec<Symbol>> {
        self.load_corpus(true)
    }

    fn symbol_count(&self) -> Result<usize> {
        Ok(self
            .conn
            .query_row("SELECT COUNT(*) FROM symbols", [], |r| r.get::<_, i64>(0))?
            as usize)
    }

    fn symbols_by_ids(&self, ids: &[u64]) -> Result<Vec<Symbol>> {
        if ids.is_empty() {
            return Ok(Vec::new());
        }
        // One prepared statement for any candidate count: the id list
        // travels as a JSON array and `json_each` turns it into an
        // ephemeral table the planner probes `symbols` by rowid from
        // (`tests/query_plans.rs` pins that this stays a rowid lookup and
        // never a scan). ids cross as the same `as i64` bit-cast every
        // other statement uses.
        let json = serde_json::to_string(&ids.iter().map(|&id| id as i64).collect::<Vec<_>>())?;
        let mut stmt = self.conn.prepare_cached(
            "SELECT file, qualified_name, name, kind, language, start_line, end_line,
                    content_hash, signature, imports_json, exported, parent, references_json
             FROM symbols WHERE id IN (SELECT value FROM json_each(?1))",
        )?;
        let rows = stmt.query_map([json], row_to_symbol)?;
        Ok(rows.collect::<std::result::Result<Vec<_>, _>>()?)
    }

    fn symbol_hash(&self, id: u64) -> Result<Option<u64>> {
        let mut stmt = self
            .conn
            .prepare("SELECT content_hash FROM symbols WHERE id = ?1")?;
        let mut rows = stmt.query([id as i64])?;
        Ok(rows
            .next()?
            .map(|r| r.get::<_, i64>(0).map(|v| v as u64))
            .transpose()?)
    }

    fn put_embedding(&mut self, symbol_id: u64, vec: &[f32]) -> Result<()> {
        let Some(chash) = self.symbol_hash(symbol_id)? else {
            return Ok(());
        };
        let bytes: Vec<u8> = vec.iter().flat_map(|f| f.to_le_bytes()).collect();
        let tx = self
            .conn
            .transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)?;
        tx.execute(
            "INSERT OR REPLACE INTO embeddings(symbol_id, content_hash, dim, vec)
             VALUES(?1,?2,?3,?4)",
            rusqlite::params![symbol_id as i64, chash as i64, vec.len() as i32, bytes],
        )?;
        bump_generation(&tx)?;
        tx.commit()?;
        Ok(())
    }

    fn put_embeddings_batch(
        &mut self,
        expected_space: &str,
        items: &[(u64, Vec<f32>)],
    ) -> Result<()> {
        let tx = self
            .conn
            .transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)?;
        ensure_migration_marker(&tx, expected_space)?;
        {
            let mut hash_stmt = tx.prepare("SELECT content_hash FROM symbols WHERE id = ?1")?;
            let mut ins = tx.prepare(
                "INSERT OR REPLACE INTO embeddings(symbol_id, content_hash, dim, vec)
                 VALUES(?1,?2,?3,?4)",
            )?;
            for (symbol_id, vec) in items {
                let chash: Option<i64> = hash_stmt
                    .query_row([*symbol_id as i64], |r| r.get(0))
                    .optional()?;
                let Some(chash) = chash else { continue };
                let bytes: Vec<u8> = vec.iter().flat_map(|f| f.to_le_bytes()).collect();
                ins.execute(rusqlite::params![
                    *symbol_id as i64,
                    chash,
                    vec.len() as i32,
                    bytes
                ])?;
            }
        }
        bump_generation(&tx)?;
        tx.commit()?;
        Ok(())
    }

    fn embedding_with_hash(&self, symbol_id: u64) -> Result<Option<(u64, Vec<f32>)>> {
        let mut stmt = self
            .conn
            .prepare("SELECT content_hash, dim, vec FROM embeddings WHERE symbol_id = ?1")?;
        let mut rows = stmt.query([symbol_id as i64])?;
        let Some(row) = rows.next()? else {
            return Ok(None);
        };
        let chash: u64 = row.get::<_, i64>(0)? as u64;
        let dim: i32 = row.get(1)?;
        let bytes: Vec<u8> = row.get(2)?;
        let floats: Vec<f32> = bytes
            .as_chunks::<4>()
            .0
            .iter()
            .map(|c| f32::from_le_bytes(*c))
            .take(dim as usize)
            .collect();
        Ok(Some((chash, floats)))
    }

    fn all_embeddings(&self) -> Result<HashMap<u64, (u64, Vec<f32>)>> {
        let mut stmt = self
            .conn
            .prepare("SELECT symbol_id, content_hash, dim, vec FROM embeddings")?;
        let rows = stmt.query_map([], |r| {
            Ok((
                r.get::<_, i64>(0)? as u64,
                r.get::<_, i64>(1)? as u64,
                r.get::<_, i32>(2)?,
                r.get::<_, Vec<u8>>(3)?,
            ))
        })?;
        let mut out = HashMap::new();
        for row in rows {
            let (id, chash, dim, bytes) = row?;
            let floats: Vec<f32> = bytes
                .as_chunks::<4>()
                .0
                .iter()
                .map(|c| f32::from_le_bytes(*c))
                .take(dim as usize)
                .collect();
            out.insert(id, (chash, floats));
        }
        Ok(out)
    }

    fn for_each_embedding(&self, visit: &mut dyn FnMut(u64, usize, &[u8])) -> Result<()> {
        let mut stmt = self
            .conn
            .prepare_cached("SELECT symbol_id, dim, vec FROM embeddings")?;
        let mut rows = stmt.query([])?;
        while let Some(row) = rows.next()? {
            let id = row.get::<_, i64>(0)? as u64;
            let dim = row.get::<_, i32>(1)?.max(0) as usize;
            // `as_blob` borrows the row buffer; nothing is copied per row.
            let bytes = row.get_ref(2)?.as_blob()?;
            visit(id, dim, bytes);
        }
        Ok(())
    }

    fn begin_embedding_migration(&mut self, fingerprint_json: &str) -> Result<()> {
        let tx = self
            .conn
            .transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)?;
        tx.execute("DELETE FROM embeddings", [])?;
        tx.execute(
            "INSERT INTO meta(key,value) VALUES(?1,?2) ON CONFLICT(key) DO UPDATE SET value=?2",
            [EMBEDDING_MIGRATION_KEY, fingerprint_json],
        )?;
        bump_generation(&tx)?;
        tx.commit()?;
        Ok(())
    }

    fn put_symbol_relations_batch(
        &mut self,
        relations: &[(u64, Vec<String>, Vec<String>)],
    ) -> Result<()> {
        let tx = self.conn.transaction()?;
        {
            let mut del = tx.prepare("DELETE FROM symbol_relations WHERE symbol_id = ?1")?;
            let mut ins = tx.prepare(
                "INSERT INTO symbol_relations (symbol_id, kind, target) VALUES (?1, ?2, ?3)",
            )?;
            for (symbol_id, calls, bases) in relations {
                del.execute([*symbol_id as i64])?;
                for target in calls {
                    ins.execute(rusqlite::params![*symbol_id as i64, "calls", target])?;
                }
                for target in bases {
                    ins.execute(rusqlite::params![*symbol_id as i64, "bases", target])?;
                }
            }
        }
        bump_generation(&tx)?;
        tx.commit()?;
        Ok(())
    }

    fn begin_bulk_writes(&mut self) -> Result<()> {
        self.conn.execute_batch(&format!(
            "PRAGMA wal_autocheckpoint = {BULK_WAL_AUTOCHECKPOINT_PAGES};"
        ))?;
        Ok(())
    }

    fn end_bulk_writes(&mut self) -> Result<()> {
        self.conn.execute_batch(&format!(
            "PRAGMA wal_autocheckpoint = {DEFAULT_WAL_AUTOCHECKPOINT_PAGES};"
        ))?;
        Ok(())
    }

    fn all_symbol_relations(&self) -> Result<SymbolRelations> {
        let mut stmt = self
            .conn
            .prepare("SELECT symbol_id, kind, target FROM symbol_relations")?;
        let mut rows = stmt.query([])?;
        let mut out: HashMap<u64, (Vec<String>, Vec<String>)> = HashMap::new();
        while let Some(r) = rows.next()? {
            let id = r.get::<_, i64>(0)? as u64;
            // `kind` is only matched on, never kept: borrow it off the row.
            let entry = out.entry(id).or_default();
            match r.get_ref(1)?.as_str()? {
                "calls" => entry.0.push(r.get(2)?),
                "bases" => entry.1.push(r.get(2)?),
                _ => {}
            }
        }
        Ok(out)
    }
}

impl SqliteStore {
    pub fn stats(&self) -> Result<IndexStats> {
        let q = |sql: &str| -> Result<usize> {
            Ok(self.conn.query_row(sql, [], |r| r.get::<_, i64>(0))? as usize)
        };
        Ok(IndexStats {
            files: q("SELECT COUNT(*) FROM files")?,
            symbols: q("SELECT COUNT(*) FROM symbols")?,
            embeddings: q("SELECT COUNT(*) FROM embeddings")?,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::symbols::Language;

    /// Foreign-key audit. OXIDE's `replace_file`/`remove_files` delete only
    /// from `symbols` and depend on `ON DELETE CASCADE` to take the
    /// dependent rows with them, so enforcement being ON is a correctness
    /// requirement, not a preference.
    ///
    /// It used to hold by accident: SQLite's own default is OFF, and the
    /// only reason it was ON is that `libsqlite3-sys` compiles its bundled
    /// amalgamation with `-DSQLITE_DEFAULT_FOREIGN_KEYS=1`. `open` now sets
    /// it explicitly; this test fails if that stops being true, whether
    /// because the pragma was dropped or because a dependency bump changed
    /// the compiled-in default under it.
    #[test]
    fn foreign_keys_are_enforced_so_cascades_actually_fire() {
        let mut store = SqliteStore::open(Path::new(":memory:")).unwrap();
        let on: i64 = store
            .conn
            .query_row("PRAGMA foreign_keys", [], |r| r.get(0))
            .unwrap();
        assert_eq!(
            on, 1,
            "foreign keys are OFF: replace_file/remove_files would silently \
             strand an embedding and a relation row per deleted symbol"
        );

        // A dependent row for a symbol that does not exist must be refused.
        // If this ever succeeds, enforcement is gone.
        let orphan = store.conn.execute(
            "INSERT INTO symbol_relations(symbol_id, kind, target) VALUES(?1,'calls','x')",
            [9_999_999_i64],
        );
        assert!(
            orphan.is_err(),
            "insert of a relation row with no parent symbol was accepted"
        );

        // And the cascade itself: delete the parent, both children follow.
        let sym = Symbol {
            qualified_name: "a.f".into(),
            name: "f".into(),
            kind: crate::symbols::SymbolKind::Function,
            language: Language::Python,
            file: "a.py".into(),
            start_line: 1,
            end_line: 2,
            content_hash: 7,
            signature: "def f()".into(),
            imports: vec![],
            exported: true,
            parent: None,
            references: vec![],
            calls: vec![],
            bases: vec![],
            completeness: Default::default(),
        };
        let id = sym.id();
        store
            .replace_file("a.py", 1, &[sym], &[(id, vec!["g".into()], vec![])], &[])
            .unwrap();
        store.put_embedding(id, &[0.5, 0.5]).unwrap();
        assert_eq!(store.all_embeddings().unwrap().len(), 1);
        assert_eq!(store.all_symbol_relations().unwrap().len(), 1);

        store.remove_files(&["a.py".to_string()]).unwrap();
        assert!(
            store.all_embeddings().unwrap().is_empty(),
            "embedding outlived its symbol"
        );
        assert!(
            store.all_symbol_relations().unwrap().is_empty(),
            "relation outlived its symbol"
        );
    }
}
