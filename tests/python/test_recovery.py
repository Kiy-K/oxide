"""Damage and crash recovery: typed fail-closed errors, safe rebuilds, nothing lost.

Native damage and crash injection run in subprocesses (``conftest.python``) with
private data locations; only the store under ``tmp_path`` is ever damaged.
"""

from __future__ import annotations

import gc
import json
import os
import textwrap
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from conftest import python, write

import oxide
from oxide.models import IncompatibleIndex, StoreCorrupt
from oxide.storage.store import Store


def store_dir(data: Path) -> Path:
    (directory,) = (data / "repos").iterdir()
    return directory


def generation(data: Path) -> Path:
    directory = store_dir(data)
    return directory / "generations" / (directory / "CURRENT").read_text()


def blobs(data: Path) -> list[Path]:
    return sorted((generation(data) / "source").iterdir())


# Reads must fail with store_corrupt, then index() must rebuild and serve again.
PROBE = textwrap.dedent(
    """
    import json, sys, oxide
    root, data = sys.argv[1], sys.argv[2]
    codes = []
    with oxide.open_repository(root, data_dir=data) as repo:
        for read in (lambda: repo.search("RetryPolicy"), lambda: repo.build_context("retry", 512)):
            try:
                read()
                codes.append("ok")
            except oxide.OxideError as error:
                codes.append(error.code)
        status = repo.index().status
        hits = [e.entity.key for e in repo.search("RetryPolicy").entries]
        used = repo.build_context("retry", 512).used
    print(json.dumps({"codes": codes, "status": status, "hits": hits[:1], "used": used}))
    """
)


def damage(path: Path, how: str) -> None:
    if how == "random":
        path.write_bytes(os.urandom(path.stat().st_size))
    elif how == "short":
        os.truncate(path, 4096)
    elif how == "empty":
        os.truncate(path, 0)
    else:
        path.unlink()


@pytest.mark.parametrize("how", ["random", "short", "empty", "missing"])
def test_a_damaged_database_fails_closed_then_rebuilds(
    repo_copy: Path, private_data: Path, fts: Path, how: str
) -> None:
    oxide.index_repository(repo_copy)
    damage(generation(private_data) / "graph.lbug", how)
    result = python(private_data, PROBE, str(repo_copy), str(private_data))
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["codes"] == ["store_corrupt", "store_corrupt"]
    assert report["status"] == "built"
    assert report["hits"] == ["symbol:oxidepy/retry.py#RetryPolicy"] and report["used"] > 0


MANIFEST_DAMAGE: dict[str, Callable[[dict[str, Any]], str]] = {
    "not-json": lambda m: "not json",
    "not-object": lambda m: "[1, 2]",
    "missing-key": lambda m: json.dumps({k: v for k, v in m.items() if k != "snapshot_id"}),
    "short-file-row": lambda m: json.dumps({**m, "files": [["a.py"]]}),
    "no-rows": lambda m: json.dumps({**m, "rows": {}}),
    "foreign-owner": lambda m: json.dumps({**m, "repo_id": "someone-else"}),
}


@pytest.mark.parametrize("edit", MANIFEST_DAMAGE.values(), ids=MANIFEST_DAMAGE.keys())
def test_a_malformed_manifest_is_store_corrupt_and_rebuilt(
    repo_copy: Path, private_data: Path, edit: Callable[[dict[str, Any]], str]
) -> None:
    oxide.index_repository(repo_copy)
    manifest = generation(private_data) / "MANIFEST.json"
    manifest.write_text(edit(json.loads(manifest.read_text())))
    with oxide.open_repository(repo_copy) as repo:
        with pytest.raises(StoreCorrupt):
            repo.files()
        assert repo.index().status == "built"
        assert repo.files()


def test_derivation_compatibility_is_checked_before_the_database_opens(
    repo_copy: Path, private_data: Path
) -> None:
    """Another ladybug version's file is never handed to this binding."""
    oxide.index_repository(repo_copy)
    manifest = generation(private_data) / "MANIFEST.json"
    meta = json.loads(manifest.read_text())
    meta["components"]["ladybug"] = "0.0.1"
    manifest.write_text(json.dumps(meta))
    (generation(private_data) / "graph.lbug").write_bytes(b"would fail if opened")
    with oxide.open_repository(repo_copy) as repo:
        with pytest.raises(IncompatibleIndex):
            repo.search("x")
        assert repo.index().status == "built"


@pytest.mark.parametrize("content", [b"{", b"[]", b'{"root": 1}', b"\xff\xfe"])
def test_invalid_repository_metadata_fails_closed_without_touching_anything(
    repo_copy: Path, private_data: Path, content: bytes
) -> None:
    oxide.index_repository(repo_copy)
    meta = store_dir(private_data) / "repo.json"
    meta.write_bytes(content)
    before = sorted(p.relative_to(private_data) for p in private_data.rglob("*"))
    with pytest.raises(StoreCorrupt):
        oxide.open_repository(repo_copy)
    assert meta.read_bytes() == content
    assert sorted(p.relative_to(private_data) for p in private_data.rglob("*")) == before


def test_lost_repository_identity_is_never_reissued(repo_copy: Path, private_data: Path) -> None:
    oxide.index_repository(repo_copy)
    kept = generation(private_data)
    (store_dir(private_data) / "repo.json").unlink()
    with pytest.raises(StoreCorrupt):
        oxide.open_repository(repo_copy)
    assert kept.is_dir() and not (store_dir(private_data) / "repo.json").exists()


@pytest.mark.parametrize("pointer", [b"f" * 64, b"../../elsewhere", b"\xff" * 64])
def test_a_bad_current_pointer_fails_closed_and_deletes_nothing(
    repo_copy: Path, private_data: Path, pointer: bytes
) -> None:
    oxide.index_repository(repo_copy)
    kept = generation(private_data)
    (store_dir(private_data) / "CURRENT").write_bytes(pointer)
    with pytest.raises(StoreCorrupt):
        oxide.open_repository(repo_copy)
    assert (kept / "MANIFEST.json").is_file()


def test_missing_retained_source_is_never_reported_unchanged(
    repo_copy: Path, private_data: Path, fts: Path
) -> None:
    with oxide.open_repository(repo_copy) as repo:
        repo.index()
        first, *rest = blobs(private_data)
        first.unlink()
        for blob in rest:
            blob.write_bytes(b"tampered")
        with pytest.raises(StoreCorrupt):
            repo.build_context("retry", 4096)
        assert repo.index().status == "built"
        assert repo.index().status == "unchanged"
        assert repo.build_context("retry", 4096).used > 0
    assert len(list((store_dir(private_data) / "generations").iterdir())) == 1


# Crash injection: the child dies (os._exit, no cleanup) at one publication step.
CRASH = textwrap.dedent(
    """
    import os, sys, oxide
    import oxide.storage.store as storage
    root, data, step = sys.argv[1], sys.argv[2], sys.argv[3]
    real_rename, real_write = os.rename, storage._write_atomic
    def rename(src, dst):
        if step == "between-renames" and str(src).endswith(".staging"):
            os._exit(9)
        real_rename(src, dst)
    def write_atomic(path, data):
        if step == "before-current" and path.name == "CURRENT":
            os._exit(9)
        real_write(path, data)
        if step == "after-current" and path.name == "CURRENT":
            os._exit(9)
    storage.os.rename = rename
    storage._write_atomic = write_atomic
    with oxide.open_repository(root, data_dir=data) as repo:
        repo.index()
    sys.exit(3)
    """
)


def crash(repo: Path, data: Path, step: str) -> None:
    result = python(data, CRASH, str(repo), str(data), step)
    assert result.returncode == 9, result.stderr


def test_a_crash_between_the_rebuild_renames_is_undone_on_open(
    repo_copy: Path, private_data: Path, fts: Path
) -> None:
    first = oxide.index_repository(repo_copy)
    blobs(private_data)[0].unlink()  # same snapshot, unreadable: rebuilt under its name
    crash(repo_copy, private_data, "between-renames")
    name = (store_dir(private_data) / "CURRENT").read_text()
    generations = store_dir(private_data) / "generations"
    assert not (generations / name).exists() and (generations / f"{name}.replaced").is_dir()
    with oxide.open_repository(repo_copy) as repo:
        assert sorted(p.name for p in generations.iterdir()) == [name]
        report = repo.index()
        assert report.status == "built" and report.snapshot == first.snapshot
        assert repo.build_context("retry", 4096).used > 0


def test_a_crash_before_current_moves_keeps_the_previous_generation(
    repo_copy: Path, private_data: Path, fts: Path
) -> None:
    first = oxide.index_repository(repo_copy)
    write(repo_copy, {"oxidepy/extra.py": "def brand_new_helper():\n    pass\n"})
    crash(repo_copy, private_data, "before-current")
    with oxide.open_repository(repo_copy) as repo:
        assert repo.snapshot == first.snapshot
        assert len(list((store_dir(private_data) / "generations").iterdir())) == 1
        assert repo.entity("symbol:oxidepy/extra.py#brand_new_helper") is None
        assert repo.index().status == "built"


def test_a_crash_after_current_moves_serves_the_new_generation(
    repo_copy: Path, private_data: Path, fts: Path
) -> None:
    first = oxide.index_repository(repo_copy)
    write(repo_copy, {"oxidepy/extra.py": "def brand_new_helper():\n    pass\n"})
    crash(repo_copy, private_data, "after-current")
    with oxide.open_repository(repo_copy) as repo:
        assert repo.snapshot != first.snapshot
        assert repo.entity("symbol:oxidepy/extra.py#brand_new_helper") is not None
        assert len(list((store_dir(private_data) / "generations").iterdir())) == 1
        assert repo.index().status == "unchanged"


@pytest.mark.parametrize("fails", [False, True], ids=["published", "failed"])
@pytest.mark.parametrize("enabled", [True, False], ids=["gc-on", "gc-off"])
def test_index_restores_the_callers_gc_state(
    repo_copy: Path, private_data: Path, monkeypatch: pytest.MonkeyPatch, enabled: bool, fails: bool
) -> None:
    seen: list[bool] = []
    publish = Store.publish

    def spy(self: Store, *args: Any) -> None:
        seen.append(gc.isenabled())
        if fails:
            raise OSError("disk full")
        publish(self, *args)

    monkeypatch.setattr(Store, "publish", spy)
    (gc.enable if enabled else gc.disable)()
    try:
        with oxide.open_repository(repo_copy) as repo:
            if fails:
                with pytest.raises(OSError, match="disk full"):
                    repo.index()
            else:
                assert repo.index().status == "built"
            assert seen == [False] and gc.isenabled() is enabled
    finally:
        gc.enable()
