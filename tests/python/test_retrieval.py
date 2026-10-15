"""Deterministic retrieval: OXIDE terms, entry-point order, hints and channel status."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from conftest import FIXTURE

import oxide
from oxide.core.retrieval import terms


@pytest.fixture
def repo(fts: Path) -> Iterator[oxide.Repository]:
    with oxide.open_repository(FIXTURE) as repository:
        repository.index()
        yield repository


def test_terms_split_code_identifiers_like_the_rust_rules() -> None:
    assert terms("RetryPolicy") == ["retry", "policy", "retrypolicy"]
    assert terms("should_retry") == ["should", "retry", "shouldretry"]
    assert terms("HTTPClient") == ["http", "client", "httpclient"]
    assert terms("TTLCache") == ["ttl", "cache", "ttlcache"]
    assert terms("sha256") == ["sha", "256", "sha256"]
    assert terms("def fetch(self, url: str)") == ["def", "fetch", "self", "url", "str"]
    assert terms("oxidepy/auth.py") == ["oxidepy", "auth", "py"]
    assert terms("__init__ é x") == ["init"]
    assert terms("v2 a.rs") == ["v2", "rs"]
    assert terms("q t 9") == []


def test_lexical_hits_are_ordered_bounded_and_repeatable(repo: oxide.Repository) -> None:
    first = repo.search("where failed HTTP requests are retried", limit=5)
    second = repo.search("where failed HTTP requests are retried", limit=5)
    assert first == second
    assert first.lexical.state == "ran" and first.lexical.truncated
    ranks = [e.lexical_rank for e in first.entries]
    scores = [e.lexical_score or 0.0 for e in first.entries]
    assert ranks == [1, 2, 3, 4, 5]
    assert scores == sorted(scores, reverse=True) and all(s > 0 for s in scores)


def test_seeds_precede_lexical_hits_and_keep_their_evidence(repo: oxide.Repository) -> None:
    found = repo.search("retry", symbols=["RetryPolicy.should_retry"], paths=["OXIDEPY/cache.py"])
    head = found.entries[:2]
    assert [e.entity.key for e in head] == [
        "file:oxidepy/cache.py",
        "symbol:oxidepy/retry.py#RetryPolicy.should_retry",
    ]
    assert head[0].seeds == (oxide.Seed("path", "OXIDEPY/cache.py", True),)
    assert head[1].lexical_rank is not None  # a seed that also matched keeps both


def test_hint_diagnostics_report_what_did_not_seed(repo: oxide.Repository) -> None:
    found = repo.search("x", symbols=["NoSuchThing"], paths=["missing.py"])
    assert {(h.kind, h.issue) for h in found.hints} == {
        ("symbol", "not_found"),
        ("path", "not_found"),
    }


def test_selection_seeds_the_innermost_declaration(repo: oxide.Repository) -> None:
    data = (FIXTURE / "oxidepy/retry.py").read_bytes()
    at = data.index(b"transient = isinstance")
    found = repo.search("", selection=("oxidepy/retry.py", at, at + 9))
    assert found.entries[0].entity.key == "symbol:oxidepy/retry.py#RetryPolicy.should_retry"


def test_missing_fts_is_reported_unavailable_and_seeds_still_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OXIDE_FTS_EXTENSION", str(tmp_path / "absent.lbug_extension"))
    with oxide.open_repository(FIXTURE) as repo:
        assert repo.index().lexical.startswith("FTS extension missing")
        found = repo.search("RetryPolicy", symbols=["RetryPolicy"])
    assert found.lexical.state == "unavailable"
    assert [e.entity.key for e in found.entries] == ["symbol:oxidepy/retry.py#RetryPolicy"]
