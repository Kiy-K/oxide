"""Persistence, reopen, publication safety, ownership and typed errors."""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from conftest import FIXTURE, env, write

import oxide
from oxide.models import (
    Closed,
    IncompatibleIndex,
    InvalidPath,
    NotIndexed,
    OwnershipConflict,
    StoreCorrupt,
)
from oxide.storage import ladybug


def store_dir(data: Path) -> Path:
    (directory,) = (data / "repos").iterdir()
    return directory


def test_reopen_reads_the_same_published_knowledge(fts: Path) -> None:
    with oxide.open_repository(FIXTURE) as repo:
        report = repo.index()
        before = repo.search("RetryPolicy")
    with oxide.open_repository(FIXTURE) as repo:
        assert repo.snapshot == report.snapshot
        assert repo.search("RetryPolicy") == before
        assert repo.index().status == "unchanged"


def test_edits_publish_a_new_generation_and_drop_the_old(
    repo_copy: Path, private_data: Path, fts: Path
) -> None:
    with oxide.open_repository(repo_copy) as repo:
        first = repo.index()
        (repo_copy / "oxidepy/cache.py").unlink()
        write(repo_copy, {"oxidepy/extra.py": "def brand_new_helper():\n    pass\n"})
        second = repo.index()
        assert second.status == "built" and second.snapshot != first.snapshot
        assert repo.entity("symbol:oxidepy/cache.py#TTLCache") is None
        assert repo.entity("symbol:oxidepy/extra.py#brand_new_helper") is not None
        hits = repo.search("brand new helper").entries
        assert hits[0].entity.key == "symbol:oxidepy/extra.py#brand_new_helper"
    generations = sorted(p.name for p in (store_dir(private_data) / "generations").iterdir())
    assert len(generations) == 1


def test_interrupted_builds_are_discarded_on_open(private_data: Path) -> None:
    oxide.index_repository(FIXTURE)
    leftover = store_dir(private_data) / "generations" / ("0" * 64 + ".staging")
    leftover.mkdir()
    with oxide.open_repository(FIXTURE) as repo:
        assert not leftover.exists()
        assert repo.files()


def test_a_dangling_current_pointer_fails_closed(private_data: Path) -> None:
    oxide.index_repository(FIXTURE)
    (store_dir(private_data) / "CURRENT").write_text("f" * 64)
    with pytest.raises(StoreCorrupt):
        oxide.open_repository(FIXTURE)


def test_another_store_schema_requires_a_rebuild(private_data: Path) -> None:
    oxide.index_repository(FIXTURE)
    directory = store_dir(private_data)
    (manifest,) = (directory / "generations").glob("*/MANIFEST.json")
    manifest.write_text(manifest.read_text().replace("oxide-ladybug-py-v1", "older-schema"))
    with oxide.open_repository(FIXTURE) as repo:
        with pytest.raises(IncompatibleIndex):
            repo.search("x")
        assert repo.index().status == "built"
        assert repo.search("RetryPolicy").entries


def test_one_owner_per_store_in_process_and_across_processes(private_data: Path) -> None:
    with oxide.open_repository(FIXTURE):
        with pytest.raises(OwnershipConflict):
            oxide.open_repository(FIXTURE)
        script = textwrap.dedent(
            f"""
            import sys, oxide
            try:
                oxide.open_repository({str(FIXTURE)!r}, data_dir={str(private_data)!r})
            except oxide.OxideError as error:
                sys.exit(0 if error.code == "ownership_conflict" else 3)
            sys.exit(4)
            """
        )
        result = subprocess.run([sys.executable, "-c", script], env=env(private_data), check=False)
        assert result.returncode == 0
    oxide.open_repository(FIXTURE).close()  # released on close


def test_the_load_pool_grows_with_the_rows_it_copies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Scaled down: at a 16 MiB floor, these 40k relations overflow a fixed pool (as
    # VS Code's 554k did 128 MiB, docs/large-repositories.md); views keep the floor.
    calls = "".join(f"    unknown_{k}()\n" for k in range(500))
    root = write(tmp_path / "repo", {f"m{i}.py": f"def f{i}():\n{calls}" for i in range(80)})
    monkeypatch.setattr(ladybug, "BUFFER_POOL", 16 << 20)
    monkeypatch.setattr(ladybug, "LOAD_POOL_PER_BYTE", 0)
    fixed = oxide.open_repository(root, data_dir=tmp_path / "fixed")
    with fixed, pytest.raises(RuntimeError, match="buffer pool is full"):
        fixed.index()
    monkeypatch.setattr(ladybug, "LOAD_POOL_PER_BYTE", 2)
    with oxide.open_repository(root, data_dir=tmp_path / "scaled") as repo:
        assert repo.index().relations > 40_000
        assert len(repo.relations("symbol:m7.py#f7")) > 500  # read back at the floor


def test_typed_errors(tmp_path: Path) -> None:
    with pytest.raises(InvalidPath):
        oxide.open_repository(tmp_path / "missing")
    with pytest.raises(InvalidPath):
        oxide.open_repository(write(tmp_path, {"file.py": ""}) / "file.py")
    with pytest.raises(NotIndexed):  # a read-only open writes nothing
        oxide.open_repository(FIXTURE, data_dir=tmp_path / "reader", create=False)
    assert not (tmp_path / "reader").exists()
    repo = oxide.open_repository(FIXTURE)
    with pytest.raises(NotIndexed):
        repo.search("x")
    repo.index()
    with pytest.raises(ValueError):
        repo.build_context("x", -1)
    repo.close()
    with pytest.raises(Closed):
        repo.search("x")
