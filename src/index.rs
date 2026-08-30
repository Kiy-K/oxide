//! Persistent index: SQLite-backed storage plus the incremental indexing
//! pipeline (file-hash short-circuit, per-symbol re-embed avoidance).

use crate::scanner;
use crate::symbols::{Language, Symbol};
use anyhow::{Context, Result};
use rusqlite::Connection;
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

/// Storage abstraction. Small by design: swap SQLite for something else by
/// implementing this trait.
/// One parsed file: (repo-relative path, content hash, source text, symbols).
pub struct ParsedFile {
    pub file: String,
    pub hash: u64,
    pub src: String,
    pub symbols: Vec<Symbol>,
}

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
    fn set_meta_all(&mut self, pairs: &[(&str, &str)]) -> Result<()>;
    fn file_hashes(&self) -> Result<HashMap<String, u64>>;
    fn replace_file(&mut self, file: &str, hash: u64, symbols: &[Symbol]) -> Result<()>;
    fn remove_files(&mut self, files: &[String]) -> Result<()>;
    fn all_symbols(&self) -> Result<Vec<Symbol>>;
    fn symbol_hash(&self, id: u64) -> Result<Option<u64>>;
    fn put_embedding(&mut self, symbol_id: u64, vec: &[f32]) -> Result<()>;
    fn embedding_with_hash(&self, symbol_id: u64) -> Result<Option<(u64, Vec<f32>)>>;
    /// All embeddings in one shot (avoids per-symbol queries in retrieval).
    fn all_embeddings(&self) -> Result<HashMap<u64, (u64, Vec<f32>)>>;
    /// Drop every vector (used when the embedding provider changed: vectors
    /// from different models are not comparable).
    fn clear_embeddings(&mut self) -> Result<()>;
    fn drop_embeddings_without_symbols(&mut self) -> Result<()>;
}

pub struct SqliteStore {
    conn: Connection,
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

    fn set_meta_all(&mut self, pairs: &[(&str, &str)]) -> Result<()> {
        let tx = self
            .conn
            .transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)?;
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

    fn replace_file(&mut self, file: &str, hash: u64, symbols: &[Symbol]) -> Result<()> {
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

    fn clear_embeddings(&mut self) -> Result<()> {
        self.conn.execute("DELETE FROM embeddings", [])?;
        Ok(())
    }

    fn drop_embeddings_without_symbols(&mut self) -> Result<()> {
        self.conn.execute(
            "DELETE FROM embeddings WHERE symbol_id NOT IN (SELECT id FROM symbols)",
            [],
        )?;
        Ok(())
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

use serde::Serialize;

/// Outcome of one incremental run; surfaced by the CLI to show work avoided.
#[derive(Debug, Default, Serialize)]
pub struct IndexReport {
    pub scanned_files: usize,
    pub unchanged_files: usize,
    pub reparsed_files: usize,
    pub removed_files: usize,
    pub new_symbols: usize,
    pub changed_symbols: usize,
    pub deleted_symbols: usize,
    pub embedded_symbols: usize,
    pub reused_embeddings: usize,
    pub duration_ms: u128,
    /// Symbols whose embedding came back empty (endpoint failure): skipped,
    /// not stored, so a later healthy run re-embeds them.
    #[serde(default)]
    pub embed_failures: usize,
    /// Discovered files that could not be read/decoded (non-UTF8, IO error) or
    /// whose language resolution failed unexpectedly during parsing. Not
    /// stored; a later run retries them. Every discovered file must land in
    /// exactly one of unchanged_files + reparsed_files + errored_files.
    #[serde(default)]
    pub errored_files: usize,
}

/// Run incremental indexing of the repo at `root` into `store`.
pub fn update_index(
    root: &Path,
    store: &mut dyn IndexBackend,
    embedder: &dyn crate::embeddings::EmbeddingProvider,
) -> Result<IndexReport> {
    let started = std::time::Instant::now();
    let mut report = IndexReport::default();

    let files = scanner::scan_repo(root)?;
    report.scanned_files = files.len();

    // Single read per file: bytes → UTF-8 string → hash + parse reuse it.
    let mut current: HashMap<String, String> = HashMap::with_capacity(files.len());
    let mut unreadable_files: usize = 0;
    for p in &files {
        let rel = p.display().to_string();
        match std::fs::read_to_string(root.join(p)) {
            Ok(src) => {
                current.insert(rel, src);
            }
            Err(_) => unreadable_files += 1, // non-UTF8/IO error: accounted as errored below
        }
    }

    let stored = store.file_hashes()?;

    // One snapshot reused for deletions, change detection and name matching.
    let existing = store.all_symbols()?;
    let before_symbols: HashMap<u64, u64> =
        existing.iter().map(|s| (s.id(), s.content_hash)).collect();

    // Deletions and stale entries.
    let removed: Vec<String> = stored
        .keys()
        .filter(|f| !current.contains_key(*f))
        .cloned()
        .collect();
    if !removed.is_empty() {
        let doomed = existing
            .iter()
            .filter(|s| removed.contains(&s.file))
            .count();
        store.remove_files(&removed)?;
        report.deleted_symbols += doomed;
        report.removed_files = removed.len();
    }

    // Changed or new files.
    let to_parse: Vec<(&String, u64)> = current
        .iter()
        .map(|(f, src)| (f, crate::symbols::content_hash(src)))
        .filter(|(f, h)| stored.get(*f).copied() != Some(*h))
        .collect();
    // Unchanged is relative to files we could actually read; unreadable files
    // are accounted separately below so the totals never silently disagree
    // with `scanned_files`.
    report.unchanged_files = current.len() - to_parse.len();

    // Parse all changed files first so reference matching sees both old
    // definitions and everything added in this run. Keep the source alive for
    // reference extraction instead of re-reading from disk.
    // Parsing is pure CPU over independent files: fan out across a small
    // bounded pool (laptop-friendly cap) and collect in order.
    let workers = std::thread::available_parallelism()
        .map(|n| n.get())
        .unwrap_or(1)
        .clamp(1, 4);
    let chunk_size = to_parse.len().div_ceil(workers.max(1));
    let mut parsed: Vec<ParsedFile> = Vec::with_capacity(to_parse.len());
    let mut results: Vec<(Vec<ParsedFile>, usize)> = Vec::with_capacity(workers);
    std::thread::scope(|scope| {
        let mut handles = Vec::new();
        for (w, chunk) in to_parse.chunks(chunk_size.max(1)).enumerate() {
            let current = &current;
            results.push((Vec::new(), 0));
            handles.push(scope.spawn(move || {
                let mut out = Vec::with_capacity(chunk.len());
                // Files reaching here were already language-filtered by the
                // scanner; `unresolved` should stay 0 in practice, but a
                // future scanner bug must be counted, never silently dropped.
                let mut unresolved = 0usize;
                for (rel, hash) in chunk {
                    let lang = match scanner::language_for_path(Path::new(rel)) {
                        Some(l) => l,
                        None => {
                            unresolved += 1;
                            continue;
                        }
                    };
                    let src = &current[*rel];
                    let syms = crate::parser::parse_file(rel, src, lang);
                    out.push(ParsedFile {
                        file: (*rel).clone(),
                        hash: *hash,
                        src: src.clone(),
                        symbols: syms,
                    });
                }
                (w, out, unresolved)
            }));
        }
        for h in handles {
            let (w, out, unresolved) = h
                .join()
                .map_err(|_| anyhow::anyhow!("parse worker panicked"))?;
            results[w] = (out, unresolved);
        }
        Ok::<(), anyhow::Error>(())
    })?;
    let mut parse_unresolved: usize = 0;
    for (mut part, unresolved) in results {
        parsed.append(&mut part);
        parse_unresolved += unresolved;
    }
    report.reparsed_files = parsed.len();
    report.errored_files = unreadable_files + parse_unresolved;

    // Known bare definition names across the project (for reference matching).
    let mut known_names: HashSet<String> = existing
        .iter()
        .filter(|s| s.kind != crate::symbols::SymbolKind::Module)
        .map(|s| s.name.clone())
        .collect();
    for pf in &parsed {
        for s in &pf.symbols {
            if s.kind != crate::symbols::SymbolKind::Module {
                known_names.insert(s.name.clone());
            }
        }
    }
    // # ponytail: identifier-name intersection only; no scope analysis. Upgrade
    // path: per-language scoped resolution if false positives hurt retrieval.
    for pf in &mut parsed {
        // Whether this file's module symbol used the coarse "imports +
        // first line" hash (parser.rs) rather than the full-source hash:
        // parser.rs only takes the full-source path when there are no
        // concrete (non-Module) symbols at all — see `empty_before_module`
        // there. That full-source hash already changes on ANY body edit
        // (including comment-only edits with no declarations to anchor to,
        // where the module symbol is the file's only index representation)
        // and must be left alone; only the coarse formula needs the fix
        // below.
        let used_coarse_module_hash = pf
            .symbols
            .iter()
            .any(|s| s.kind != crate::symbols::SymbolKind::Module);
        for s in &mut pf.symbols {
            s.references = extract_references(s, &pf.src, &known_names);
            // The coarse module hash covers only imports + first line, but
            // its embedding input (embed_text) also includes `references`,
            // which are resolved here — one stage later, once whole-project
            // known names exist. A body-only edit that adds/removes an
            // in-file reference therefore changes embed_text without the
            // parser hash noticing. Recompute the module's content_hash as
            // the literal hash of its own embed_text now that references
            // are final, so the cache-invalidation key can never drift from
            // the actual embedding input (see AGENTS.md invariant) — but
            // only where the coarse formula was actually used.
            if s.kind == crate::symbols::SymbolKind::Module && used_coarse_module_hash {
                s.content_hash = crate::symbols::content_hash(&embed_text(s));
            }
        }
    }

    // `replace_file` deletes every existing symbol row for a changed file and
    // reinserts the freshly parsed set, so a symbol removed or renamed within
    // an otherwise-still-present file (not just a whole-file deletion) is a
    // real deletion too. Group the pre-edit snapshot by file so that delta is
    // counted, not just symbols new/changed_symbols above it.
    let mut existing_ids_by_file: HashMap<&str, HashSet<u64>> = HashMap::new();
    for s in &existing {
        existing_ids_by_file
            .entry(s.file.as_str())
            .or_default()
            .insert(s.id());
    }

    for pf in &parsed {
        let mut new_ids: HashSet<u64> = HashSet::with_capacity(pf.symbols.len());
        for s in &pf.symbols {
            new_ids.insert(s.id());
            match before_symbols.get(&s.id()) {
                None => report.new_symbols += 1,
                Some(old) if *old != s.content_hash => report.changed_symbols += 1,
                Some(_) => {}
            }
        }
        if let Some(old_ids) = existing_ids_by_file.get(pf.file.as_str()) {
            report.deleted_symbols += old_ids.difference(&new_ids).count();
        }
        store.replace_file(&pf.file, pf.hash, &pf.symbols)?;
    }

    // Vectors from a different vector space are not comparable: wipe them
    // once so everything below re-embeds under the current model. The
    // structured fingerprint (Phase 3.3 item 3) is the real contract when
    // both sides have one; the plain `embedder` name string is the fallback
    // for indices/providers that predate it, so upgrading OXIDE doesn't
    // force every existing index to reindex on the next run.
    let current_fp = embedder.fingerprint();
    let stored_fp: Option<Option<crate::embeddings::EmbeddingSpaceFingerprint>> = store
        .get_meta("embedding_fingerprint")?
        .filter(|s| !s.is_empty())
        .map(|s| serde_json::from_str(&s).ok());
    match stored_fp {
        // Both sides have a fingerprint: it alone decides. A stored value
        // that fails to parse (older/foreign schema) is conservatively
        // treated as incompatible rather than guessed at.
        Some(Some(prev)) if prev != current_fp => {
            eprintln!(
                "oxide: embedding space changed ({} -> {}); re-embedding all symbols",
                prev.model, current_fp.model
            );
            store.clear_embeddings()?;
        }
        Some(Some(_)) => {}
        Some(None) => {
            eprintln!(
                "oxide: stored embedding fingerprint is unreadable; re-embedding all symbols"
            );
            store.clear_embeddings()?;
        }
        // No fingerprint stored (legacy index): fall back to the name check
        // this codebase has always used.
        None => match store.get_meta("embedder")? {
            Some(prev) if !prev.is_empty() && prev != embedder.name() => {
                eprintln!(
                    "oxide: embedder changed ({} -> {}); re-embedding all symbols",
                    prev,
                    embedder.name()
                );
                store.clear_embeddings()?;
            }
            _ => {}
        },
    }

    // Embed only symbols whose embedding is missing or whose content changed;
    // everything else reuses its stored vector untouched. Vector computation
    // is pure CPU: fan out over the same bounded pool, write serially after.
    let embeddings = store.all_embeddings()?;
    let all = store.all_symbols()?;
    let to_embed: Vec<&Symbol> = all
        .iter()
        .filter(|s| match embeddings.get(&s.id()) {
            Some((old_hash, _)) if *old_hash == s.content_hash => {
                report.reused_embeddings += 1;
                false
            }
            _ => true,
        })
        .collect();
    let chunk_size = to_embed.len().div_ceil(workers.max(1));
    // Batched path: providers with batch endpoints (HTTP) get one request per
    // chunk; the thread pool stays useful for per-text providers.
    if to_embed.len() < 8 || std::env::var("OXIDE_EMBED_URL").is_ok() {
        for chunk in to_embed.chunks(64) {
            let texts: Vec<String> = chunk.iter().map(|s| embed_text(s)).collect();
            let vectors = embedder.embed_documents(&texts);
            for (s, vec) in chunk.iter().zip(vectors) {
                if vec.iter().all(|f| *f == 0.0) || vec.is_empty() {
                    report.embed_failures += 1;
                    continue;
                }
                store.put_embedding(s.id(), &vec)?;
                report.embedded_symbols += 1;
            }
        }
    } else {
        let computed: Vec<Vec<(u64, Vec<f32>)>> = std::thread::scope(|scope| {
            let mut handles = Vec::new();
            for chunk in to_embed.chunks(chunk_size.max(1)) {
                handles.push(scope.spawn(|| {
                    chunk
                        .iter()
                        .map(|s| (s.id(), embedder.embed_document(&embed_text(s))))
                        .collect::<Vec<_>>()
                }));
            }
            let mut out = Vec::new();
            for h in handles {
                out.push(
                    h.join()
                        .map_err(|_| anyhow::anyhow!("embed worker panicked"))?,
                );
            }
            Ok::<_, anyhow::Error>(out)
        })?;
        for part in computed {
            for (id, vec) in part {
                store.put_embedding(id, &vec)?;
                report.embedded_symbols += 1;
            }
        }
    }

    let root_str = root.display().to_string();
    let dim_str = embedder.dim().to_string();
    let schema_str = SCHEMA_VERSION.to_string();
    let extraction_str = EXTRACTION_VERSION.to_string();
    // current_fp was already computed above for the staleness check; reuse
    // it so the stored fingerprint reflects exactly what was just compared.
    let fingerprint_json = serde_json::to_string(&current_fp)?;
    store.set_meta_all(&[
        ("root", root_str.as_str()),
        ("embedder", embedder.name()),
        ("dim", dim_str.as_str()),
        ("schema_version", schema_str.as_str()),
        ("extraction_version", extraction_str.as_str()),
        ("embedding_fingerprint", fingerprint_json.as_str()),
    ])?;
    report.duration_ms = started.elapsed().as_millis();

    // Every discovered file must land in exactly one accounted state; a
    // mismatch means a file silently vanished somewhere in the pipeline.
    anyhow::ensure!(
        report.scanned_files == report.unchanged_files + report.reparsed_files + report.errored_files,
        "index accounting invariant violated: scanned {} != unchanged {} + reparsed {} + errored {}",
        report.scanned_files,
        report.unchanged_files,
        report.reparsed_files,
        report.errored_files
    );
    Ok(report)
}

/// References = identifiers appearing in the symbol body that match a known
/// project definition name (excluding the symbol itself).
fn extract_references(s: &Symbol, src: &str, known: &HashSet<String>) -> Vec<String> {
    let body: String = src
        .lines()
        .skip(s.start_line.saturating_sub(1) as usize)
        .take(s.end_line.saturating_sub(s.start_line - 1) as usize + 1)
        .collect::<Vec<_>>()
        .join("\n");
    let mut refs: HashSet<String> = HashSet::new();
    for tok in crate::embeddings::tokenize(&body) {
        // tokenize splits camelCase; match on both raw and joined forms.
        if known.contains(&tok) && tok != s.name {
            refs.insert(tok);
        }
    }
    for raw in body.split(|c: char| !(c.is_alphanumeric() || c == '_')) {
        if known.contains(raw) && raw != s.name {
            refs.insert(raw.to_string());
        }
    }
    let mut out: Vec<String> = refs.into_iter().collect();
    out.sort();
    out
}

/// Text fed to the embedder for a symbol.
pub fn embed_text(s: &Symbol) -> String {
    format!(
        "{} {} {} {} {} {}",
        s.file,
        s.kind,
        s.qualified_name,
        s.signature,
        s.imports.join(" "),
        s.references.join(" ")
    )
}

#[cfg(test)]
mod error_classification_tests {
    use super::is_locked_error;

    fn sqlite_error(result_code: std::ffi::c_int) -> anyhow::Error {
        let inner = rusqlite::ffi::Error::new(result_code);
        anyhow::Error::new(rusqlite::Error::SqliteFailure(inner, None))
    }

    #[test]
    fn classifies_busy_and_locked_as_transient() {
        assert!(is_locked_error(&sqlite_error(rusqlite::ffi::SQLITE_BUSY)));
        assert!(is_locked_error(&sqlite_error(rusqlite::ffi::SQLITE_LOCKED)));
    }

    #[test]
    fn does_not_classify_other_errors_as_transient() {
        assert!(!is_locked_error(&sqlite_error(
            rusqlite::ffi::SQLITE_CORRUPT
        )));
        assert!(!is_locked_error(&anyhow::anyhow!("unrelated io error")));
    }

    #[test]
    fn sees_through_context_wrapping() {
        // `open`/`open_read_only` wrap the underlying rusqlite::Error with
        // `.with_context(...)`; the classifier must still find it via the
        // error chain, not just the outermost layer.
        let wrapped = sqlite_error(rusqlite::ffi::SQLITE_BUSY).context("open index at /some/path");
        assert!(is_locked_error(&wrapped));
    }
}
