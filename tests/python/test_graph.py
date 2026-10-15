"""Typed graph relationships: resolved, ambiguous and unresolved evidence, never invented."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from conftest import write

import oxide

SOURCES = {
    "pkg/__init__.py": "",
    "pkg/base.py": "class Base:\n    pass\n\ndef helper():\n    return 1\n",
    "pkg/dup.py": "def twice(): pass\ndef twice(): pass\n",
    "pkg/use.py": (
        "from .base import Base, helper\n"
        "from .dup import twice\n"
        "import os\n"
        "\n"
        "class Child(Base):\n"
        "    def run(self, helper2):\n"
        "        helper()\n"
        "        twice()\n"
        "        print('x')\n"
        "        self.run(1)\n"
        "        helper2()\n"
    ),
    "pkg/shadow.py": "from .base import helper\n\ndef f(helper):\n    helper()\n",
    "pkg/star.py": "from .base import *\n\ndef g():\n    helper()\n",
    "tests/test_use.py": "from pkg.base import helper\n\ndef test_helper():\n    helper()\n",
}


@pytest.fixture
def repo(tmp_path: Path) -> Iterator[oxide.Repository]:
    with oxide.open_repository(write(tmp_path / "src", SOURCES)) as repository:
        repository.index()
        yield repository


def calls(repo: oxide.Repository, key: str) -> list[oxide.Relation]:
    return [r for r in repo.relations(key) if r.kind == "calls" and r.source == key]


def test_cross_file_calls_resolve_through_relative_imports(repo: oxide.Repository) -> None:
    run = calls(repo, "symbol:pkg/use.py#Child.run")
    resolved = [r for r in run if r.resolution == "resolved"]
    assert [r.target for r in resolved] == ["symbol:pkg/base.py#helper"]
    assert resolved[0].basis == "resolved"
    assert resolved[0].evidence is not None and resolved[0].evidence.path == "pkg/use.py"


def test_ambiguous_targets_keep_every_candidate_under_one_site(repo: oxide.Repository) -> None:
    run = calls(repo, "symbol:pkg/use.py#Child.run")
    twice = [r for r in run if r.resolution == "ambiguous"]
    assert sorted(r.target or "" for r in twice) == [
        "symbol:pkg/dup.py#twice",
        "symbol:pkg/dup.py#twice~1",
    ]
    assert len({r.site for r in twice}) == 1


def test_builtins_attributes_and_parameters_stay_unresolved(repo: oxide.Repository) -> None:
    unresolved = [
        r for r in calls(repo, "symbol:pkg/use.py#Child.run") if r.resolution == "unresolved"
    ]
    assert {r.name for r in unresolved} == {"print", "self.run", "helper2"}
    assert all(r.target is None for r in unresolved)


def test_shadowing_and_star_imports_never_invent_an_endpoint(repo: oxide.Repository) -> None:
    for key in ("symbol:pkg/shadow.py#f", "symbol:pkg/star.py#g"):
        assert [r.resolution for r in calls(repo, key)] == ["unresolved"]


def test_bases_imports_containment_and_tests_are_typed(repo: oxide.Repository) -> None:
    child = repo.relations("symbol:pkg/use.py#Child")
    assert any(r.kind == "references" and r.target == "symbol:pkg/base.py#Base" for r in child)
    imports = [r for r in repo.relations("file:pkg/use.py") if r.kind == "imports"]
    assert {r.target for r in imports if r.resolution == "resolved"} == {
        "symbol:pkg/base.py#Base",
        "symbol:pkg/base.py#helper",
    }
    assert any(r.name == "os" and r.resolution == "unresolved" for r in imports)
    tested = [r for r in repo.relations("symbol:pkg/base.py#helper") if r.kind == "tested_by"]
    assert [(r.target, r.basis) for r in tested] == [
        ("symbol:tests/test_use.py#test_helper", "heuristic")
    ]
    module = repo.relations("module:python:pkg.use")
    assert any(r.kind == "contains_logical" and r.target == "file:pkg/use.py" for r in module)
    assert any(r.kind == "defines" and r.target == "symbol:pkg/use.py#Child" for r in module)
