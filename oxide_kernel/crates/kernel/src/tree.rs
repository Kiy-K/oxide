//! TreeIndex projection and navigation. Projection 1 is the minimal test
//! hierarchy (README § TreeIndex projection 1): one region per entity,
//! arranged by physical containment (repository ⊃ module/file ⊃ symbol ⊃
//! symbol). Every other relation, including logical containment and cyclic
//! calls/imports, stays a typed cross-edge of the region. Regions are a
//! computed view over the store's typed adjacency, so fake and real stores
//! share this navigation code; materializing them is a later decision.

use crate::id::EntityId;
use crate::knowledge::{Containment, Coverage, Entity, Relation, RelationKind};
use crate::store::{AdjacencyRequest, Direction, ReadView, StoreError};

/// Version of the projection rules that produced a region reference.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct ProjectionId(pub u32);

pub const PROJECTION: ProjectionId = ProjectionId(1);

const HIERARCHY: RelationKind = RelationKind::Contains(Containment::Physical);

/// Domain reference to a TreeIndex region, scoped (like entity IDs) by the
/// read view it is resolved against.
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct RegionId {
    pub projection: ProjectionId,
    pub anchor: EntityId,
}

impl RegionId {
    pub fn of(anchor: EntityId) -> Self {
        Self {
            projection: PROJECTION,
            anchor,
        }
    }

    pub fn root() -> Self {
        Self::of(EntityId::Repository)
    }
}

/// Per-call bounds on what one region view returns.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct NavLimits {
    pub children: usize,
    pub cross_edges: usize,
}

/// One bounded region view: identity, anchor evidence, coverage of the
/// anchor's file (`None` above file scope), hierarchy and cross-edges, each
/// list in stable domain order with an explicit truncation flag.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Region {
    pub id: RegionId,
    pub anchor: Entity,
    pub coverage: Option<Coverage>,
    pub parent: Option<RegionId>,
    pub children: Vec<RegionId>,
    pub children_truncated: bool,
    pub cross_edges: Vec<Relation>,
    pub cross_edges_truncated: bool,
}

pub fn region(
    view: &impl ReadView,
    id: &RegionId,
    limits: NavLimits,
) -> Result<Region, StoreError> {
    if id.projection != PROJECTION {
        return Err(StoreError::Unsupported(format!(
            "projection {}",
            id.projection.0
        )));
    }
    let anchor = view
        .entities(std::slice::from_ref(&id.anchor))?
        .pop()
        .flatten()
        .ok_or_else(|| StoreError::MissingEntity(id.anchor.clone()))?;
    let file = match &id.anchor {
        EntityId::File(path) => Some(path),
        EntityId::Symbol(symbol) => Some(symbol.file()),
        _ => None,
    };
    let coverage = file
        .and_then(|f| view.snapshot().files.get(f))
        .map(|f| f.coverage.clone());

    let adjacency = |direction, kinds: Vec<RelationKind>, limit| {
        view.adjacency(&AdjacencyRequest {
            entity: id.anchor.clone(),
            direction,
            kinds,
            limit,
        })
    };
    let parent = adjacency(Direction::Incoming, vec![HIERARCHY], 1)?
        .edges
        .pop()
        .map(|edge| RegionId::of(edge.from));
    let children = adjacency(Direction::Outgoing, vec![HIERARCHY], limits.children)?;
    let cross_kinds = RelationKind::ALL
        .into_iter()
        .filter(|k| *k != HIERARCHY)
        .collect();
    let cross = adjacency(Direction::Both, cross_kinds, limits.cross_edges)?;

    Ok(Region {
        id: id.clone(),
        anchor,
        coverage,
        parent,
        children: children
            .edges
            .into_iter()
            .flat_map(|edge| edge.to.entities().to_vec())
            .map(RegionId::of)
            .collect(),
        children_truncated: children.truncated,
        cross_edges: cross.edges,
        cross_edges_truncated: cross.truncated,
    })
}
