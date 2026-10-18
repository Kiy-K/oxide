"""Parsing and indexing: entities, identity, coverage and diagnostics."""

from __future__ import annotations

import random
import time
from pathlib import Path

from conftest import FIXTURE, write

import oxide
from oxide.capture import capture
from oxide.core import python
from oxide.core.indexing import derive, validate


def keys(root: Path) -> dict[str, oxide.Entity]:
    derived = derive(capture(root), "test-repo")
    validate(derived)
    return {e.key: e for e in derived.entities}


def test_fixture_derivation_matches_the_rust_slice_counts() -> None:
    # The Rust oxide-python-v1 slice recorded 65 entities / 194 relations (docs/phase-3.md).
    derived = derive(capture(FIXTURE), "test-repo")
    assert (len(derived.entities), len(derived.edges)) == (65, 194)


def test_symbols_get_nested_ordinal_identities_and_kinds(tmp_path: Path) -> None:
    source = (
        "def f(): pass\n"
        "def f(): pass\n"
        "class A:\n"
        "    @staticmethod\n"
        "    def m(x):\n"
        "        def inner(): pass\n"
        "        return inner\n"
    )
    found = keys(write(tmp_path, {"pkg/mod.py": source}))
    assert {"symbol:pkg/mod.py#f", "symbol:pkg/mod.py#f~1"} <= found.keys()
    method = found["symbol:pkg/mod.py#A.m"]
    assert method.kind == "method"
    assert found["symbol:pkg/mod.py#A.m.inner"].kind == "function"
    assert found["symbol:pkg/mod.py#A"].kind == "class"
    # A decorated range starts at its decorator; the signature is the header itself.
    assert method.source is not None
    assert source.encode()[method.source.start :].startswith(b"@staticmethod")
    assert method.signature == "def m(x)"
    assert found["module:python:pkg.mod"].category == "module"


def test_pytest_discovery_sets_the_test_facet(tmp_path: Path) -> None:
    found = keys(
        write(
            tmp_path,
            {
                "tests/test_a.py": "def test_x(): pass\ndef helper(): pass\n"
                "class TestA:\n    def test_y(self): pass\n",
            },
        )
    )
    assert found["symbol:tests/test_a.py#test_x"].test
    assert found["symbol:tests/test_a.py#TestA.test_y"].test
    assert not found["symbol:tests/test_a.py#helper"].test


def test_coverage_is_explicit_for_partial_unsupported_and_foreign_files(tmp_path: Path) -> None:
    root = write(
        tmp_path,
        {
            "broken.py": "def ok(): pass\ndef bad(:\n    pass\n",
            "latin.py": b"x = '\xe9'\n",
            "notes.md": "# notes\n",
        },
    )
    derived = derive(capture(root), "test-repo")
    files = {f.path: f for f in derived.files}
    assert files["broken.py"].coverage == "partial"
    assert files["broken.py"].diagnostics
    assert files["latin.py"].coverage == "unsupported"
    assert files["notes.md"].coverage == "unsupported"
    entities = {e.key for e in derived.entities}
    # Partial parses keep what parsed; every file stays file evidence.
    assert {"symbol:broken.py#ok", "file:latin.py", "file:notes.md"} <= entities


def test_diagnostics_are_bounded() -> None:
    parsed = python.parse(b"".join(b"def f%d(:\n" % i for i in range(40)))
    assert isinstance(parsed, python.Parsed)
    assert len(parsed.diagnostics) == python.MAX_DIAGNOSTICS + 1
    assert parsed.diagnostics[-1].endswith("more syntax errors omitted")


def test_long_chains_parse_in_linear_time_and_deeper_syntax_is_unsupported(
    tmp_path: Path,
) -> None:
    # A call chain nests two syntax levels per link. tree-sitter's query cursor drops
    # matches below depth 65,535, so the deeper file is refused, not silently partial.
    chain = b"def f():\n    return p" + b".then(g)" * 32_000 + b".x(h)\n"
    started = time.perf_counter()
    parsed = python.parse(chain)
    assert time.perf_counter() - started < 10  # was minutes: Node.parent is O(depth)
    assert isinstance(parsed, python.Parsed)
    assert [u.name for u in parsed.scopes[1].calls] == ["p.then"]
    root = write(tmp_path, {"deep.py": "p" + ".then(g)" * 33_000 + "\n", "ok.py": "x = 1\n"})
    files = {f.path: f for f in derive(capture(root), "test-repo").files}
    assert files["deep.py"].coverage == "unsupported"
    assert files["deep.py"].diagnostics == (python.TOO_DEEP,)
    assert files["ok.py"].coverage == "complete"


def test_nesting_finds_the_innermost_scope_like_a_parent_walk() -> None:
    rng = random.Random(7)
    for _ in range(200):
        size = 400
        nesting = python.Nesting(size)
        bodies: list[tuple[int, int]] = [(0, size)]
        parents = [0]
        for start in sorted(rng.sample(range(1, size), 30)):
            parent = nesting.scope_at(start)
            # A body nests in its parent's, or is empty (a declaration without one).
            end = rng.choice([start, rng.randint(start, bodies[parent][1])])
            nesting.add(parent, (start, end))
            bodies.append((start, end))
            parents.append(parent)
        walked = [_walk(bodies, parents, at) for at in range(size)]
        assert [nesting.scope_at(at) for at in range(size)] == walked


def _walk(bodies: list[tuple[int, int]], parents: list[int], at: int) -> int:
    """The replaced lookup: the last body starting at or before ``at``, then up."""
    k = max((i for i, (start, _) in enumerate(bodies) if i > 0 and start <= at), default=0)
    while k and not bodies[k][0] <= at < bodies[k][1]:
        k = parents[k]
    return k


def test_module_names_follow_the_path_convention() -> None:
    assert python.module_name("a/b.py") == "a.b"
    assert python.module_name("a/b/__init__.py") == "a.b"
    assert python.module_name("__init__.py") is None
    assert python.module_name("my-tool/x.py") is None
    assert python.module_name("README.md") is None
