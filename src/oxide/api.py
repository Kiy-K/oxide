"""The public Python API: the stable product boundary of OXIDE v2 (ADR-0011).

``index_repository -> search -> build_context`` over persisted repository state::

    with oxide.open_repository("path/to/repo") as repo:
        repo.index()
        hits = repo.search("where failed requests are retried")
        bundle = repo.build_context("where failed requests are retried", budget=1024)
        print(bundle.payload)

Every result names the snapshot it was read from. A ``Repository`` owns its store
exclusively (a second owner, in any process, gets ``OwnershipConflict``) and is not
thread-safe.
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterable
from pathlib import Path
from types import TracebackType

from oxide.capture import capture
from oxide.core import packing, retrieval, routing
from oxide.core.indexing import components, derive, validate
from oxide.core.retrieval import Hints
from oxide.models import (
    Closed,
    ContextBundle,
    Entity,
    EntryPoint,
    FileInfo,
    IndexReport,
    InvalidArgument,
    InvalidPath,
    Relation,
    RouteSummary,
    SearchResult,
    Snapshot,
)
from oxide.storage.ladybug import (
    Store,
    View,
    default_data_dir,
    fts_location,
    fts_unavailable,
    storage_components,
)

# Upper bounds of caller-chosen sizes, checked before any query is built.
MAX_LIMIT = 1000
MAX_BUDGET = 1 << 20
_MAX_OFFSET = (1 << 63) - 1  # LadybugDB INT64


def _count(name: str, value: object, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= high:
        raise InvalidArgument(f"{name} must be an integer in [0, {high}], got {value!r}")
    return value


def _texts(name: str, values: Iterable[str]) -> tuple[str, ...]:
    out = () if isinstance(values, str) else tuple(values)
    if isinstance(values, str) or not all(isinstance(v, str) for v in out):
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
    return Hints(
        selection, _texts("paths", paths), _texts("symbols", symbols), _texts("changed", changed)
    )


class Repository:
    def __init__(
        self, path: str | os.PathLike[str], data_dir: str | os.PathLike[str] | None = None
    ) -> None:
        try:
            root = Path(path).resolve(strict=True)
        except OSError as error:
            raise InvalidPath(f"{path}: {error.strerror}") from error
        if not root.is_dir():
            raise InvalidPath(f"{root}: not a directory")
        self.root = root
        self._store: Store | None = Store(root, Path(data_dir or default_data_dir()).absolute())

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
        if self._store is not None:
            self._store.close()
            self._store = None

    @property
    def store(self) -> Store:
        if self._store is None:
            raise Closed(f"{self.root}: repository is closed")
        return self._store

    @property
    def repo_id(self) -> str:
        return self.store.repo_id

    def index(self) -> IndexReport:
        """Captures the worktree and publishes a new generation (a full rebuild), or
        reports ``unchanged`` when the capture and derivation equal the current one."""
        started = time.perf_counter()
        store = self.store
        # The data location is never source, wherever it sits (compared by identity).
        snapshot = capture(self.root, exclude=(store.data, store.data / "repos"))
        lexical_unavailable = fts_unavailable(fts_location())
        parts = components(storage_components(lexical_unavailable is None))
        built = not store.is_current(snapshot.snapshot_id, parts)
        if built:
            derived = derive(snapshot, store.repo_id)
            validate(derived)
            store.publish(snapshot, derived, parts, lexical_unavailable)
        view = store.view()
        files = view.files.values()
        return IndexReport(
            snapshot=view.snapshot,
            status="built" if built else "unchanged",
            files=len(view.files),
            entities=view.counts[0],
            relations=view.counts[1],
            partial=tuple(f.path for f in files if f.coverage == "partial"),
            unsupported=sum(f.coverage == "unsupported" for f in files),
            skipped=snapshot.skipped,
            case_collisions=snapshot.case_collisions,
            lexical=view.lexical_unavailable or "available",
            seconds=time.perf_counter() - started,
        )

    def _view(self) -> View:
        """The current generation; its compatibility is checked before it is opened."""
        return self.store.view()

    @property
    def snapshot(self) -> Snapshot:
        return self._view().snapshot

    def files(self) -> list[FileInfo]:
        return list(self._view().files.values())

    def entity(self, key: str) -> Entity | None:
        return self._view().entities([key]).get(key)

    def relations(self, key: str) -> list[Relation]:
        return self._view().relations(key)

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
        entities = view.entities(e.key for e in found.entries)
        entries = tuple(
            EntryPoint(entities[e.key], e.rank, e.score, tuple(e.seeds)) for e in found.entries
        )
        return SearchResult(view.snapshot, query, entries, found.lexical, tuple(found.issues))

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
        """A bounded, source-backed context bundle: ``used <= budget`` units."""
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
        known = view.entities([*order, *(a for k in order for a in packing.ancestors(k))])
        pool = [
            packing.Candidate(
                known[k],
                routed[k].depth,
                entries[k].rank if k in entries else None,
                bool(entries[k].seeds) if k in entries else False,
            )
            for k in order
            if k in known
        ]
        items, omissions = packing.pack(pool, known, budget, view.source)
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
            versions=versions,
        )


def open_repository(
    path: str | os.PathLike[str], *, data_dir: str | os.PathLike[str] | None = None
) -> Repository:
    """Opens (and exclusively owns) the store of the repository at ``path``. Nothing is
    indexed until ``index()``; reads before that raise ``NotIndexed``."""
    return Repository(path, data_dir)


def index_repository(
    path: str | os.PathLike[str], *, data_dir: str | os.PathLike[str] | None = None
) -> IndexReport:
    with Repository(path, data_dir) as repo:
        return repo.index()
