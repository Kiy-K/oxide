//! SQLite-backed persistent storage for OXIDE symbols, metadata, vectors, and relations.

use crate::symbols::{Language, Symbol};
use anyhow::{Context, Result};
use rusqlite::{Connection, OptionalExtension};
use serde::Serialize;
use std::collections::{HashMap, HashSet};
use std::path::Path;

/// SQLite table/column layout version. Bump when the physical schema changes
/// in a way that is not purely additive (existing `CREATE TABLE IF NOT
/// EXISTS` statements would not pick up the change on their own).
pub const SCHEMA_VERSION: u32 = 1;
/// Symbol-extraction semantics version: id composition, hashing, or which
/// fields feed comparisons. Bump when a change would make an old index's
/// stored symbols not directly comparable to freshly-parsed ones.
pub const EXTRACTION_VERSION: u32 = 1;

/// Meta key holding the in-flight embedding-space fingerprint while a
/// provider migration is running. Non-empty means "the vectors in this index
/// belong to *this* fingerprint, and the published identity metadata has not
/// caught up yet" — see [`IndexBackend::begin_embedding_migration`]. Cleared
/// (set to the empty string, matching the `filter(|s| !s.is_empty())` idiom
/// used for every other optional meta value) in the same atomic
/// `set_meta_all` that publishes the completed identity.
pub const EMBEDDING_MIGRATION_KEY: &str = "embedding_migration";

/// Storage abstraction. Small by design: swap SQLite for something else by
/// implementing this trait.
/// One parsed file: (repo-relative path, content hash, source text, symbols).
pub struct ParsedFile {
    pub file: String,
    pub hash: u64,
    pub src: String,
    pub symbols: Vec<Symbol>,
}

/// Per symbol id: `(calls, bases)`, the precomputed-relations side-table
/// shape (`IndexBackend::all_symbol_relations`).
pub type SymbolRelations = HashMap<u64, (Vec<String>, Vec<String>)>;

pub trait IndexBackend {
    fn get_meta(&self, key: &str) -> Result<Option<String>>;
    fn set_meta(&mut self, key: &str, value: &str) -> Result<()>;
    /// Set several meta keys as one atomic transaction: either all of them
    /// land or none do. `update_index` uses this for its closing
    /// root/embedder/dim/schema_version/extraction_version writes so a
    /// process interrupted mid-write can never leave a torn subset behind —
    /// `validate_index`'s "index predates version tracking" fallback for a
    /// missing `schema_version` key would otherwise treat that torn state
    /// as a compatible legacy index instead of an incomplete one.
    ///
    /// `expected_space` is the value [`EMBEDDING_MIGRATION_KEY`] must still
    /// hold for this publication to be honest — see
    /// [`IndexBackend::put_embeddings_batch`] for what that guard buys and
    /// what it does not.
    fn set_meta_all(&mut self, expected_space: &str, pairs: &[(&str, &str)]) -> Result<()>;
    fn file_hashes(&self) -> Result<HashMap<String, u64>>;
    /// Replaces `file`'s symbols (and, since a symbol whose body didn't
    /// change keeps its embedding across the rewrite, its embeddings) and
    /// its precomputed relations, as **one** transaction. Relations were
    /// briefly a separate `put_symbol_relations_batch` call issued right
    /// after this one from `update_index` — a process interrupted between
    /// the two left `symbols`/`files.content_hash` already updated to the
    /// new content while `symbol_relations` still held the old, wrong
    /// values, and because content_hash already matched, no future run
    /// would ever reparse that file to fix it (`tests/interrupted_index_recovery.rs`
    /// pins the general class of bug this pattern already guards against
    /// for symbols+embeddings; this closes the same class for relations).
    /// `relations` is typically `structural_relations::compute_file_relations`'s
    /// output for `symbols`; pass `&[]` when the caller has no relations to
    /// write (every non-`update_index` test call site).
    fn replace_file(
        &mut self,
        file: &str,
        hash: u64,
        symbols: &[Symbol],
        relations: &[(u64, Vec<String>, Vec<String>)],
    ) -> Result<()>;
    fn remove_files(&mut self, files: &[String]) -> Result<()>;
    fn all_symbols(&self) -> Result<Vec<Symbol>>;
    fn symbol_hash(&self, id: u64) -> Result<Option<u64>>;
    fn put_embedding(&mut self, symbol_id: u64, vec: &[f32]) -> Result<()>;
    /// Same effect as calling [`Self::put_embedding`] once per item, but as
    /// one transaction instead of one autocommit per row — profiling the
    /// embedding stage found the per-symbol `execute()` calls (each an
    /// implicit transaction under SQLite's default autocommit behavior,
    /// each paying its own fsync) were a real, avoidable cost independent
    /// of embedder latency, unlike the batch/thread-chunking around it
    /// (already near the empirically-measured optimum — see
    /// docs/indexing-rebuild-scopes/README.md). A symbol id with no
    /// matching row in `symbols` is skipped, same as `put_embedding`.
    ///
    /// Fails, in the same transaction, unless [`EMBEDDING_MIGRATION_KEY`]
    /// still holds `expected_space` — the writer's own fingerprint while a
    /// migration is in flight, or `""` for an ordinary incremental run.
    /// Without this, `begin_embedding_migration`'s atomicity only holds for
    /// one process: a second `oxide index` starting its own migration
    /// mid-run would empty the table under the first, which would then keep
    /// appending its vectors alongside the second's and one of them would
    /// publish a single identity over the mix. With it, the loser fails
    /// loudly and the table only ever holds the last migration's rows.
    ///
    /// What this does **not** close: a run whose compatibility check
    /// happened *before* another run's migration completed, and whose first
    /// write lands after it — the marker is legitimately `""` at both
    /// moments, so the guard cannot see the difference. Closing that needs
    /// run-level writer serialization (one transaction spanning the whole
    /// embedding phase, or an advisory lock), which is deliberately out of
    /// scope: embedding takes minutes, and holding SQLite's write lock that
    /// long would block every other writer and grow the WAL without bound.
    fn put_embeddings_batch(
        &mut self,
        expected_space: &str,
        items: &[(u64, Vec<f32>)],
    ) -> Result<()>;
    fn embedding_with_hash(&self, symbol_id: u64) -> Result<Option<(u64, Vec<f32>)>>;
    /// All embeddings in one shot (avoids per-symbol queries in retrieval).
    fn all_embeddings(&self) -> Result<HashMap<u64, (u64, Vec<f32>)>>;
    /// Clear every vector AND record `fingerprint_json` under
    /// [`EMBEDDING_MIGRATION_KEY`] as **one** transaction — the crash-safety
    /// primitive for a provider switch, and deliberately the *only* way to
    /// empty the embeddings table. A plain `clear_embeddings` used to sit
    /// beside it; it was removed rather than left available, because an
    /// unmarked clear is precisely the state this method exists to make
    /// unreachable.
    ///
    /// `update_embeddings` publishes the new provider identity only at the
    /// very end (`set_meta_all`), so a process killed after the replacement
    /// vectors commit but before that write used to leave rows from provider
    /// B under metadata naming provider A. Nothing downstream could tell:
    /// row counts matched, and a same-dimension switch scored queries
    /// against the wrong vector space rather than failing loudly.
    ///
    /// The marker closes that window because the atomicity runs the right
    /// way round: the marker is never present unless the table was emptied
    /// in the same transaction that set it, so "marker == the provider I am
    /// about to use" proves every surviving row belongs to that provider's
    /// space. Setting the marker *before* clearing (two statements) would
    /// prove nothing — a crash between them leaves the old provider's rows
    /// under a marker claiming the new one, which is the original bug with
    /// extra steps.
    ///
    /// This alone only proves it within one process. Extending it across
    /// concurrent `oxide index` runs is [`IndexBackend::put_embeddings_batch`]'s
    /// and [`IndexBackend::set_meta_all`]'s `expected_space` guard, which
    /// re-reads the marker inside each write's own transaction; read that
    /// method's doc for the interleaving it closes and the one it does not.
    fn begin_embedding_migration(&mut self, fingerprint_json: &str) -> Result<()>;
    fn drop_embeddings_without_symbols(&mut self) -> Result<()>;
    /// Replaces precomputed call/base relations for every `(symbol_id,
    /// calls, bases)` triple in `relations`, as one transaction per call —
    /// `update_index` calls this once per reparsed file
    /// (`structural_relations::compute_file_relations`), so one transaction
    /// per file, not per symbol (an earlier per-symbol-transaction version
    /// left a file only partially updated if interrupted mid-run; per-file
    /// batching narrows that window — full run-level atomicity would need
    /// a completion marker, not added here). `calls`/`bases` are bare
    /// names, same heuristic tier as `Symbol::references`. Every entry is
    /// written even when both are empty — that's what clears a symbol's
    /// stale relations after an edit removes its last call/base; see
    /// `compute_file_relations`'s doc comment.
    fn put_symbol_relations_batch(
        &mut self,
        relations: &[(u64, Vec<String>, Vec<String>)],
    ) -> Result<()>;
    /// All precomputed relations, keyed by symbol id, as `(calls, bases)`.
    /// Empty/absent for any symbol with no calls/bases at all. Read by
    /// `structural_relations::load_symbols_with_relations`, which
    /// `context.rs::build_context` uses instead of calling this crate's
    /// `all_symbols` directly whenever `RelationGraph::callers_of`/
    /// `implementors_of` are needed.
    fn all_symbol_relations(&self) -> Result<SymbolRelations>;
}

pub struct SqliteStore {
    conn: Connection,
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

const SCHEMA_SQL: &str = r#"
    PRAGMA journal_mode = WAL;
    CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS files(
        path TEXT PRIMARY KEY,
        content_hash INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS symbols(
        id INTEGER PRIMARY KEY,
        file TEXT NOT NULL,
        qualified_name TEXT NOT NULL,
        name TEXT NOT NULL,
        kind TEXT NOT NULL,
        language TEXT NOT NULL,
        start_line INTEGER NOT NULL,
        end_line INTEGER NOT NULL,
        content_hash INTEGER NOT NULL,
        signature TEXT NOT NULL,
        imports_json TEXT NOT NULL,
        exported INTEGER NOT NULL,
        parent TEXT,
        references_json TEXT NOT NULL DEFAULT '[]'
    );
    CREATE INDEX IF NOT EXISTS idx_symbols_file ON symbols(file);
    CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(name);
    CREATE TABLE IF NOT EXISTS embeddings(
        symbol_id INTEGER PRIMARY KEY REFERENCES symbols(id) ON DELETE CASCADE,
        content_hash INTEGER NOT NULL,
        dim INTEGER NOT NULL,
        vec BLOB NOT NULL
    );
    -- Precomputed AST-precise call/base relations (structural_relations.rs),
    -- one row per (symbol, target). Populated by update_index itself, one
    -- reparsed file at a time. A side table, not new columns on `symbols`
    -- — `CREATE TABLE IF NOT EXISTS` is a no-op against an already-created
    -- `symbols` table on an existing on-disk index.db, so new columns
    -- there would never appear on an upgrade; a brand-new table name is
    -- picked up cleanly by the same `IF NOT EXISTS` on any existing
    -- database, no SCHEMA_VERSION bump needed.
    CREATE TABLE IF NOT EXISTS symbol_relations(
        symbol_id INTEGER NOT NULL REFERENCES symbols(id) ON DELETE CASCADE,
        kind TEXT NOT NULL,
        target TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_symbol_relations_symbol_id ON symbol_relations(symbol_id);
"#;

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

impl SqliteStore {
    pub fn open(path: &Path) -> Result<Self> {
        if let Some(dir) = path.parent() {
            std::fs::create_dir_all(dir)?;
        }
        let conn =
            Connection::open(path).with_context(|| format!("open index at {}", path.display()))?;
        conn.busy_timeout(std::time::Duration::from_secs(5))?;
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
        self.conn.execute(
            "INSERT INTO meta(key,value) VALUES(?1,?2) ON CONFLICT(key) DO UPDATE SET value=?2",
            [key, value],
        )?;
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
        // symbols/content_hash, not as a follow-up call.
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
        tx.commit()?;
        Ok(())
    }

    fn remove_files(&mut self, files: &[String]) -> Result<()> {
        // IMMEDIATE: acquire the write lock up front. A deferred
        // transaction that reads before it writes can hit SQLITE_BUSY on
        // the read->write lock upgrade, which busy_timeout does NOT retry
        // (observed directly by racing indexers against the same repo).
        let tx = self
            .conn
            .transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)?;
        for f in files {
            tx.execute("DELETE FROM symbols WHERE file = ?1", [f])?;
            tx.execute("DELETE FROM files WHERE path = ?1", [f])?;
        }
        tx.commit()?;
        self.drop_embeddings_without_symbols()?;
        // Same orphan-sweep shape as embeddings above (foreign keys aren't
        // enforced — `PRAGMA foreign_keys` is never turned on in this
        // codebase — so `symbol_relations`'s `ON DELETE CASCADE` is
        // declarative only): a symbol_relations row for a symbol deleted by
        // this call, or by an earlier `replace_file` rename-within-file,
        // becomes an orphan until this sweep runs. Matches the existing,
        // accepted embeddings behavior exactly rather than holding this one
        // table to a stricter standard.
        self.conn.execute(
            "DELETE FROM symbol_relations WHERE symbol_id NOT IN (SELECT id FROM symbols)",
            [],
        )?;
        Ok(())
    }

    fn all_symbols(&self) -> Result<Vec<Symbol>> {
        let mut stmt = self.conn.prepare(
            "SELECT file, qualified_name, name, kind, language, start_line, end_line,
                    content_hash, signature, imports_json, exported, parent, references_json
             FROM symbols ORDER BY file, start_line",
        )?;
        let rows = stmt.query_map([], row_to_symbol)?;
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
        self.conn.execute(
            "INSERT OR REPLACE INTO embeddings(symbol_id, content_hash, dim, vec)
             VALUES(?1,?2,?3,?4)",
            rusqlite::params![symbol_id as i64, chash as i64, vec.len() as i32, bytes],
        )?;
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

    fn begin_embedding_migration(&mut self, fingerprint_json: &str) -> Result<()> {
        let tx = self
            .conn
            .transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)?;
        tx.execute("DELETE FROM embeddings", [])?;
        tx.execute(
            "INSERT INTO meta(key,value) VALUES(?1,?2) ON CONFLICT(key) DO UPDATE SET value=?2",
            [EMBEDDING_MIGRATION_KEY, fingerprint_json],
        )?;
        tx.commit()?;
        Ok(())
    }

    fn drop_embeddings_without_symbols(&mut self) -> Result<()> {
        self.conn.execute(
            "DELETE FROM embeddings WHERE symbol_id NOT IN (SELECT id FROM symbols)",
            [],
        )?;
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
        tx.commit()?;
        Ok(())
    }

    fn all_symbol_relations(&self) -> Result<SymbolRelations> {
        let mut stmt = self
            .conn
            .prepare("SELECT symbol_id, kind, target FROM symbol_relations")?;
        let rows = stmt.query_map([], |r| {
            Ok((
                r.get::<_, i64>(0)? as u64,
                r.get::<_, String>(1)?,
                r.get::<_, String>(2)?,
            ))
        })?;
        let mut out: HashMap<u64, (Vec<String>, Vec<String>)> = HashMap::new();
        for row in rows {
            let (id, kind, target) = row?;
            let entry = out.entry(id).or_default();
            match kind.as_str() {
                "calls" => entry.0.push(target),
                "bases" => entry.1.push(target),
                _ => {}
            }
        }
        Ok(out)
    }
}

fn row_to_symbol(r: &rusqlite::Row<'_>) -> rusqlite::Result<Symbol> {
    Ok(Symbol {
        file: r.get(0)?,
        qualified_name: r.get(1)?,
        name: r.get(2)?,
        kind: r
            .get::<_, String>(3)?
            .parse()
            .unwrap_or(crate::symbols::SymbolKind::Function),
        language: match r.get::<_, String>(4)?.as_str() {
            "python" => Language::Python,
            "tsx" => Language::Tsx,
            _ => Language::TypeScript,
        },
        start_line: r.get(5)?,
        end_line: r.get(6)?,
        content_hash: r.get::<_, i64>(7)? as u64,
        signature: r.get(8)?,
        imports: serde_json::from_str(&r.get::<_, String>(9)?).unwrap_or_default(),
        exported: r.get::<_, i64>(10)? != 0,
        parent: r.get(11)?,
        references: serde_json::from_str(&r.get::<_, String>(12)?).unwrap_or_default(),
        // Not columns on `symbols` — populated separately by
        // `structural_relations::load_symbols_with_relations` from the
        // side table `symbol_relations`, never by this loader.
        calls: Vec::new(),
        bases: Vec::new(),
    })
}

/// Index statistics for `oxide stats`.
#[derive(Debug, Serialize)]
pub struct IndexStats {
    pub files: usize,
    pub symbols: usize,
    pub embeddings: usize,
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
