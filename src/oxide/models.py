"""Typed public data model of OXIDE v2 (ADR-0011, SPEC § Repository knowledge model).

Every value here is plain, immutable data. Entity keys are the domain identity
(ADR-0010): deterministic, snapshot-local and never a database row id.

- repository: ``repo``
- module: ``module:python:a.b``
- file: ``file:<repo path>``
- symbol: ``symbol:<repo path>#Outer.inner~1`` (``~n`` only for a nonzero ordinal)

Domain order is the code point order of keys.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Category = Literal["repository", "module", "file", "symbol"]
RelationKind = Literal[
    "contains", "contains_logical", "defines", "references", "calls", "imports", "tested_by"
]
Resolution = Literal["resolved", "ambiguous", "unresolved"]
Basis = Literal["syntactic", "resolved", "heuristic"]
Coverage = Literal["complete", "partial", "unsupported"]
SkipReason = Literal["symlink", "special", "too_large", "unreadable", "invalid_name"]
HintKind = Literal["selection", "path", "symbol", "changed"]


@dataclass(frozen=True, slots=True)
class SourceRef:
    """Half-open byte range ``[start, end)`` of one captured file, named by its digest."""

    path: str
    start: int
    end: int
    digest: str


@dataclass(frozen=True, slots=True)
class Entity:
    key: str
    category: Category
    kind: str
    name: str
    signature: str | None
    source: SourceRef | None
    test: bool


@dataclass(frozen=True, slots=True)
class Relation:
    """One typed edge. An ambiguous relation is several rows sharing ``site``; an
    unresolved one has no ``target``, only the ``name`` it used."""

    kind: RelationKind
    source: str
    target: str | None
    resolution: Resolution
    basis: Basis
    name: str | None
    site: int
    evidence: SourceRef | None


@dataclass(frozen=True, slots=True)
class Snapshot:
    """Which published generation a result came from (ADR-0010 identities)."""

    repo_id: str
    snapshot_id: str
    derivation_id: str


@dataclass(frozen=True, slots=True)
class FileInfo:
    path: str
    digest: str
    size: int
    language: str
    coverage: Coverage
    diagnostics: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Skip:
    path: str
    reason: SkipReason


@dataclass(frozen=True, slots=True)
class IndexReport:
    snapshot: Snapshot
    status: Literal["built", "unchanged"]
    files: int
    entities: int
    relations: int
    partial: tuple[str, ...]
    unsupported: int
    skipped: tuple[Skip, ...]
    case_collisions: tuple[tuple[str, ...], ...]
    lexical: str
    seconds: float


@dataclass(frozen=True, slots=True)
class ChannelStatus:
    """``ran``: ``detail`` names the scorer. ``disabled``/``unavailable``: why.
    An unavailable channel is never reported as empty or as a zero score."""

    state: Literal["ran", "disabled", "unavailable"]
    detail: str
    returned: int = 0
    truncated: bool = False


@dataclass(frozen=True, slots=True)
class Seed:
    kind: HintKind
    hint: str
    case_insensitive: bool = False


@dataclass(frozen=True, slots=True)
class HintIssue:
    kind: HintKind
    hint: str
    issue: Literal["not_found", "ambiguous", "truncated"]
    count: int = 0


@dataclass(frozen=True, slots=True)
class EntryPoint:
    entity: Entity
    lexical_rank: int | None
    lexical_score: float | None
    seeds: tuple[Seed, ...]


@dataclass(frozen=True, slots=True)
class SearchResult:
    """Entry points, not context: seeds in hint order, then lexical rank."""

    snapshot: Snapshot
    query: str
    entries: tuple[EntryPoint, ...]
    lexical: ChannelStatus
    hints: tuple[HintIssue, ...]


@dataclass(frozen=True, slots=True)
class ItemEntity:
    key: str
    name: str
    complete: bool
    reason: str


@dataclass(frozen=True, slots=True)
class ContextItem:
    source: SourceRef
    lines: tuple[int, int]
    entities: tuple[ItemEntity, ...]
    units: int
    text: str


@dataclass(frozen=True, slots=True)
class Omission:
    key: str
    stage: Literal["selection", "packing"]
    reason: str


@dataclass(frozen=True, slots=True)
class RouteSummary:
    root_fallback: bool
    regions: int
    candidates: int
    deferred: int
    fanout_truncations: int
    stopped_by: str | None


@dataclass(frozen=True, slots=True)
class ContextBundle:
    """``payload`` is the agent-facing context; ``used <= budget`` in ``units``.
    Everything else is the diagnostic envelope, outside the budget."""

    snapshot: Snapshot
    query: str
    budget: int
    used: int
    units: str
    items: tuple[ContextItem, ...]
    omissions: tuple[Omission, ...]
    lexical: ChannelStatus
    hints: tuple[HintIssue, ...]
    route: RouteSummary
    degradations: tuple[str, ...]
    versions: dict[str, str] = field(default_factory=dict)

    @property
    def payload(self) -> str:
        return "".join(item.text for item in self.items)


class OxideError(Exception):
    """Base of every OXIDE failure; ``code`` is stable, the message is for humans."""

    code = "oxide_error"


class InvalidPath(OxideError):
    code = "invalid_path"


class OwnershipConflict(OxideError):
    code = "ownership_conflict"


class NotIndexed(OxideError):
    code = "not_indexed"


class IncompatibleIndex(OxideError):
    code = "incompatible_index"


class StoreCorrupt(OxideError):
    code = "store_corrupt"


class CaptureConflict(OxideError):
    code = "capture_conflict"


class CaptureLimit(OxideError):
    code = "capture_limit"


class Closed(OxideError):
    code = "closed"


class InvalidArgument(OxideError, ValueError):
    """A malformed or out-of-range argument; still a ``ValueError`` for callers."""

    code = "invalid_argument"
