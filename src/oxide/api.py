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

import gc
import os
import time
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from types import TracebackType
from typing import Any, Literal

from oxide import jev
from oxide.capture import capture
from oxide.core import navigation, ordering, packing, retrieval, routing, structural
from oxide.core.indexing import components, derive, source_dialect, validate
from oxide.core.retrieval import Hints
from oxide.models import (
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
from oxide.storage.ladybug import (
    View,
    default_data_dir,
    fts_location,
    fts_unavailable,
    storage_components,
)
from oxide.storage.store import Store

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

    def index(self, *, progress: Callable[[str], None] | None = None) -> IndexReport:
        """Captures the worktree and publishes a new generation, or reports ``unchanged``
        when the capture and derivation equal the current one. Supported source
        files whose bytes the current generation already parsed reuse its facts;
        resolution and publication always cover the whole capture, so the result equals
        a full rebuild. ``progress`` receives a line as each stage starts."""
        started = time.perf_counter()
        report = progress or (lambda line: None)
        store = self.store
        report(f"capturing {self.root}")
        # The data location is never source, wherever it sits (compared by identity).
        snapshot = capture(self.root, exclude=(store.data, store.data / "repos"))
        lexical_unavailable = fts_unavailable(fts_location())
        parts = components(storage_components(lexical_unavailable is None))
        built = not store.is_current(snapshot.snapshot_id, parts)
        previous: Snapshot | None = None
        changes: tuple[tuple[str, ...], ...] = ((), (), ())
        parsed = reused = 0
        if built:
            old = store.previous()
            before = {f.path: f.digest for f in old.files.values()} if old else {}
            after = {f.path: f.digest for f in snapshot.files}
            changes = (
                tuple(p for p in after if p not in before),
                tuple(p for p in after if p in before and before[p] != after[p]),
                tuple(sorted(p for p in before if p not in after)),
            )
            previous = old.snapshot if old else None
            cached = old.facts() if old else {}
            sources = [
                (lang, f.digest)
                for f in snapshot.files
                if (lang := source_dialect(f.path, f.data)) is not None
            ]
            hits = sum(isinstance(s := cached.get(lang), dict) and d in s for lang, d in sources)
            report(
                f"deriving {len(after)} files: parsing {len(sources) - hits} source files, "
                f"reusing {hits}"
            )
            # A build allocates millions of objects that hold no cycles; the cyclic GC only
            # re-scans them (~8% of a build, docs/phase-6/indexing-throughput). The switch
            # is process-global, so the caller's state is restored on every exit.
            enabled = gc.isenabled()
            gc.disable()
            try:
                derived = derive(snapshot, store.repo_id, cached)
                reused, parsed = derived.reused, len(sources) - derived.reused
                if reused < hits:
                    report(f"{hits - reused} cached parse facts were unusable and parsed again")
                validate(derived)
                report(
                    f"publishing {len(derived.entities)} entities, {len(derived.edges)} relations"
                )
                store.publish(snapshot, derived, parts, lexical_unavailable)
            finally:
                if enabled:
                    gc.enable()
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
            previous=previous,
            added=changes[0],
            modified=changes[1],
            deleted=changes[2],
            parsed=parsed,
            reused=reused,
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
            raise InvalidArgument(f"no entity {key!r} in this snapshot")
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

    def judge(self, enable: bool | None = None) -> JudgeStatus:
        """The optional judge's status for this repository (ADR-0009). ``True`` authorizes
        ``build_context`` to send this repository's source to it, ``False`` revokes that.
        The authorization lives in this repository's store, never in the repository."""
        if enable is not None and not isinstance(enable, bool):
            raise InvalidArgument(f"enable must be True, False or None, got {enable!r}")
        if enable is not None:
            (jev.authorize if enable else jev.revoke)(self)
        return jev.status(self)

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
        known = view.entities([*order, *(a for k in order for a in packing.ancestors(k))])
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
        children = view.adjacency(files)
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
