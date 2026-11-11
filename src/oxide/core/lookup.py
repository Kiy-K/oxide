"""Code navigation over one sealed generation: outline, symbol resolution, relations.

Deterministic reads of indexed facts. Line numbers come from the captured bytes of the
snapshot (``View.source``), never from the worktree. A name that matches several
declarations is returned as ranked candidates; nothing here picks between them.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from collections import Counter
from collections.abc import Callable, Iterable
from typing import Protocol

from oxide.models import Entity, FileInfo, InvalidArgument, Relation, Snapshot, SourceRef
from oxide.models_nav import (
    Candidate,
    Declaration,
    Direction,
    Inspection,
    Label,
    Link,
    Location,
    Outline,
    OutlineFile,
    Reference,
    References,
    Status,
)

MAX_TEXT = 160  # characters of evidence text
_NAMED = 200  # name matches fetched per lookup before ranking
_STRUCTURAL = ("contains", "contains_logical", "defines")
_WHY = ("key", "path", "line", "qualified", "suffix", "name")
_ORDER: tuple[Label, ...] = ("proven", "heuristic", "ambiguous", "unresolved")
_LINE = re.compile(r"(.+):(\d+)")
_OVERLOAD = re.compile(r"~\d+$")


class Facts(Protocol):
    """The part of a sealed generation navigation reads (``storage.ladybug.View``)."""

    snapshot: Snapshot
    files: dict[str, FileInfo]

    def entities(self, keys: Iterable[str]) -> dict[str, Entity]: ...
    def named(self, name: str, limit: int) -> tuple[list[str], bool]: ...
    def innermost(self, path: str, start: int, end: int) -> str | None: ...
    def relations(self, key: str) -> list[Relation]: ...
    def mentions(self, names: list[str], kinds: tuple[str, ...]) -> int: ...
    def source(self, ref: SourceRef) -> bytes: ...


Declared = Callable[[list[str]], list[Entity]]


class _Lines:
    """Line arithmetic over the captured bytes of the files one request touches."""

    def __init__(self, view: Facts) -> None:
        self.view = view
        self._files: dict[str, tuple[bytes, list[int]]] = {}

    def _file(self, path: str) -> tuple[bytes, list[int]]:
        if path not in self._files:
            data = self.view.source(SourceRef(path, 0, 0, self.view.files[path].digest))
            self._files[path] = (data, [0, *(m.end() for m in re.finditer(rb"\n", data))])
        return self._files[path]

    def location(self, ref: SourceRef) -> Location:
        _, starts = self._file(ref.path)
        first = bisect_right(starts, ref.start)
        return Location(ref.path, first, max(first, bisect_right(starts, ref.end - 1)))

    def text(self, ref: SourceRef) -> str:
        data, starts = self._file(ref.path)
        line = bisect_right(starts, ref.start) - 1
        stop = starts[line + 1] if line + 1 < len(starts) else len(data)
        text = data[starts[line] : stop].decode("utf-8", "replace").strip()
        return text if len(text) <= MAX_TEXT else text[: MAX_TEXT - 1] + "…"

    def code_start(self, path: str, line: int) -> int | None:
        """Byte offset of the first non-blank character of 1-based ``line``, if it has one."""
        data, starts = self._file(path)
        if not 1 <= line <= len(starts):
            return None
        first = starts[line - 1]
        row = data[first : starts[line] if line < len(starts) else len(data)]
        return first + len(row) - len(row.lstrip()) if row.strip() else None


def _declaration(entity: Entity, lines: _Lines, depth: int = 0) -> Declaration:
    where = lines.location(entity.source) if entity.source else None
    return Declaration(
        entity.key, entity.kind, entity.name, entity.signature, where, entity.test, depth
    )


def _qualified(entity: Entity) -> str:
    """``Outer.inner`` for a symbol (the key after ``#`` without its overload suffix)."""
    if entity.category != "symbol":
        return entity.name
    return _OVERLOAD.sub("", entity.key.split("#", 1)[-1])


def outline(view: Facts, declared: Declared, target: str | None, limit: int) -> Outline:
    """Files at or under ``target`` (``None``: all) with their declarations, at most
    ``limit`` declarations in all. A directory lists only files that declare something."""
    name = (target or "").strip().removeprefix("./").replace("\\", "/").strip("/")
    name = "" if name == "." else name
    single = name in view.files
    paths = [name] if single else [p for p in sorted(view.files) if p.startswith(name + "/")]
    if not name:
        paths = sorted(view.files)
    if not paths:
        raise InvalidArgument(
            f"no indexed file or directory {target!r}; run `oxide outline` without a target "
            "to see what is indexed"
        )
    unsupported = sum(view.files[p].coverage == "unsupported" for p in paths)
    paths = [p for p in paths if view.files[p].coverage != "unsupported"]
    by_path: dict[str, list[Entity]] = {}
    for entity in declared(paths):
        if entity.source:
            by_path.setdefault(entity.source.path, []).append(entity)
    lines, files, left, truncated = _Lines(view), [], limit, False
    for path in paths:
        symbols = by_path.get(path, [])
        if not symbols and not single:
            continue
        if left <= 0 and symbols:
            truncated = True
            break
        shown: list[Declaration] = []
        ends: list[int] = []
        for entity in symbols[:left]:
            assert entity.source is not None
            while ends and ends[-1] <= entity.source.start:
                ends.pop()
            shown.append(_declaration(entity, lines, len(ends)))
            ends.append(entity.source.end)
        cut = len(symbols) > len(shown)
        left -= len(shown)
        truncated = truncated or cut
        info = view.files[path]
        files.append(OutlineFile(path, info.language, info.coverage, tuple(shown), cut))
    return Outline(view.snapshot, name or ".", tuple(files), unsupported, truncated)


def _in_file(entity: Entity, name: str) -> bool:
    path = entity.source.path if entity.source else ""
    return path == name or path.endswith("/" + name)


def _resolve(view: Facts, query: str, limit: int) -> tuple[list[tuple[str, Entity]], bool, _Lines]:
    """Entities ``query`` may mean as (why, entity), best first, and whether a bound cut them."""
    text = query.strip()
    if not text:
        raise InvalidArgument("empty query; give a name, path, path:line or entity key")
    lines = _Lines(view)
    found: dict[str, tuple[str, Entity]] = {}

    def add(why: str, entities: Iterable[Entity]) -> None:
        for entity in entities:
            found.setdefault(entity.key, (why, entity))

    add("key", view.entities([text]).values())
    path, line = text.removeprefix("./").replace("\\", "/"), None
    m = _LINE.fullmatch(path)
    if m and any(p == m[1] or p.endswith("/" + m[1]) for p in view.files):
        path, line = m[1], int(m[2])
    files = [p for p in sorted(view.files) if p == path or p.endswith("/" + path)]
    if line is not None:
        for p in files:
            start = lines.code_start(p, line)
            if start is not None:  # blank or out-of-range lines name nothing
                inner = view.innermost(p, start, start + 1)
                add("line", view.entities([inner or f"file:{p}"]).values())
    else:
        add("path", view.entities([f"file:{p}" for p in files]).values())
    truncated = False
    if line is None and path not in view.files:  # an exact path names no symbol
        file_part, hash_, qual = text.partition("#")
        qual = (qual if hash_ else text).replace("::", ".")
        # a module is named by its whole dotted path, a symbol by its last component
        for name in dict.fromkeys([qual, qual.rsplit(".", 1)[-1]]):
            keys, cut = view.named(name, _NAMED)
            truncated = truncated or cut
            for entity in view.entities(keys).values():
                if hash_ and not _in_file(entity, file_part):
                    continue
                own = _qualified(entity)
                if own == qual:
                    add("qualified", [entity])
                elif own.endswith("." + qual):
                    add("suffix", [entity])
                elif entity.name == qual:
                    add("name", [entity])
    ranked = sorted(
        found.values(),
        key=lambda i: (
            _WHY.index(i[0]),
            i[1].test,
            i[1].source.path if i[1].source else "",
            i[1].source.start if i[1].source else -1,
            i[1].key,
        ),
    )
    return ranked[:limit], truncated or len(ranked) > limit, lines


def _label(relation: Relation) -> Label:
    if relation.resolution != "resolved":
        return relation.resolution
    return "heuristic" if relation.basis == "heuristic" else "proven"


def _status(ranked: list[tuple[str, Entity]], truncated: bool) -> Status:
    if not ranked:
        return "not_found"
    return "resolved" if len(ranked) == 1 and not truncated else "ambiguous"


def _candidates(ranked: list[tuple[str, Entity]], lines: _Lines) -> tuple[Candidate, ...]:
    return tuple(
        Candidate(_declaration(e, lines), why)  # type: ignore[arg-type]
        for why, e in ranked
    )


def inspect(view: Facts, query: str, limit: int) -> Inspection:
    """Resolve ``query``; when exactly one entity matches, add its parent, members and
    the counts of its non-structural relations by label."""
    ranked, truncated, lines = _resolve(view, query, limit)
    status = _status(ranked, truncated)
    candidates = _candidates(ranked, lines)
    if status != "resolved":
        return Inspection(view.snapshot, query, status, candidates, truncated)
    key = ranked[0][1].key
    relations = view.relations(key)
    parents = [r.source for r in relations if r.kind == "contains" and r.target == key]
    children = [r.target for r in relations if r.kind == "contains" and r.source == key]
    near = view.entities([*parents[:1], *(k for k in children if k)])
    members = sorted(
        (near[k] for k in children if k in near),
        key=lambda e: (e.source.start if e.source else -1, e.key),
    )
    counts: Counter[tuple[str, Direction, Label]] = Counter()
    for r in relations:
        if r.kind not in _STRUCTURAL:
            counts[(r.kind, "out" if r.source == key else "in", _label(r))] += 1
    links = tuple(
        Link(kind, direction, *(counts[(kind, direction, label)] for label in _ORDER))
        for kind, direction in sorted({(k, d) for k, d, _ in counts})
    )
    return Inspection(
        view.snapshot,
        query,
        status,
        candidates,
        len(members) > limit,
        _declaration(near[parents[0]], lines) if parents and parents[0] in near else None,
        tuple(_declaration(m, lines) for m in members[:limit]),
        links,
    )


def refs(view: Facts, query: str, limit: int) -> References:
    """Direct relations of the one entity ``query`` resolves to, labelled by evidence."""
    ranked, truncated, lines = _resolve(view, query, limit)
    status = _status(ranked, truncated)
    candidates = _candidates(ranked, lines)
    if status != "resolved":
        return References(view.snapshot, query, status, candidates, (), 0, truncated)
    entity = ranked[0][1]
    relations = [r for r in view.relations(entity.key) if r.kind not in _STRUCTURAL]
    ends = {r.target if r.source == entity.key else r.source for r in relations}
    far = view.entities(k for k in ends if k and k != entity.key)
    rows: list[tuple[tuple[int, str, int, str, str, int, str], Reference]] = []
    for r in relations:
        direction: Direction = "out" if r.source == entity.key else "in"
        other_key = r.target if direction == "out" else r.source
        at = lines.location(r.evidence) if r.evidence else None
        label = _label(r)
        other = far.get(other_key or "")
        reference = Reference(
            r.kind,
            direction,
            label,
            r.name,
            _declaration(other, lines) if other else None,
            at,
            lines.text(r.evidence) if r.evidence else None,
        )
        where = (at.path, at.start_line) if at else ("", 0)
        rows.append(
            ((_ORDER.index(label), *where, r.kind, other_key or "", r.site, direction), reference)
        )
    rows.sort(key=lambda row: row[0])
    sites = (
        view.mentions([entity.name], ("calls", "references")) if entity.category == "symbol" else 0
    )
    return References(
        view.snapshot,
        query,
        status,
        candidates,
        tuple(reference for _, reference in rows[:limit]),
        sites,
        len(rows) > limit,
    )
