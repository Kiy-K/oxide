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

A ``paths`` hint names where the relevant code lives: candidates in a hinted file are
taken before all others (value, floor and order unchanged within each tier), and the
hinted file itself contributes only its header view, so its declarations, not its
module-level glue, get the budget.

Units are ``oxide-units-v1``: each run of word characters costs ceil(len/4), each
newline 1, other whitespace 0, any other character 1. They claim no model's
tokenizer. Payload ``oxide-payload-v4``: per item ``### <path>:<l1>-<l2> <names>``
then its source (a module-level run is named ``(module-level)``); files in
first-selection order, spans in byte order, spans of one file that overlap or are
separated only by whitespace merged into one item and counted once.

An optional ``DecisionProvider`` (SPEC § DecisionProvider) may replace ``value`` for
the candidates it judges; floor, caps, tiers, order and budget stay deterministic. A
missing, abstained, invalid or failed judgment keeps the heuristic value, with the
reason. Without a provider the bundle is byte-identical to the heuristic path.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from oxide.models import ContextItem, Entity, ItemEntity, Omission, SourceRef

SELECTOR_VERSION = "oxide-select-greedy-v6"
PAYLOAD_VERSION = "oxide-payload-v4"
UNITS_VERSION = "oxide-units-v1"
GRAPH_NODES = 64
MAX_ITEMS = 24
INCLUDE_FLOOR = 0.4
MAX_HEADER_LINES = 8
TEST_WEIGHT = 0.5

_WORDS = re.compile(r"\w+")
_SYMBOLS = re.compile(r"[^\w\s]")
_TEST_DIR = frozenset(("test", "tests", "__tests__", "testing"))
_TEST_FILE = re.compile(
    r"(?:^test_[^/]*\.py$|^conftest\.py$|_test\.(?:py|go)$|\.(?:test|spec)\.[cm]?[jt]sx?$)"
)


def units(text: str) -> int:
    words = sum((len(w) + 3) // 4 for w in _WORDS.findall(text))
    return words + text.count("\n") + len(_SYMBOLS.findall(text))


def test_role(entity: Entity) -> bool:
    """Test evidence: the derived facet, or a test-file location (helpers and
    fixtures the facet does not flag). Language file conventions, like the facet."""
    if entity.test:
        return True
    source = entity.source
    if source is None:
        return False
    parts = source.path.split("/")
    return bool(set(parts[:-1]) & _TEST_DIR) or bool(_TEST_FILE.search(parts[-1]))


@dataclass(frozen=True, slots=True)
class Candidate:
    entity: Entity
    depth: int
    rank: int | None
    seeded: bool
    hinted: bool = False  # its file resolved from a ``paths`` hint
    # Evidence for a judge's capsule only; selection never reads it.
    score: float | None = None  # lexical score; None when not a lexical hit
    seeds: tuple[str, ...] = ()  # ``kind:hint`` of each structural seed
    origins: tuple[str, ...] = ()  # how routing reached it (``routing.Routed.origins``)


def value(c: Candidate, *, test_weight: float = 1.0) -> tuple[float, str]:
    """Heuristic relevance and its basis; a model judgment would replace only this.
    A file's module-level view is evidence when retrieval found the file itself; a
    file reached only by routing (a scope, an importer, the root fallback) is valued
    as the container it is, like modules and the repository. ``test_weight`` (1.0
    unless production evidence exists) down-weights test candidates; an explicit
    seed is a request, never down-weighted."""
    routed_file = c.entity.category == "file" and not c.seeded and c.rank is None
    if c.entity.category not in ("symbol", "file") or routed_file:
        return 0.2, "container"
    if c.seeded:
        return 0.9, "structural seed"
    weight = test_weight if test_role(c.entity) else 1.0
    if c.rank is not None:
        base, basis = max(0.5, 0.85 - 0.025 * (c.rank - 1)), f"lexical rank {c.rank}"
    else:
        base, basis = (0.45 if c.depth == 1 else 0.3), f"routed depth {c.depth}"
    score = base * weight
    if base >= INCLUDE_FLOOR:
        # Down-weighting admits but never suppresses: an admitted test stays admitted.
        score = max(score, INCLUDE_FLOOR)
    return score, basis + (f", test weight {weight}" if weight != 1.0 else "")


class DecisionProvider(Protocol):
    """A request-scoped judge (it already holds the query): a relevance value in
    [0, 1] per candidate key, or None to abstain. Keys it omits keep the heuristic."""

    @property
    def identity(self) -> str: ...

    def judge(self, candidates: Sequence[Candidate]) -> Mapping[str, float | None]: ...


@dataclass(frozen=True, slots=True)
class Replay:
    """Recorded judgments by candidate key: the deterministic fake, and the exact
    replay of a captured judged run."""

    identity: str
    judgments: Mapping[str, float | None]

    def judge(self, candidates: Sequence[Candidate]) -> Mapping[str, float | None]:
        keys = {c.entity.key for c in candidates}
        return {k: v for k, v in self.judgments.items() if k in keys}


def _reply(
    provider: DecisionProvider, candidates: Sequence[Candidate]
) -> tuple[Mapping[str, float | None] | None, str | None]:
    """One provider call: its mapping, or the reason it failed."""
    keys = {c.entity.key for c in candidates}
    try:
        found = provider.judge(tuple(candidates))
        if not isinstance(found, Mapping):
            return None, "returned no mapping"
        if not found.keys() <= keys:
            return None, "judged an unknown candidate"
        return found, None
    except Exception as error:  # any provider failure leaves the deterministic path
        return None, f"failed ({type(error).__name__})"


def judged(
    provider: DecisionProvider,
    candidates: Sequence[Candidate],
    *,
    test_weight: float = 1.0,
    reply: tuple[Mapping[str, float | None] | None, str | None] | None = None,
) -> dict[str, tuple[float, str]]:
    """Value and basis per candidate: a valid judgment, else the heuristic with why.
    ``reply`` is the result of the single provider call (ADR-0009: asked once);
    heuristic fallbacks — failure, abstain, missing — take ``test_weight``."""
    values = {c.entity.key: value(c, test_weight=test_weight) for c in candidates}
    name = provider.identity
    found, failure = reply if reply is not None else _reply(provider, candidates)
    if failure is not None:
        return {k: (score, f"{basis}, {name} {failure}") for k, (score, basis) in values.items()}
    out = {}
    for key, (score, basis) in values.items():
        got = found.get(key, ...) if found is not None else ...
        if got is ...:
            out[key] = (score, f"{basis}, {name} missing")
        elif got is None:
            out[key] = (score, f"{basis}, {name} abstained")
        elif isinstance(got, (int, float)) and not isinstance(got, bool) and 0 <= got <= 1:
            out[key] = (float(got), f"judged by {name}")  # NaN and infinities fail the range
        else:
            out[key] = (score, f"{basis}, {name} invalid")
    return out


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
                assert piece.entity.source is not None
                end = max(p.end for p in spans[-1]) if spans else 0
                # Only whitespace between two views: one item, so one heading.
                gap = self.data(piece.entity.source)[end : piece.start]
                if spans and (piece.start < end or not gap.strip()):
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
    judge: DecisionProvider | None = None,
) -> tuple[list[ContextItem], list[Omission]]:
    """``pool`` in graph order (entry points first); ``scopes`` holds every enclosing
    declaration of a pool symbol; ``declarations`` the top-level declaration byte
    ranges of every parsed Python file in the pool; ``judge`` an optional provider.
    Issue #77: a query about production code must not watch tests fill its budget.
    Pack once at equal values; retry with test evidence down-weighted only when that
    pack shows the symptom -- test bodies holding more units than production
    bodies while production symbols lost their own bodies to the budget -- and
    keep the retry only when it visibly brings production back. A judgment
    replaces the heuristic, so it is never re-weighted; fallback values
    (failure, abstain, missing) are heuristic and follow the weight. The
    provider is still called exactly once (ADR-0009)."""
    packer = _Packer(budget, source, declarations)

    def container(c: Candidate) -> bool:
        category = c.entity.category
        return category not in ("symbol", "file") or (
            category == "file" and c.entity.key not in declarations
        )

    asked = [
        c for c in pool if not container(c) and c.entity.source is not None
    ]  # ADR-0009: the judge is asked about packable candidates only
    roles = {c.entity.key: test_role(c.entity) for c in asked}
    roles |= {k: test_role(e) for k, e in scopes.items()}  # a test class header is test role too
    reply = _reply(judge, asked) if judge is not None and asked else None

    def pass_(weight: float) -> tuple[list[ContextItem], list[Omission], dict[str, int]]:
        omissions: list[Omission] = []
        scored = []
        values = (
            judged(judge, asked, test_weight=weight, reply=reply)
            if judge is not None and reply is not None
            else {}
        )  # mypy: reply is only set with a judge
        for position, candidate in enumerate(pool):
            key = candidate.entity.key
            score, basis = values.get(key) or value(candidate, test_weight=weight)
            if container(candidate):
                omissions.append(Omission(key, "selection", "container"))
            elif candidate.entity.source is None:
                omissions.append(Omission(key, "selection", "no source"))
            elif score < INCLUDE_FLOOR:
                why = f"value {score:.2f} below floor" + (f", {basis}" if key in values else "")
                omissions.append(Omission(key, "selection", why))
            elif not packer.view(candidate.entity, False, ""):
                omissions.append(Omission(key, "selection", "no module-level source"))
            else:
                basis += ", hinted path" if candidate.hinted else ""
                scored.append(
                    (
                        not candidate.hinted,
                        -score,
                        candidate.entity.category == "file",
                        position,
                        candidate,
                        f"value {score:.2f}, {basis}",
                    )
                )
        pieces: list[_Piece] = []
        items: list[ContextItem] = []
        planned = 0
        demoted: set[str] = set()
        for *_, candidate, reason in sorted(scored, key=lambda s: s[:4]):
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
            outline = candidate.hinted and entity.category == "file"
            for reduced in (True,) if outline and packer.view(entity, True, "") else (False, True):
                view = packer.view(entity, reduced, reason + (", header view" if reduced else ""))
                fitted = packer.fits([*pieces, *prerequisites, *view]) if view else None
                if fitted is not None:
                    if reduced and entity.category == "symbol":
                        demoted.add(entity.key)  # lost its own body to the budget
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
        # Symptom: test evidence occupying body units while production candidates
        # were dropped or left as headers. Occupation is item-level (the budget a
        # test actually holds, a method beside its class header included); it drives
        # whether to try a remedy. A header is presence, not content.
        test_units = prod_units = 0
        for item in items:
            whole = [e for e in item.entities if e.complete]
            if whole:
                if all(roles.get(e.key, False) for e in whole):
                    test_units += item.units
                else:
                    prod_units += item.units
        # Crowded out means the candidate's own body lost room: not a module-level
        # run (never "complete"), not a class header shown beside its packed method.
        lost = {o.key for o in omissions if o.stage == "packing"} | demoted
        prod_short = sum(
            1
            for c in asked
            if c.entity.category == "symbol" and c.entity.key in lost and not roles[c.entity.key]
        )
        # Remedy: keep the retry only if it brings back more production BODY units --
        # the union of complete symbol source ranges, never module glue, headers, or
        # heading overhead. A class recovered whole by its merged methods counts too:
        # its ItemEntity is complete even though only header pieces were packed.
        sources = {c.entity.key: c.entity.source for c in asked} | {
            k: e.source for k, e in scopes.items()
        }
        ranges: list[tuple[SourceRef, int, int]] = []
        for item in items:
            for e in item.entities:
                if not e.complete or e.key.startswith("file:") or roles.get(e.key, False):
                    continue
                src_ref = sources.get(e.key)
                if (
                    src_ref is not None
                    and item.source.start <= src_ref.start
                    and src_ref.end <= item.source.end
                ):
                    ranges.append((src_ref, src_ref.start, src_ref.end))
        prod_body = 0
        ranges.sort(key=lambda r: (r[0].path, r[1], r[2]))
        cur: SourceRef | None = None
        start = end = 0
        for ref, s, span_end in ranges:
            if cur is None or ref.path != cur.path or s > end:
                if cur is not None and end > start:
                    prod_body += units(packer.data(cur)[start:end].decode("utf-8", "replace"))
                cur, start, end = ref, s, span_end
            else:
                end = max(end, span_end)
        if cur is not None and end > start:
            prod_body += units(packer.data(cur)[start:end].decode("utf-8", "replace"))
        stats = {
            "test_units": test_units,
            "prod_units": prod_units,
            "prod_short": prod_short,
            "prod_body": prod_body,
        }
        return items, omissions, stats

    items, omissions, stats = pass_(1.0)
    # Symptom first, remedy second: test evidence holding budget units while
    # production candidates were dropped or left as headers trips a retry. The retry
    # is kept only if it recovers more production BODY bytes (span union), so glue
    # rendered beside an already-complete function never wins. Queries about tests
    # never trip the gate. A judged run retries on the same cached reply: real
    # judgments keep their value, heuristic fallbacks follow the weight.
    if stats["prod_short"] and stats["test_units"] > stats["prod_units"]:
        retry = pass_(TEST_WEIGHT)
        if retry[2]["prod_body"] > stats["prod_body"]:
            items, omissions = retry[0], retry[1]
    return items, omissions
