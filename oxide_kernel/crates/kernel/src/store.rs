//! The `KnowledgeStore` port: the kernel-defined contract for derived
//! repository knowledge, plus [`MemoryStore`], the in-memory implementation
//! the contract suite also runs against the real adapter (Phase 2).
//!
//! Signatures are synchronous. The runtime owns threads and connections and
//! can move blocking calls off its executor; an async port can replace this
//! if the pinned LadybugDB API demands it (README § Phase 1 decisions).

use std::collections::{BTreeMap, BTreeSet};
use std::fmt;
use std::sync::Arc;

use crate::id::{DerivationId, EntityId, RepoId, SnapshotKey};
use crate::knowledge::{Batch, Entity, Relation, RelationKind, RepositorySnapshot, validate};

/// Upper bound on items in one read request (IDs looked up, edges returned).
pub const MAX_REQUEST_ITEMS: usize = 4096;

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum StoreError {
    /// Another runtime owns the derived store.
    OwnershipConflict,
    /// Runtime filesystem operation failed.
    Io(String),
    /// A database batch failed atomically; none of that batch was retained.
    Transaction(String),
    /// No published generation matches.
    MissingSnapshot,
    /// The snapshot is published, but only under these other derivations.
    IncompatibleDerivation {
        available: Vec<DerivationId>,
    },
    /// The entity is not part of the pinned generation.
    MissingEntity(EntityId),
    /// The key is already staged or published; generations are immutable.
    Conflict,
    /// No staged generation has this key.
    NotStaged,
    /// The batch or generation breaks a publication invariant.
    InvalidBatch(String),
    /// The request exceeds a declared bound.
    LimitExceeded {
        limit: usize,
    },
    /// The store, or this view, does not offer the operation.
    Unsupported(String),
    /// Stored evidence failed an integrity check.
    Corrupt(String),
    Cancelled,
}

impl fmt::Display for StoreError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        fmt::Debug::fmt(self, f)
    }
}

impl std::error::Error for StoreError {}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Direction {
    Outgoing,
    Incoming,
    Both,
}

/// Typed adjacency of one entity. Only `kinds` are returned (an empty list
/// matches nothing). Incoming edges include ambiguous targets that list the
/// entity; unresolved targets have no incoming side.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AdjacencyRequest {
    pub entity: EntityId,
    pub direction: Direction,
    pub kinds: Vec<RelationKind>,
    pub limit: usize,
}

/// Edges in [`Relation`] order, at most `limit`; `truncated` when more exist.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Adjacency {
    pub edges: Vec<Relation>,
    pub truncated: bool,
}

/// Write side: stage a generation, fill it, then publish it atomically.
/// Readers never see a staged generation, and a failed publication leaves it
/// staged (and invisible) until discarded.
pub trait KnowledgeStore {
    type View: ReadView;

    /// Pins one published generation. Errors distinguish a missing snapshot
    /// from one published only under other derivations.
    fn open(&self, key: &SnapshotKey) -> Result<Self::View, StoreError>;
    /// The most recently published generation of `repo`.
    fn current(&self, repo: &RepoId) -> Result<SnapshotKey, StoreError>;

    fn begin(&mut self, manifest: RepositorySnapshot) -> Result<(), StoreError>;
    fn write(&mut self, key: &SnapshotKey, batch: Batch) -> Result<(), StoreError>;
    fn publish(&mut self, key: &SnapshotKey) -> Result<(), StoreError>;
    fn discard(&mut self, key: &SnapshotKey) -> Result<(), StoreError>;
}

/// A pinned, immutable view of one published generation. It stays valid and
/// unchanged while later generations are published.
pub trait ReadView {
    fn snapshot(&self) -> &RepositorySnapshot;
    /// One result per requested ID, in request order; `None` is missing.
    fn entities(&self, ids: &[EntityId]) -> Result<Vec<Option<Entity>>, StoreError>;
    fn adjacency(&self, request: &AdjacencyRequest) -> Result<Adjacency, StoreError>;
}

#[derive(Debug)]
struct Generation {
    snapshot: RepositorySnapshot,
    entities: BTreeMap<EntityId, Entity>,
    /// Sorted, so every scan yields the stable adjacency order.
    relations: Vec<Relation>,
}

/// In-memory store for kernel tests and the shared contract suite.
#[derive(Debug, Default)]
pub struct MemoryStore {
    staged: BTreeMap<SnapshotKey, Generation>,
    published: BTreeMap<SnapshotKey, Arc<Generation>>,
    current: BTreeMap<RepoId, SnapshotKey>,
}

#[derive(Debug, Clone)]
pub struct MemoryView(Arc<Generation>);

impl KnowledgeStore for MemoryStore {
    type View = MemoryView;

    fn open(&self, key: &SnapshotKey) -> Result<MemoryView, StoreError> {
        if let Some(generation) = self.published.get(key) {
            return Ok(MemoryView(Arc::clone(generation)));
        }
        let available: Vec<DerivationId> = self
            .published
            .keys()
            .filter(|k| k.repo == key.repo && k.snapshot == key.snapshot)
            .map(|k| k.derivation.clone())
            .collect();
        if available.is_empty() {
            Err(StoreError::MissingSnapshot)
        } else {
            Err(StoreError::IncompatibleDerivation { available })
        }
    }

    fn current(&self, repo: &RepoId) -> Result<SnapshotKey, StoreError> {
        self.current
            .get(repo)
            .cloned()
            .ok_or(StoreError::MissingSnapshot)
    }

    fn begin(&mut self, manifest: RepositorySnapshot) -> Result<(), StoreError> {
        let key = manifest.key.clone();
        if self.staged.contains_key(&key) || self.published.contains_key(&key) {
            return Err(StoreError::Conflict);
        }
        let generation = Generation {
            snapshot: manifest,
            entities: BTreeMap::new(),
            relations: Vec::new(),
        };
        self.staged.insert(key, generation);
        Ok(())
    }

    fn write(&mut self, key: &SnapshotKey, batch: Batch) -> Result<(), StoreError> {
        let generation = self.staged.get_mut(key).ok_or(StoreError::NotStaged)?;
        // Check the whole batch first so a rejected batch writes nothing.
        let mut ids = BTreeSet::new();
        for entity in &batch.entities {
            if generation.entities.contains_key(&entity.id) || !ids.insert(&entity.id) {
                return Err(StoreError::InvalidBatch(format!(
                    "duplicate entity {:?}",
                    entity.id
                )));
            }
        }
        for entity in batch.entities {
            generation.entities.insert(entity.id.clone(), entity);
        }
        generation.relations.extend(batch.relations);
        Ok(())
    }

    fn publish(&mut self, key: &SnapshotKey) -> Result<(), StoreError> {
        let generation = self.staged.get_mut(key).ok_or(StoreError::NotStaged)?;
        generation.relations.sort();
        generation.relations.dedup();
        validate(
            &generation.snapshot,
            &generation.entities,
            &generation.relations,
        )
        .map_err(StoreError::InvalidBatch)?;
        let generation = self.staged.remove(key).expect("checked above");
        self.published.insert(key.clone(), Arc::new(generation));
        self.current.insert(key.repo.clone(), key.clone());
        Ok(())
    }

    fn discard(&mut self, key: &SnapshotKey) -> Result<(), StoreError> {
        self.staged
            .remove(key)
            .map(drop)
            .ok_or(StoreError::NotStaged)
    }
}

impl ReadView for MemoryView {
    fn snapshot(&self) -> &RepositorySnapshot {
        &self.0.snapshot
    }

    fn entities(&self, ids: &[EntityId]) -> Result<Vec<Option<Entity>>, StoreError> {
        if ids.len() > MAX_REQUEST_ITEMS {
            return Err(StoreError::LimitExceeded {
                limit: MAX_REQUEST_ITEMS,
            });
        }
        Ok(ids
            .iter()
            .map(|id| self.0.entities.get(id).cloned())
            .collect())
    }

    fn adjacency(&self, request: &AdjacencyRequest) -> Result<Adjacency, StoreError> {
        if request.limit > MAX_REQUEST_ITEMS {
            return Err(StoreError::LimitExceeded {
                limit: MAX_REQUEST_ITEMS,
            });
        }
        let id = &request.entity;
        if !self.0.entities.contains_key(id) {
            return Err(StoreError::MissingEntity(id.clone()));
        }
        // ponytail: linear scan per call; index edges by endpoint if fake-store
        // suites outgrow it.
        let mut matching = self.0.relations.iter().filter(|r| {
            let out = r.from == *id;
            let inc = r.to.entities().contains(id);
            let side = match request.direction {
                Direction::Outgoing => out,
                Direction::Incoming => inc,
                Direction::Both => out || inc,
            };
            side && request.kinds.contains(&r.kind)
        });
        let edges: Vec<Relation> = matching.by_ref().take(request.limit).cloned().collect();
        Ok(Adjacency {
            edges,
            truncated: matching.next().is_some(),
        })
    }
}
