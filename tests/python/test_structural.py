"""Explicit structural search: ast-grep rules over the published snapshot, read-only."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from conftest import write

import oxide
from oxide.models import InvalidArgument, NativeUnavailable

SOURCES = {
    "pkg/io.py": (
        "# open(p) in a comment\n"
        's = "open(p)"\n'
        "def read(p):\n"
        "    café = open(p)\n"  # non-ASCII before the match: bytes != characters
        '    return open(p, encoding="utf-8")\n'
        "open(\n"
        "    'x',\n"
        ")\n"
    ),
    "pkg/other.py": "def f():\n    return open(1)\n",
    "pkg/broken.py": "def g(:\n    open(2)\n",
    "web/a.ts": (
        "const a = JSON.parse(x);\n"
        "try { JSON.parse(y); } catch (e) {}\n"
        "function h() { return JSON.parse(z); }\n"
    ),
    "web/b.tsx": "const v = <div>{JSON.parse(w)}</div>;\n",
}
NO_ENCODING = {
    "rule": {
        "pattern": "open($$$A)",
        "not": {
            "has": {
                "kind": "argument_list",
                "has": {
                    "kind": "keyword_argument",
                    "has": {"field": "name", "regex": "^encoding$"},
                },
            }
        },
    }
}


@pytest.fixture
def repo(tmp_path: Path) -> Iterator[oxide.Repository]:
    with oxide.open_repository(write(tmp_path / "src", SOURCES)) as repository:
        repository.index()
        yield repository


def test_matches_are_exact_byte_spans_of_the_snapshot(repo: oxide.Repository) -> None:
    found = repo.structural_search(NO_ENCODING, language="python", paths=["pkg/io.py"])
    data = SOURCES["pkg/io.py"].encode()
    assert [data[m.source.start : m.source.end] for m in found.matches] == [
        b"open(p)",
        b"open(\n    'x',\n)",
    ]
    first, second = found.matches
    assert (first.start_line, first.end_line, second.start_line, second.end_line) == (4, 4, 6, 8)
    assert first.preview == "    café = open(p)"
    assert first.entity == "symbol:pkg/io.py#read" and second.entity is None
    digest = next(f.digest for f in repo.files() if f.path == "pkg/io.py")
    assert first.source.digest == digest
    assert found.snapshot == repo.snapshot and not found.truncated


def test_a_pattern_string_ignores_comments_and_strings(repo: oxide.Repository) -> None:
    found = repo.structural_search("open($P)", language="python", paths=["pkg/io.py"])
    assert [m.start_line for m in found.matches] == [4, 6]  # not lines 1-2


def test_relational_rules_express_what_grep_cannot(repo: oxide.Repository) -> None:
    unguarded = {
        "rule": {
            "pattern": "JSON.parse($$$A)",
            "not": {"inside": {"kind": "try_statement", "stopBy": "end"}},
        }
    }
    found = repo.structural_search(unguarded, language="typescript")
    assert [(m.source.path, m.start_line) for m in found.matches] == [
        ("web/a.ts", 1),
        ("web/a.ts", 3),
        ("web/b.tsx", 1),
    ]
    assert found.matches[1].entity == "symbol:web/a.ts#h"
    assert found.files == 2


def test_paths_limit_and_order_bound_the_search(repo: oxide.Repository) -> None:
    every = repo.structural_search("open($$$A)", language="python")
    assert [(m.source.path, m.start_line) for m in every.matches] == [
        ("pkg/broken.py", 2),
        ("pkg/io.py", 4),
        ("pkg/io.py", 5),
        ("pkg/io.py", 6),
        ("pkg/other.py", 2),
    ]
    first = repo.structural_search("open($$$A)", language="python", limit=2)
    assert first.matches == every.matches[:2] and first.truncated
    scoped = repo.structural_search("open($$$A)", language="python", paths=["pkg/other.py", "web"])
    assert [m.source.path for m in scoped.matches] == ["pkg/other.py"] and scoped.files == 1
    assert repo.structural_search("open($$$A)", language="python", limit=0).matches == ()


def test_partial_files_are_reported(repo: oxide.Repository) -> None:
    found = repo.structural_search("open($$$A)", language="python")
    assert found.partial == ("pkg/broken.py",)


def test_it_reads_the_indexed_snapshot_not_the_worktree(
    repo: oxide.Repository, tmp_path: Path
) -> None:
    write(tmp_path / "src", {"pkg/other.py": "def f():\n    pass\n"})
    found = repo.structural_search("open($$$A)", language="python", paths=["pkg/other.py"])
    assert [m.preview for m in found.matches] == ["    return open(1)"]


@pytest.mark.parametrize(
    ("rule", "options"),
    [
        ({"rule": {"kind": 3}}, {"language": "python"}),
        ("", {"language": "python"}),
        (42, {"language": "python"}),
        ("f($A)", {"language": "rust"}),
        ("f($A)", {"language": "python", "limit": -1}),
        ("f($A)", {"language": "python", "paths": "pkg"}),
    ],
)
def test_bad_arguments_are_invalid(
    repo: oxide.Repository, rule: object, options: dict[str, object]
) -> None:
    with pytest.raises(InvalidArgument):
        repo.structural_search(rule, **options)  # type: ignore[arg-type]


def test_without_the_extra_it_is_unavailable(
    repo: oxide.Repository, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(sys.modules, "ast_grep_py", None)
    with pytest.raises(NativeUnavailable, match="structural"):
        repo.structural_search("f($A)", language="python")
