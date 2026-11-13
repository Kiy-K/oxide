"""Results of code navigation: ``Repository.outline``, ``inspect`` and ``refs``.

Every result names its snapshot. Lines are 1-based and inclusive, computed from the
captured bytes of that snapshot, never from the live worktree.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from oxide.models import Snapshot

Label = Literal["proven", "heuristic", "ambiguous", "unresolved"]
Direction = Literal["in", "out"]
Status = Literal["resolved", "ambiguous", "not_found"]


@dataclass(frozen=True, slots=True)
class Location:
    path: str
    start_line: int
    end_line: int


@dataclass(frozen=True, slots=True)
class Declaration:
    """One declaration. ``depth`` is its nesting inside the same file (0: top level);
    ``location`` is None for entities without source (the repository, modules)."""

    key: str
    kind: str
    name: str
    signature: str | None
    location: Location | None
    test: bool
    depth: int = 0


@dataclass(frozen=True, slots=True)
class OutlineFile:
    path: str
    language: str
    coverage: str
    declarations: tuple[Declaration, ...]
    truncated: bool


@dataclass(frozen=True, slots=True)
class Outline:
    """Declarations of one file, or of the files under a directory, in path then source
    order. ``unsupported`` counts files under the target OXIDE does not parse (not listed);
    ``truncated`` says a limit cut the files or declarations."""

    snapshot: Snapshot
    target: str
    files: tuple[OutlineFile, ...]
    unsupported: int
    truncated: bool


@dataclass(frozen=True, slots=True)
class Candidate:
    """A possible meaning of a name, best first. ``why`` says how the name matched."""

    declaration: Declaration
    why: Literal["key", "path", "line", "qualified", "suffix", "name"]


@dataclass(frozen=True, slots=True)
class Link:
    """How many relations of one kind touch an entity, by label."""

    kind: str
    direction: Direction
    proven: int
    heuristic: int
    ambiguous: int
    unresolved: int


@dataclass(frozen=True, slots=True)
class Inspection:
    """``resolved``: exactly one candidate, detailed below. ``ambiguous``: several, none
    chosen. ``not_found``: none. OXIDE never guesses between candidates."""

    snapshot: Snapshot
    query: str
    status: Status
    candidates: tuple[Candidate, ...]
    truncated: bool
    parent: Declaration | None = None
    members: tuple[Declaration, ...] = ()
    links: tuple[Link, ...] = ()


@dataclass(frozen=True, slots=True)
class Reference:
    """One relation of the subject. ``other`` is the entity at the far end (None when
    unresolved); ``at`` the source line that evidences it, ``text`` that line."""

    kind: str
    direction: Direction
    label: Label
    name: str | None
    other: Declaration | None
    at: Location | None
    text: str | None


@dataclass(frozen=True, slots=True)
class References:
    """The relations of the one entity ``query`` resolved to (``status`` as ``Inspection``;
    ``references`` is empty unless resolved), proven first, then by location. Evidence of
    what OXIDE derived, never a claim of completeness: ``unresolved_same_name`` counts call
    and reference sites by that name that could not be resolved to any entity, which may
    include uses of this one. ``truncated`` says ``limit`` cut the list."""

    snapshot: Snapshot
    query: str
    status: Status
    candidates: tuple[Candidate, ...]
    references: tuple[Reference, ...]
    unresolved_same_name: int
    truncated: bool
