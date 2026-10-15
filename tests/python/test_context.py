"""Context budgets, provenance and determinism of the bounded ContextBundle."""

from __future__ import annotations

import hashlib
import itertools
from collections.abc import Iterator
from pathlib import Path

import pytest
from conftest import FIXTURE

import oxide
from oxide.core.packing import header_end, units

QUERY = "where failed HTTP requests are retried"


@pytest.fixture
def repo(fts: Path) -> Iterator[oxide.Repository]:
    with oxide.open_repository(FIXTURE) as repository:
        repository.index()
        yield repository


def test_units_follow_oxide_units_v1() -> None:
    assert units("") == 0
    assert units("abcd") == 1
    assert units("abcde_x") == 2
    assert units("a(b)\n") == 5
    assert units("  \t") == 0


@pytest.mark.parametrize("budget", [0, 1, 16, 64, 256, 1024, 4096])
def test_bundles_never_exceed_the_budget(repo: oxide.Repository, budget: int) -> None:
    bundle = repo.build_context(QUERY, budget)
    assert bundle.used <= budget
    assert bundle.used == units(bundle.payload) == sum(i.units for i in bundle.items)
    if budget == 0:
        assert bundle.payload == "" and bundle.items == ()
        assert any(o.stage == "packing" for o in bundle.omissions)


def test_items_are_verified_source_with_provenance(repo: oxide.Repository) -> None:
    bundle = repo.build_context(QUERY, 1024)
    assert bundle.items and bundle.snapshot == repo.snapshot
    spans: dict[str, list[tuple[int, int]]] = {}
    for item in bundle.items:
        data = (FIXTURE / item.source.path).read_bytes()
        assert item.source.digest == "sha256:" + hashlib.sha256(data).hexdigest()
        body = data[item.source.start : item.source.end].decode()
        assert item.text.split("\n", 1)[1].rstrip("\n") == body.rstrip("\n")
        assert item.text.startswith(f"### {item.source.path}:{item.lines[0]}-{item.lines[1]} ")
        assert all(e.reason for e in item.entities)
        spans.setdefault(item.source.path, []).append((item.source.start, item.source.end))
    for ranges in spans.values():  # merged spans never overlap or repeat source
        ranges.sort()
        assert all(a[1] <= b[0] for a, b in itertools.pairwise(ranges))


def test_small_budgets_reduce_to_marked_headers(repo: oxide.Repository) -> None:
    bundle = repo.build_context(QUERY, 64)
    headers = [e for i in bundle.items for e in i.entities if not e.complete]
    assert headers and all(f"{e.name} (header)" in bundle.payload for e in headers)


def test_header_view_stops_at_the_declaring_line() -> None:
    source = b"@decorator\ndef f(a,\n      b):\n    return a\n"
    assert source[: header_end(source, 0, len(source), "f")] == b"@decorator\ndef f(a,\n"
    assert header_end(b"def g(): pass\n", 0, 14, "g") is None


def test_context_is_deterministic_and_reports_routing(repo: oxide.Repository) -> None:
    first = repo.build_context(QUERY, 512, symbols=["RetryPolicy"])
    assert first == repo.build_context(QUERY, 512, symbols=["RetryPolicy"])
    assert first.route.regions > 0 and not first.route.root_fallback
    assert first.route.stopped_by == "depth"
    assert "routing stopped by its depth bound" in first.degradations
    assert first.versions["payload"] == "oxide-payload-v2"


def test_no_entry_points_fall_back_to_the_root_region(repo: oxide.Repository) -> None:
    bundle = repo.build_context("zzzz qqqq", 256)
    assert bundle.route.root_fallback and bundle.used <= 256
