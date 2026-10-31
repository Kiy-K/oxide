"""Context budgets, provenance and determinism of the bounded ContextBundle."""

from __future__ import annotations

import hashlib
import itertools
from collections.abc import Iterator
from pathlib import Path

import pytest
from conftest import FIXTURE, write

import oxide
from oxide.core.packing import Candidate, header_end, module_regions, pack, units
from oxide.models import ContextItem, Entity, Omission, SourceRef

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
    assert first.versions["payload"] == "oxide-payload-v3"


def test_no_entry_points_fall_back_to_the_root_region(repo: oxide.Repository) -> None:
    bundle = repo.build_context("zzzz qqqq", 256)
    assert bundle.route.root_fallback and bundle.used <= 256


# --- module-level evidence: a parsed file's view outside its top-level declarations ---

MODULE = (
    b'"""Settings."""\nimport os\nLIMIT = 3\n\n\n@cache\ndef f():\n    return LIMIT\n\n\n'
    b"if os.name:\n    def g():\n        pass\nelse:\n    g = None\nclass C:\n    y = 2\n\n"
    b"TIMEOUT = 30\n"
)
DECLARED = [
    (MODULE.index(b"@cache"), MODULE.index(b"return LIMIT") + 12),
    (MODULE.index(b"def g"), MODULE.index(b"pass") + 4),
    (MODULE.index(b"class C"), MODULE.index(b"y = 2") + 5),
]


def test_module_regions_skip_declarations_and_blank_runs() -> None:
    regions = module_regions(MODULE, DECLARED)
    assert [MODULE[s:e] for s, e in regions] == [
        b'"""Settings."""\nimport os\nLIMIT = 3',  # decorators belong to the declaration
        b"if os.name:",  # a nested declaration splits its block
        b"else:\n    g = None",
        b"TIMEOUT = 30",
    ]
    assert module_regions(b"def f():\n    pass\n", [(0, 17)]) == []


def _file(path: str, data: bytes) -> Entity:
    return Entity(
        f"file:{path}", "file", "file", path, None, SourceRef(path, 0, len(data), "x"), False
    )


def test_module_views_fit_whole_reduced_or_not_at_all() -> None:
    entity = _file("m.py", MODULE)
    found = [Candidate(entity, 0, 1, False)]

    def packed(budget: int) -> tuple[list[ContextItem], list[Omission]]:
        return pack(found, {}, budget, lambda ref: MODULE, {entity.key: DECLARED})

    full, _ = packed(4096)
    assert [i.lines for i in full] == [(1, 3), (11, 11), (14, 15), (19, 19)]
    assert full[0].text.startswith("### m.py:1-3 (module-level)\n")
    assert all(e.key == entity.key and not e.complete for i in full for e in i.entities)
    header, _ = packed(full[0].units)  # the full view does not fit: its first run does
    assert [i.text for i in header] == [full[0].text]
    assert header[0].entities[0].reason.endswith("header view")
    nothing, omitted = packed(full[0].units - 1)
    assert nothing == [] and omitted == [Omission(entity.key, "packing", "too large")]


def test_files_without_module_evidence_are_omitted_with_a_reason() -> None:
    unparsed, routed = _file("b.py", MODULE), _file("c.py", MODULE)
    empty = _file("d.py", b"def f():\n    pass\n")
    pool = [Candidate(unparsed, 0, 1, False), Candidate(empty, 0, 2, False)]
    pool.append(Candidate(routed, 1, None, False))  # reached only by routing
    declarations = {routed.key: DECLARED, empty.key: [(0, 17)]}
    data = {"d.py": b"def f():\n    pass\n"}
    items, omitted = pack(pool, {}, 4096, lambda ref: data.get(ref.path, MODULE), declarations)
    assert items == []
    assert omitted == [
        Omission(unparsed.key, "selection", "container"),  # not a parsed Python file
        Omission(empty.key, "selection", "no module-level source"),
        Omission(routed.key, "selection", "value 0.20 below floor"),
    ]


def test_retrieved_files_contribute_bounded_module_evidence(fts: Path, tmp_path: Path) -> None:
    root = write(tmp_path / "repo", {"settings.py": MODULE})
    query = "settings"
    with oxide.open_repository(root) as repository:
        repository.index()
        bundle = repository.build_context(query, 2048)
        assert bundle == repository.build_context(query, 2048)
        for budget in (0, 8, 32, 64, 2048):
            small = repository.build_context(query, budget)
            assert small.used <= budget and small.used == units(small.payload)
    module = [i for i in bundle.items if i.entities[0].key == "file:settings.py"]
    assert module and all(len(i.entities) == 1 and not i.entities[0].complete for i in module)
    assert all("lexical rank" in i.entities[0].reason for i in module)
    shown = "".join(i.text for i in module)
    assert "import os" in shown and "TIMEOUT = 30" in shown
    assert "def " not in shown and "class " not in shown  # declarations are their own views
    ranges = sorted((i.source.start, i.source.end) for i in bundle.items)
    assert all(a[1] <= b[0] for a, b in itertools.pairwise(ranges))


def test_a_hinted_file_contributes_its_header_view() -> None:
    entity = _file("m.py", MODULE)
    found = [Candidate(entity, 0, None, True, hinted=True)]
    items, _ = pack(found, {}, 4096, lambda ref: MODULE, {entity.key: DECLARED})
    assert [i.lines for i in items] == [(1, 3)]  # its imports, not every module-level run
    assert items[0].entities[0].reason.endswith("hinted path, header view")


def test_path_hints_pack_the_hinted_files_declarations_first(repo: oxide.Repository) -> None:
    plain = repo.build_context(QUERY, 512)
    hinted = repo.build_context(QUERY, 512, paths=["oxidepy/cache.py"])
    assert hinted == repo.build_context(QUERY, 512, paths=["oxidepy/cache.py"])
    assert not any("hinted" in e.reason for i in plain.items for e in i.entities)
    first = [i for i in hinted.items if i.source.path == "oxidepy/cache.py"]
    assert hinted.items[: len(first)] == tuple(first)  # before any lexical hit elsewhere
    assert any(e.name == "TTLCache" and e.complete for i in first for e in i.entities)
    assert any(i.source.path != "oxidepy/cache.py" for i in hinted.items)  # not exclusive
