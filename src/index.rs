//! Persistent index: SQLite-backed storage plus the incremental indexing
//! pipeline (file-hash short-circuit, per-symbol re-embed avoidance).

use crate::scanner;
use crate::symbols::{Language, Symbol};
use anyhow::{Context, Result};
use rusqlite::Connection;
use std::collections::{HashMap, HashSet};
use std::path::Path;

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
    fn file_hashes(&self) -> Result<HashMap<String, u64>>;
    fn replace_file(&mut self, file: &str, hash: u64, symbols: &[Symbol]) -> Result<()>;
    fn remove_files(&mut self, files: &[String]) -> Result<()>;
    fn all_symbols(&self) -> Result<Vec<Symbol>>;
    fn symbol_hash(&self, id: u64) -> Result<Option<u64>>;
    fn put_embedding(&mut self, symbol_id: u64, vec: &[f32]) -> Result<()>;
    fn embedding_with_hash(&self, symbol_id: u64) -> Result<Option<(u64, Vec<f32>)>>;
    /// All embeddings in one shot (avoids per-symbol queries in retrieval).
    fn all_embeddings(&self) -> Result<HashMap<u64, (u64, Vec<f32>)>>;
    fn drop_embeddings_without_symbols(&mut self) -> Result<()>;
}

pub struct SqliteStore {
    conn: Connection,
}

impl SqliteStore {
    pub fn open(path: &Path) -> Result<Self> {
        if let Some(dir) = path.parent() {
            std::fs::create_dir_all(dir)?;
        }
        let conn =
            Connection::open(path).with_context(|| format!("open index at {}", path.display()))?;
        conn.execute_batch(
            r#"
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
            "#,
        )?;
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

    fn file_hashes(&self) -> Result<HashMap<String, u64>> {
        let mut stmt = self.conn.prepare("SELECT path, content_hash FROM files")?;
        let rows = stmt.query_map([], |r| {
            Ok((r.get::<_, String>(0)?, r.get::<_, i64>(1)? as u64))
        })?;
        Ok(rows.collect::<std::result::Result<HashMap<_, _>, _>>()?)
    }

    fn replace_file(&mut self, file: &str, hash: u64, symbols: &[Symbol]) -> Result<()> {
        let tx = self.conn.transaction()?;
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
        let tx = self.conn.transaction()?;
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
            .chunks_exact(4)
            .map(|c| f32::from_le_bytes([c[0], c[1], c[2], c[3]]))
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
                .chunks_exact(4)
                .map(|c| f32::from_le_bytes([c[0], c[1], c[2], c[3]]))
                .take(dim as usize)
                .collect();
            out.insert(id, (chash, floats));
        }
        Ok(out)
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
    for p in &files {
        let rel = p.display().to_string();
        match std::fs::read_to_string(root.join(p)) {
            Ok(src) => {
                current.insert(rel, src);
            }
            Err(_) => continue, // unreadable/non-UTF8: skip
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
    report.reparsed_files = to_parse.len();
    report.unchanged_files = files.len() - to_parse.len();

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
    let mut results: Vec<Vec<ParsedFile>> = Vec::with_capacity(workers);
    std::thread::scope(|scope| {
        let mut handles = Vec::new();
        for (w, chunk) in to_parse.chunks(chunk_size.max(1)).enumerate() {
            let current = &current;
            results.push(Vec::new());
            handles.push(scope.spawn(move || {
                let mut out = Vec::with_capacity(chunk.len());
                for (rel, hash) in chunk {
                    let lang = match scanner::language_for_path(Path::new(rel)) {
                        Some(l) => l,
                        None => continue,
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
                (w, out)
            }));
        }
        for h in handles {
            let (w, out) = h
                .join()
                .map_err(|_| anyhow::anyhow!("parse worker panicked"))?;
            results[w] = out;
        }
        Ok::<(), anyhow::Error>(())
    })?;
    for mut part in results {
        parsed.append(&mut part);
    }

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
        for s in &mut pf.symbols {
            s.references = extract_references(s, &pf.src, &known_names);
        }
    }

    for pf in &parsed {
        for s in &pf.symbols {
            match before_symbols.get(&s.id()) {
                None => report.new_symbols += 1,
                Some(old) if *old != s.content_hash => report.changed_symbols += 1,
                Some(_) => {}
            }
        }
        store.replace_file(&pf.file, pf.hash, &pf.symbols)?;
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
    let computed: Vec<Vec<(u64, Vec<f32>)>> = std::thread::scope(|scope| {
        let mut handles = Vec::new();
        for chunk in to_embed.chunks(chunk_size.max(1)) {
            handles.push(scope.spawn(|| {
                chunk
                    .iter()
                    .map(|s| (s.id(), embedder.embed(&embed_text(s))))
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

    store.set_meta("root", &root.display().to_string())?;
    store.set_meta("embedder", embedder.name())?;
    store.set_meta("dim", &embedder.dim().to_string())?;
    report.duration_ms = started.elapsed().as_millis();
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
