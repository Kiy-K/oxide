"""Bounded impact navigation: dependents and dependencies with provenance tiers."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from conftest import python, write

import oxide
from oxide.core import navigation
from oxide.models import InvalidArgument
from oxide.storage.ladybug import unresolved_key

SOURCES = {
    "pkg/__init__.py": "",
    "pkg/core.py": (
        "class Store:\n    def get(self):\n        return 1\n\ndef low():\n    return Store().get\n"
    ),
    "pkg/dup.py": "def twice(): pass\ndef twice(): pass\n",
    "pkg/mid.py": (
        "from .core import low\nfrom .dup import twice\n\ndef mid():\n    low()\n    twice()\n"
    ),
    "pkg/top.py": "from .mid import mid\n\ndef top():\n    mid()\n",
    "pkg/loop.py": (
        "def ping():\n    pong()\n\ndef pong():\n    ping()\n    low()\n\nfrom .core import low\n"
    ),
    "pkg/opaque.py": "def far(x):\n    x.low()\n",
    "tests/test_mid.py": "from pkg.mid import mid\n\ndef test_mid():\n    mid()\n",
}


@pytest.fixture
def repo(tmp_path: Path) -> Iterator[oxide.Repository]:
    with oxide.open_repository(write(tmp_path / "src", SOURCES)) as repository:
        repository.index()
        yield repository


def hops(impact: oxide.Impact) -> dict[str, tuple[int, int | None]]:
    return {h.entity.key: (h.depth, h.proven_depth) for h in impact.hops}


def test_upstream_reaches_callers_and_tests_by_depth(
    repo: oxide.Repository,
) -> None:
    impact = repo.impact("symbol:pkg/core.py#low")
    found = hops(impact)
    assert found["symbol:pkg/mid.py#mid"] == (1, 1)
    assert found["symbol:pkg/top.py#top"] == (2, 2)
    # proven by its call; the parallel tested_by heuristic alone would not prove it
    assert found["symbol:tests/test_mid.py#test_mid"] == (2, 2)
    assert found["file:pkg/mid.py"] == (1, 1)  # it imports ``low``
    assert impact.hops == tuple(sorted(impact.hops, key=lambda h: (h.depth, h.entity.key)))
    # ``x.low()`` names it but is never resolved to it
    assert impact.unresolved == 1
    assert impact.stopped_by is None


def test_ambiguous_candidates_are_reachable_but_unproven(repo: oxide.Repository) -> None:
    found = hops(repo.impact("symbol:pkg/dup.py#twice"))
    assert found["symbol:pkg/mid.py#mid"] == (1, None)


def test_cycles_are_flagged_and_terminate(repo: oxide.Repository) -> None:
    impact = repo.impact("symbol:pkg/core.py#low", depth=8)
    cyclic = {h.entity.key for h in impact.hops if h.cyclic}
    assert cyclic == {"symbol:pkg/loop.py#ping", "symbol:pkg/loop.py#pong"}


def test_the_walk_keeps_its_edges_as_a_networkx_projection(repo: oxide.Repository) -> None:
    found = navigation.walk(repo._view(), "symbol:pkg/core.py#low", "upstream", 8, 200)
    graph = found.graph
    assert {h.key for h in found.hops} <= set(graph.nodes)
    ping, pong = "symbol:pkg/loop.py#ping", "symbol:pkg/loop.py#pong"
    assert graph.has_edge(ping, pong) and graph.has_edge(pong, ping)
    assert graph.edges[pong, ping]["via"] == f"calls:in:{pong}"  # traversal direction


def test_networkx_is_imported_only_when_needed(tmp_path: Path) -> None:
    probe = "import sys, oxide; assert 'networkx' not in sys.modules; print('lazy')"
    assert python(tmp_path, probe).stdout.strip() == "lazy"


def test_a_class_stands_for_its_members_and_a_file_for_its_module(
    repo: oxide.Repository,
) -> None:
    impact = repo.impact("symbol:pkg/core.py#Store")
    assert impact.roots == ("symbol:pkg/core.py#Store", "symbol:pkg/core.py#Store.get")
    assert "symbol:pkg/core.py#low" in hops(impact)
    importers = repo.impact("file:pkg/mid.py", depth=1)
    assert "module:python:pkg.mid" in importers.roots
    assert {"file:pkg/top.py", "file:tests/test_mid.py"} <= set(hops(importers))


def test_downstream_follows_dependencies(repo: oxide.Repository) -> None:
    found = hops(repo.impact("symbol:pkg/top.py#top", direction="downstream"))
    assert found["symbol:pkg/mid.py#mid"] == (1, 1)
    assert found["symbol:pkg/core.py#low"] == (2, 2)
    assert found["symbol:pkg/dup.py#twice~1"] == (2, None)


def test_bounds_fire_visibly(repo: oxide.Repository) -> None:
    shallow = repo.impact("symbol:pkg/core.py#low", depth=1)
    assert shallow.stopped_by == "depth"
    assert max(h.depth for h in shallow.hops) == 1
    assert repo.impact("symbol:pkg/core.py#low", limit=2).stopped_by == "limit"
    assert len(repo.impact("symbol:pkg/core.py#low", limit=2).hops) == 2
    assert repo.impact("symbol:pkg/core.py#low", depth=0).hops == ()


def test_invalid_arguments(repo: oxide.Repository) -> None:
    with pytest.raises(InvalidArgument):
        repo.impact("symbol:missing#x")
    with pytest.raises(InvalidArgument):
        repo.impact("symbol:pkg/core.py#low", direction="sideways")  # type: ignore[arg-type]
    with pytest.raises(InvalidArgument):
        repo.impact("symbol:pkg/core.py#low", depth=9)


@pytest.mark.parametrize(
    ("name", "sites"),
    [
        ("low", ["low", "x.low", "a.b.low", "mod::low", "a:::low"]),
        ("Store.get", ["Store.get", "x.Store.get", "m::Store.get"]),
        ("Vec::new", ["Vec::new", "std::vec::Vec::new", "x.Vec::new"]),
    ],
)
def test_unresolved_sites_share_the_key_of_every_name_they_may_target(
    name: str, sites: list[str]
) -> None:
    # ``mentions`` reads only the Unresolved node of the name's key; the count stays exact
    assert {unresolved_key(s) for s in sites} == {unresolved_key(name)}
    assert unresolved_key("") == "u:"
