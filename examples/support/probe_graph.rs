//! Issue #14 diagnostic challenger — **benchmark only**, included by
//! `examples/retrieval_profile.rs`; nothing in `src/` calls it.
//!
//! Answers `RelationGraph::neighbors` and context's file-scoped
//! `callers_of` for a bounded seed set with SQLite probes instead of a
//! whole-corpus `SymbolSnapshot`. Every rule is a transcription of
//! `src/relations.rs` at `6413856`; `RelationGraph` is the oracle and
//! `--stage probe_oracle` compares the two with every symbol as a seed.
//!
//! Deliberately not assumed from SQLite, and done in Rust instead:
//! - **Corpus order.** Production's `ORDER BY file, start_line` leaves tie
//!   order to the sorter, which SQLite does not document as stable. Probes
//!   sort their (small) results in Rust by `(file, start_line, rowid as
//!   i64)`; `probe_oracle` checks that key reproduces `all_symbols`' id
//!   sequence on each corpus rather than trusting it.
//! - **Predicates.** `is_test_symbol`, `kind != module` and the substring
//!   test run in Rust: rusqlite's `functions` feature (needed for a SQL
//!   UDF) is not enabled in OXIDE, and SQL `lower()`/`LIKE` fold ASCII
//!   only where Rust's `to_lowercase` folds Unicode.
use oxide::relations::resolve_module_with;
use oxide::symbols::{Symbol, SymbolKind};
use rusqlite::Connection;
use std::cell::Cell;
use std::collections::{HashMap, HashSet};

/// Which access paths the probes may use.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Access {
    /// Existing indexes only; corpus-global classes via one `symbols` pass.
    Scan,
    /// Diagnostic indexes present on a benchmark copy (`build_diag`).
    Diag,
}

/// What the probes cost in SQLite terms, per call sequence.
#[derive(Default, Clone, Copy, Debug)]
pub struct ProbeStats {
    pub statements: usize,
    pub rows: usize,
    /// Text bytes the probes read off result rows.
    pub bytes: usize,
}

/// Corpus-order key: `(file, start_line, rowid)`, rowid as the signed
/// `i64` SQLite stores (sorting the `u64` bit-cast would misorder
/// negative rowids — PG-1's first bug).
#[derive(Clone, PartialEq, Eq, PartialOrd, Ord, Debug)]
struct Key {
    file: String,
    line: i64,
    rowid: i64,
}

impl Key {
    fn id(&self) -> u64 {
        self.rowid as u64
    }
}

/// A probed symbol: order key plus the fields the rules below read.
#[derive(Clone, Debug)]
struct Row {
    key: Key,
    name: String,
    kind: SymbolKind,
}

pub struct ProbeGraph<'c> {
    conn: &'c Connection,
    access: Access,
    stats: Cell<ProbeStats>,
}

/// `relations.rs::is_test_symbol`, same behavior: ASCII input lowercased
/// byte-wise, anything else through `to_lowercase`, and the last clause
/// is `ends_with("test") && (Function | Method)`.
pub fn is_test_symbol(file: &str, name: &str, kind: SymbolKind) -> bool {
    fn lower(src: &str) -> String {
        if src.is_ascii() {
            src.to_ascii_lowercase()
        } else {
            src.to_lowercase()
        }
    }
    let f = lower(file);
    let n = lower(name);
    f.starts_with("test_")
        || f.contains("_test.")
        || f.contains(".test.")
        || f.contains(".spec.")
        || f.contains("/tests/")
        || f.contains("\\tests\\")
        || n.starts_with("test_")
        || n.ends_with("_test")
        || n.ends_with("test") && (matches!(kind, SymbolKind::Function | SymbolKind::Method))
}

/// `row_to_symbol`'s decode of `symbols.kind`, fallback included.
fn parse_kind(s: &str) -> SymbolKind {
    s.parse().unwrap_or(SymbolKind::Function)
}

fn json_list<'a>(items: impl IntoIterator<Item = &'a str>) -> String {
    serde_json::to_string(&items.into_iter().collect::<Vec<_>>()).expect("strings serialize")
}

/// Diagnostic indexes, created on a **benchmark copy** only (issue #14:
/// never a user index). Returns the DDL it ran, for the report.
pub fn build_diag(conn: &Connection) -> rusqlite::Result<Vec<&'static str>> {
    const DDL: [&str; 3] = [
        "CREATE INDEX IF NOT EXISTS diag_symbols_qualified ON symbols(qualified_name)",
        "CREATE INDEX IF NOT EXISTS diag_symbols_parent ON symbols(parent)",
        "CREATE TABLE IF NOT EXISTS diag_test_refs(
             ref TEXT NOT NULL, symbol_id INTEGER NOT NULL,
             PRIMARY KEY(ref, symbol_id)) WITHOUT ROWID",
    ];
    let tx = conn.unchecked_transaction()?;
    for ddl in DDL {
        tx.execute(ddl, [])?;
    }
    tx.execute("DELETE FROM diag_test_refs", [])?;
    {
        let mut read = tx.prepare("SELECT id, file, name, kind, references_json FROM symbols")?;
        let mut ins =
            tx.prepare("INSERT OR IGNORE INTO diag_test_refs(ref, symbol_id) VALUES(?1, ?2)")?;
        let mut rows = read.query([])?;
        let mut pending: Vec<(String, i64)> = Vec::new();
        while let Some(r) = rows.next()? {
            let kind = parse_kind(r.get_ref(3)?.as_str()?);
            if !is_test_symbol(r.get_ref(1)?.as_str()?, r.get_ref(2)?.as_str()?, kind) {
                continue;
            }
            let refs: Vec<String> =
                serde_json::from_str(r.get_ref(4)?.as_str()?).unwrap_or_default();
            let id: i64 = r.get(0)?;
            pending.extend(refs.into_iter().map(|x| (x, id)));
        }
        for (x, id) in pending {
            ins.execute(rusqlite::params![x, id])?;
        }
    }
    tx.commit()?;
    Ok(DDL.to_vec())
}

impl<'c> ProbeGraph<'c> {
    pub fn new(conn: &'c Connection, access: Access) -> Self {
        Self {
            conn,
            access,
            stats: Cell::new(ProbeStats::default()),
        }
    }

    pub fn stats(&self) -> ProbeStats {
        self.stats.get()
    }

    fn count(&self, statements: usize, rows: usize, bytes: usize) {
        let mut s = self.stats.get();
        s.statements += statements;
        s.rows += rows;
        s.bytes += bytes;
        self.stats.set(s);
    }

    /// `SELECT id, file, start_line, name, kind, <col> FROM symbols WHERE
    /// <col> IN (json list)` — the shape the point probes share.
    fn rows_where_in(&self, col: &str, values: &[&str]) -> rusqlite::Result<Vec<(Row, String)>> {
        if values.is_empty() {
            return Ok(Vec::new());
        }
        let sql = format!(
            "SELECT id, file, start_line, name, kind, {col} FROM symbols
             WHERE {col} IN (SELECT value FROM json_each(?1))"
        );
        let mut stmt = self.conn.prepare(&sql)?;
        let mut rows = stmt.query([json_list(values.iter().copied())])?;
        let (mut out, mut bytes) = (Vec::new(), 0usize);
        while let Some(r) = rows.next()? {
            let file: String = r.get(1)?;
            let name: String = r.get(3)?;
            let matched: String = r.get(5)?;
            bytes += file.len() + name.len() + matched.len();
            out.push((
                Row {
                    key: Key {
                        file,
                        line: r.get(2)?,
                        rowid: r.get(0)?,
                    },
                    name,
                    kind: parse_kind(r.get_ref(4)?.as_str()?),
                },
                matched,
            ));
        }
        self.count(1, out.len(), bytes);
        Ok(out)
    }

    /// Existence of every path asked about (any kind, modules included —
    /// `RelationIndex::files` indexes every symbol).
    fn existing_files(&self, paths: &HashSet<String>) -> rusqlite::Result<HashSet<String>> {
        if paths.is_empty() {
            return Ok(HashSet::new());
        }
        let mut stmt = self.conn.prepare(
            "SELECT DISTINCT file FROM symbols WHERE file IN (SELECT value FROM json_each(?1))",
        )?;
        let out: HashSet<String> = stmt
            .query_map([json_list(paths.iter().map(String::as_str))], |r| r.get(0))?
            .collect::<rusqlite::Result<_>>()?;
        self.count(1, out.len(), out.iter().map(String::len).sum());
        Ok(out)
    }

    /// `resolve_module_with` needs the existence answer for *every*
    /// candidate (more than one match is ambiguity ⇒ `None`), and its
    /// candidate list does not depend on those answers — so one recording
    /// pass collects every path it would ask about, one batched probe
    /// answers them, and the real resolution replays against that set.
    fn resolutions(
        &self,
        seeds: &[&Symbol],
    ) -> rusqlite::Result<HashMap<(String, String), Option<String>>> {
        let asked = std::cell::RefCell::new(HashSet::<String>::new());
        let mut pairs: HashSet<(String, String)> = HashSet::new();
        for s in seeds {
            for m in &s.imports {
                if pairs.insert((s.file.clone(), m.clone())) {
                    resolve_module_with(m, &s.file, &|p| {
                        asked.borrow_mut().insert(p.to_string());
                        false
                    });
                }
            }
        }
        let exists = self.existing_files(&asked.into_inner())?;
        Ok(pairs
            .into_iter()
            .map(|(file, m)| {
                let r = resolve_module_with(&m, &file, &|p| exists.contains(p));
                ((file, m), r)
            })
            .collect())
    }

    /// Definitions by name (`uses`) and non-module symbols by resolved
    /// file (`imported-definition`), both from existing indexes.
    #[allow(clippy::type_complexity)]
    fn local_classes(
        &self,
        seeds: &[&Symbol],
        res: &HashMap<(String, String), Option<String>>,
    ) -> rusqlite::Result<(HashMap<String, Vec<Row>>, HashMap<String, Vec<Row>>)> {
        let names: HashSet<&str> = seeds
            .iter()
            .flat_map(|s| s.references.iter().map(String::as_str))
            .collect();
        let names: Vec<&str> = names.into_iter().collect();
        // `defs_by_name`: parentless, non-module. `parent IS NULL` in SQL
        // is exact (an empty-string parent is neither NULL nor `None`).
        let mut defs: HashMap<String, Vec<Row>> = HashMap::new();
        if !names.is_empty() {
            let mut stmt = self.conn.prepare(
                "SELECT id, file, start_line, name, kind FROM symbols
                 WHERE name IN (SELECT value FROM json_each(?1)) AND parent IS NULL",
            )?;
            let mut rows = stmt.query([json_list(names.iter().copied())])?;
            let (mut n, mut bytes) = (0usize, 0usize);
            while let Some(r) = rows.next()? {
                n += 1;
                let kind = parse_kind(r.get_ref(4)?.as_str()?);
                if kind == SymbolKind::Module {
                    continue;
                }
                let file: String = r.get(1)?;
                let name: String = r.get(3)?;
                bytes += file.len() + name.len();
                defs.entry(name.clone()).or_default().push(Row {
                    key: Key {
                        file,
                        line: r.get(2)?,
                        rowid: r.get(0)?,
                    },
                    name,
                    kind,
                });
            }
            self.count(1, n, bytes);
        }
        let targets: HashSet<&str> = res.values().flatten().map(String::as_str).collect();
        let targets: Vec<&str> = targets.into_iter().collect();
        let mut by_file: HashMap<String, Vec<Row>> = HashMap::new();
        for (row, file) in self.rows_where_in("file", &targets)? {
            if row.kind != SymbolKind::Module {
                by_file.entry(file).or_default().push(row);
            }
        }
        for v in defs.values_mut().chain(by_file.values_mut()) {
            v.sort_by(|a, b| a.key.cmp(&b.key));
        }
        Ok((defs, by_file))
    }

    /// `by_qualified` (last in corpus order), `children_of` (all, corpus
    /// order) and full `related_tests` per seed — the corpus-global
    /// classes. `want_tests[i]` false skips seed `i`'s test lookup.
    #[allow(clippy::type_complexity)]
    fn global_classes(
        &self,
        seeds: &[&Symbol],
        want_tests: &[bool],
    ) -> rusqlite::Result<(
        HashMap<String, Key>,
        HashMap<String, Vec<Key>>,
        Vec<Vec<Key>>,
    )> {
        let parents: HashSet<&str> = seeds.iter().filter_map(|s| s.parent.as_deref()).collect();
        let mut child_of: HashSet<&str> = parents.clone();
        child_of.extend(seeds.iter().map(|s| s.qualified_name.as_str()));
        let test_names: Vec<(usize, &str)> = seeds
            .iter()
            .enumerate()
            .filter(|(i, _)| want_tests[*i])
            .map(|(i, s)| (i, s.name.as_str()))
            .collect();
        let mut last: HashMap<String, Key> = HashMap::new();
        let mut children: HashMap<String, Vec<Key>> = HashMap::new();
        let mut tests: Vec<Vec<Key>> = vec![Vec::new(); seeds.len()];
        match self.access {
            Access::Scan => {
                let mut stmt = self.conn.prepare(
                    "SELECT id, file, start_line, qualified_name, name, kind, parent,
                            references_json FROM symbols",
                )?;
                let mut rows = stmt.query([])?;
                let (mut n, mut bytes) = (0usize, 0usize);
                while let Some(r) = rows.next()? {
                    n += 1;
                    let qn = r.get_ref(3)?.as_str()?;
                    let parent = r.get_ref(6)?.as_str_or_null()?;
                    let file = r.get_ref(1)?.as_str()?;
                    let name = r.get_ref(4)?.as_str()?;
                    bytes += qn.len() + file.len() + name.len() + parent.map_or(0, str::len);
                    let key = || -> rusqlite::Result<Key> {
                        Ok(Key {
                            file: file.to_string(),
                            line: r.get(2)?,
                            rowid: r.get(0)?,
                        })
                    };
                    if parents.contains(qn) {
                        let k = key()?;
                        match last.get_mut(qn) {
                            Some(e) if k > *e => *e = k,
                            Some(_) => {}
                            None => {
                                last.insert(qn.to_string(), k);
                            }
                        }
                    }
                    if let Some(p) = parent.filter(|p| child_of.contains(p)) {
                        children.entry(p.to_string()).or_default().push(key()?);
                    }
                    if test_names.is_empty() {
                        continue;
                    }
                    let kind = parse_kind(r.get_ref(5)?.as_str()?);
                    if !is_test_symbol(file, name, kind) {
                        continue;
                    }
                    let refs_json = r.get_ref(7)?.as_str()?;
                    bytes += refs_json.len();
                    let mut refs: Option<Vec<String>> = None;
                    for &(i, sn) in &test_names {
                        let hit = name.contains(sn) || {
                            let refs = refs.get_or_insert_with(|| {
                                serde_json::from_str(refs_json).unwrap_or_default()
                            });
                            refs.iter().any(|x| x == sn)
                        };
                        if hit {
                            tests[i].push(key()?);
                        }
                    }
                }
                self.count(1, n, bytes);
            }
            Access::Diag => {
                let ps: Vec<&str> = parents.iter().copied().collect();
                for (row, qn) in self.rows_where_in("qualified_name", &ps)? {
                    match last.get_mut(&qn) {
                        Some(e) if row.key > *e => *e = row.key,
                        Some(_) => {}
                        None => {
                            last.insert(qn, row.key);
                        }
                    }
                }
                let cs: Vec<&str> = child_of.iter().copied().collect();
                for (row, p) in self.rows_where_in("parent", &cs)? {
                    children.entry(p).or_default().push(row.key);
                }
                if !test_names.is_empty() {
                    self.diag_tests(&test_names, &mut tests)?;
                }
            }
        }
        for v in children.values_mut().chain(tests.iter_mut()) {
            v.sort();
        }
        Ok((last, children, tests))
    }

    /// `related_tests` with the diagnostic side table: the name-substring
    /// clause over a covering scan of `idx_symbols_name` (O(N) keys — no
    /// B-tree answers a substring), the reference clause from
    /// `diag_test_refs`, then one rowid fetch of the union to apply
    /// `is_test_symbol` and read the order key.
    fn diag_tests(
        &self,
        test_names: &[(usize, &str)],
        tests: &mut [Vec<Key>],
    ) -> rusqlite::Result<()> {
        let mut cand: Vec<HashSet<i64>> = vec![HashSet::new(); tests.len()];
        {
            let mut stmt = self.conn.prepare("SELECT id, name FROM symbols")?;
            let mut rows = stmt.query([])?;
            let (mut n, mut bytes) = (0usize, 0usize);
            while let Some(r) = rows.next()? {
                n += 1;
                let name = r.get_ref(1)?.as_str()?;
                bytes += name.len();
                for &(i, sn) in test_names {
                    if name.contains(sn) {
                        cand[i].insert(r.get(0)?);
                    }
                }
            }
            self.count(1, n, bytes);
        }
        {
            let mut stmt = self.conn.prepare(
                "SELECT ref, symbol_id FROM diag_test_refs
                 WHERE ref IN (SELECT value FROM json_each(?1))",
            )?;
            let mut rows = stmt.query([json_list(test_names.iter().map(|(_, n)| *n))])?;
            let mut n = 0usize;
            while let Some(r) = rows.next()? {
                n += 1;
                let x = r.get_ref(0)?.as_str()?;
                let id: i64 = r.get(1)?;
                for &(i, sn) in test_names {
                    if x == sn {
                        cand[i].insert(id);
                    }
                }
            }
            self.count(1, n, 0);
        }
        let all: HashSet<i64> = cand.iter().flatten().copied().collect();
        if all.is_empty() {
            return Ok(());
        }
        let mut stmt = self.conn.prepare(
            "SELECT id, file, start_line, name, kind FROM symbols
             WHERE id IN (SELECT value FROM json_each(?1))",
        )?;
        let ids = serde_json::to_string(&all.iter().collect::<Vec<_>>()).expect("ints serialize");
        let mut rows = stmt.query([ids])?;
        let (mut n, mut bytes) = (0usize, 0usize);
        while let Some(r) = rows.next()? {
            n += 1;
            let id: i64 = r.get(0)?;
            let file = r.get_ref(1)?.as_str()?;
            let name = r.get_ref(3)?.as_str()?;
            bytes += file.len() + name.len();
            if !is_test_symbol(file, name, parse_kind(r.get_ref(4)?.as_str()?)) {
                continue;
            }
            for (i, c) in cand.iter().enumerate() {
                if c.contains(&id) {
                    tests[i].push(Key {
                        file: file.to_string(),
                        line: r.get(2)?,
                        rowid: id,
                    });
                }
            }
        }
        self.count(1, n, bytes);
        Ok(())
    }

    /// `RelationGraph::neighbors` for every seed, as `(relation, id)` in the
    /// production order, truncated to 24.
    pub fn neighbors_ids(
        &self,
        seeds: &[&Symbol],
    ) -> rusqlite::Result<Vec<Vec<(&'static str, u64)>>> {
        Ok(self.neighbors_and_tests(seeds, false)?.0)
    }

    /// [`Self::neighbors_ids`], plus (with `full_tests`) every seed's
    /// untruncated `related_tests` ids for the oracle.
    #[allow(clippy::type_complexity)]
    pub fn neighbors_and_tests(
        &self,
        seeds: &[&Symbol],
        full_tests: bool,
    ) -> rusqlite::Result<(Vec<Vec<(&'static str, u64)>>, Vec<Vec<u64>>)> {
        let res = self.resolutions(seeds)?;
        let (defs, by_file) = self.local_classes(seeds, &res)?;
        let mut mid: Vec<Vec<(&'static str, u64)>> = Vec::with_capacity(seeds.len());
        for seed in seeds {
            let imported: HashSet<&str> = seed
                .imports
                .iter()
                .filter_map(|m| res[&(seed.file.clone(), m.clone())].as_deref())
                .collect();
            let mut out = Vec::new();
            for r in &seed.references {
                let Some(ds) = defs.get(r) else { continue };
                let cross = || ds.iter().filter(|d| d.key.file != seed.file);
                let any_backed = cross().any(|d| imported.contains(d.key.file.as_str()));
                for d in cross() {
                    if any_backed && !imported.contains(d.key.file.as_str()) {
                        continue;
                    }
                    out.push(("uses", d.key.id()));
                }
            }
            for m in &seed.imports {
                let Some(t) = &res[&(seed.file.clone(), m.clone())] else {
                    continue;
                };
                for d in by_file.get(t).into_iter().flatten() {
                    if seed.references.contains(&d.name) {
                        out.push(("imported-definition", d.key.id()));
                    }
                }
            }
            mid.push(out);
        }
        // A seed whose `uses` + `imported-definition` alone already fill the
        // truncation can never show a test; parent/sibling/child only add
        // to that count, so this skip is exact.
        let want: Vec<bool> = mid.iter().map(|m| full_tests || m.len() < 24).collect();
        let (last, children, tests) = self.global_classes(seeds, &want)?;
        let mut outs = Vec::with_capacity(seeds.len());
        for (i, seed) in seeds.iter().enumerate() {
            let mut out: Vec<(&'static str, u64)> = Vec::new();
            if let Some(p) = &seed.parent {
                if let Some(k) = last.get(p) {
                    out.push(("parent", k.id()));
                }
                for k in children.get(p).into_iter().flatten() {
                    out.push(("sibling", k.id()));
                }
            }
            for k in children.get(&seed.qualified_name).into_iter().flatten() {
                out.push(("child", k.id()));
            }
            out.append(&mut mid[i]);
            for k in &tests[i] {
                out.push(("test", k.id()));
            }
            out.truncate(24);
            outs.push(out);
        }
        let full = tests
            .iter()
            .map(|v| v.iter().map(Key::id).collect())
            .collect();
        Ok((outs, full))
    }

    /// `resolve_import(seed.file, m)` ids for each of the seed's imports.
    pub fn resolve_import_ids(&self, seed: &Symbol) -> rusqlite::Result<Vec<Vec<u64>>> {
        let res = self.resolutions(&[seed])?;
        let (_, by_file) = self.local_classes(&[seed], &res)?;
        Ok(seed
            .imports
            .iter()
            .map(|m| match &res[&(seed.file.clone(), m.clone())] {
                Some(t) => by_file
                    .get(t)
                    .into_iter()
                    .flatten()
                    .map(|d| d.key.id())
                    .collect(),
                None => Vec::new(),
            })
            .collect())
    }

    /// `RelationGraph::callers_of(name)` restricted to `scope` files, in the
    /// order the production filter leaves it: sorted `(file, start_line)`
    /// stably over corpus order, one entry per `calls` row (a symbol that
    /// calls `name` twice is listed twice there too).
    pub fn scoped_callers(&self, name: &str, scope: &[String]) -> rusqlite::Result<Vec<u64>> {
        if scope.is_empty() {
            return Ok(Vec::new());
        }
        let mut stmt = self.conn.prepare(
            "SELECT s.id, s.file, s.start_line, r.rowid FROM symbols s
             JOIN symbol_relations r ON r.symbol_id = s.id
             WHERE s.file IN (SELECT value FROM json_each(?2))
               AND r.kind = 'calls' AND r.target = ?1",
        )?;
        let mut rows: Vec<(Key, i64)> = stmt
            .query_map(
                rusqlite::params![name, json_list(scope.iter().map(String::as_str))],
                |r| {
                    Ok((
                        Key {
                            file: r.get(1)?,
                            line: r.get(2)?,
                            rowid: r.get(0)?,
                        },
                        r.get(3)?,
                    ))
                },
            )?
            .collect::<rusqlite::Result<_>>()?;
        self.count(1, rows.len(), rows.iter().map(|(k, _)| k.file.len()).sum());
        rows.sort();
        Ok(rows.into_iter().map(|(k, _)| k.id()).collect())
    }

    /// Full rows for the ids a request consumes, with `calls`/`bases`
    /// merged from `symbol_relations` in rowid order (what
    /// `all_symbol_relations`' table scan yields per symbol).
    pub fn hydrate(
        &self,
        store: &dyn oxide::storage::IndexBackend,
        ids: &[u64],
        with_relations: bool,
    ) -> anyhow::Result<HashMap<u64, Symbol>> {
        let mut out: HashMap<u64, Symbol> = store
            .symbols_by_ids(ids)?
            .into_iter()
            .map(|s| (s.id(), s))
            .collect();
        self.count(1, out.len(), 0);
        if with_relations && !ids.is_empty() {
            let json = serde_json::to_string(&ids.iter().map(|&i| i as i64).collect::<Vec<_>>())?;
            let mut stmt = self.conn.prepare(
                "SELECT symbol_id, rowid, kind, target FROM symbol_relations
                 WHERE symbol_id IN (SELECT value FROM json_each(?1))",
            )?;
            let mut rel: Vec<(i64, i64, String, String)> = stmt
                .query_map([json], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?)))?
                .collect::<rusqlite::Result<_>>()?;
            self.count(1, rel.len(), rel.iter().map(|x| x.3.len()).sum());
            rel.sort();
            for (id, _, kind, target) in rel {
                if let Some(s) = out.get_mut(&(id as u64)) {
                    match kind.as_str() {
                        "calls" => s.calls.push(target),
                        "bases" => s.bases.push(target),
                        _ => {}
                    }
                }
            }
        }
        Ok(out)
    }
}
