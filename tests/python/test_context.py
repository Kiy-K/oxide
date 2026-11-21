"""Context budgets, provenance and determinism of the bounded ContextBundle."""

from __future__ import annotations

import hashlib
import itertools
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path

import pytest
from conftest import FIXTURE, write

import oxide
from oxide.core.packing import (
    Candidate,
    header_end,
    module_regions,
    pack,
    units,
    unpacked_bodies,
    value,
)
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
    assert first.versions["payload"] == "oxide-payload-v4"


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
    module = [e for i in bundle.items for e in i.entities if e.key == "file:settings.py"]
    assert module and all(not e.complete and "lexical rank" in e.reason for e in module)
    shown = "".join(i.text for i in bundle.items)
    assert "import os" in shown and "TIMEOUT = 30" in shown
    # Declarations are their own views (one item here only because they abut the module's).
    assert {e.name for i in bundle.items for e in i.entities} >= {"f", "g", "C"}
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


def test_views_separated_only_by_whitespace_share_one_item() -> None:
    data = b"def f():\n    return 1\n\n\ndef g():\n    return 2\nX = 1\ndef h():\n    pass\n"

    def symbol(name: str) -> Candidate:
        start = data.index(f"def {name}".encode())
        end = data.index(b"\n", data.index(b"\n", start) + 1)
        ref = SourceRef("m.py", start, end, "x")
        entity = Entity(f"symbol:m.py#{name}", "symbol", "function", name, None, ref, False)
        return Candidate(entity, 0, 1, True)

    items, _ = pack([symbol("f"), symbol("g"), symbol("h")], {}, 4096, lambda ref: data, {})
    assert [i.lines for i in items] == [(1, 6), (8, 9)]  # ``X = 1`` keeps h apart
    assert items[0].text.startswith("### m.py:1-6 f, g\n")


# --- issue #77: query-sensitive test down-weighting and hint priority ---


def _pooled(path: str, name: str, body: str, rank: int | None = None) -> tuple[Candidate, bytes]:
    data = body.encode()
    entity = Entity(
        f"symbol:{path}#{name}",
        "symbol",
        "function",
        name,
        None,
        SourceRef(path, 0, len(data), "x"),
        path.startswith("tests/"),
    )
    return Candidate(entity, 0, rank, False), data


def test_tests_yield_when_they_crowd_production_out_of_the_budget() -> None:
    big = "def t():\n" + "    x = 1\n" * 90
    small = "def p():\n" + "    y = 2\n" * 55
    t, td = _pooled("tests/test_p.py", "t", big, rank=1)
    p1, d1 = _pooled("pkg/a.py", "p1", small, rank=2)
    p2, d2 = _pooled("pkg/b.py", "p2", small, rank=3)
    data = {"tests/test_p.py": td, "pkg/a.py": d1, "pkg/b.py": d2}
    probe, _ = pack([p1, p2], {}, 1 << 20, lambda ref: data[ref.path], {})
    budget = sum(i.units for i in probe)  # exactly two production bodies
    items, omitted = pack([t, p1, p2], {}, budget, lambda ref: data[ref.path], {})
    # The test body (rank 1) claims the budget and both production candidates lose
    # room: the symptom. The retry that gives both production bodies wins.
    assert {i.source.path for i in items} == {"pkg/a.py", "pkg/b.py"}
    assert omitted == [Omission(t.entity.key, "packing", "budget")]


def test_a_failing_judge_packs_exactly_the_default_when_the_retry_fires() -> None:
    big = "def t():\n" + "    x = 1\n" * 90
    small = "def p():\n" + "    y = 2\n" * 55
    t, td = _pooled("tests/test_p.py", "t", big, rank=1)
    p1, d1 = _pooled("pkg/a.py", "p1", small, rank=2)
    p2, d2 = _pooled("pkg/b.py", "p2", small, rank=3)
    data = {"tests/test_p.py": td, "pkg/a.py": d1, "pkg/b.py": d2}
    probe, _ = pack([p1, p2], {}, 1 << 20, lambda ref: data[ref.path], {})
    budget = sum(i.units for i in probe)  # the crowding symptom, as above

    class Boom:
        identity = "boom"

        calls = 0

        def judge(self, candidates: Sequence[Candidate]) -> Mapping[str, float]:
            type(self).calls += 1
            raise RuntimeError("http 529")

    plain_items, plain_omissions = pack([t, p1, p2], {}, budget, lambda ref: data[ref.path], {})
    judge = Boom()
    items, omissions = pack([t, p1, p2], {}, budget, lambda ref: data[ref.path], {}, judge)
    assert judge.calls == 1  # ADR-0009: asked once even when the retry runs
    assert [(i.source, i.units) for i in items] == [(i.source, i.units) for i in plain_items]
    assert omissions == plain_omissions


def test_real_judgments_survive_the_crowding_retry() -> None:
    big = "def t():\n" + "    x = 1\n" * 90
    small = "def p():\n" + "    y = 2\n" * 55
    t, td = _pooled("tests/test_p.py", "t", big, rank=1)
    p1, d1 = _pooled("pkg/a.py", "p1", small, rank=2)
    p2, d2 = _pooled("pkg/b.py", "p2", small, rank=3)
    data = {"tests/test_p.py": td, "pkg/a.py": d1, "pkg/b.py": d2}
    probe, _ = pack([p1, p2], {}, 1 << 20, lambda ref: data[ref.path], {})
    budget = sum(i.units for i in probe)  # the crowding symptom, as above

    class Ranked:
        identity = "ranked"

        calls = 0

        def judge(self, candidates: Sequence[Candidate]) -> Mapping[str, float | None]:
            type(self).calls += 1
            return {c.entity.key: 0.85 for c in candidates}

    judge = Ranked()
    items, omitted = pack([t, p1, p2], {}, budget, lambda ref: data[ref.path], {}, judge)
    # The gate fires on this bundle (both productions are header-only), but a
    # judgment is a request: equal judged values pack equal bundles, the retry
    # gains nothing, and the test body keeps the budget it was judged onto.
    assert judge.calls == 1
    packed = {e.key: e.complete for i in items for e in i.entities}
    assert packed[t.entity.key]  # an unjudged retry would have demoted this
    assert not packed[p1.entity.key] and not packed[p2.entity.key]  # presence only
    assert omitted == []


def test_a_query_about_tests_keeps_its_test_evidence() -> None:
    t, td = _pooled("tests/test_p.py", "t", "def t():\n" + "    x = 1\n" * 90, rank=1)
    p, pd = _pooled("pkg/a.py", "p", "def p():\n" + "    y = 2\n" * 55, rank=2)
    data = {"tests/test_p.py": td, "pkg/a.py": pd}
    items, omitted = pack([t, p], {}, 1 << 20, lambda ref: data[ref.path], {})
    # Ample budget: nothing lost room, so the gate never fires and the test body
    # is shown complete even though production evidence exists.
    assert not omitted
    shown = {e.key: e.complete for i in items for e in i.entities}
    assert shown[t.entity.key] and shown[p.entity.key]


def test_a_class_method_rescued_from_a_test_keeps_its_body() -> None:
    # Issue #77 in production shape: most code lives in methods, and a method
    # packs beside its class header, so the item as a whole is never "complete".
    prod = (
        "class C:\n    def m(self):\n"
        + "".join("        x = 1\n" for _ in range(40))
        + "\n    def n(self):\n"
        + "".join("        z = 1\n" for _ in range(30))
        + "\n"
    ).encode()
    test = ("def t():\n" + "    y = 1\n" * 70).encode()
    data = {"pkg/a.py": prod, "tests/test_a.py": test}

    def source(ref: SourceRef) -> bytes:
        return data[ref.path]

    class_c = Entity(
        "symbol:pkg/a.py#C",
        "symbol",
        "class",
        "C",
        None,
        SourceRef("pkg/a.py", 0, len(prod), "x"),
        False,
    )
    method = Entity(
        "symbol:pkg/a.py#C.m",
        "symbol",
        "method",
        "C.m",
        None,
        SourceRef("pkg/a.py", prod.index(b"    def m"), prod.index(b"\n    def n"), "x"),
        False,
    )
    crowd = Entity(
        "symbol:tests/test_a.py#t",
        "symbol",
        "function",
        "t",
        None,
        SourceRef("tests/test_a.py", 0, len(test), "x"),
        True,
    )
    pool = [Candidate(crowd, 0, 1, False), Candidate(method, 0, 2, False)]
    scopes = {class_c.key: class_c}
    probe_t, _ = pack([pool[0]], scopes, 1 << 20, source, {})
    probe_m, _ = pack([pool[1]], scopes, 1 << 20, source, {})
    units_t = sum(i.units for i in probe_t)
    units_m = sum(i.units for i in probe_m)
    # One body fits, the other only its header: the crowding symptom.
    items, omitted = pack(pool, scopes, units_t + units_m - 1, source, {})
    packed = {e.key: e.complete for i in items for e in i.entities}
    assert packed[method.key]  # the method body came back, not just its header
    assert not omitted


def test_a_retry_that_gains_nothing_is_thrown_away(monkeypatch: pytest.MonkeyPatch) -> None:
    # The gate fires (a production body lost room to a test body), but the
    # down-weighted pass cannot add production units: the production body is
    # too large for the budget either way. The first-pass bundle must survive.
    t, td = _pooled("tests/test_p.py", "t", "def t():\n" + "    x = 1\n" * 90, rank=1)
    p, pd = _pooled("pkg/a.py", "p", "def p():\n" + "    y = 2\n" * 180, rank=2)
    data = {"tests/test_p.py": td, "pkg/a.py": pd}
    probe_t, _ = pack([t], {}, 1 << 20, lambda ref: data[ref.path], {})
    budget = sum(i.units for i in probe_t) + 250  # test body fits; production body never does
    weights: list[float] = []
    real_value = value

    def spy(candidate: Candidate, *, test_weight: float = 1.0) -> tuple[float, str]:
        weights.append(test_weight)
        return real_value(candidate, test_weight=test_weight)

    monkeypatch.setattr("oxide.core.packing.value", spy)
    items, omitted = pack([t, p], {}, budget, lambda ref: data[ref.path], {})
    assert 0.5 in weights  # the retry ran
    assert items[0].source.path == "tests/test_p.py"  # an adopted retry would lead with production
    packed = {e.key: e.complete for i in items for e in i.entities}
    assert packed[t.entity.key] and not packed[p.entity.key]
    assert omitted == []


def test_a_lost_file_outline_does_not_trigger_the_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    # A hinted file whose outline cannot fit is not a production symbol losing
    # its body: the gate must stay off even though test bodies hold the budget.
    # Two module-level runs: the outline is the first run, too large for 150.
    data = ("X = 1\n" * 200 + "\ndef s():\n" + "    x = 1\n" * 10).encode()
    file_ = Candidate(_file("pkg/a.py", data), 0, None, True, hinted=True)
    t, td = _pooled("tests/test_p.py", "t", "def t():\n" + "    y = 1\n" * 40, rank=2)
    sources = {"pkg/a.py": data, "tests/test_p.py": td}
    declarations = {file_.entity.key: [(data.index(b"def"), len(data))]}
    weights: list[float] = []
    real_value = value

    def spy(candidate: Candidate, *, test_weight: float = 1.0) -> tuple[float, str]:
        weights.append(test_weight)
        return real_value(candidate, test_weight=test_weight)

    monkeypatch.setattr("oxide.core.packing.value", spy)
    items, omitted = pack([file_, t], {}, 150, lambda ref: sources[ref.path], declarations)
    assert 0.5 not in weights  # no retry: only a file outline was lost
    assert [i.source.path for i in items] == ["tests/test_p.py"]
    assert Omission(file_.entity.key, "packing", "too large") in omitted


def test_downweighting_admits_but_never_suppresses_a_test() -> None:
    candidate, _ = _pooled("tests/test_p.py", "t", "def t(): pass\n", rank=20)
    score, basis = value(candidate, test_weight=0.5)
    assert (score, basis) == (0.4, "lexical rank 20, test weight 0.5")  # stays at the floor
    seed = Candidate(candidate.entity, 0, None, True, seeds=("symbol:t",))
    assert value(seed, test_weight=0.5) == (0.9, "structural seed")  # a request, not a guess


def test_symbol_seeds_pack_before_a_hinted_file_of_equal_value() -> None:
    data = ("X = 1\n" + "def s():\n" + "    x = 1\n" * 40).encode()
    symbol_data = data[data.index(b"def") :]
    ref = SourceRef("pkg/a.py", data.index(b"def"), len(data), "x")
    entity = Entity("symbol:pkg/a.py#s", "symbol", "function", "s", None, ref, False)
    seeded_symbol = Candidate(entity, 0, None, True, hinted=True, seeds=("symbol:s",))
    file_ = Candidate(_file("pkg/a.py", data), 0, None, True, hinted=True)
    declarations = {file_.entity.key: [(ref.start, ref.end)]}
    budget = units(f"### pkg/a.py:2-42 s\n{symbol_data.decode()}")
    items, omitted = pack([file_, seeded_symbol], {}, budget, lambda ref_: data, declarations)
    # A named symbol is the requested source: it claims the budget before the
    # hinted file's outline does (the outline would otherwise eat the bundle).
    assert [i.source.path for i in items] == ["pkg/a.py"]
    assert all(e.key == entity.key and e.complete for i in items for e in i.entities)
    assert omitted == [Omission(file_.entity.key, "packing", "budget")]


def test_a_symbol_seed_that_lost_its_body_is_reported(repo: oxide.Repository) -> None:
    named = repo.build_context(QUERY, 512, symbols=["RetryPolicy"])
    assert not [d for d in named.degradations if "body" in d]  # it fits: no notice
    small = repo.build_context(QUERY, 256, symbols=["RetryPolicy"])
    assert [d for d in small.degradations if "body" in d] == [
        "symbol:oxidepy/retry.py#RetryPolicy packed without its body; raise the budget"
    ]
    body = "def crowd():\n" + "    x = 1\n" * 60
    crowd, cd = _pooled("pkg/a.py", "crowd", body, rank=1)
    asked = Candidate(crowd.entity, 0, None, True, seeds=("path:pkg/a.py",))
    shown, _ = pack([asked], {}, 128, lambda ref: cd, {})
    assert not all(e.complete for item in shown for e in item.entities)  # header only
    assert unpacked_bodies([asked], shown, ()) == []  # a path seed stays silent
