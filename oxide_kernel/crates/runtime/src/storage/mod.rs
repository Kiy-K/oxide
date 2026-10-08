//! Lock-owning, generation-isolated Ladybug adapter. Native values stay private.
mod codec;

use lbug::{Connection, Database, SystemConfig, Value};

use oxide_kernel::lexical::{self, LexicalHit, LexicalRequest, LexicalResult};
use oxide_kernel::{id::*, knowledge::*, source::SourceCapture, store::*};

use sha2::{Digest as _, Sha256};

use std::{
    collections::{BTreeMap, BTreeSet},
    fs::{self, File, OpenOptions},
    path::{Path, PathBuf},
    sync::{Arc, Weak, mpsc},
    thread,
};

type R<T> = Result<T, StoreError>;

/// LadybugDB on-disk format at the pinned lbug (ADR-0002 § 1). A generation
/// in another format is rebuilt, never migrated.
pub const STORAGE_FORMAT: u64 = 47;

/// Version of this adapter's physical schema and domain encodings, recorded
/// in every generation's MANIFEST and checked on reopen beside the storage
/// format: a generation is never read under another encoding. 1 had a
/// `Pending` staging table; 2 bulk-loads at publication.
pub const SCHEMA_VERSION: u64 = 2;

/// The derivation component naming [`SCHEMA_VERSION`].
pub fn schema() -> String {
    format!("oxide-ladybug-schema-v{SCHEMA_VERSION}")
}

/// The pinned FTS extension (ADR-0002 § 3, `mise run native:prepare`).
const FTS_SHA256: &str = "742f2756c2f80bcd1886b6ee0446483038cff0ecf6cf47f698bc6fe855cbaef6";

/// The FTS extension file, verified now: its digest is checked on every
/// call (derivation, staging, each generation open) right before it is
/// used. The error says why lexical retrieval is unavailable. Never
/// downloaded or `INSTALL`ed here.
fn fts_extension() -> Result<&'static str, String> {
    let path = env!("OXIDE_FTS_EXTENSION");
    if path.contains(['\'', '\\']) {
        return Err("FTS extension path is not loadable".into());
    }
    let bytes = fs::read(path)
        .map_err(|e| format!("FTS extension missing ({e}); run mise run native:prepare"))?;
    if hash(&bytes) != FTS_SHA256 {
        return Err("FTS extension digest mismatch; run mise run native:prepare".into());
    }
    Ok(path)
}

/// The derivation component naming a generation's lexical index: what is
/// indexed (OXIDE terms) and how (pinned FTS, porter stemmer, default
/// stopwords), or that the generation has none.
pub fn lexical_component() -> String {
    match fts_extension() {
        Ok(_) => format!(
            "ladybug-fts-0.21.0;stemmer=porter;stopwords=default;{}",
            lexical::TERMS_VERSION
        ),
        Err(_) => "unavailable".into(),
    }
}

/// Query-time scoring of [`lexical_component`]: BM25 with fixed
/// parameters, every term optional.
const FTS_SCORER: &str = "ladybug-fts-0.21.0-bm25;k1=1.2;b=0.75;stemmer=porter;disjunctive";

fn load_fts(db: &Database) -> R<()> {
    let path = fts_extension().map_err(StoreError::Unsupported)?;
    let c = Connection::new(db).map_err(native)?;
    query(&c, &format!("LOAD EXTENSION '{path}'")).map(drop)
}

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
fn is_generation_name(name: &str) -> bool {
    name.len() == 64
        && name
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

/// Opens one sealed generation read-only after verifying its identity.
/// `None` means it was written in an older storage format or adapter
/// encoding and must be rebuilt. A newer one belongs to a newer OXIDE: it is
/// refused, never deleted.
fn load_generation(path: &Path, name: &str, owner: &Arc<Lock>) -> R<Option<Generation>> {
    let manifest = codec::parse(&fs::read_to_string(path.join("MANIFEST")).map_err(io)?)?;
    // Schema 1 manifests have no encoding field: the key sits in its place.
    let schema = if manifest[1].is_array() {
        Some(1)
    } else {
        manifest[1].as_u64()
    };
    let fields = [
        ("storage format", manifest[0].as_u64(), STORAGE_FORMAT),
        ("adapter encoding", schema, SCHEMA_VERSION),
    ];
    // Newer anywhere is refused before older anywhere is discarded, so a
    // newer OXIDE's generation is never deleted.
    for (what, found, ours) in fields {
        match found {
            None => {
                return Err(StoreError::Corrupt(format!(
                    "invalid {what}; rebuild required"
                )));
            }
            Some(v) if v > ours => {
                return Err(StoreError::Unsupported(format!(
                    "store uses {what} {v}, newer than this runtime's {ours}"
                )));
            }
            Some(_) => {}
        }
    }
    if fields.iter().any(|(_, found, ours)| *found < Some(*ours)) {
        return Ok(None);
    }
    let m = codec::de_manifest(&manifest)?;
    let derivation_path = path.join("DERIVATION");
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
    Ok(Some(Generation::open(path.to_path_buf(), m, owner)?))
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

/// A sealed, read-only generation. Its `Database` and buffer pool live
/// exactly as long as the current pointer or some view holds it.
struct Generation {
    db: Database,
    manifest: RepositorySnapshot,
    path: PathBuf,
    /// Why lexical search is unavailable, or `None` when the FTS extension
    /// is loaded and this generation has its index.
    lexical: Option<String>,
    _owner: Arc<Lock>,
}

impl Generation {
    fn open(path: PathBuf, manifest: RepositorySnapshot, owner: &Arc<Lock>) -> R<Self> {
        let db = read_db(&path.join("db"))?;
        // A generation stays readable without its accelerator: lexical
        // search then reports why it is unavailable.
        let lexical = match load_fts(&db) {
            Err(StoreError::Unsupported(why)) => Some(why),
            Err(e) => Some(format!("FTS extension failed to load: {e}")),
            Ok(()) => {
                let c = Connection::new(&db).map_err(native)?;
                let indexed = query(&c, "CALL SHOW_INDEXES() RETURN index_name")?
                    .iter()
                    .any(|r| matches!(r.as_slice(), [Value::String(n)] if n == "lexical"));
                (!indexed).then(|| "generation was built without a lexical index".into())
            }
        };
        Ok(Self {
            db,
            manifest,
            path,
            lexical,
            _owner: owner.clone(),
        })
    }
}

/// A generation being built. Facts stay in memory until publication
/// bulk-loads them, so a write is checked whole before anything is kept and
/// nothing half-written reaches the database.
struct Staged {
    db: Database,
    manifest: RepositorySnapshot,
    path: PathBuf,
    entities: BTreeMap<EntityId, Entity>,
    relations: Vec<Relation>,
}

struct State {
    root: PathBuf,
    staged: BTreeMap<SnapshotKey, Staged>,
    /// Sealed generations of this process that are still open. Only
    /// `current` holds one strongly; any other stays open while a view pins
    /// it and is collected after the last view drops.
    sealed: BTreeMap<SnapshotKey, Weak<Generation>>,
    current: Option<Arc<Generation>>,
    poisoned: bool,
    owner: Arc<Lock>,
}

impl State {
    fn current_key(&self) -> Option<&SnapshotKey> {
        self.current.as_ref().map(|g| &g.manifest.key)
    }

    fn live(&self, key: &SnapshotKey) -> Option<Arc<Generation>> {
        self.sealed.get(key).and_then(Weak::upgrade)
    }

    /// Removes superseded generations no view pins. Their `Database` closed
    /// when the last `Arc` dropped; this deletes the directory. Linux unlink
    /// semantics keep that safe even while such a close is still finishing.
    fn collect(&mut self) {
        let dead: Vec<SnapshotKey> = self
            .sealed
            .iter()
            .filter(|(_, g)| g.strong_count() == 0)
            .map(|(k, _)| k.clone())
            .collect();
        for key in dead {
            // Kept (and retried next call) if removal fails; publication
            // also clears a leftover directory before sealing over it.
            let dir = self.root.join("generations").join(generation(&key));
            if fs::remove_dir_all(&dir).is_ok() || !dir.exists() {
                self.sealed.remove(&key);
            }
        }
    }
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
        if lbug::get_storage_version() != STORAGE_FORMAT {
            return Err(StoreError::Unsupported(
                "linked LadybugDB storage format differs from the pin".into(),
            ));
        }
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
            sealed: BTreeMap::new(),
            current: None,
            poisoned: false,
        };

        // Recovery (ADR-0002 § 6.7). Only CURRENT names a published
        // generation; no view can be pinned before startup, so every other
        // directory is an incomplete build, a seal that never reached CURRENT
        // or a superseded generation. CURRENT is checked before anything is
        // removed, and a bad pointer fails closed.
        let current = match fs::read_to_string(state.root.join("CURRENT")) {
            Ok(name) => {
                let name = name.trim().to_owned();
                if !is_generation_name(&name) {
                    return Err(StoreError::Corrupt(
                        "invalid CURRENT; rebuild required".into(),
                    ));
                }
                if !dirs.join(&name).is_dir() {
                    return Err(StoreError::Corrupt(
                        "CURRENT generation missing; rebuild required".into(),
                    ));
                }
                Some(name)
            }
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => None,
            Err(e) => return Err(io(e)),
        };
        let mut entries = Vec::new();
        for entry in fs::read_dir(&dirs).map_err(io)? {
            let entry = entry.map_err(io)?;
            let name = entry.file_name().to_string_lossy().into_owned();
            let unpublished = name
                .strip_suffix(".staging")
                .is_some_and(is_generation_name);
            if !unpublished && !is_generation_name(&name) {
                return Err(StoreError::Corrupt(
                    "invalid generation directory; rebuild required".into(),
                ));
            }
            entries.push((name, entry.path()));
        }
        if let Some(name) = &current {
            match load_generation(&dirs.join(name), name, &state.owner)? {
                Some(g) => {
                    let g = Arc::new(g);
                    state
                        .sealed
                        .insert(g.manifest.key.clone(), Arc::downgrade(&g));
                    state.current = Some(g);
                }
                // Older storage format: rebuild, never migrate.
                None => {
                    fs::remove_file(state.root.join("CURRENT")).map_err(io)?;
                    sync(&state.root)?;
                }
            }
        }
        for (name, path) in entries {
            if state.current.is_none() || current.as_deref() != Some(name.as_str()) {
                fs::remove_dir_all(path).map_err(io)?;
            }
        }
        sync(&dirs)?;

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
                s.collect();
                let result = if s.poisoned {
                    Err(StoreError::Corrupt(
                        "publication durability failed; reopen required".into(),
                    ))
                } else {
                    f(s)
                };
                // Frees whatever this job superseded right away.
                s.collect();
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
    /// Atomically points CURRENT at a generation this store sealed and still
    /// holds open, e.g. when a rebuild reproduces a snapshot a view pins.
    /// `MissingSnapshot` when it is not open (never sealed, or collected).
    pub fn activate(&mut self, key: &SnapshotKey) -> R<()> {
        let key = key.clone();
        self.call(move |s| {
            let g = s.live(&key).ok_or(StoreError::MissingSnapshot)?;
            point_current(s, &key)?;
            s.current = Some(g);
            s.poisoned = false;
            Ok(())
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
            if let Some(generation) = s.live(&key) {
                return Ok(LadybugView { generation });
            }

            let available = s
                .sealed
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
            s.current_key()
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
        let mut lookup = c
            .prepare("MATCH (e:Entity {key:$key}) RETURN e.data")
            .map_err(native)?;

        let result = ids
            .iter()
            .map(|id| {
                let rows: Vec<Vec<Value>> = c
                    .execute(
                        &mut lookup,
                        vec![("key", codec::text(&codec::id(id)).into())],
                    )
                    .map_err(native)?
                    .collect();

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
        let key = codec::text(&codec::id(&r.entity));
        let sides: &[&str] = match r.direction {
            Direction::Outgoing => &["(a:Entity {key:$key})-[e]->(b)"],
            Direction::Incoming => &["(a:Entity)-[e]->(b:Entity {key:$key})"],
            Direction::Both => &[
                "(a:Entity {key:$key})-[e]->(b)",
                "(a:Entity)-[e]->(b:Entity {key:$key})",
            ],
        };
        let c = Connection::new(&self.generation.db).map_err(native)?;
        query(&c, "BEGIN TRANSACTION READ ONLY")?;
        // One query per direction over every relation table, filtered and
        // ordered here: the stored ordinal is the position in the sorted
        // domain relation set, and native ORDER BY/LIMIT cost more than the
        // rest of the query (docs/phase-3.md).
        // ponytail: fetches the entity's whole degree per direction; push
        // the limit down if high-degree nodes make that measurable.
        let rows = sides
            .iter()
            .map(|side| {
                execute(
                    &c,
                    &format!("MATCH {side} RETURN e.ordinal, e.data"),
                    vec![("key", key.clone().into())],
                )
            })
            .collect::<R<Vec<_>>>();
        let _ = query(&c, "COMMIT");
        let mut selected: BTreeMap<i64, Relation> = BTreeMap::new();
        let mut any = false;
        for row in rows?.into_iter().flatten() {
            any = true;
            let [Value::Int64(ordinal), Value::String(data)] = row.as_slice() else {
                return Err(StoreError::Corrupt("invalid adjacency row".into()));
            };
            if !selected.contains_key(ordinal) {
                let relation = codec::de_relation(&codec::parse(data)?)?;
                if r.kinds.contains(&relation.kind) {
                    selected.insert(*ordinal, relation);
                }
            }
        }
        if !any && self.entities(std::slice::from_ref(&r.entity))?[0].is_none() {
            return Err(StoreError::MissingEntity(r.entity.clone()));
        }
        let truncated = selected.len() > r.limit;
        Ok(Adjacency {
            edges: selected.into_values().take(r.limit).collect(),
            truncated,
        })
    }

    fn lexical(&self, r: &LexicalRequest) -> R<LexicalResult> {
        if r.limit > MAX_REQUEST_ITEMS {
            return Err(StoreError::LimitExceeded {
                limit: MAX_REQUEST_ITEMS,
            });
        }
        if let Some(why) = &self.generation.lexical {
            return Err(StoreError::Unsupported(format!("lexical search: {why}")));
        }
        let text = r.terms.join(" ");
        let mut hits = Vec::new();
        if !text.is_empty() {
            let c = Connection::new(&self.generation.db).map_err(native)?;
            query(&c, "BEGIN TRANSACTION READ ONLY")?;
            // FTS order is unspecified and `top` cuts ties arbitrarily (measured
            // at the pin), so every match is ranked here.
            // ponytail: all matches materialize; push a bounded top-k down once
            // its tie handling is proven.
            let rows = execute(
                &c,
                "CALL QUERY_FTS_INDEX('Entity', 'lexical', $q, conjunctive := false, K := 1.2, B := 0.75) RETURN node.key, score",
                vec![("q", text.into())],
            );
            let _ = query(&c, "COMMIT");
            for row in rows? {
                let [Value::String(key), Value::Double(score)] = row.as_slice() else {
                    return Err(StoreError::Corrupt("invalid lexical row".into()));
                };
                hits.push(LexicalHit {
                    entity: codec::de_id(&codec::parse(key)?)?,
                    score: *score,
                });
            }
        }
        lexical::rank(FTS_SCORER.into(), hits, r.limit).map_err(StoreError::Corrupt)
    }

    fn named(&self, name: &str, limit: usize) -> R<Named> {
        if limit > MAX_REQUEST_ITEMS {
            return Err(StoreError::LimitExceeded {
                limit: MAX_REQUEST_ITEMS,
            });
        }
        let c = Connection::new(&self.generation.db).map_err(native)?;
        query(&c, "BEGIN TRANSACTION READ ONLY")?;
        let rows = execute(
            &c,
            "MATCH (e:Entity) WHERE e.name = $name RETURN e.key",
            vec![("name", name.into())],
        );
        let _ = query(&c, "COMMIT");
        // Domain order, not key-text order, before the limit.
        let mut entities = rows?
            .iter()
            .map(|row| match row.as_slice() {
                [Value::String(key)] => codec::de_id(&codec::parse(key)?),
                _ => Err(StoreError::Corrupt("invalid entity row".into())),
            })
            .collect::<R<Vec<_>>>()?;
        entities.sort();
        let truncated = entities.len() > limit;
        entities.truncate(limit);
        Ok(Named {
            entities,
            truncated,
        })
    }
}

fn begin_state(s: &mut State, manifest: RepositorySnapshot) -> R<()> {
    let key = manifest.key.clone();
    s.collect();
    if s.staged.contains_key(&key) || s.live(&key).is_some() {
        return Err(StoreError::Conflict);
    }
    let path = s
        .root
        .join("generations")
        .join(format!("{}.staging", generation(&key)));
    // COPY FROM takes a quoted path literal; refuse rather than escape.
    if path.to_str().is_none_or(|p| p.contains(['\'', '\\'])) {
        return Err(StoreError::Unsupported(
            "store path must be UTF-8 without quotes or backslashes".into(),
        ));
    }
    fs::create_dir(&path).map_err(io)?;
    let created = create_staged(&path);
    if created.is_err() {
        // Never left behind half-made: a retry of this key starts clean.
        let _ = fs::remove_dir_all(&path);
    }
    s.staged.insert(
        key,
        Staged {
            db: created?,
            manifest,
            path,
            entities: BTreeMap::new(),
            relations: Vec::new(),
        },
    );
    Ok(())
}

/// The empty schema-2 database of a new staged generation.
fn create_staged(path: &Path) -> R<Database> {
    let database = db(&path.join("db"))?;
    let c = Connection::new(&database).map_err(native)?;
    query(
        &c,
        "CREATE NODE TABLE Entity(key STRING PRIMARY KEY, data STRING, category STRING, kind STRING, name STRING, signature STRING, test BOOLEAN, file STRING, start_byte INT64, end_byte INT64, digest STRING, language STRING, coverage STRING, terms STRING)",
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
    // Same availability as the derivation component that names this key.
    if fts_extension().is_ok() {
        load_fts(&database)?;
    }
    Ok(database)
}

/// A source offset as the database's INT64.
fn offset(source: Option<&SourceRef>, end: bool) -> R<i64> {
    source.map_or(Ok(-1), |s| {
        i64::try_from(if end { s.range.end } else { s.range.start })
            .map_err(|_| StoreError::InvalidBatch("source offset exceeds native range".into()))
    })
}

fn write_state(s: &mut State, key: SnapshotKey, batch: Batch) -> R<()> {
    let g = s.staged.get_mut(&key).ok_or(StoreError::NotStaged)?;
    // Check the whole batch first so a rejected batch keeps nothing.
    let mut ids = BTreeSet::new();
    for e in &batch.entities {
        if g.entities.contains_key(&e.id) || !ids.insert(&e.id) {
            return Err(StoreError::InvalidBatch("duplicate entity".into()));
        }
        offset(e.source.as_ref(), true)?;
    }
    for r in &batch.relations {
        offset(r.evidence.as_ref(), true)?;
    }
    g.entities
        .extend(batch.entities.into_iter().map(|e| (e.id.clone(), e)));
    g.relations.extend(batch.relations);
    Ok(())
}

/// One CSV field, always quoted, quotes doubled. COPY reads it back
/// byte-exactly (commas, quotes, backslashes, newlines and empty strings
/// verified at the pin, docs/phase-3.md).
fn field(s: &str) -> String {
    format!("\"{}\"", s.replace('"', "\"\""))
}

/// Bulk-loads one staged generation's facts in its open write transaction.
fn load(c: &Connection, g: &Staged, dir: &Path) -> R<()> {
    let copy = |table: &str, file: &str, rows: &str, pair: Option<&str>| -> R<()> {
        if rows.is_empty() {
            return Ok(());
        }
        let path = dir.join(file);
        fs::write(&path, rows).map_err(io)?;
        let pair = pair.map_or(String::new(), |to| format!("from='Entity', to='{to}', "));
        query(
            c,
            &format!(
                "COPY {table} FROM '{}' ({pair}header=false, quote='\"', escape='\"', delim=',', parallel=false)",
                path.display()
            ),
        )
        .map(drop)
    };

    let mut rows = String::new();
    for e in g.entities.values() {
        let category = match e.id {
            EntityId::Repository => "repository",
            EntityId::Module(_) => "module",
            EntityId::File(_) => "file",
            EntityId::Symbol(_) => "symbol",
        };
        let file = match &e.id {
            EntityId::File(p) => Some(p),
            EntityId::Symbol(s) => Some(s.file()),
            _ => None,
        };
        let m = file.and_then(|p| g.manifest.files.get(p));
        let source = e.source.as_ref();
        let cells = [
            field(&codec::text(&codec::id(&e.id))),
            field(&codec::text(&codec::entity(e))),
            field(category),
            field(&e.kind),
            field(&e.name),
            field(e.signature.as_deref().unwrap_or_default()),
            e.test.to_string(),
            field(file.map_or("", RepoPath::as_str)),
            offset(source, false)?.to_string(),
            offset(source, true)?.to_string(),
            field(source.map_or("", |s| s.digest.as_str())),
            field(m.map_or("", |m| m.language.as_str())),
            field(m.map_or("", |m| match m.coverage {
                Coverage::Complete => "complete",
                Coverage::Partial { .. } => "partial",
                Coverage::Unsupported { .. } => "unsupported",
            })),
            field(&lexical::document(e)),
        ];
        rows += &cells.join(",");
        rows.push('\n');
    }
    copy("Entity", "entities.csv", &rows, None)?;

    // Ordinal: position in the sorted domain relation set, the stable
    // adjacency order. An ambiguous relation is one row per candidate
    // sharing its site.
    let mut unresolved = String::new();
    let mut edges: BTreeMap<(&str, &str), String> = BTreeMap::new();
    for (ordinal, r) in g.relations.iter().enumerate() {
        let data = codec::text(&codec::relation(r));
        let site = hash(data.as_bytes());
        let (resolution, label, targets) = match &r.to {
            Target::Resolved(x) => ("resolved", "Entity", vec![codec::text(&codec::id(x))]),
            Target::Ambiguous(xs) => (
                "ambiguous",
                "Entity",
                xs.iter().map(|x| codec::text(&codec::id(x))).collect(),
            ),
            Target::Unresolved { name } => {
                unresolved += &format!("{},{}\n", field(&site), field(name));
                ("unresolved", "Unresolved", vec![site.clone()])
            }
        };
        let from = codec::text(&codec::id(&r.from));
        let evidence = r.evidence.as_ref();
        let basis = match r.basis {
            Basis::Syntactic => "syntactic",
            Basis::Resolved => "resolved",
            Basis::Heuristic => "heuristic",
        };
        let rows = edges.entry((table(r.kind), label)).or_default();
        for to in targets {
            let cells = [
                field(&from),
                field(&to),
                field(&data),
                field(&site),
                field(resolution),
                (r.kind == RelationKind::Contains(Containment::Physical)).to_string(),
                ordinal.to_string(),
                field(basis),
                offset(evidence, false)?.to_string(),
                offset(evidence, true)?.to_string(),
                field(evidence.map_or("", |e| e.digest.as_str())),
            ];
            *rows += &cells.join(",");
            rows.push('\n');
        }
    }
    copy("Unresolved", "unresolved.csv", &unresolved, None)?;
    for ((t, label), rows) in &edges {
        copy(t, &format!("{t}-{label}.csv"), rows, Some(label))?;
    }
    Ok(())
}

fn publish_state(s: &mut State, key: SnapshotKey) -> R<()> {
    let g = s.staged.get_mut(&key).ok_or(StoreError::NotStaged)?;
    g.relations.sort();
    g.relations.dedup();
    validate(&g.manifest, &g.entities, &g.relations).map_err(StoreError::InvalidBatch)?;
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

    let g = &*g;
    let dir = g.path.join("load");
    let _ = fs::remove_dir_all(&dir);
    fs::create_dir(&dir).map_err(io)?;
    let c = Connection::new(&g.db).map_err(native)?;
    query(&c, "BEGIN TRANSACTION")?;
    let result = (|| {
        load(&c, g, &dir)?;
        // ADR-0002 § 6.3: what was written reads back exactly. Counting
        // rows catches a skipped endpoint; the decoded readback catches any
        // encoding loss.
        let expected: usize = g
            .relations
            .iter()
            .map(|r| r.to.entities().len().max(1))
            .sum();
        let mut actual = 0;
        for t in TABLES {
            actual += count(&c, &format!("MATCH ()-[r:{t}]->() RETURN count(r)"))?;
        }
        if actual != expected || load_entities(&c)? != g.entities {
            return Err(StoreError::Corrupt("graph endpoint/count mismatch".into()));
        }
        if load_relations(&c)? != g.relations {
            return Err(StoreError::Corrupt(
                "graph relation readback mismatch".into(),
            ));
        }
        query(&c, "COMMIT")?;
        Ok(())
    })();
    // The load files are scratch; startup removes the staging dir anyway.
    let _ = fs::remove_dir_all(&dir);
    if result.is_err() {
        let _ = query(&c, "ROLLBACK");
        return result;
    }
    // FTS indexes are built only in auto-commit mode, so after the load
    // commits. A failure here leaves the generation staged, never sealed.
    if fts_extension().is_ok() {
        query(
            &c,
            "CALL CREATE_FTS_INDEX('Entity', 'lexical', ['terms'], stemmer := 'porter')",
        )?;
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
    if sealed.exists() && s.live(&key).is_none() {
        fs::remove_dir_all(&sealed).map_err(io)?;
    }
    fs::rename(&g.path, &sealed).map_err(io)?;
    sync(&s.root.join("generations"))?;
    let published = Arc::new(Generation::open(sealed, g.manifest, &s.owner)?);
    point_current(s, &key)?;
    s.sealed.insert(key, Arc::downgrade(&published));
    s.current = Some(published);
    s.poisoned = false;
    Ok(())
}

/// Publication: the only step readers observe (ADR-0002 § 6.5).
/// A failure before the rename leaves CURRENT unchanged; from the rename on
/// the store is poisoned until the caller clears it.
fn point_current(s: &mut State, key: &SnapshotKey) -> R<()> {
    let tmp = s.root.join("CURRENT.tmp");
    fs::write(&tmp, format!("{}\n", generation(key))).map_err(io)?;
    sync(&tmp)?;
    s.poisoned = true;
    fs::rename(tmp, s.root.join("CURRENT")).map_err(io)?;
    sync(&s.root)
}
