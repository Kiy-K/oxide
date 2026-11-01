"""CandidateCapsule v1 (SPEC § Candidate capsule): the bounded, versioned evidence a
judge sees about one candidate, and the ``DecisionProvider`` that sends capsules to
an external adapter under a deadline.

A capsule holds the task (query, clipped, with the digest of the whole query), the
subject (key, category, kind, name, signature, path, lines, test role, file digest),
a source excerpt of the subject's own range, retrieval evidence (lexical rank and
score, ``None`` when not a lexical hit; seeds; paths hint) and route provenance (depth
and origins as ``routing.Routed`` records them). Neighbors appear only as keys inside
origins; no other entity's source is hydrated. It carries no heuristic value,
selection outcome, gold or oracle field.

Truncation is deterministic and declared: every text field is clipped to its UTF-8
byte limit (the excerpt at the last newline that fits, else at the limit), origins
and seeds keep their first entries, and ``truncated`` names every field that was
cut. A subject key is never clipped: a candidate whose key or canonical capsule
exceeds its bound is not sent, and keeps the heuristic value as ``missing``.

The canonical form is compact, key-sorted UTF-8 JSON; ``digest`` is its sha256 and
correlates a judgment with the exact evidence it was given, so a recorded judgment
replays only on an identical capsule. Capsules are sent in digest order, which
carries no pool position.
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, fields

from oxide.core.packing import Candidate
from oxide.models import SourceRef

CAPSULE_VERSION = "oxide-capsule-v1"
MAX_QUERY_BYTES = 1024
MAX_SOURCE_BYTES = 4096
MAX_FIELD_BYTES = 512  # name, signature, path, each seed and each origin
MAX_KEY_BYTES = 1024
MAX_ORIGINS = 8
MAX_SEEDS = 4
MAX_CAPSULE_BYTES = 16384  # canonical JSON, escapes included
DEADLINE = 2.0  # seconds, for the one adapter call of a request


def _sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


@dataclass(frozen=True, slots=True)
class Capsule:
    schema: str
    derivation: str
    query: str
    query_digest: str
    key: str
    category: str
    kind: str
    name: str
    signature: str | None
    path: str | None
    lines: tuple[int, int] | None
    test: bool
    source_digest: str | None
    excerpt: str
    source_bytes: int
    lexical_rank: int | None
    lexical_score: float | None
    seeds: tuple[str, ...]
    hinted: bool
    depth: int
    origins: tuple[str, ...]
    origins_omitted: int
    truncated: tuple[str, ...]
    canonical: bytes = field(init=False, repr=False, compare=False)
    digest: str = field(init=False, compare=False)

    def __post_init__(self) -> None:
        data = {f.name: getattr(self, f.name) for f in fields(self) if f.init}
        text = json.dumps(
            data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        )
        object.__setattr__(self, "canonical", text.encode())
        object.__setattr__(self, "digest", _sha(self.canonical))


def _excerpt(data: bytes) -> tuple[str, bool]:
    if len(data) <= MAX_SOURCE_BYTES:
        return data.decode(errors="replace"), False
    cut = data.rfind(b"\n", 0, MAX_SOURCE_BYTES) + 1 or MAX_SOURCE_BYTES
    return data[:cut].decode(errors="replace"), True


def build(
    query: str, derivation: str, candidate: Candidate, source: Callable[[SourceRef], bytes]
) -> Capsule | None:
    """The capsule of ``candidate``, or None when it cannot be represented within bounds."""
    entity, truncated = candidate.entity, []
    if len(entity.key.encode()) > MAX_KEY_BYTES:
        return None

    def clip(name: str, text: str, limit: int = MAX_FIELD_BYTES) -> str:
        data = text.encode()
        if len(data) <= limit:
            return text
        if name not in truncated:
            truncated.append(name)
        return data[:limit].decode(errors="ignore")

    ref, lines, excerpt, size = entity.source, None, "", 0
    if ref is not None:
        data = source(ref)
        piece = data[ref.start : ref.end]
        first = data.count(b"\n", 0, ref.start) + 1
        lines = (first, first + piece.count(b"\n", 0, max(0, len(piece) - 1)))
        excerpt, cut = _excerpt(piece)
        size = len(piece)
        if cut:
            truncated.append("excerpt")
    if len(candidate.seeds) > MAX_SEEDS:
        truncated.append("seeds")
    if len(candidate.origins) > MAX_ORIGINS:
        truncated.append("origins")
    score = candidate.score
    capsule = Capsule(
        schema=CAPSULE_VERSION,
        derivation=derivation,
        query=clip("query", query, MAX_QUERY_BYTES),
        query_digest=_sha(query.encode()),
        key=entity.key,
        category=entity.category,
        kind=entity.kind,
        name=clip("name", entity.name),
        signature=None if entity.signature is None else clip("signature", entity.signature),
        path=None if ref is None else clip("path", ref.path),
        lines=lines,
        test=entity.test,
        source_digest=None if ref is None else ref.digest,
        excerpt=excerpt,
        source_bytes=size,
        lexical_rank=candidate.rank,
        lexical_score=None if score is None else round(score, 6),
        seeds=tuple(clip("seeds", s) for s in candidate.seeds[:MAX_SEEDS]),
        hinted=candidate.hinted,
        depth=candidate.depth,
        origins=tuple(clip("origins", o) for o in candidate.origins[:MAX_ORIGINS]),
        origins_omitted=max(0, len(candidate.origins) - MAX_ORIGINS),
        truncated=tuple(truncated),
    )
    return capsule if len(capsule.canonical) <= MAX_CAPSULE_BYTES else None


Adapter = Callable[[Sequence[Capsule]], Mapping[str, float | None]]


@dataclass(frozen=True, slots=True)
class Recorded:
    """An adapter answering from judgments recorded by capsule digest: the
    deterministic fake, and the replay of a captured run."""

    judgments: Mapping[str, float | None]

    def __call__(self, capsules: Sequence[Capsule]) -> Mapping[str, float | None]:
        return {c.digest: self.judgments[c.digest] for c in capsules if c.digest in self.judgments}


def _within(adapter: Adapter, capsules: tuple[Capsule, ...], deadline: float) -> object:
    """The adapter's result, or raises. A late adapter is abandoned on a daemon thread
    (Python cannot stop it), so a real adapter must honor its own deadline too."""
    box: list[object] = []

    def run() -> None:
        try:
            box.append(adapter(capsules))
        except Exception as error:
            box.append(error)

    worker = threading.Thread(target=run, name="oxide-judge", daemon=True)
    worker.start()
    worker.join(deadline)
    if not box:
        raise TimeoutError if worker.is_alive() else RuntimeError("adapter exited")
    if isinstance(box[0], Exception):
        raise box[0]
    return box[0]


@dataclass(frozen=True, slots=True)
class CapsuleJudge:
    """A request-scoped ``DecisionProvider``: builds the capsules, makes one adapter
    call under ``deadline`` and maps digests back to candidates. Any failure raises,
    which ``packing.judged`` turns into the heuristic for every candidate."""

    name: str
    query: str
    derivation: str
    source: Callable[[SourceRef], bytes]
    adapter: Adapter
    deadline: float = DEADLINE

    @property
    def identity(self) -> str:
        return f"{self.name}@{CAPSULE_VERSION}"

    def capsules(self, candidates: Sequence[Candidate]) -> list[Capsule]:
        """The sendable capsules, in digest order."""
        built = (build(self.query, self.derivation, c, self.source) for c in candidates)
        return sorted((c for c in built if c is not None), key=lambda c: c.digest)

    def judge(self, candidates: Sequence[Candidate]) -> Mapping[str, float | None]:
        sent = self.capsules(candidates)
        if not sent:
            return {}
        found = _within(self.adapter, tuple(sent), self.deadline)
        if not isinstance(found, Mapping):
            raise TypeError("adapter returned no mapping")
        by_digest = {c.digest: c.key for c in sent}
        if not found.keys() <= by_digest.keys():
            raise LookupError("adapter judged an unknown capsule")
        return {by_digest[digest]: judgment for digest, judgment in found.items()}
