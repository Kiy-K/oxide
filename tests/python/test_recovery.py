"""Damage and crash recovery: typed fail-closed errors, safe rebuilds, nothing lost.

Native damage and crash injection run in subprocesses (``conftest.python``) with
private data locations; only the store under ``tmp_path`` is ever damaged.
"""

from __future__ import annotations

import gc
import json
import os
import shutil
import textwrap
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from conftest import python, write

import oxide
from oxide.capture import digest
from oxide.models import IncompatibleIndex, StoreCorrupt
from oxide.storage.store import Store

# Durability is this file's subject: storage flushes for real (conftest.no_fsync).
pytestmark = pytest.mark.real_fsync


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


@pytest.mark.parametrize("database", ["graph.lbug", "lexical.lbug"])
@pytest.mark.parametrize("how", ["random", "short", "empty", "missing"])
def test_a_damaged_database_fails_closed_then_rebuilds(
    repo_copy: Path, private_data: Path, fts: Path, how: str, database: str
) -> None:
    oxide.index_repository(repo_copy)
    damage(generation(private_data) / database, how)
    result = python(private_data, PROBE, str(repo_copy), str(private_data))
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["codes"] == ["store_corrupt", "store_corrupt"]
    assert report["status"] == "built"
    assert report["hits"] == ["symbol:oxidepy/retry.py#RetryPolicy"] and report["used"] > 0


def test_a_foreign_graph_with_the_same_shape_fails_closed_then_rebuilds(
    repo_copy: Path, private_data: Path, tmp_path: Path
) -> None:
    """Another repository's graph with identical rows must not pass as this one's."""
    twin = tmp_path / "twin"
    shutil.copytree(repo_copy, twin)
    oxide.index_repository(repo_copy)
    mine = generation(private_data) / "graph.lbug"
    other = tmp_path / "other-data"
    oxide.index_repository(twin, data_dir=other)
    shutil.copyfile(generation(other) / "graph.lbug", mine)
    with oxide.open_repository(repo_copy) as repo:
        with pytest.raises(StoreCorrupt):
            repo.files()
        assert repo.index().status == "built"
        assert repo.files()


def test_another_generations_lexical_index_fails_closed_then_rebuilds(
    repo_copy: Path, private_data: Path, fts: Path, tmp_path: Path
) -> None:
    """A lexical index over other documents must not serve this generation's search."""
    oxide.index_repository(repo_copy)
    mine = generation(private_data) / "lexical.lbug"
    kept = tmp_path / "lexical.lbug"
    shutil.copyfile(mine, kept)
    write(repo_copy, {"oxidepy/extra.py": "def brand_new_helper():\n    pass\n"})
    oxide.index_repository(repo_copy)
    shutil.copyfile(kept, generation(private_data) / "lexical.lbug")
    with oxide.open_repository(repo_copy) as repo:
        with pytest.raises(StoreCorrupt):
            repo.search("brand new helper")
        assert repo.index().status == "built"
        assert repo.search("brand new helper").entries


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
        oxide.open_repository(repo_copy, create=False)  # a reader too, not NotIndexed
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
        assert repo._tracking.ready.wait(5)  # a background reconcile would heal the tampering
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


def test_unchanged_sources_are_linked_never_rewritten(repo_copy: Path, private_data: Path) -> None:
    oxide.index_repository(repo_copy)
    before = {b.name: b.stat().st_ino for b in blobs(private_data)}
    edited = next(p for p in sorted(repo_copy.rglob("*.py")) if p.stat().st_size)
    old = edited.read_bytes()
    edited.write_bytes(old + b"\n# edited\n")
    assert oxide.index_repository(repo_copy).status == "built"
    after = {b.name: b.stat().st_ino for b in blobs(private_data)}
    gone = digest(old).removeprefix("sha256:")
    assert set(after) - set(before) == {digest(edited.read_bytes()).removeprefix("sha256:")}
    assert all(after[n] == ino for n, ino in before.items() if n != gone)


# Crash injection: the child dies (os._exit, no cleanup) at one publication step.
CRASH = textwrap.dedent(
    """
    import os, sys, oxide
    import oxide.storage.store as storage
    root, data, step = sys.argv[1], sys.argv[2], sys.argv[3]
    real_rename, real_write, real_fsync = os.rename, storage._write_atomic, storage._fsync
    def fsync(path):
        if step == "in-seal" and path.name == "source":  # blobs linked, not yet published
            os._exit(9)
        real_fsync(path)
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
    real_rows = storage._rows
    def rows(conn, query, params=None):
        if step == "in-lexical" and "CREATE_FTS_INDEX" in query:  # lexical.lbug half built
            os._exit(9)
        return real_rows(conn, query, params)
    storage.os.rename = rename
    storage._write_atomic = write_atomic
    storage._fsync = fsync
    storage._rows = rows
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
        # Inspect recovery before the first API read reconciles the edited worktree.
        assert repo.store.view().snapshot == first.snapshot
        assert len(list((store_dir(private_data) / "generations").iterdir())) == 1
        assert not repo.store.view().entities(["symbol:oxidepy/extra.py#brand_new_helper"])
        assert repo.entity("symbol:oxidepy/extra.py#brand_new_helper") is not None
        assert repo.index().status == "unchanged"


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


def test_a_crash_inside_the_lexical_build_keeps_the_previous_generation(
    repo_copy: Path, private_data: Path, fts: Path
) -> None:
    first = oxide.index_repository(repo_copy)
    write(repo_copy, {"oxidepy/extra.py": "def brand_new_helper():\n    pass\n"})
    crash(repo_copy, private_data, "in-lexical")
    with oxide.open_repository(repo_copy) as repo:
        assert repo.store.view().snapshot == first.snapshot
        assert len(list((store_dir(private_data) / "generations").iterdir())) == 1
        assert repo.search("RetryPolicy").entries
        assert repo.index().status == "unchanged"
        assert repo.search("brand new helper").entries


@pytest.mark.parametrize("step", ["before-current", "after-current"])
def test_a_crash_around_a_reused_lexical_index_leaves_one_whole_generation(
    repo_copy: Path, private_data: Path, fts: Path, step: str
) -> None:
    """The linked index shares its inode with the previous generation's: either one
    surviving the crash must still search exactly as a fresh build would."""
    oxide.index_repository(repo_copy)
    with oxide.open_repository(repo_copy) as repo:
        expected = repo.search("retry policy")
    with (repo_copy / "oxidepy/retry.py").open("a") as f:
        f.write("\n# a comment changes bytes, not documents\n")
    crash(repo_copy, private_data, step)
    with oxide.open_repository(repo_copy) as repo:
        assert len(list((store_dir(private_data) / "generations").iterdir())) == 1
        assert (repo.store.view().snapshot == expected.snapshot) is (step == "before-current")
        hits = repo.search("retry policy").entries
        assert hits
        assert [(e.entity.key, e.lexical_score) for e in hits] == [
            (e.entity.key, e.lexical_score) for e in expected.entries
        ]
        assert repo.index().status == "unchanged"


def test_a_crash_after_linking_blobs_leaves_the_current_generation_whole(
    repo_copy: Path, private_data: Path, fts: Path
) -> None:
    first = oxide.index_repository(repo_copy)
    before = {b.name: b.read_bytes() for b in blobs(private_data)}
    write(repo_copy, {"oxidepy/extra.py": "def brand_new_helper():\n    pass\n"})
    crash(repo_copy, private_data, "in-seal")
    with oxide.open_repository(repo_copy) as repo:
        assert repo.store.view().snapshot == first.snapshot
        assert len(list((store_dir(private_data) / "generations").iterdir())) == 1
        assert {b.name: b.read_bytes() for b in blobs(private_data)} == before
        assert repo.build_context("retry", 4096).used > 0
        assert repo.index().status == "unchanged"
        assert repo.entity("symbol:oxidepy/extra.py#brand_new_helper") is not None


def test_without_hard_links_every_blob_is_written(
    repo_copy: Path, private_data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FAT, some network mounts and sandboxes refuse links: the seal writes instead."""
    oxide.index_repository(repo_copy)
    before = {b.name: b.stat().st_ino for b in blobs(private_data)}

    def refuse(*args: object, **kwargs: object) -> None:
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(os, "link", refuse)
    write(repo_copy, {"oxidepy/extra.py": "def brand_new_helper():\n    pass\n"})
    with oxide.open_repository(repo_copy) as repo:
        assert repo.index().status == "built"
        after = {b.name: b.stat().st_ino for b in blobs(private_data)}
        assert set(before) < set(after) and not set(before.values()) & set(after.values())
        assert repo.index().status == "unchanged"  # every retained source verifies


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


@pytest.mark.parametrize("side", ["_retain", "_lexical", "_load"])
def test_a_failure_on_either_side_of_the_concurrent_publish_keeps_the_current_generation(
    repo_copy: Path, private_data: Path, monkeypatch: pytest.MonkeyPatch, fts: Path, side: str
) -> None:
    """Blobs and the lexical index are written on threads while the graph loads: whichever
    fails, the others finish first, the staging is removed and the previous generation
    keeps serving."""
    first = oxide.index_repository(repo_copy)
    before = {b.name: b.read_bytes() for b in blobs(private_data)}
    retain, started = Store._retain, threading.Event()

    def retaining(self: Store, *args: Any) -> None:
        started.set()
        if side == "_retain":
            raise OSError("disk full")
        retain(self, *args)

    def loading(self: Store, *args: Any) -> None:
        assert started.wait(10)  # fail while the blob thread is running
        raise OSError("disk full")

    with monkeypatch.context() as patched:  # undo() would also drop the private data dir
        patched.setattr(Store, "_retain", retaining)
        if side != "_retain":
            patched.setattr(Store, side, loading)
        write(repo_copy, {"oxidepy/extra.py": "def brand_new_helper():\n    pass\n"})
        with oxide.open_repository(repo_copy) as repo:
            with pytest.raises(OSError, match="disk full"):
                repo.index()
            assert repo.store.view().snapshot == first.snapshot
            repo.store.view().verify_sources()
            with pytest.raises(OSError, match="disk full"):
                repo.build_context("retry", 4096)
        generations = store_dir(private_data) / "generations"
        assert [p.name for p in generations.iterdir()] == [
            (store_dir(private_data) / "CURRENT").read_text()
        ]
        assert {b.name: b.read_bytes() for b in blobs(private_data)} == before
    with oxide.open_repository(repo_copy) as repo:
        assert repo.entity("symbol:oxidepy/extra.py#brand_new_helper") is not None
        assert repo.snapshot != first.snapshot
        assert repo.index().status == "unchanged"


def test_a_linked_lexical_index_is_not_flushed_again(
    repo_copy: Path, private_data: Path, monkeypatch: pytest.MonkeyPatch, fts: Path
) -> None:
    import oxide.storage.ladybug as ladybug_storage
    import oxide.storage.store as store

    oxide.index_repository(repo_copy)
    with (repo_copy / "oxidepy/retry.py").open("a") as f:
        f.write("\n# a comment changes bytes, not documents\n")
    flushed: list[Path] = []
    real = ladybug_storage._fsync

    def record(path: Path) -> None:
        flushed.append(path)
        real(path)

    monkeypatch.setattr(store, "_fsync", record)
    oxide.index_repository(repo_copy)
    names = {p.name for p in flushed}
    assert {"graph.lbug", "MANIFEST.json"} <= names and "lexical.lbug" not in names


def test_publication_flushes_every_byte_before_current_moves(
    repo_copy: Path, private_data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import oxide.storage.ladybug as ladybug_storage
    import oxide.storage.store as store

    flushed: list[Path] = []
    real = ladybug_storage._fsync

    def record(path: Path) -> None:
        flushed.append(path)
        real(path)

    monkeypatch.setattr(ladybug_storage, "_fsync", record)
    monkeypatch.setattr(store, "_fsync", record)
    oxide.index_repository(repo_copy)
    name = (store_dir(private_data) / "CURRENT").read_text()
    pointer = flushed.index(store_dir(private_data) / "CURRENT.tmp")
    before = {p.name for p in flushed[:pointer]}
    # Staged under another name, so compare names: every blob, every top-level file
    # (graph, facts, manifest), the staging and its parents reach disk before CURRENT.
    assert {b.name for b in blobs(private_data)} <= before
    files = {p.name for p in generation(private_data).iterdir() if p.is_file()}
    assert {"MANIFEST.json", "facts.json"} <= files <= before
    assert {f"{name}.staging", "source", "generations"} <= before
    assert flushed[pointer + 1] == store_dir(private_data)  # the rename itself
