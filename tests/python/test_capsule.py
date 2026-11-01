"""CandidateCapsule v1: deterministic, bounded evidence for a judge, correlated by
digest, sent under a deadline; every adapter failure falls back to the heuristic."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest
from conftest import FIXTURE

import oxide
from oxide.core import capsule, packing
from oxide.core.capsule import Capsule, CapsuleJudge, Recorded, build
from oxide.core.packing import Candidate, pack, value
from oxide.models import ContextItem, Entity, Omission, SourceRef

SOURCE = b"def a():\n    return 1\ndef b():\n    return 2\ndef c():\n    return 3\n"
QUERY = "where is b"
FAKE = "fake@oxide-capsule-v1"


def _symbol(name: str) -> Entity:
    start = SOURCE.index(f"def {name}".encode())
    end = SOURCE.index(b"\n", SOURCE.index(b"return", start)) + 1
    ref = SourceRef("m.py", start, end, "sha256:x")
    return Entity(f"symbol:m.py#{name}", "symbol", "function", name, f"def {name}()", ref, False)


A, B, C = _symbol("a"), _symbol("b"), _symbol("c")
POOL = [
    Candidate(A, 0, 1, False, score=3.25, origins=("entry",)),
    Candidate(B, 0, 2, False, score=2.5, origins=("entry",)),
    Candidate(C, 1, None, False, origins=("calls:in:symbol:m.py#a",)),
]
ONE = 22  # units of one function item


def _judge(adapter: Any, data: bytes = SOURCE, deadline: float = 2.0) -> CapsuleJudge:
    return CapsuleJudge("fake", QUERY, "derivation-x", lambda ref: data, adapter, deadline)


def _pack(
    judge: object = None, budget: int = 4096, data: bytes = SOURCE, pool: list[Candidate] = POOL
) -> tuple[list[ContextItem], list[Omission]]:
    return pack(pool, {}, budget, lambda ref: data, {}, judge)  # type: ignore[arg-type]


def _reasons(items: list[ContextItem]) -> dict[str, str]:
    return {e.name: e.reason for i in items for e in i.entities}


def _capsule(candidate: Candidate, query: str = QUERY, data: bytes = SOURCE) -> Capsule:
    built = build(query, "derivation-x", candidate, lambda ref: data)
    assert built is not None
    return built


def _digests(data: bytes = SOURCE) -> dict[str, str]:
    return {c.key: c.digest for c in _judge(None, data).capsules(POOL)}


def test_a_capsule_is_canonical_source_backed_evidence() -> None:
    cap = _capsule(POOL[1])
    body = json.loads(cap.canonical)
    assert (
        cap.canonical
        == json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    )
    assert body["schema"] == "oxide-capsule-v1" and cap.digest.startswith("sha256:")
    assert body["excerpt"] == "def b():\n    return 2\n" and body["lines"] == [3, 4]
    assert (body["lexical_rank"], body["lexical_score"], body["depth"]) == (2, 2.5, 0)
    assert body["truncated"] == [] and body["source_bytes"] == len(body["excerpt"])
    routed = json.loads(_capsule(POOL[2]).canonical)
    assert routed["lexical_rank"] is None and routed["lexical_score"] is None  # missing, not 0
    assert routed["origins"] == ["calls:in:symbol:m.py#a"]
    # No policy outcome, heuristic value, gold or oracle field reaches a judge.
    assert not {"value", "basis", "selected", "included", "gold", "oracle"} & body.keys()


def test_capsules_are_deterministic_and_digests_track_the_evidence() -> None:
    assert _capsule(POOL[0]).digest == _capsule(POOL[0]).digest
    changed = SOURCE.replace(b"return 1", b"return 9")
    assert _capsule(POOL[0], data=changed).digest != _capsule(POOL[0]).digest
    assert _capsule(POOL[0], query="other").digest != _capsule(POOL[0]).digest


def test_recorded_judgments_replay_only_on_identical_capsules() -> None:
    digests = _digests()
    recorded = Recorded({digests[C.key]: 0.95, digests[A.key]: 0.5})
    items, _ = _pack(_judge(recorded), ONE)
    assert _reasons(items) == {"c": f"value 0.95, judged by {FAKE}"}
    assert _pack(_judge(recorded), ONE)[0] == items  # replay is exact
    # Edit a's body (the fixture keeps the file digest): only a's capsule changes, so
    # only its recorded judgment stops applying. A real edit also changes the digest.
    edited = SOURCE.replace(b"return 1", b"return 7")
    reasons = _reasons(_pack(_judge(recorded, edited), 4096, edited)[0])
    assert reasons["a"] == f"value 0.85, lexical rank 1, {FAKE} missing"
    assert reasons["c"] == f"value 0.95, judged by {FAKE}"


def test_heuristic_capsule_replay_packs_the_heuristic_bundle() -> None:
    digests = _digests()
    recorded = Recorded({digests[c.entity.key]: value(c)[0] for c in POOL})
    for budget in (0, 1, ONE, 2 * ONE, 4096):
        expected, judged = _pack(None, budget), _pack(_judge(recorded), budget)
        assert [i.text for i in judged[0]] == [i.text for i in expected[0]]
        assert [(o.key, o.stage) for o in judged[1]] == [(o.key, o.stage) for o in expected[1]]


def test_capsules_are_sent_in_digest_order_whatever_the_pool_order() -> None:
    sent: list[tuple[str, ...]] = []

    def spy(capsules: Sequence[Capsule]) -> Mapping[str, float | None]:
        sent.append(tuple(c.digest for c in capsules))
        return {}

    _pack(_judge(spy))
    _pack(_judge(spy), pool=POOL[::-1])
    assert sent[0] == sent[1] == tuple(sorted(sent[0]))


def test_fields_are_clipped_deterministically_and_declared() -> None:
    query = "é" * 600  # 1,200 bytes: clipped on a character boundary
    seeds = tuple(f"path:p{i}.py" for i in range(6))
    origins = tuple(f"child:file:{'x' * 600}{i}" for i in range(10))
    entity = Entity("symbol:m.py#a", "symbol", "function", "n" * 700, None, A.source, False)
    cap = _capsule(Candidate(entity, 1, None, True, seeds=seeds, origins=origins), query)
    assert len(cap.query.encode()) == 1024 and cap.query == "é" * 512
    assert cap.query_digest == capsule._sha(query.encode())  # digest of the whole query
    assert len(cap.name.encode()) == 512 and len(cap.seeds) == 4
    assert len(cap.origins) == 8 and cap.origins_omitted == 2
    assert all(len(o.encode()) == 512 for o in cap.origins)
    assert cap.truncated == ("seeds", "origins", "query", "name")


@pytest.mark.parametrize(
    ("body", "kept"),
    [
        (b"x = 1\n" * 1000, 4092),  # cut after the last whole line that fits
        (b"x" * 5000 + b"\n", 4096),  # one long line: cut at the limit
    ],
    ids=["lines", "one-line"],
)
def test_long_sources_are_cut_with_an_indicator(body: bytes, kept: int) -> None:
    ref = SourceRef("m.py", 0, len(body), "d")
    entity = Entity("file:m.py", "file", "file", "m.py", None, ref, False)
    cap = _capsule(Candidate(entity, 0, 1, False), data=body)
    assert len(cap.excerpt.encode()) == kept and body.startswith(cap.excerpt.encode())
    assert cap.truncated == ("excerpt",) and cap.source_bytes == len(body)


def test_unrepresentable_candidates_are_not_sent_and_keep_the_heuristic() -> None:
    key = "symbol:m.py#" + "k" * 1100
    long_key = Entity(key, "symbol", "function", "k", None, B.source, False)
    escapes = b"\x01" * 3000 + b"\n"  # 6 bytes each once JSON-escaped: over the capsule bound
    ref = SourceRef("m.py", 0, len(escapes), "d")
    noisy = Entity("symbol:m.py#n", "symbol", "function", "n", None, ref, False)
    assert build(QUERY, "d", Candidate(long_key, 0, 1, False), lambda r: SOURCE) is None
    assert build(QUERY, "d", Candidate(noisy, 0, 1, False), lambda r: escapes) is None
    calls: list[int] = []

    def adapter(capsules: Sequence[Capsule]) -> Mapping[str, float | None]:
        calls.append(len(capsules))
        return {}

    items, _ = _pack(_judge(adapter, escapes), 4096, escapes, [Candidate(noisy, 0, 1, False)])
    assert calls == []  # nothing sendable: the adapter is not called
    assert _reasons(items) == {"n": f"value 0.85, lexical rank 1, {FAKE} missing"}


def _wholesale(adapter: Any, why: str, deadline: float = 2.0) -> None:
    heuristic, _ = _pack(None, 2 * ONE)
    items, omitted = _pack(_judge(adapter, deadline=deadline), 2 * ONE)
    assert [i.text for i in items] == [i.text for i in heuristic]
    assert _reasons(items)["a"] == f"value 0.85, lexical rank 1, {FAKE} {why}"
    assert omitted == [Omission(C.key, "packing", "budget")]


def test_a_late_adapter_is_abandoned_at_the_deadline() -> None:
    release = threading.Event()
    start = time.monotonic()
    try:
        _wholesale(lambda caps: release.wait(10) and {}, "failed (TimeoutError)", 0.05)
    finally:
        release.set()
    assert time.monotonic() - start < 2  # the request never waits out the adapter


def _raise(capsules: Sequence[Capsule]) -> Mapping[str, float | None]:
    raise ConnectionError("unreachable")


@pytest.mark.parametrize(
    ("adapter", "why"),
    [
        (_raise, "failed (ConnectionError)"),
        (lambda caps: [0.9] * len(caps), "failed (TypeError)"),
        (lambda caps: {"sha256:ghost": 0.9}, "failed (LookupError)"),
        (lambda caps: {A.key: 0.9}, "failed (LookupError)"),  # keyed by subject, not digest
    ],
)
def test_adapter_failures_fall_back_wholesale(adapter: Any, why: str) -> None:
    _wholesale(adapter, why)


def test_bad_values_fall_back_per_candidate() -> None:
    digests = _digests()
    adapter = Recorded({digests[A.key]: float("nan"), digests[B.key]: None, digests[C.key]: 0.6})
    assert _reasons(_pack(_judge(adapter))[0]) == {
        "a": f"value 0.85, lexical rank 1, {FAKE} invalid",
        "b": f"value 0.82, lexical rank 2, {FAKE} abstained",
        "c": f"value 0.60, judged by {FAKE}",
    }


@pytest.fixture
def repo(fts: Path) -> Iterator[oxide.Repository]:
    with oxide.open_repository(FIXTURE) as repository:
        repository.index()
        yield repository


def test_build_context_hands_candidates_their_evidence(
    repo: oxide.Repository, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[list[Candidate]] = []
    real = packing.pack

    def recording(pool: list[Candidate], *args: Any) -> Any:
        seen.append(pool)
        return real(pool, *args)

    monkeypatch.setattr(packing, "pack", recording)
    repo.build_context("where failed HTTP requests are retried", 4096)
    hits = [c for c in seen[0] if c.rank is not None]
    assert hits and all(c.score is not None for c in hits)
    assert all(c.score is None for c in seen[0] if c.rank is None)
    assert all(c.origins for c in seen[0])
    assert {o for c in seen[0] for o in c.origins} & {"entry", "root"}
