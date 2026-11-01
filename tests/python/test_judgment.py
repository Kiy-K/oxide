"""The optional DecisionProvider seam behind ``packing.value``: a replay fake judges,
deterministic policy decides, and every failure falls back to the heuristic."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import pytest

from oxide.core.packing import Candidate, Replay, pack, value
from oxide.models import ContextItem, Entity, Omission, SourceRef

SOURCE = b"def a():\n    return 1\ndef b():\n    return 2\ndef c():\n    return 3\n"
PARENT = Entity("file:m.py", "file", "file", "m.py", None, SourceRef("m.py", 0, 66, "x"), False)


def _symbol(name: str) -> Entity:
    start = SOURCE.index(f"def {name}".encode())
    end = SOURCE.index(b"\n", SOURCE.index(b"return", start)) + 1
    ref = SourceRef("m.py", start, end, "x")
    return Entity(f"symbol:m.py#{name}", "symbol", "function", name, None, ref, False)


A, B, C = _symbol("a"), _symbol("b"), _symbol("c")
POOL = [Candidate(A, 0, 1, False), Candidate(B, 0, 2, False), Candidate(C, 0, 3, False)]
ONE = 22  # units of one function item


def _pack(
    pool: list[Candidate], budget: int = 4096, judge: object = None
) -> tuple[list[ContextItem], list[Omission]]:
    return pack(pool, {}, budget, lambda ref: SOURCE, {}, judge)  # type: ignore[arg-type]


def _names(items: list[ContextItem]) -> list[str]:
    return [e.name for i in items for e in i.entities]


def _reasons(items: list[ContextItem]) -> dict[str, str]:
    return {e.name: e.reason for i in items for e in i.entities}


@dataclass
class Raw:
    """A provider returning whatever it is given, counting its calls."""

    out: object
    identity: str = "raw"
    calls: int = 0

    def judge(self, candidates: Sequence[Candidate]) -> Mapping[str, float | None]:
        self.calls += 1
        if isinstance(self.out, Exception):
            raise self.out
        return self.out  # type: ignore[return-value]


def test_judgments_replace_only_the_value() -> None:
    items, omitted = _pack(POOL, ONE)
    assert _names(items) == ["a"] and len(omitted) == 2
    judged, _ = _pack(POOL, ONE, Replay("fake", {C.key: 0.9, B.key: 0.6, A.key: 0.5}))
    assert _names(judged) == ["c"]  # the budget, caps and rendering are unchanged
    assert judged[0].units == items[0].units
    assert _reasons(judged) == {"c": "value 0.90, judged by fake"}


def test_ties_keep_pool_order_and_payload_merges_spans() -> None:
    items, _ = _pack(POOL, 4096, Replay("fake", {A.key: 0.7, B.key: 0.7, C.key: 0.7}))
    assert [i.text for i in items] == [i.text for i in _pack(POOL)[0]]


def test_the_floor_stays_deterministic_on_judged_values() -> None:
    routed = Candidate(_symbol("b"), 2, None, False)  # heuristic 0.30: below the floor
    pool = [POOL[0], routed]
    assert _names(_pack(pool)[0]) == ["a"]
    items, omitted = _pack(pool, 4096, Replay("fake", {A.key: 0.1, B.key: 0.8}))
    assert _names(items) == ["b"]
    assert omitted == [Omission(A.key, "selection", "value 0.10 below floor, judged by fake")]


def test_hinted_candidates_stay_first_whatever_their_judgment() -> None:
    pool = [*POOL[:2], Candidate(C, 0, 3, False, hinted=True)]
    items, _ = _pack(pool, ONE, Replay("fake", {A.key: 1.0, C.key: 0.41}))
    assert _names(items) == ["c"]


def test_only_packable_candidates_are_asked_once() -> None:
    asked: list[tuple[str, ...]] = []

    class Spy:
        identity = "spy"

        def judge(self, candidates: Sequence[Candidate]) -> Mapping[str, float | None]:
            asked.append(tuple(c.entity.key for c in candidates))
            return {}

    sourceless = Entity("symbol:m.py#z", "symbol", "function", "z", None, None, False)
    module = Entity("module:m", "module", "module", "m", None, None, False)
    pool = [Candidate(module, 0, None, True), Candidate(PARENT, 0, 1, False)]
    pool += [*POOL, Candidate(sourceless, 1, None, False)]
    _pack(pool, 4096, Spy())
    assert asked == [(A.key, B.key, C.key)]  # no container, unparsed file or sourceless symbol
    _pack(pool[:2], 4096, Spy())
    assert len(asked) == 1  # nothing packable: the provider is not called


def test_replay_returns_only_requested_candidates() -> None:
    recorded = Replay("fake", {A.key: 0.5, "symbol:other.py#x": 1.0})
    assert recorded.judge(POOL[:1]) == {A.key: 0.5}


@pytest.mark.parametrize(
    ("judgment", "why"),
    [
        (None, "abstained"),
        (math.nan, "invalid"),
        (math.inf, "invalid"),
        (1.5, "invalid"),
        (-0.1, "invalid"),
        (True, "invalid"),
        ("0.9", "invalid"),
    ],
)
def test_bad_judgments_fall_back_per_candidate(judgment: object, why: str) -> None:
    provider = Raw({A.key: judgment, B.key: 0.95})
    assert _names(_pack(POOL, ONE, provider)[0]) == ["b"]  # judged b outranks a
    assert _names(_pack(POOL, 2 * ONE, provider)[0]) == ["a", "b"]  # a keeps 0.85 over c
    items, _ = _pack(POOL, 4096, provider)  # c is missing, a is rejected: both heuristic
    assert _reasons(items) == {
        "a": f"value 0.85, lexical rank 1, raw {why}",
        "b": "value 0.95, judged by raw",
        "c": "value 0.80, lexical rank 3, raw missing",
    }


@pytest.mark.parametrize(
    ("out", "why"),
    [
        (RuntimeError("timeout"), "failed (RuntimeError)"),
        ([0.9, 0.8, 0.7], "returned no mapping"),
        ({A.key: 0.1, "symbol:m.py#ghost": 0.9}, "judged an unknown candidate"),
    ],
)
def test_provider_failures_fall_back_wholesale(out: object, why: str) -> None:
    heuristic, _ = _pack(POOL, 2 * ONE)
    provider = Raw(out)
    items, omitted = _pack(POOL, 2 * ONE, provider)
    assert provider.calls == 1
    assert [i.text for i in items] == [i.text for i in heuristic]
    assert _reasons(items)["a"] == f"value 0.85, lexical rank 1, raw {why}"
    assert omitted == [Omission(C.key, "packing", "budget")]


def test_replaying_heuristic_values_packs_the_same_bundle() -> None:
    heuristic = Replay("heuristic-replay", {c.entity.key: value(c)[0] for c in POOL})
    for budget in (0, 1, ONE, 2 * ONE, 4096):
        expected, judged = _pack(POOL, budget), _pack(POOL, budget, heuristic)
        assert [i.text for i in judged[0]] == [i.text for i in expected[0]]
        assert [(o.key, o.stage) for o in judged[1]] == [(o.key, o.stage) for o in expected[1]]
        assert sum(i.units for i in judged[0]) <= budget
