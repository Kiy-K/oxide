"""Native LadybugDB safety at the pin: FTS queries, literal construction, extension
loading and paths with quotes. Anything that could crash runs in a subprocess."""

from __future__ import annotations

import json
import shutil
import textwrap
from pathlib import Path

import pytest
from conftest import FIXTURE, python

import oxide
from oxide.storage.ladybug import _literal

STRESS = textwrap.dedent(
    r"""
    import json, sys, oxide
    queries = [
        "RetryPolicy", "where failed HTTP requests are retried", "", "   ", "a'b", "\\",
        "'); MATCH (n) DETACH DELETE n; //", "${x} $keys", "naïve café 東京", "\x00\x01",
        "retry " * 2000, "x" * 10000, "should_retry TTLCache sha256",
    ]
    seen = {}
    with oxide.open_repository(sys.argv[1], data_dir=sys.argv[2]) as repo:
        repo.index()
        for round in range(25):
            for query in queries:
                result = repo.search(query, limit=10)
                keys = [(e.entity.key, e.lexical_score) for e in result.entries]
                assert seen.setdefault(query, keys) == keys, query
        count = len(repo.files())
    with oxide.open_repository(sys.argv[1], data_dir=sys.argv[2]) as repo:
        assert len(repo.files()) == count  # the store survived every query
    print(json.dumps({"queries": 25 * len(queries), "hits": len(seen["RetryPolicy"])}))
    """
)


def test_repeated_and_hostile_fts_queries_neither_crash_nor_drift(
    private_data: Path, fts: Path
) -> None:
    result = python(private_data, STRESS, str(FIXTURE), str(private_data))
    assert result.returncode == 0, result.stderr  # a SIGSEGV would be -11
    assert json.loads(result.stdout) == {"queries": 325, "hits": 10}


def test_the_fts_literal_only_ever_holds_oxide_terms(fts: Path) -> None:
    with oxide.open_repository(FIXTURE) as repo:
        repo.index()
        view = repo._view()
        for text in ("a'b", "x\\", "Upper", "two  spaces", " lead", "é"):
            with pytest.raises(ValueError):
                view.lexical(text, 3)


def test_literals_escape_quotes_and_backslashes() -> None:
    assert _literal(Path("/a/o'brien\\x")) == "'/a/o\\'brien\\\\x'"
    with pytest.raises(oxide.OxideError):
        _literal(Path("/a/\udcff"))


def test_paths_with_quotes_and_backslashes_work_end_to_end(
    tmp_path: Path, fts: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    odd = tmp_path / "o'brien \\ data"
    extension = odd / "ext'\\" / "libfts.lbug_extension"
    extension.parent.mkdir(parents=True)
    shutil.copyfile(fts, extension)
    monkeypatch.setenv("OXIDE_FTS_EXTENSION", str(extension))
    with oxide.open_repository(FIXTURE, data_dir=odd / "store") as repo:
        assert repo.index().lexical == "available"
        found = repo.search("RetryPolicy")
    assert found.lexical.state == "ran"
    assert found.entries[0].entity.key == "symbol:oxidepy/retry.py#RetryPolicy"
    with oxide.open_repository(FIXTURE, data_dir=odd / "store") as repo:
        assert repo.index().status == "unchanged"
