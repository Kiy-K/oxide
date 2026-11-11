"""Bounded impact navigation: dependents and dependencies with provenance tiers."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from conftest import python, write

import oxide
from oxide.capture import capture
from oxide.core import blast, navigation
from oxide.core.blast import BLAST_DEPTH, BLAST_FILES, BLAST_LIMIT
from oxide.core.indexing import derive
from oxide.models import BlastRoot, InvalidArgument, ResourceExhausted, StoreCorrupt, advice
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


def test_mentions_counts_each_unresolved_site_once_by_exact_name_or_suffix() -> None:
    # The indexed lookup replaces a scan with this rule: a site counts when its name is the
    # target's name or ends with ``.name`` or ``::name``, and each site counts once per query.
    sites = [("calls", s) for s in ("get", "obj.get", "pkg::mod.get", "getter", "x::get.z")]
    sites += [("calls", s) for s in ("a.b", "q.a.b", "a::b")] + [("imports", "get")]
    entities: list[Any] = [SimpleNamespace(key="k")]  # Graph reads only ``key``
    edges: list[Any] = [
        SimpleNamespace(
            source="k", targets=(), kind=kind, resolution="unresolved", basis="syntactic", name=name
        )
        for kind, name in sites
    ]
    graph = blast.Graph(entities, edges)
    assert graph.mentions(["get"], ("calls",)) == 3
    assert graph.mentions(["get"], ("imports",)) == 1
    assert graph.mentions(["a.b"], ("calls",)) == 2
    assert graph.mentions(["b"], ("calls",)) == 3
    assert graph.mentions(["get", "b"], ("calls",)) == 6
    assert graph.mentions([], ("calls",)) == 0


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


def test_store_reads_stop_at_the_edge_allowance_and_say_so(
    repo: oxide.Repository, monkeypatch: pytest.MonkeyPatch
) -> None:
    from oxide.core import routing

    view, low = repo._view(), "symbol:pkg/core.py#low"
    whole = view.adjacency([low])[low]
    rows = max(map(len, whole))
    assert view.adjacency([low], rows) == {low: whole}
    assert view.adjacency([low], rows - 1) == {}  # cut keys are absent, never partial
    calls = view.adjacency([low], kinds=("calls",))[low]
    assert calls == tuple([e for e in side if e[0] == "calls"] for side in whole) != whole
    monkeypatch.setattr(navigation, "MAX_EDGES", 0)
    assert repo.impact(low).stopped_by == "edges"
    monkeypatch.setattr(routing, "MAX_ROWS", 0)
    assert routing.route(view, [low]).stopped_by == "edges"


def test_the_in_memory_graph_cuts_at_the_allowance_as_the_store_does(
    repo: oxide.Repository, tmp_path: Path
) -> None:
    # The stored blast radius is walked over ``blast.Graph``; its cut rule must match the view's.
    derived = derive(capture(tmp_path / "src"), "r")
    graph, view = blast.Graph(derived.entities, derived.edges), repo._view()
    keys = sorted(graph.known)
    cuts = 0
    for group in (keys[:1], keys[:4], keys[-7:], keys):
        for allowance in (None, 0, 1, 2, 3, 5, 8, 13, 50):
            for kinds in ((), ("calls",), ("contains", "tested_by")):
                want = view.adjacency(group, allowance, kinds)
                got = graph.adjacency(group, allowance, kinds)
                assert got.keys() == want.keys(), (group, allowance, kinds)
                cuts += len(got) < len(group)
                for key in got:  # the store orders rows by key only
                    assert [sorted(side) for side in got[key]] == [sorted(s) for s in want[key]]
    assert cuts  # the comparison reached the cut path


def test_distinct_query_limits_do_not_exhaust_the_read_pool(repo: oxide.Repository) -> None:
    # #55: an allowance inlined in the query text kept ~1 MiB of the 128 MiB read pool per
    # distinct value, so a long-lived repository's walks ended in store_corrupt.
    view, low = repo._view(), "symbol:pkg/core.py#low"
    whole = view.adjacency([low])[low]
    for allowance in range(10, 510):
        assert view.adjacency([low], allowance) == {low: whole}
    for limit in range(1, 501):  # the same holds for the name lookup's limit
        view.named("low", limit)


def test_a_full_read_pool_is_resource_exhaustion_and_other_native_failures_are_damage(
    repo: oxide.Repository, monkeypatch: pytest.MonkeyPatch
) -> None:
    import oxide.storage.ladybug as storage

    full = "Buffer manager exception: Unable to allocate memory! The buffer pool is full"
    root = repo.root
    cases = ((full, ResourceExhausted), ("Corrupted page", StoreCorrupt))
    for message, expected in cases:

        def fail(*_: object, message: str = message) -> list[Any]:
            raise RuntimeError(message)

        with monkeypatch.context() as patch:
            patch.setattr(storage, "_rows", fail)
            with pytest.raises(expected) as caught:  # on a read of the open view
                repo.impact("symbol:pkg/core.py#low")
            assert type(caught.value) is expected  # distinct: neither subclasses the other
        repo.close()  # and on opening the view
        with monkeypatch.context() as patch:
            patch.setattr(storage, "_rows", fail)
            with pytest.raises(expected) as caught, oxide.open_repository(root) as again:
                again.snapshot  # noqa: B018 - the first read opens the view
            assert type(caught.value) is expected
        repo = oxide.open_repository(root)
    repo.close()
    assert ResourceExhausted.code == "resource_exhausted"
    assert advice(ResourceExhausted("full"), "/repo")


def test_search_and_context_carry_the_stored_blast_of_their_first_entry_points(
    repo: oxide.Repository,
) -> None:
    found = repo.search("low", symbols=["low"])
    bundle = repo.build_context("low", 1024, symbols=["low"])
    assert found.blast == bundle.blast
    assert [b.key for b in found.blast] == [e.entity.key for e in found.entries][:3]
    assert "symbol:pkg/core.py#low" in {b.key for b in found.blast}


def test_stored_blast_radius_equals_the_impact_walk_of_every_entity(
    repo: oxide.Repository,
) -> None:
    # Computed at publication from the in-memory relations; the store's own walk must agree.
    view = repo._view()
    keys = sorted(row[0] for row in view._query("MATCH (e:Entity) RETURN e.key"))
    stored = {key: root for key, (_, root) in view.entries(keys).items()}
    assert set(stored) == set(keys)
    for key in keys:
        walked = repo.impact(key, depth=BLAST_DEPTH, limit=BLAST_LIMIT)
        paths = sorted({h.entity.source.path for h in walked.hops if h.entity.source is not None})
        expected = BlastRoot(
            key,
            len(walked.hops),
            sum(h.entity.test for h in walked.hops),
            tuple(paths[:BLAST_FILES]),
            len(paths),
            walked.unresolved,
            walked.stopped_by,
        )
        assert stored[key] == expected, key
