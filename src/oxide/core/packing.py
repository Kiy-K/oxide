"""Deterministic selection and packing (SPEC § Selection, § Context packing).

Routed candidates get a heuristic relevance value (the Rust ``heuristic-evidence/1``
rules), then one greedy pass takes the best candidate whose group (the item plus a
header of every enclosing declaration not yet shown) fits the remaining budget,
whole or reduced to its header view, otherwise omits it with a reason. Fit is
checked by rendering the exact payload, so ``used <= budget`` holds by construction.

A symbol's view is its declaration. A parsed Python file's view is its module-level
source (imports, constants, top-level statements): the runs of non-blank lines
between its top-level declarations, never the whole file; its header view is the
first run, and it competes only when retrieval found the file. Other files, modules
and the repository are containers without a view.

Units are ``oxide-units-v1``: each run of word characters costs ceil(len/4), each
newline 1, other whitespace 0, any other character 1. They claim no model's
tokenizer. Payload ``oxide-payload-v3``: per item ``### <path>:<l1>-<l2> <names>``
then its source (a module-level run is named ``(module-level)``); files in
first-selection order, spans in byte order, overlapping spans of one file merged and
counted once.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from oxide.models import ContextItem, Entity, ItemEntity, Omission, SourceRef

SELECTOR_VERSION = "oxide-select-greedy-v3"
PAYLOAD_VERSION = "oxide-payload-v3"
UNITS_VERSION = "oxide-units-v1"
GRAPH_NODES = 64
MAX_ITEMS = 24
INCLUDE_FLOOR = 0.4
MAX_HEADER_LINES = 8

_WORDS = re.compile(r"\w+")
_SYMBOLS = re.compile(r"[^\w\s]")


def units(text: str) -> int:
    words = sum((len(w) + 3) // 4 for w in _WORDS.findall(text))
    return words + text.count("\n") + len(_SYMBOLS.findall(text))


@dataclass(frozen=True, slots=True)
class Candidate:
    entity: Entity
    depth: int
    rank: int | None
    seeded: bool


def value(c: Candidate) -> tuple[float, str]:
    """Heuristic relevance and its basis; a model judgment would replace only this.
    A file's module-level view is evidence when retrieval found the file itself; a
    file reached only by routing (a scope, an importer, the root fallback) is valued
    as the container it is, like modules and the repository."""
    routed_file = c.entity.category == "file" and not c.seeded and c.rank is None
    if c.entity.category not in ("symbol", "file") or routed_file:
        return 0.2, "container"
    if c.seeded:
        return 0.9, "structural seed"
    if c.rank is not None:
        return max(0.5, 0.85 - 0.025 * (c.rank - 1)), f"lexical rank {c.rank}"
    return (0.45 if c.depth == 1 else 0.3), f"routed depth {c.depth}"


def display(key: str) -> str:
    return key.rsplit("#", 1)[1] if key.startswith("symbol:") else key.split(":", 1)[-1]


def ancestors(key: str) -> list[str]:
    """Enclosing declarations of a symbol, outermost first."""
    if not key.startswith("symbol:"):
        return []
    file, path = key.rsplit("#", 1)
    segments = path.split(".")
    return [f"{file}#{'.'.join(segments[:i])}" for i in range(1, len(segments))]


def header_end(data: bytes, start: int, end: int, name: str) -> int | None:
    """End of the header view: through the first line naming the entity, at most
    ``MAX_HEADER_LINES`` (the first line when none does). None when not smaller."""
    named = re.compile(rb"(?<![\w\x80-\xff])" + re.escape(name.encode()) + rb"(?![\w\x80-\xff])")
    at, first = start, None
    for line in data[start:end].splitlines(keepends=True)[:MAX_HEADER_LINES]:
        at += len(line)
        first = first or at
        if named.search(line):
            return at if at < end else None
    return first if first is not None and first < end else None


def module_regions(data: bytes, declarations: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Byte ranges of a file's module-level source: maximal runs of whole lines that
    no top-level declaration touches, from the first to the last non-blank line."""
    spans = sorted(declarations)
    regions: list[tuple[int, int]] = []
    run: tuple[int, int] | None = None
    at = d = 0
    for line in data.split(b"\n"):
        start, end = at, at + len(line)
        at = end + 1
        while d < len(spans) and spans[d][1] <= start:
            d += 1
        if d < len(spans) and spans[d][0] <= end:
            if run is not None:
                regions.append(run)
            run = None
        elif line.strip():
            run = (run[0] if run else start, end)
    return [*regions, run] if run is not None else regions


@dataclass(frozen=True, slots=True)
class _Piece:
    entity: Entity
    start: int
    end: int
    reason: str


class _Packer:
    def __init__(
        self,
        budget: int,
        source: Callable[[SourceRef], bytes],
        declarations: Mapping[str, list[tuple[int, int]]],
    ) -> None:
        self.budget = budget
        self.source = source
        self.declarations = declarations
        self.cache: dict[str, bytes] = {}

    def data(self, ref: SourceRef) -> bytes:
        if ref.path not in self.cache:
            self.cache[ref.path] = self.source(ref)
        return self.cache[ref.path]

    def render(self, pieces: list[_Piece]) -> list[ContextItem] | None:
        """Items for ``pieces``; None when a span is not UTF-8 text."""
        by_file: dict[str, list[_Piece]] = {}
        for piece in pieces:
            assert piece.entity.source is not None
            by_file.setdefault(piece.entity.source.path, []).append(piece)
        items = []
        for path, group in by_file.items():
            spans: list[list[_Piece]] = []
            for piece in sorted(group, key=lambda p: (p.start, p.end)):
                if spans and piece.start < max(p.end for p in spans[-1]):
                    spans[-1].append(piece)
                else:
                    spans.append([piece])
            for span in spans:
                item = self.item(path, span)
                if item is None:
                    return None
                items.append(item)
        return items

    def item(self, path: str, span: list[_Piece]) -> ContextItem | None:
        start, end = min(p.start for p in span), max(p.end for p in span)
        ref = span[0].entity.source
        assert ref is not None
        data = self.data(ref)
        try:
            body = data[start:end].decode("utf-8")
        except UnicodeDecodeError:
            return None
        entities: dict[str, ItemEntity] = {}
        for piece in span:
            source = piece.entity.source
            assert source is not None
            complete = start <= source.start and source.end <= end
            entities.setdefault(
                piece.entity.key,
                ItemEntity(piece.entity.key, display(piece.entity.key), complete, piece.reason),
            )
        labels = ", ".join(
            "(module-level)"
            if e.key.startswith("file:")
            else e.name + ("" if e.complete else " (header)")
            for e in entities.values()
        )
        first = data.count(b"\n", 0, start) + 1
        last = data.count(b"\n", 0, max(start, end - 1)) + 1
        text = f"### {path}:{first}-{last} {labels}\n{body}"
        text += "" if text.endswith("\n") else "\n"
        source = SourceRef(path, start, end, ref.digest)
        return ContextItem(source, (first, last), tuple(entities.values()), units(text), text)

    def fits(self, pieces: list[_Piece]) -> list[ContextItem] | None:
        items = self.render(pieces)
        if items is None or sum(i.units for i in items) > self.budget:
            return None
        return items

    def view(self, entity: Entity, header: bool, reason: str) -> list[_Piece]:
        """The pieces of a full or header view; empty when there is no such view."""
        source = entity.source
        assert source is not None
        if entity.category == "file":
            regions = module_regions(self.data(source), self.declarations[entity.key])
            chosen = (regions[:1] if len(regions) > 1 else []) if header else regions
            return [_Piece(entity, start, end, reason) for start, end in chosen]
        if not header:
            return [_Piece(entity, source.start, source.end, reason)]
        end = header_end(self.data(source), source.start, source.end, entity.name)
        return [] if end is None else [_Piece(entity, source.start, end, reason)]


def pack(
    pool: list[Candidate],
    scopes: dict[str, Entity],
    budget: int,
    source: Callable[[SourceRef], bytes],
    declarations: Mapping[str, list[tuple[int, int]]],
) -> tuple[list[ContextItem], list[Omission]]:
    """``pool`` in graph order (entry points first); ``scopes`` holds every enclosing
    declaration of a pool symbol; ``declarations`` the top-level declaration byte
    ranges of every parsed Python file in the pool. Returns packed items and every
    omission."""
    packer = _Packer(budget, source, declarations)
    omissions: list[Omission] = []
    scored = []
    for position, candidate in enumerate(pool):
        score, basis = value(candidate)
        key = candidate.entity.key
        if candidate.entity.category not in ("symbol", "file") or (
            candidate.entity.category == "file" and key not in declarations
        ):
            omissions.append(Omission(key, "selection", "container"))
        elif candidate.entity.source is None:
            omissions.append(Omission(key, "selection", "no source"))
        elif score < INCLUDE_FLOOR:
            omissions.append(Omission(key, "selection", f"value {score:.2f} below floor"))
        elif not packer.view(candidate.entity, False, ""):
            omissions.append(Omission(key, "selection", "no module-level source"))
        else:
            scored.append((-score, position, candidate, f"value {score:.2f}, {basis}"))
    pieces: list[_Piece] = []
    items: list[ContextItem] = []
    planned = 0
    for _, _, candidate, reason in sorted(scored, key=lambda s: (s[0], s[1])):
        entity = candidate.entity
        if planned >= MAX_ITEMS:
            omissions.append(Omission(entity.key, "selection", f"item cap {MAX_ITEMS}"))
            continue
        shown = {p.entity.key for p in pieces}
        prerequisites: list[_Piece] = []
        for outer in ancestors(entity.key):
            if outer not in shown and outer in scopes:
                header = packer.view(scopes[outer], True, f"header of {display(entity.key)}")
                prerequisites += header or packer.view(scopes[outer], False, "prerequisite")
        for reduced in (False, True):
            view = packer.view(entity, reduced, reason + (", header view" if reduced else ""))
            fitted = packer.fits([*pieces, *prerequisites, *view]) if view else None
            if fitted is not None:
                pieces += [*prerequisites, *view]
                items = fitted
                planned += 1
                break
        else:
            smallest = packer.view(entity, True, "") or packer.view(entity, False, "")
            assert smallest
            alone = packer.render([*prerequisites, *smallest])
            too_large = alone is None or sum(i.units for i in alone) > budget
            omissions.append(
                Omission(entity.key, "packing", "too large" if too_large else "budget")
            )
    return items, omissions
