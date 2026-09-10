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
///
/// A bump alone is not enough, and must be paired with the forced reparse in
/// `update_base` — `validate_index` refuses to serve a mismatched index, but
/// plain `oxide index` compares source hashes, so an unchanged file would
/// never be revisited and the version would be republished over stale rows.
///
/// 2: decorated definitions span their decorators (spans, `content_hash`,
/// `signature` all move), base clauses capture qualified/generic names, JSX
/// element usage counts as a call, and `mod`/`namespace` blocks qualify
/// their members.
pub const EXTRACTION_VERSION: u32 = 2;

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
        postings: &[crate::lexical::DocPostings],
    ) -> Result<()>;
    fn remove_files(&mut self, files: &[String]) -> Result<()>;
    /// `(document count, summed document length)` over the persisted lexical
    /// index — BM25's `avg_len` denominator and numerator. Summed in SQL as
    /// an integer rather than by folding `f32` document lengths in hash-map
    /// order, which is what the in-memory index does; the two agree exactly
    /// while the total stays under `f32`'s 2^24 integer limit, and past it
    /// the SQL sum is the one that stays deterministic across processes.
    /// Rewrite lexical postings for symbols whose rows are missing or stale
    /// while the symbols themselves are unchanged — the backfill path for an
    /// index that predates the persisted lexical tables, or whose earlier
    /// backfill was interrupted.
    ///
    /// Separate from [`Self::replace_file`] because it must not touch
    /// `symbols`: reparsing an unchanged file to repair a derived table
    /// would be wasted work and would misreport `reparsed_files`. Safe
    /// outside `replace_file`'s transaction only because the symbols are not
    /// moving underneath it, and because an interrupted backfill leaves
    /// [`LEXICAL_INDEX_KEY`] unpublished, which makes the whole persisted
    /// lexical index unreadable rather than partially trusted.
    /// Returns `false` without writing if `file`'s stored `content_hash` no
    /// longer equals `expected_hash` — another process replaced that file
    /// between this run's scan and this write, and the caller's postings
    /// describe the older revision.
    fn put_file_lexical(
        &mut self,
        file: &str,
        expected_hash: u64,
        postings: &[crate::lexical::DocPostings],
    ) -> Result<bool>;
    fn lexical_totals(&self) -> Result<(usize, i64)>;
    /// Postings for one query term: `(symbol_id, weighted tf, document
    /// length)`, one row per matching document.
    fn lexical_postings(&self, term: &str) -> Result<Vec<(u64, u32, u32)>>;
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
    -- The `ON DELETE CASCADE` clauses below are load-bearing, not
    -- decorative: `replace_file` and `remove_files` delete only from
    -- `symbols` and rely on both dependent tables following. `open` turns
    -- foreign-key enforcement on explicitly for that reason.
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
    -- Persisted BM25 postings (lexical.rs). One row per (symbol, term) with
    -- the weighted term frequency, and one `lexical_docs` row per symbol —
    -- for EVERY symbol, including those that produce no terms, so BM25's
    -- length normalization never silently falls back to the corpus average.
    --
    -- `WITHOUT ROWID` with the primary key in (term, symbol_id) order makes
    -- a query-term lookup a covering range scan: term, symbol_id and tf all
    -- live in the one b-tree, so scoring never touches a second structure.
    -- The secondary index on symbol_id is what the `ON DELETE CASCADE` uses;
    -- without it, deleting one symbol would scan the whole postings table,
    -- and `replace_file` deletes every symbol in a file.
    CREATE TABLE IF NOT EXISTS lexical_postings(
        term TEXT NOT NULL,
        symbol_id INTEGER NOT NULL REFERENCES symbols(id) ON DELETE CASCADE,
        tf INTEGER NOT NULL,
        PRIMARY KEY(term, symbol_id)
    ) WITHOUT ROWID;
    CREATE INDEX IF NOT EXISTS idx_lexical_postings_symbol ON lexical_postings(symbol_id);
    CREATE TABLE IF NOT EXISTS lexical_docs(
        symbol_id INTEGER PRIMARY KEY REFERENCES symbols(id) ON DELETE CASCADE,
        len INTEGER NOT NULL
    );
"#;

/// Format generation of the persisted lexical index. The value stored under
/// [`LEXICAL_INDEX_KEY`] when, and only when, a full-corpus base pass has
/// finished writing postings for every symbol. Bump when the tokenizer,
/// field weights, or table layout change, so an index built by an older
/// binary is rebuilt rather than scored under new rules.
pub const LEXICAL_INDEX_VERSION: u32 = 1;

/// Meta key carrying [`LEXICAL_INDEX_VERSION`] for a **complete** persisted
/// lexical index.
///
/// Absence is the in-flight state, and that is the whole design: the tables
/// existing, or even holding rows, proves nothing about whether every symbol
/// is covered. An index upgraded from a build that predates them starts with
/// zero rows; a backfill interrupted halfway leaves some files covered and
/// some not, and the covered files' `content_hash` values already match, so
/// no later incremental run would ever revisit them. Publishing this key
/// only at the end of a full pass — and treating any other value, including
/// none, as "not usable, rebuild it" — makes a partial index unreadable
/// instead of quietly wrong. Same shape as [`EMBEDDING_MIGRATION_KEY`], and
/// the same lesson as `schema_version`: a torn write must not be able to
/// present itself as a healthy index.
pub const LEXICAL_INDEX_KEY: &str = "lexical_index_version";

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

impl SqliteStore {
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
        tx.commit()?;
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
        // Parsed via `Language::ALL`, not a second hand-written match —
        // see `Language::from_str`. The fallback only covers a value this
        // build has no variant for at all.
        language: r
            .get::<_, String>(4)?
            .parse()
            .unwrap_or(Language::TypeScript),
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

#[cfg(test)]
mod tests {
    use super::*;

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
