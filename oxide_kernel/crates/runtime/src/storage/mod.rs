//! Lock-owning, generation-isolated Ladybug adapter. Native values stay private.
mod codec;

use lbug::{Connection, Database, SystemConfig, Value};

use oxide_kernel::{id::*, knowledge::*, source::SourceCapture, store::*};

use sha2::{Digest as _, Sha256};

use std::{
    collections::{BTreeMap, BTreeSet},
    fs::{self, File, OpenOptions},
    path::{Path, PathBuf},
    sync::{Arc, mpsc},
    thread,
};

type R<T> = Result<T, StoreError>;

const TABLES: [&str; 7] = [
    "CONTAINS",
    "DEFINES",
    "REFERENCES",
    "CALLS",
    "IMPORTS",
    "IMPLEMENTS",
    "TESTED_BY",
];

fn table(k: RelationKind) -> &'static str {
    TABLES[match k {
        RelationKind::Contains(_) => 0,
        RelationKind::Defines => 1,
        RelationKind::References => 2,
        RelationKind::Calls => 3,
        RelationKind::Imports => 4,
        RelationKind::Implements => 5,
        RelationKind::TestedBy => 6,
    }]
}

fn io(e: std::io::Error) -> StoreError {
    StoreError::Io(e.to_string())
}

fn native(e: lbug::Error) -> StoreError {
    let message = e.to_string();
    if message.to_ascii_lowercase().contains("interrupt") {
        StoreError::Cancelled
    } else {
        StoreError::Transaction(message)
    }
}

fn config() -> SystemConfig {
    SystemConfig::default()
        .buffer_pool_size(32 << 20)
        .max_num_threads(2)
        .max_db_size(1 << 30)
}

fn db(path: &Path) -> R<Database> {
    Database::new(path, config()).map_err(native)
}

fn read_db(path: &Path) -> R<Database> {
    Database::new(path, config().read_only(true))
        .map_err(|e| StoreError::Corrupt(format!("unreadable generation; rebuild required: {e}")))
}
fn query(c: &Connection, q: &str) -> R<Vec<Vec<Value>>> {
    Ok(c.query(q).map_err(native)?.collect())
}

fn execute(c: &Connection, q: &str, params: Vec<(&str, Value)>) -> R<Vec<Vec<Value>>> {
    let mut p = c.prepare(q).map_err(native)?;

    Ok(c.execute(&mut p, params).map_err(native)?.collect())
}

fn strings(c: &Connection, q: &str) -> R<Vec<String>> {
    query(c, q)?
        .into_iter()
        .map(|r| match r.as_slice() {
            [Value::String(s)] => Ok(s.clone()),
            _ => Err(StoreError::Corrupt("invalid database row".into())),
        })
        .collect()
}

fn count(c: &Connection, q: &str) -> R<usize> {
    match query(c, q)?.as_slice() {
        [r] => match r.as_slice() {
            [Value::Int64(n)] => {
                usize::try_from(*n).map_err(|_| StoreError::Corrupt("negative count".into()))
            }

            _ => Err(StoreError::Corrupt("invalid count".into())),
        },
        _ => Err(StoreError::Corrupt("invalid count rows".into())),
    }
}

fn hash(s: &[u8]) -> String {
    Sha256::digest(s)
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect()
}

fn generation(k: &SnapshotKey) -> String {
    let mut canonical = Vec::new();
    for part in [
        "oxide-generation-v1",
        k.repo.as_str(),
        k.snapshot.as_str(),
        k.derivation.as_str(),
    ] {
        canonical.extend_from_slice(&(part.len() as u64).to_be_bytes());
        canonical.extend_from_slice(part.as_bytes());
    }
    hash(&canonical)
}

fn source_name(digest: &Digest) -> R<&str> {
    let name = digest
        .as_str()
        .strip_prefix("sha256:")
        .ok_or_else(|| StoreError::Corrupt("invalid source digest".into()))?;
    if name.len() != 64
        || !name
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
    {
        return Err(StoreError::Corrupt("invalid source digest".into()));
    }
    Ok(name)
}
fn sync(path: &Path) -> R<()> {
    File::open(path).map_err(io)?.sync_all().map_err(io)
}

fn sync_tree(path: &Path) -> R<()> {
    if path.is_dir() {
        for e in fs::read_dir(path).map_err(io)? {
            sync_tree(&e.map_err(io)?.path())?;
        }
    }

    sync(path)
}

struct Lock(File);

impl Drop for Lock {
    fn drop(&mut self) {
        let _ = self.0.unlock();
    }
}

struct Generation {
    db: Database,
    manifest: RepositorySnapshot,
    path: PathBuf,
    _owner: Arc<Lock>,
}

struct Staged {
    db: Database,
    manifest: RepositorySnapshot,
    path: PathBuf,
}

struct State {
    root: PathBuf,
    staged: BTreeMap<SnapshotKey, Staged>,
    published: BTreeMap<SnapshotKey, Arc<Generation>>,
    current: Option<SnapshotKey>,
    poisoned: bool,
    owner: Arc<Lock>,
}

type Job = Box<dyn FnOnce(&mut State) + Send>;

/// One bounded writer queue owns all generation mutations.
pub struct LadybugStore {
    sender: mpsc::SyncSender<Job>,
    worker: Option<thread::JoinHandle<()>>,
}

impl Drop for LadybugStore {
    fn drop(&mut self) {
        let (replacement, _) = mpsc::sync_channel(1);

        let old = std::mem::replace(&mut self.sender, replacement);

        drop(old);

        if let Some(worker) = self.worker.take() {
            let _ = worker.join();
        }
    }
}

#[derive(Clone)]
pub struct LadybugView {
    generation: Arc<Generation>,
}

impl LadybugStore {
    pub fn new(path: &Path) -> R<Self> {
        fs::create_dir_all(path).map_err(io)?;

        let lock = OpenOptions::new()
            .create(true)
            .truncate(false)
            .read(true)
            .write(true)
            .open(path.join("store.lock"))
            .map_err(io)?;

        lock.try_lock().map_err(|_| StoreError::OwnershipConflict)?;

        let owner = Arc::new(Lock(lock));

        let root = fs::canonicalize(path).map_err(io)?;

        let dirs = root.join("generations");

        fs::create_dir_all(&dirs).map_err(io)?;

        let mut state = State {
            root,
            owner,
            staged: BTreeMap::new(),
            published: BTreeMap::new(),
            current: None,
            poisoned: false,
        };

        for entry in fs::read_dir(&dirs).map_err(io)? {
            let entry = entry.map_err(io)?;

            let name = entry.file_name().to_string_lossy().into_owned();

            if name.ends_with(".staging") {
                fs::remove_dir_all(entry.path()).map_err(io)?;

                continue;
            }

            if name.len() != 64 || !name.bytes().all(|b| b.is_ascii_hexdigit()) {
                return Err(StoreError::Corrupt(
                    "invalid generation directory; rebuild required".into(),
                ));
            }

            let m = codec::de_manifest(&codec::parse(
                &fs::read_to_string(entry.path().join("MANIFEST")).map_err(io)?,
            )?)?;

            let derivation_path = entry.path().join("DERIVATION");
            if m.key.derivation.as_str().starts_with("sha256:") && !derivation_path.exists() {
                return Err(StoreError::Corrupt(
                    "derivation manifest missing; rebuild required".into(),
                ));
            }
            if derivation_path.exists() {
                let components: BTreeMap<String, String> =
                    serde_json::from_slice(&fs::read(&derivation_path).map_err(io)?)
                        .map_err(|e| StoreError::Corrupt(e.to_string()))?;
                if crate::derivation::derivation_id(&components) != m.key.derivation {
                    return Err(StoreError::Corrupt(
                        "derivation manifest identity mismatch; rebuild required".into(),
                    ));
                }
            }
            if generation(&m.key) != name {
                return Err(StoreError::Corrupt(
                    "generation identity mismatch; rebuild required".into(),
                ));
            }

            let g = Arc::new(Generation {
                db: read_db(&entry.path().join("db"))?,
                manifest: m.clone(),
                path: entry.path(),
                _owner: state.owner.clone(),
            });

            state.published.insert(m.key, g);
        }

        match fs::read_to_string(state.root.join("CURRENT")) {
            Ok(name) => {
                let name = name.trim();

                if name.len() != 64 || !name.bytes().all(|b| b.is_ascii_hexdigit()) {
                    return Err(StoreError::Corrupt(
                        "invalid CURRENT; rebuild required".into(),
                    ));
                }

                state.current = Some(
                    state
                        .published
                        .keys()
                        .find(|k| generation(k) == name)
                        .cloned()
                        .ok_or_else(|| {
                            StoreError::Corrupt(
                                "CURRENT generation missing; rebuild required".into(),
                            )
                        })?,
                );
            }

            Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
                if !state.published.is_empty() {
                    return Err(StoreError::Corrupt(
                        "CURRENT missing; rebuild required".into(),
                    ));
                }
            }

            Err(e) => return Err(io(e)),
        }

        let (sender, receiver) = mpsc::sync_channel::<Job>(16);

        let worker = thread::spawn(move || {
            while let Ok(job) = receiver.recv() {
                job(&mut state)
            }
        });

        Ok(Self {
            sender,
            worker: Some(worker),
        })
    }

    fn call<T: Send + 'static>(&self, f: impl FnOnce(&mut State) -> R<T> + Send + 'static) -> R<T> {
        let (tx, rx) = mpsc::sync_channel(1);

        self.sender
            .send(Box::new(move |s| {
                let result = if s.poisoned {
                    Err(StoreError::Corrupt(
                        "publication durability failed; reopen required".into(),
                    ))
                } else {
                    f(s)
                };
                let _ = tx.send(result);
            }))
            .map_err(|_| StoreError::Cancelled)?;

        rx.recv().map_err(|_| StoreError::Cancelled)?
    }

    /// Retains the exact version components that name this derivation.
    pub fn retain_derivation(
        &mut self,
        key: &SnapshotKey,
        components: &BTreeMap<String, String>,
    ) -> R<()> {
        if crate::derivation::derivation_id(components) != key.derivation {
            return Err(StoreError::InvalidBatch(
                "derivation manifest identity mismatch".into(),
            ));
        }
        let key = key.clone();
        let components = components.clone();
        self.call(move |s| {
            let g = s.staged.get(&key).ok_or(StoreError::NotStaged)?;
            let bytes =
                serde_json::to_vec(&components).map_err(|e| StoreError::Corrupt(e.to_string()))?;
            fs::write(g.path.join("DERIVATION"), bytes).map_err(io)
        })
    }
    pub fn retain_source(&mut self, capture: &SourceCapture) -> R<()> {
        let capture = capture.clone();

        self.call(move |s| {
            let mut found = false;

            for g in s
                .staged
                .values()
                .filter(|g| g.manifest.key.snapshot == capture.snapshot)
            {
                found = true;

                if capture.files.len() != g.manifest.files.len() {
                    return Err(StoreError::InvalidBatch("capture manifest mismatch".into()));
                }

                let dir = g.path.join("source");

                fs::create_dir_all(&dir).map_err(io)?;

                for (p, f) in &capture.files {
                    let m =
                        g.manifest.files.get(p).ok_or_else(|| {
                            StoreError::InvalidBatch("capture path mismatch".into())
                        })?;

                    if m.digest != f.digest
                        || m.byte_length != f.bytes.len() as u64
                        || f.digest.as_str() != format!("sha256:{}", hash(&f.bytes))
                    {
                        return Err(StoreError::Corrupt(
                            "captured source digest mismatch".into(),
                        ));
                    }

                    fs::write(dir.join(source_name(&f.digest)?), &f.bytes).map_err(io)?;
                }
            }

            if found {
                Ok(())
            } else {
                Err(StoreError::NotStaged)
            }
        })
    }
}

impl KnowledgeStore for LadybugStore {
    type View = LadybugView;

    fn open(&self, key: &SnapshotKey) -> R<LadybugView> {
        let key = key.clone();

        self.call(move |s| {
            if let Some(g) = s.published.get(&key) {
                return Ok(LadybugView {
                    generation: g.clone(),
                });
            }

            let available = s
                .published
                .keys()
                .filter(|k| k.repo == key.repo && k.snapshot == key.snapshot)
                .map(|k| k.derivation.clone())
                .collect::<Vec<_>>();

            if available.is_empty() {
                Err(StoreError::MissingSnapshot)
            } else {
                Err(StoreError::IncompatibleDerivation { available })
            }
        })
    }

    fn current(&self, repo: &RepoId) -> R<SnapshotKey> {
        let repo = repo.clone();

        self.call(move |s| {
            s.current
                .as_ref()
                .filter(|k| k.repo == repo)
                .cloned()
                .ok_or(StoreError::MissingSnapshot)
        })
    }

    fn begin(&mut self, manifest: RepositorySnapshot) -> R<()> {
        self.call(move |s| begin_state(s, manifest))
    }

    fn write(&mut self, key: &SnapshotKey, batch: Batch) -> R<()> {
        let key = key.clone();

        self.call(move |s| write_state(s, key, batch))
    }

    fn publish(&mut self, key: &SnapshotKey) -> R<()> {
        let key = key.clone();

        self.call(move |s| publish_state(s, key))
    }

    fn discard(&mut self, key: &SnapshotKey) -> R<()> {
        let key = key.clone();

        self.call(move |s| {
            let g = s.staged.remove(&key).ok_or(StoreError::NotStaged)?;

            drop(g.db);

            fs::remove_dir_all(g.path).map_err(io)
        })
    }
}

fn load_entities(c: &Connection) -> R<BTreeMap<EntityId, Entity>> {
    strings(c, "MATCH (e:Entity) RETURN e.data")?
        .into_iter()
        .map(|s| {
            let e = codec::de_entity(&codec::parse(&s)?)?;

            Ok((e.id.clone(), e))
        })
        .collect()
}

fn load_relations(c: &Connection) -> R<Vec<Relation>> {
    let mut groups: BTreeMap<Relation, Vec<String>> = BTreeMap::new();

    for t in TABLES {
        for row in query(
            c,
            &format!("MATCH (a)-[r:{t}]->(b) RETURN a.key, b.key, r.data, r.resolution"),
        )? {
            let [
                Value::String(from),
                Value::String(to),
                Value::String(data),
                Value::String(resolution),
            ] = row.as_slice()
            else {
                return Err(StoreError::Corrupt("invalid edge row".into()));
            };

            let r = codec::de_relation(&codec::parse(data)?)?;

            if table(r.kind) != t || *from != codec::text(&codec::id(&r.from)) {
                return Err(StoreError::Corrupt("edge source/table mismatch".into()));
            }

            let expected_resolution = match r.to {
                Target::Resolved(_) => "resolved",
                Target::Ambiguous(_) => "ambiguous",
                Target::Unresolved { .. } => "unresolved",
            };

            if resolution != expected_resolution {
                return Err(StoreError::Corrupt("edge resolution mismatch".into()));
            }

            groups.entry(r).or_default().push(to.clone());
        }
    }

    for (r, actual) in &mut groups {
        actual.sort();

        let data = codec::text(&codec::relation(r));

        let mut expected = match &r.to {
            Target::Resolved(x) => vec![codec::text(&codec::id(x))],
            Target::Ambiguous(xs) => xs.iter().map(|x| codec::text(&codec::id(x))).collect(),
            Target::Unresolved { .. } => vec![hash(data.as_bytes())],
        };

        expected.sort();

        if *actual != expected {
            return Err(StoreError::Corrupt(
                "edge endpoint readback mismatch".into(),
            ));
        }
    }

    Ok(groups.into_keys().collect())
}

impl LadybugView {
    pub fn hydrate(&self, source: &SourceRef) -> R<Vec<u8>> {
        let m = self
            .generation
            .manifest
            .files
            .get(&source.file)
            .ok_or_else(|| StoreError::Corrupt("source outside pinned manifest".into()))?;

        if m.digest != source.digest
            || source.range.start > source.range.end
            || source.range.end > m.byte_length
        {
            return Err(StoreError::Corrupt("invalid source evidence".into()));
        }

        let bytes = fs::read(
            self.generation
                .path
                .join("source")
                .join(source_name(&source.digest)?),
        )
        .map_err(io)?;

        if bytes.len() as u64 != m.byte_length
            || format!("sha256:{}", hash(&bytes)) != m.digest.as_str()
        {
            return Err(StoreError::Corrupt(
                "retained source digest mismatch".into(),
            ));
        }

        Ok(bytes[source.range.start as usize..source.range.end as usize].to_vec())
    }
}

impl ReadView for LadybugView {
    fn snapshot(&self) -> &RepositorySnapshot {
        &self.generation.manifest
    }

    fn entities(&self, ids: &[EntityId]) -> R<Vec<Option<Entity>>> {
        if ids.len() > MAX_REQUEST_ITEMS {
            return Err(StoreError::LimitExceeded {
                limit: MAX_REQUEST_ITEMS,
            });
        }

        let c = Connection::new(&self.generation.db).map_err(native)?;

        query(&c, "BEGIN TRANSACTION READ ONLY")?;

        let result = ids
            .iter()
            .map(|id| {
                let rows = execute(
                    &c,
                    "MATCH (e:Entity {key:$key}) RETURN e.data",
                    vec![("key", codec::text(&codec::id(id)).into())],
                )?;

                match rows.as_slice() {
                    [] => Ok(None),
                    [r] => match r.as_slice() {
                        [Value::String(data)] => Ok(Some(codec::de_entity(&codec::parse(data)?)?)),
                        _ => Err(StoreError::Corrupt("invalid entity row".into())),
                    },
                    _ => Err(StoreError::Corrupt("duplicate entity".into())),
                }
            })
            .collect();

        let _ = query(&c, "COMMIT");

        result
    }

    fn adjacency(&self, r: &AdjacencyRequest) -> R<Adjacency> {
        if r.limit > MAX_REQUEST_ITEMS {
            return Err(StoreError::LimitExceeded {
                limit: MAX_REQUEST_ITEMS,
            });
        }

        if self.entities(std::slice::from_ref(&r.entity))?[0].is_none() {
            return Err(StoreError::MissingEntity(r.entity.clone()));
        }

        let c = Connection::new(&self.generation.db).map_err(native)?;

        query(&c, "BEGIN TRANSACTION READ ONLY")?;

        let result = (|| {
            let mut selected = BTreeSet::new();

            let key = codec::text(&codec::id(&r.entity));

            let tables = r.kinds.iter().map(|k| table(*k)).collect::<BTreeSet<_>>();

            for t in tables {
                let side = match r.direction {
                    Direction::Outgoing => "a.key = $key",
                    Direction::Incoming => "b.key = $key",
                    Direction::Both => "a.key = $key OR b.key = $key",
                };

                let facet = if t == "CONTAINS" {
                    match (
                        r.kinds
                            .contains(&RelationKind::Contains(Containment::Physical)),
                        r.kinds
                            .contains(&RelationKind::Contains(Containment::Logical)),
                    ) {
                        (true, false) => " AND e.physical = true",
                        (false, true) => " AND e.physical = false",
                        _ => "",
                    }
                } else {
                    ""
                };

                // Ordinal is assigned after sorting the domain Relation set at seal.
                let q = format!(
                    "MATCH (a:Entity)-[e:{t}]->(b) WHERE ({side}){facet} RETURN DISTINCT e.ordinal, e.data ORDER BY e.ordinal LIMIT {}",
                    r.limit + 1
                );

                for row in execute(&c, &q, vec![("key", key.clone().into())])? {
                    let [Value::Int64(_), Value::String(data)] = row.as_slice() else {
                        return Err(StoreError::Corrupt("invalid adjacency row".into()));
                    };

                    selected.insert(codec::de_relation(&codec::parse(data)?)?);
                }
            }

            let edges = selected
                .into_iter()
                .filter(|e| {
                    let out = e.from == r.entity;

                    let incoming = e.to.entities().contains(&r.entity);

                    r.kinds.contains(&e.kind)
                        && match r.direction {
                            Direction::Outgoing => out,
                            Direction::Incoming => incoming,
                            Direction::Both => out || incoming,
                        }
                })
                .collect::<Vec<_>>();

            let truncated = edges.len() > r.limit;

            Ok(Adjacency {
                edges: edges.into_iter().take(r.limit).collect(),
                truncated,
            })
        })();

        let _ = query(&c, "COMMIT");

        result
    }
}

fn begin_state(s: &mut State, manifest: RepositorySnapshot) -> R<()> {
    let key = manifest.key.clone();
    if s.staged.contains_key(&key) || s.published.contains_key(&key) {
        return Err(StoreError::Conflict);
    }
    let path = s
        .root
        .join("generations")
        .join(format!("{}.staging", generation(&key)));
    fs::create_dir(&path).map_err(io)?;
    let database = db(&path.join("db"))?;
    let c = Connection::new(&database).map_err(native)?;
    query(
        &c,
        "CREATE NODE TABLE Entity(key STRING PRIMARY KEY, data STRING, category STRING, kind STRING, name STRING, signature STRING, test BOOLEAN, file STRING, start_byte INT64, end_byte INT64, digest STRING, language STRING, coverage STRING)",
    )?;
    query(
        &c,
        "CREATE NODE TABLE Pending(key STRING PRIMARY KEY, data STRING)",
    )?;
    query(
        &c,
        "CREATE NODE TABLE Unresolved(key STRING PRIMARY KEY, name STRING)",
    )?;
    for t in TABLES {
        query(
            &c,
            &format!(
                "CREATE REL TABLE {t}(FROM Entity TO Entity, FROM Entity TO Unresolved, data STRING, site STRING, resolution STRING, physical BOOLEAN, ordinal INT64, basis STRING, ev_start INT64, ev_end INT64, ev_digest STRING)"
            ),
        )?;
    }
    drop(c);
    s.staged.insert(
        key,
        Staged {
            db: database,
            manifest,
            path,
        },
    );
    Ok(())
}
fn write_state(s: &mut State, key: SnapshotKey, batch: Batch) -> R<()> {
    let g = s.staged.get(&key).ok_or(StoreError::NotStaged)?;
    let c = Connection::new(&g.db).map_err(native)?;
    let existing = strings(&c, "MATCH (e:Entity) RETURN e.key")?
        .into_iter()
        .collect::<BTreeSet<_>>();
    let mut ids = BTreeSet::new();
    for e in &batch.entities {
        let id = codec::text(&codec::id(&e.id));
        if existing.contains(&id) || !ids.insert(id) {
            return Err(StoreError::InvalidBatch("duplicate entity".into()));
        }
    }

    query(&c, "BEGIN TRANSACTION")?;
    let result = (|| {
        for e in batch.entities {
            let category = match e.id {
                EntityId::Repository => "repository",
                EntityId::Module(_) => "module",
                EntityId::File(_) => "file",
                EntityId::Symbol(_) => "symbol",
            };
            let source = e.source.as_ref();
            let file = match &e.id {
                EntityId::File(p) => Some(p),
                EntityId::Symbol(s) => Some(s.file()),
                _ => None,
            };
            let file_manifest = file.and_then(|p| g.manifest.files.get(p));
            execute(
                &c,
                "CREATE (:Entity {key:$key, data:$data, category:$category, kind:$kind, name:$name, signature:$signature, test:$test, file:$file, start_byte:$start, end_byte:$src_end, digest:$digest, language:$language, coverage:$coverage})",
                vec![
                    ("key", codec::text(&codec::id(&e.id)).into()),
                    ("data", codec::text(&codec::entity(&e)).into()),
                    ("category", category.into()),
                    ("kind", e.kind.clone().into()),
                    ("name", e.name.clone().into()),
                    ("signature", e.signature.clone().unwrap_or_default().into()),
                    ("test", Value::Bool(e.test)),
                    ("file", file.map_or("", RepoPath::as_str).into()),
                    (
                        "start",
                        source
                            .map_or(Ok(-1), |s| {
                                i64::try_from(s.range.start).map_err(|_| {
                                    StoreError::InvalidBatch(
                                        "source offset exceeds native range".into(),
                                    )
                                })
                            })?
                            .into(),
                    ),
                    (
                        "src_end",
                        source
                            .map_or(Ok(-1), |s| {
                                i64::try_from(s.range.end).map_err(|_| {
                                    StoreError::InvalidBatch(
                                        "source offset exceeds native range".into(),
                                    )
                                })
                            })?
                            .into(),
                    ),
                    ("digest", source.map_or("", |s| s.digest.as_str()).into()),
                    (
                        "language",
                        file_manifest.map_or("", |m| m.language.as_str()).into(),
                    ),
                    (
                        "coverage",
                        file_manifest
                            .map_or("", |m| match m.coverage {
                                Coverage::Complete => "complete",
                                Coverage::Partial { .. } => "partial",
                                Coverage::Unsupported { .. } => "unsupported",
                            })
                            .into(),
                    ),
                ],
            )?;
        }
        for r in batch.relations {
            let data = codec::text(&codec::relation(&r));
            execute(
                &c,
                "MERGE (:Pending {key:$key, data:$data})",
                vec![("key", data.clone().into()), ("data", data.into())],
            )?;
        }
        query(&c, "COMMIT")?;
        Ok(())
    })();
    if result.is_err() {
        let _ = query(&c, "ROLLBACK");
    }
    result
}
fn publish_state(s: &mut State, key: SnapshotKey) -> R<()> {
    let g = s.staged.get(&key).ok_or(StoreError::NotStaged)?;
    let c = Connection::new(&g.db).map_err(native)?;
    let entities = load_entities(&c)?;
    let mut relations = strings(&c, "MATCH (p:Pending) RETURN p.data")?
        .into_iter()
        .map(|r| codec::de_relation(&codec::parse(&r)?))
        .collect::<R<Vec<_>>>()?;
    relations.sort();
    relations.dedup();
    validate(&g.manifest, &entities, &relations).map_err(StoreError::InvalidBatch)?;
    if key.derivation.as_str().starts_with("sha256:") && !g.path.join("DERIVATION").is_file() {
        return Err(StoreError::InvalidBatch(
            "derivation manifest required".into(),
        ));
    }
    if key.snapshot.as_str().starts_with("sha256:") && !g.path.join("source").is_dir() {
        return Err(StoreError::InvalidBatch(
            "captured source retention required".into(),
        ));
    }

    if g.path.join("source").exists() {
        for manifest in g.manifest.files.values() {
            let bytes =
                fs::read(g.path.join("source").join(source_name(&manifest.digest)?)).map_err(io)?;
            if bytes.len() as u64 != manifest.byte_length
                || format!("sha256:{}", hash(&bytes)) != manifest.digest.as_str()
            {
                return Err(StoreError::Corrupt(
                    "retained source failed publication verification".into(),
                ));
            }
        }
    }

    query(&c, "BEGIN TRANSACTION")?;
    let result = (|| {
        for t in TABLES {
            query(&c, &format!("MATCH ()-[r:{t}]->() DELETE r"))?;
        }
        query(&c, "MATCH (u:Unresolved) DELETE u")?;
        let mut expected = 0;

        for (ordinal, r) in relations.iter().enumerate() {
            let data = codec::text(&codec::relation(r));
            let site = hash(data.as_bytes());
            let from = codec::text(&codec::id(&r.from));
            let (resolution, targets) = match &r.to {
                Target::Resolved(x) => ("resolved", vec![("Entity", codec::text(&codec::id(x)))]),
                Target::Ambiguous(xs) => (
                    "ambiguous",
                    xs.iter()
                        .map(|x| ("Entity", codec::text(&codec::id(x))))
                        .collect(),
                ),
                Target::Unresolved { name } => {
                    execute(
                        &c,
                        "CREATE (:Unresolved {key:$key, name:$name})",
                        vec![("key", site.clone().into()), ("name", name.clone().into())],
                    )?;
                    ("unresolved", vec![("Unresolved", site.clone())])
                }
            };

            for (label, to) in targets {
                let q = format!(
                    "MATCH (a:Entity {{key:$from}}), (b:{label} {{key:$to}}) CREATE (a)-[:{} {{data:$data, site:$site, resolution:$resolution, physical:$physical, ordinal:$ordinal, basis:$basis, ev_start:$ev_start, ev_end:$ev_end, ev_digest:$ev_digest}}]->(b)",
                    table(r.kind)
                );
                execute(
                    &c,
                    &q,
                    vec![
                        ("from", from.clone().into()),
                        ("to", to.into()),
                        ("data", data.clone().into()),
                        ("site", site.clone().into()),
                        ("resolution", resolution.into()),
                        (
                            "physical",
                            Value::Bool(r.kind == RelationKind::Contains(Containment::Physical)),
                        ),
                        ("ordinal", Value::Int64(ordinal as i64)),
                        (
                            "basis",
                            match r.basis {
                                Basis::Syntactic => "syntactic",
                                Basis::Resolved => "resolved",
                                Basis::Heuristic => "heuristic",
                            }
                            .into(),
                        ),
                        (
                            "ev_start",
                            r.evidence
                                .as_ref()
                                .map_or(Ok(-1), |e| {
                                    i64::try_from(e.range.start).map_err(|_| {
                                        StoreError::InvalidBatch(
                                            "evidence offset exceeds native range".into(),
                                        )
                                    })
                                })?
                                .into(),
                        ),
                        (
                            "ev_end",
                            r.evidence
                                .as_ref()
                                .map_or(Ok(-1), |e| {
                                    i64::try_from(e.range.end).map_err(|_| {
                                        StoreError::InvalidBatch(
                                            "evidence offset exceeds native range".into(),
                                        )
                                    })
                                })?
                                .into(),
                        ),
                        (
                            "ev_digest",
                            r.evidence.as_ref().map_or("", |e| e.digest.as_str()).into(),
                        ),
                    ],
                )?;
                expected += 1;
            }
        }

        let mut actual = 0;
        for t in TABLES {
            actual += count(&c, &format!("MATCH ()-[r:{t}]->() RETURN count(r)"))?;
        }
        if actual != expected || count(&c, "MATCH (e:Entity) RETURN count(e)")? != entities.len() {
            return Err(StoreError::Corrupt("graph endpoint/count mismatch".into()));
        }
        let readback = load_relations(&c)?;
        if readback != relations {
            return Err(StoreError::Corrupt(
                "graph relation readback mismatch".into(),
            ));
        }
        query(&c, "COMMIT")?;
        Ok(())
    })();
    if result.is_err() {
        let _ = query(&c, "ROLLBACK");
        return result;
    }
    query(&c, "CHECKPOINT")?;
    drop(c);

    s.poisoned = true;
    let g = s.staged.remove(&key).ok_or(StoreError::NotStaged)?;
    fs::write(
        g.path.join("MANIFEST"),
        codec::text(&codec::manifest(&g.manifest)),
    )
    .map_err(io)?;
    drop(g.db);
    sync_tree(&g.path)?;
    let sealed = s.root.join("generations").join(generation(&key));
    fs::rename(&g.path, &sealed).map_err(io)?;
    sync(&s.root.join("generations"))?;
    let published = Arc::new(Generation {
        db: read_db(&sealed.join("db"))?,
        manifest: g.manifest,
        path: sealed,
        _owner: s.owner.clone(),
    });
    let tmp = s.root.join("CURRENT.tmp");
    fs::write(&tmp, format!("{}\n", generation(&key))).map_err(io)?;
    sync(&tmp)?;
    fs::rename(tmp, s.root.join("CURRENT")).map_err(io)?;
    sync(&s.root)?;
    s.published.insert(key.clone(), published);
    s.current = Some(key);
    s.poisoned = false;
    Ok(())
}
