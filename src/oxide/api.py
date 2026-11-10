"""The public Python API: the stable product boundary of OXIDE v2 (ADR-0011).

``index_repository -> search -> build_context`` over persisted repository state::

    with oxide.open_repository("path/to/repo") as repo:
        repo.index()
        hits = repo.search("where failed requests are retried")
        bundle = repo.build_context("where failed requests are retried", budget=1024)
        print(bundle.payload)

Every result names the snapshot it was read from. A ``Repository`` owns its store
exclusively (a second owner, in any process, gets ``OwnershipConflict``). Calls are
serialized with automatic local filesystem updates.
"""

from __future__ import annotations

import os
import threading
import time
import weakref
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from types import TracebackType
from typing import Any, Literal

from oxide import _index, jev
from oxide.capture import CaptureCache
from oxide.core import navigation, ordering, packing, retrieval, routing, structural
from oxide.core.blast import BLAST_ROOTS
from oxide.core.retrieval import Hints
from oxide.models import (
    BlastRoot,
    Closed,
    ContextBundle,
    Entity,
    EntryPoint,
    FileInfo,
    Impact,
    ImpactHop,
    IndexReport,
    InvalidArgument,
    InvalidPath,
    JudgeStatus,
    Relation,
    RouteSummary,
    SearchResult,
    Snapshot,
    StructuralResult,
)
from oxide.storage.fts import prepare_fts
from oxide.storage.ladybug import View, default_data_dir
from oxide.storage.store import Store
from oxide.tracking import RECONCILE_SECONDS, Tracking, serialized

# Upper bounds of caller-chosen sizes, checked before any query is built.
MAX_LIMIT = 1000
MAX_BUDGET = 1 << 20
MAX_DEPTH = 8
_MAX_OFFSET = (1 << 63) - 1  # LadybugDB INT64


def _count(name: str, value: object, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= high:
        raise InvalidArgument(f"{name} must be an integer in [0, {high}], got {value!r}")
    return value


def _texts(name: str, values: Iterable[str]) -> tuple[str, ...]:
    try:
        out = () if isinstance(values, str) else tuple(values)
    except TypeError:
        out = None
    if out is None or isinstance(values, str) or not all(isinstance(v, str) for v in out):
        raise InvalidArgument(f"{name} must be an iterable of strings, got {values!r}")
    return out


def _hints(
    query: str,
    paths: Iterable[str],
    symbols: Iterable[str],
    selection: tuple[str, int, int] | None,
    changed: Iterable[str],
) -> Hints:
    if not isinstance(query, str):
        raise InvalidArgument(f"query must be a string, got {query!r}")
    if selection is not None:
        if not (isinstance(selection, tuple) and len(selection) == 3):
            raise InvalidArgument(f"selection must be (path, start, end), got {selection!r}")
        path, start, end = selection
        _texts("selection path", [path])
        if _count("selection start", start, _MAX_OFFSET) > _count(
            "selection end", end, _MAX_OFFSET
        ):
            raise InvalidArgument(f"selection start {start} is after its end {end}")
    seed_paths = _texts("paths", paths)
    seed_symbols = _texts("symbols", symbols)
    seed_changed = _texts("changed", changed)
    if not query.strip() and not (selection or seed_paths or seed_symbols or seed_changed):
        raise InvalidArgument("query is empty: give words or identifiers, or a path or symbol hint")
    return Hints(selection, seed_paths, seed_symbols, seed_changed)


def _blast(known: Mapping[str, tuple[Entity, BlastRoot]], keys: list[str]) -> tuple[BlastRoot, ...]:
    """The blast radius of the first ``BLAST_ROOTS`` entry points, in result order."""
    return tuple(known[key][1] for key in keys[:BLAST_ROOTS])


class Repository:
    def __init__(
        self,
        path: str | os.PathLike[str],
        data_dir: str | os.PathLike[str] | None = None,
        create: bool = True,
    ) -> None:
        try:
            root = Path(path).resolve(strict=True)
        except OSError as error:
            raise InvalidPath(f"{path}: {error.strerror}") from error
        if not root.is_dir():
            raise InvalidPath(f"{root}: not a directory")
        self.root = root
        data = Path(data_dir or default_data_dir()).absolute()
        self._store: Store | None = Store(root, data, create)
        self._lock = threading.RLock()
        self._cache: CaptureCache = {}
        self._active = False
        self._tracking = Tracking(self, data)
        self._finalizer = weakref.finalize(self, self._tracking.close)

    def __enter__(self) -> Repository:
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        trace: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self._tracking.stop.set()
        with self._lock:
            if self._store is not None:
                self._store.close()
                self._store = None
                self._cache.clear()
        self._tracking.close()
        self._finalizer.detach()

    @property
    def store(self) -> Store:
        if self._store is None:
            raise Closed(f"{self.root}: repository is closed")
        return self._store

    @property
    def repo_id(self) -> str:
        return self.store.repo_id

    @serialized
    def index(self, *, progress: Callable[[str], None] | None = None) -> IndexReport:
        """Fully reconcile captured bytes and publish atomically, reusing unchanged parse facts."""
        return self._index(True, progress, rehash=True)

    def _index(
        self, full: bool, progress: Callable[[str], None] | None = None, *, rehash: bool = False
    ) -> IndexReport:
        self._tracking.dirty.clear()
        if rehash:  # periodic reconciliation only re-walks; stat keys decide which bytes to re-read
            self._cache.clear()
        try:
            report = _index.index(self.root, self.store, self._cache, progress)
        except Exception:
            self._tracking.dirty.set()
            raise
        self._active = True
        self._tracking.error = None
        if full:
            self._tracking.last_full = time.monotonic()
        return report

    @serialized
    def _update(self, full: bool, paths: tuple[str, ...] = ()) -> None:
        if self._store is not None and self._active:
            # Native events invalidate bytes even on filesystems with coarse timestamps.
            for path in paths:
                self._cache.pop(path, None)
            self._index(full)

    def _view(self) -> View:
        # Preserve explicit not-indexed, incompatible and corrupt-store failures on first read.
        self.store.view()
        full = not self._active or time.monotonic() - self._tracking.last_full >= RECONCILE_SECONDS
        if full or self._tracking.dirty.is_set() or self._tracking.failed:
            self._index(full or self._tracking.failed)
        return self.store.view()

    @property
    @serialized
    def snapshot(self) -> Snapshot:
        return self._view().snapshot

    @serialized
    def files(self) -> list[FileInfo]:
        return list(self._view().files.values())

    @serialized
    def entity(self, key: str) -> Entity | None:
        return self._view().entities([key]).get(key)

    @serialized
    def relations(self, key: str) -> list[Relation]:
        return self._view().relations(key)

    @serialized
    def impact(
        self,
        key: str,
        *,
        direction: Literal["upstream", "downstream"] = "upstream",
        depth: int = 3,
        limit: int = 200,
    ) -> Impact:
        """Bounded dependents (``upstream``) or dependencies (``downstream``) of the entity
        ``key``, nearest first, each with its proven depth. Read-only; never ranked or
        packed into context."""
        if direction not in ("upstream", "downstream"):
            raise InvalidArgument(f"direction must be upstream or downstream, got {direction!r}")
        _texts("key", [key])
        depth = _count("depth", depth, MAX_DEPTH)
        limit = _count("limit", limit, MAX_LIMIT)
        view = self._view()
        root = view.entities([key]).get(key)
        if root is None:
            raise InvalidArgument(
                f"no entity {key!r} in this snapshot; find keys with oxide search"
            )
        found = navigation.walk(view, key, direction, depth, limit)
        known = view.entities(h.key for h in found.hops)
        names = [e.name for e in view.entities(found.roots).values() if e.category == "symbol"]
        return Impact(
            view.snapshot,
            root,
            direction,
            found.roots,
            tuple(
                ImpactHop(known[h.key], h.depth, h.proven_depth, h.via, h.cyclic)
                for h in found.hops
            ),
            view.mentions(names, ("calls", "references")) if direction == "upstream" else 0,
            found.stopped_by,
        )

    @serialized
    def search(
        self,
        query: str,
        *,
        limit: int = retrieval.LEXICAL_LIMIT,
        paths: Iterable[str] = (),
        symbols: Iterable[str] = (),
        selection: tuple[str, int, int] | None = None,
        changed: Iterable[str] = (),
    ) -> SearchResult:
        """Entry-point candidates for inspection; never the final context."""
        hints = _hints(query, paths, symbols, selection, changed)
        limit = _count("limit", limit, MAX_LIMIT)
        view = self._view()
        found = retrieval.retrieve(view, query, hints, lexical_limit=limit)
        rooted = view.entries(e.key for e in found.entries)
        entries = tuple(
            EntryPoint(rooted[e.key][0], e.rank, e.score, tuple(e.seeds)) for e in found.entries
        )
        return SearchResult(
            view.snapshot,
            query,
            entries,
            found.lexical,
            tuple(found.issues),
            _blast(rooted, [e.key for e in found.entries]),
        )

    @serialized
    def structural_search(
        self,
        rule: str | Mapping[str, Any],
        *,
        language: str,
        paths: Iterable[str] = (),
        limit: int = 100,
    ) -> StructuralResult:
        """Nodes of the published snapshot an ast-grep ``rule`` matches: a pattern string
        or a rule config (``{"rule": ..., "constraints": ...}``). Explicit and read-only;
        never ranked or packed. Needs the ``structural`` extra."""
        if language not in structural.GRAMMARS:
            raise InvalidArgument(f"language must be python or typescript, got {language!r}")
        if not (isinstance(rule, Mapping) or (isinstance(rule, str) and rule.strip())):
            raise InvalidArgument(f"rule must be a pattern string or a rule config, got {rule!r}")
        scope = _texts("paths", paths)
        return structural.search(
            self._view(), rule, language, scope, _count("limit", limit, MAX_LIMIT)
        )

    @serialized
    def build_context(
        self,
        query: str,
        budget: int,
        *,
        paths: Iterable[str] = (),
        symbols: Iterable[str] = (),
        selection: tuple[str, int, int] | None = None,
        changed: Iterable[str] = (),
    ) -> ContextBundle:
        """A bounded, source-backed context bundle: ``used <= budget`` units. Ordered by
        the optional judge only when this repository authorized it (``judge``)."""
        by = jev.configured(self)
        return self._context(query, budget, paths, symbols, selection, changed, by)

    @serialized
    def judge(self, enable: bool | None = None) -> JudgeStatus:
        """The optional judge's status for this repository (ADR-0009). ``True`` authorizes
        ``build_context`` to send this repository's source to it, ``False`` revokes that.
        The authorization lives in this repository's store, never in the repository."""
        if enable is not None and not isinstance(enable, bool):
            raise InvalidArgument(f"enable must be True, False or None, got {enable!r}")
        if enable is not None:
            (jev.authorize if enable else jev.revoke)(self)
        return jev.status(self)

    @serialized
    def _context(
        self,
        query: str,
        budget: int,
        paths: Iterable[str],
        symbols: Iterable[str],
        selection: tuple[str, int, int] | None,
        changed: Iterable[str],
        by: ordering.Order | None,
    ) -> ContextBundle:
        """``build_context``, optionally ordered ``by`` a judge. Internal (ADR-0009 § 5)."""
        hints = _hints(query, paths, symbols, selection, changed)
        budget = _count("budget", budget, MAX_BUDGET)
        view = self._view()
        found = retrieval.retrieve(view, query, hints)
        entries = {e.key: e for e in found.entries}
        route = routing.route(view, list(entries))
        routed = {c.key: c for c in route.candidates}
        later = sorted((k for k in routed if k not in entries), key=lambda k: (routed[k].depth, k))
        order = [k for k in entries if k in routed] + later
        beyond = max(0, len(order) - packing.GRAPH_NODES)
        order = order[: packing.GRAPH_NODES]
        blast_keys = [e.key for e in found.entries][:BLAST_ROOTS]
        rooted = view.entries(
            [*order, *(a for k in order for a in packing.ancestors(k)), *blast_keys]
        )
        known = {key: entity for key, (entity, _) in rooted.items()}
        hinted = {k[5:] for k, e in entries.items() if any(s.kind == "path" for s in e.seeds)}
        pool = [
            packing.Candidate(
                known[k],
                routed[k].depth,
                entries[k].rank if k in entries else None,
                bool(entries[k].seeds) if k in entries else False,
                (source := known[k].source) is not None and source.path in hinted,
                entries[k].score if k in entries else None,
                tuple(f"{s.kind}:{s.hint}" for s in entries[k].seeds) if k in entries else (),
                routed[k].origins,
            )
            for k in order
            if k in known
        ]
        # Module-level views need the top-level declarations of every parsed file.
        files = [
            c.entity.key
            for c in pool
            if c.entity.category == "file"
            and c.entity.source is not None
            and view.files[c.entity.source.path].coverage != "unsupported"
        ]
        children = view.adjacency(files, kinds=("contains",))
        tops = view.entities(
            b for k in files for kind, b, *_ in children[k][0] if kind == "contains"
        )
        declarations = {
            k: [
                (s.start, s.end)
                for kind, b, *_ in children[k][0]
                if kind == "contains" and b in tops and (s := tops[b].source) is not None
            ]
            for k in files
        }
        judge = by(query, view.snapshot.derivation_id, view.source) if by else None
        items, omissions = packing.pack(pool, known, budget, view.source, declarations, judge)
        degradations = []
        if found.lexical.state != "ran":
            degradations.append(f"lexical channel {found.lexical.state}: {found.lexical.detail}")
        if route.stopped_by is not None:
            degradations.append(f"routing stopped by its {route.stopped_by} bound")
        if beyond:
            degradations.append(f"{beyond} routed candidates beyond the graph node limit")
        incomplete = {f.path for f in view.files.values() if f.coverage != "complete"}
        used_paths = {c.entity.source.path for c in pool if c.entity.source is not None}
        degradations += [f"incomplete parse: {path}" for path in sorted(used_paths & incomplete)]
        if judge is not None and judge.outcome not in (None, "judged"):
            degradations.append(f"judge {judge.identity} fell back: {judge.outcome}")
        summary = RouteSummary(
            route.root_fallback,
            route.regions,
            len(route.candidates),
            route.deferred,
            route.fanout_truncations,
            route.stopped_by,
        )
        versions = {
            "retrieval": retrieval.RETRIEVAL_VERSION,
            "router": routing.ROUTER_VERSION,
            "selector": packing.SELECTOR_VERSION,
            "payload": packing.PAYLOAD_VERSION,
            "derivation": view.snapshot.derivation_id,
        }
        if judge is not None and judge.outcome == "judged":
            versions |= judge.versions
        return ContextBundle(
            snapshot=view.snapshot,
            query=query,
            budget=budget,
            used=sum(i.units for i in items),
            units=packing.UNITS_VERSION,
            items=tuple(items),
            omissions=tuple(omissions),
            lexical=found.lexical,
            hints=tuple(found.issues),
            route=summary,
            degradations=tuple(degradations),
            blast=_blast(rooted, blast_keys),
            versions=versions,
        )


def open_repository(
    path: str | os.PathLike[str],
    *,
    data_dir: str | os.PathLike[str] | None = None,
    create: bool = True,
) -> Repository:
    """Opens (and exclusively owns) the store of the repository at ``path``. Nothing is
    indexed until ``index()``; reads before that raise ``NotIndexed``. With
    ``create=False`` (read-only clients) a repository without a store raises
    ``NotIndexed`` here, and nothing is written."""
    return Repository(path, data_dir, create)


def index_repository(
    path: str | os.PathLike[str], *, data_dir: str | os.PathLike[str] | None = None
) -> IndexReport:
    with Repository(path, data_dir) as repo:
        return repo.index()


def prepare() -> Path:
    """Fetches and verifies the pinned FTS extension (``oxide prepare``); returns its
    path. The only network access OXIDE makes, and only when called."""
    return prepare_fts()
