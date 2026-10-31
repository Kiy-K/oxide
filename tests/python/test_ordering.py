"""Admitted-candidate ordering (ADR-0009 § 1): the heuristic admits, a judge only orders,
and any incomplete answer keeps the heuristic for the whole request."""

from __future__ import annotations

import time
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path

import pytest
from conftest import FIXTURE

import oxide
from oxide.core import ordering
from oxide.core.capsule import Capsule, CapsuleJudge
from oxide.core.ordering import AdmittedOrder, Unanswered
from oxide.core.packing import INCLUDE_FLOOR, Candidate, pack, value
from oxide.models import Entity, SourceRef

SOURCE = b"def a():\n    return 1\ndef b():\n    return 2\ndef c():\n    return 3\n"
QUERY = "where is b"


def _symbol(name: str, key: str | None = None) -> Entity:
    start = SOURCE.index(f"def {name}".encode())
    end = SOURCE.index(b"\n", SOURCE.index(b"return", start)) + 1
    ref = SourceRef("m.py", start, end, "sha256:x")
    return Entity(key or f"symbol:m.py#{name}", "symbol", "function", name, None, ref, False)


# a and b are admitted (lexical ranks 1 and 2), c is routed only (0.3, below the floor).
POOL = [
    Candidate(_symbol("a"), 0, 1, False, score=3.0, origins=("entry",)),
    Candidate(_symbol("b"), 0, 2, False, score=2.0, origins=("entry",)),
    Candidate(_symbol("c"), 2, None, False, origins=("root",)),
]
P = {"a": 0.1, "b": 0.9, "c": 1.0}


def _by_name(p: Mapping[str, object]) -> object:
    def adapter(capsules: Sequence[Capsule]) -> Mapping[str, object]:
        return {c.digest: p[c.name] for c in capsules if c.name in p}

    return adapter


def _order(adapter: object, deadline: float = 2.0) -> AdmittedOrder:
    judge = CapsuleJudge("fake", QUERY, "d", lambda ref: SOURCE, adapter, deadline)  # type: ignore[arg-type]
    return AdmittedOrder(judge, "sha256:m")


ONE = 22  # units of one function item: the budget shows which candidate comes first


def _packed(provider: object, budget: int = ONE) -> list[str]:
    items, _ = pack(POOL, {}, budget, lambda ref: SOURCE, {}, provider)  # type: ignore[arg-type]
    return [e.name for i in items for e in i.entities]


def test_the_judge_orders_only_the_admitted_set() -> None:
    assert [value(c)[0] >= INCLUDE_FLOOR for c in POOL] == [True, True, False]
    provider = _order(_by_name(P))
    values = provider.judge(POOL)
    assert values == {
        POOL[0].entity.key: INCLUDE_FLOOR + 0.59 * 0.1,
        POOL[1].entity.key: INCLUDE_FLOOR + 0.59 * 0.9,
        POOL[2].entity.key: None,  # c abstains: a judge never admits
    }
    assert provider.outcome == "judged"
    assert provider.versions == {
        "order": ordering.POLICY,
        "judge": "fake@oxide-capsule-v1",
        "judge_manifest": "sha256:m",
    }
    assert _packed(None, 4096) == _packed(_order(_by_name(P)), 4096) == ["a", "b"]  # c stays out
    assert _packed(None) == ["a"]
    assert _packed(_order(_by_name(P))) == ["b"]  # same admitted set, JEV's order


@pytest.mark.parametrize(
    ("answers", "outcome"),
    [
        ({"a": 0.1, "c": 1.0}, "incomplete"),  # b admitted but unjudged
        ({"a": 0.1, "b": None}, "unparsable"),
        ({"a": 0.1, "b": float("nan")}, "unparsable"),
        ({"a": True, "b": 0.9}, "unparsable"),
        ({"a": 0.1, "b": 1.5}, "unparsable"),
    ],
)
def test_any_incomplete_answer_keeps_the_heuristic_for_the_whole_request(
    answers: dict[str, object], outcome: str
) -> None:
    provider = _order(_by_name(answers))
    with pytest.raises(Unanswered):
        provider.judge(POOL)
    assert provider.outcome == outcome
    assert _packed(_order(_by_name(answers))) == _packed(None)


def test_an_unsendable_admitted_candidate_falls_back() -> None:
    huge = Candidate(_symbol("b", "symbol:m.py#" + "b" * 2000), 0, 2, False)
    provider = _order(lambda caps: {c.digest: 0.5 for c in caps})
    with pytest.raises(Unanswered):
        provider.judge([POOL[0], huge])
    assert provider.outcome == "unsendable"


def test_a_judge_without_an_answer_by_the_deadline_falls_back() -> None:
    def slow(capsules: Sequence[Capsule]) -> Mapping[str, float]:
        time.sleep(1.0)
        return {c.digest: 0.5 for c in capsules}

    provider = _order(slow, deadline=0.2)
    with pytest.raises(TimeoutError):
        provider.judge(POOL)
    assert provider.outcome == "deadline"


def test_an_adapter_failure_names_its_outcome() -> None:
    def busy(capsules: Sequence[Capsule]) -> Mapping[str, float]:
        raise Unanswered("http 529")

    provider = _order(busy)
    assert _packed(provider) == _packed(None)
    assert provider.outcome == "http 529"


@pytest.fixture
def repo(fts: Path) -> Iterator[oxide.Repository]:
    with oxide.open_repository(FIXTURE) as repository:
        repository.index()
        yield repository


CONTEXT = "where failed HTTP requests are retried"


def _context(repo: oxide.Repository, by: object, budget: int = 1024) -> oxide.ContextBundle:
    return repo._context(CONTEXT, budget, (), (), None, (), by)  # type: ignore[arg-type]


def _factory(adapter: object) -> ordering.Order:
    def build(query: str, derivation: str, source: object) -> AdmittedOrder:
        judge = CapsuleJudge("fake", query, derivation, source, adapter)  # type: ignore[arg-type]
        return AdmittedOrder(judge, "sha256:m")

    return build


def test_without_a_judge_the_entry_point_is_build_context(repo: oxide.Repository) -> None:
    for budget in (0, 256, 1024, 4096):
        assert _context(repo, None, budget) == repo.build_context(CONTEXT, budget)


def test_a_judged_bundle_names_its_policy_and_judge(repo: oxide.Repository) -> None:
    bundle = _context(repo, _factory(lambda caps: {c.digest: 0.5 for c in caps}))
    plain = repo.build_context(CONTEXT, 1024)
    assert bundle.versions == {
        **plain.versions,
        "order": ordering.POLICY,
        "judge": "fake@oxide-capsule-v1",
        "judge_manifest": "sha256:m",
    }
    assert bundle.degradations == plain.degradations
    assert any("judged by fake" in e.reason for i in bundle.items for e in i.entities)
    assert bundle.used <= 1024


def test_a_fallback_packs_production_and_reports_why(repo: oxide.Repository) -> None:
    def busy(capsules: Sequence[Capsule]) -> Mapping[str, float]:
        raise Unanswered("http 529")

    for budget in (256, 1024, 4096):
        bundle = _context(repo, _factory(busy), budget)
        plain = repo.build_context(CONTEXT, budget)
        assert [(i.source, i.units) for i in bundle.items] == [
            (i.source, i.units) for i in plain.items
        ]
        assert bundle.used == plain.used and bundle.versions == plain.versions
        assert bundle.degradations == (
            *plain.degradations,
            "judge fake@oxide-capsule-v1 fell back: http 529",
        )
