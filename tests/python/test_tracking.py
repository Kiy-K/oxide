"""Automatic local freshness, incremental capture, and lost-event recovery."""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from conftest import write

import oxide
from oxide import capture as source
from oxide.models import CaptureConflict

pytestmark = pytest.mark.tracked


def eventually(check: Callable[[], bool], timeout: float = 10) -> None:
    deadline = time.monotonic() + timeout
    while not check():
        assert time.monotonic() < deadline, "automatic freshness did not converge"
        time.sleep(0.02)


def test_capture_reuses_only_unchanged_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = write(tmp_path / "repo", {"a.py": "x = 1\n", "b.py": "y = 1\n"})
    cache: source.CaptureCache = {}
    original = source.capture(root, cache=cache)
    reads: list[str] = []
    real = source._read

    def counted(base: Path, path: str, expected: source.StatKey) -> bytes | bool | None:
        reads.append(path)
        return real(base, path, expected)

    monkeypatch.setattr(source, "_read", counted)
    assert source.capture(root, cache=cache) == original
    assert reads == []
    stat = (root / "a.py").stat()
    write(root, {"a.py": "x = 2\n"})
    os.utime(root / "a.py", ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert source.capture(root, cache=cache) == source.capture(root)
    assert reads == ["a.py", "a.py", "b.py"]  # restored mtime cannot hide changed bytes
    (root / "b.py").unlink()
    source.capture(root, cache=cache)
    assert set(cache) == {"a.py"}


def test_reopen_reconciles_without_manual_index(tmp_path: Path) -> None:
    root = write(tmp_path / "repo", {"a.py": "def old(): pass\n"})
    first = oxide.index_repository(root)
    write(root, {"a.py": "def new(): pass\n"})
    with oxide.open_repository(root, create=False) as repo:
        result = repo.search("new", symbols=["new"])
        assert result.snapshot != first.snapshot
        assert any(e.entity.name == "new" for e in result.entries)


def test_native_events_publish_without_a_read(tmp_path: Path) -> None:
    root = write(tmp_path / "repo", {"a.py": "def old(): pass\n"})
    with oxide.open_repository(root) as repo:
        first = repo.index()
        assert repo._tracking.ready.wait(5)
        write(root, {"a.py": "def new(): pass\n", "b.py": "from a import new\n"})

        def published() -> bool:
            with repo._lock:
                return repo.store.view().snapshot != first.snapshot

        eventually(published)
        assert repo.entity("symbol:a.py#new") is not None
        assert repo.entity("symbol:a.py#old") is None
        assert {f.path for f in repo.files()} == {"a.py", "b.py"}
    assert not repo._tracking.thread.is_alive()


def test_native_events_invalidate_bytes_even_with_coarse_timestamps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_key = source._key

    def coarse(stat: os.stat_result) -> source.StatKey:
        mode, size, _, device, inode, _ = real_key(stat)
        return mode, size, 0, device, inode, 0

    monkeypatch.setattr(source, "_key", coarse)
    root = write(tmp_path / "repo", {"a.py": "def old(): pass\n"})
    with oxide.open_repository(root) as repo:
        first = repo.index()
        assert repo._tracking.ready.wait(5)
        write(root, {"a.py": "def new(): pass\n"})

        def published() -> bool:
            with repo._lock:
                return repo.store.view().snapshot != first.snapshot

        eventually(published)


@pytest.mark.parametrize("location", [".", "..", "data", "../data"])
def test_event_scope_matches_capture_when_data_overlaps_the_root(
    tmp_path: Path, location: str
) -> None:
    from watchfiles import Change

    root = write(tmp_path / "repo", {"a.py": "x = 1\n"})
    data = (root / location).resolve()
    with oxide.open_repository(root, data_dir=data) as repo:
        accept = repo._tracking.accept
        assert accept(Change.modified, str(root / "a.py"))
        assert not accept(Change.modified, str(data / "repos" / "generation"))
        assert not accept(Change.modified, str(root / ".git" / "HEAD"))
        assert not accept(Change.modified, str(tmp_path / "outside.py"))


def test_missed_events_reconcile_and_ignored_files_stay_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from oxide import tracking

    monkeypatch.setattr(tracking, "RECONCILE_SECONDS", 0.3)
    monkeypatch.setattr(tracking.Tracking, "accept", lambda *_: False)  # lose all native events
    root = write(tmp_path / "repo", {".gitignore": "secret/\n", "a.py": "def old(): pass\n"})
    with oxide.open_repository(root) as repo:
        first = repo.index()
        assert repo._tracking.ready.wait(5)
        write(root, {"a.py": "def new(): pass\n", "secret/key.py": "secret = 1\n"})

        def published() -> bool:
            with repo._lock:
                return repo.store.view().snapshot != first.snapshot

        eventually(published)
        assert repo.entity("symbol:a.py#new") is not None
        assert {f.path for f in repo.files()} == {".gitignore", "a.py"}
        write(root, {".gitignore": ""})
        eventually(lambda: any(f.path == "secret/key.py" for f in repo.files()))


def test_watcher_failure_falls_back_to_full_reconciliation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from oxide import tracking

    def unavailable(*args: object, **kwargs: object) -> None:
        raise OSError("watch limit exhausted")

    monkeypatch.setattr(tracking, "watch", unavailable)
    root = write(tmp_path / "repo", {"a.py": "def old(): pass\n"})
    with oxide.open_repository(root) as repo:
        first = repo.index()
        assert repo._tracking.ready.wait(5)
        assert repo._tracking.failed
        write(root, {"a.py": "def new(): pass\n"})
        assert repo.snapshot != first.snapshot
        assert repo.entity("symbol:a.py#new") is not None


def test_failed_watcher_is_restarted_and_resynced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from watchfiles import watch as real

    from oxide import tracking

    calls: list[int] = []

    def flaky(*args: Any, **kwargs: Any) -> Any:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("inotify queue overflow")
        return real(*args, **kwargs)

    monkeypatch.setattr(tracking, "watch", flaky)
    monkeypatch.setattr(tracking, "POLL_SECONDS", 0.5)
    root = write(tmp_path / "repo", {"a.py": "def old(): pass\n"})
    with oxide.open_repository(root) as repo:
        repo.index()
        # Events lost while the watcher was down are recovered by the post-restart walk.
        eventually(
            lambda: len(calls) > 1 and not repo._tracking.failed and not repo._tracking.resync
        )
        write(root, {"a.py": "def new(): pass\n"})
        eventually(lambda: repo.entity("symbol:a.py#new") is not None)


def test_failed_background_update_is_retried_before_serving(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from oxide import _index

    root = write(tmp_path / "repo", {"a.py": "def old(): pass\n"})
    with oxide.open_repository(root) as repo:
        first = repo.index()
        assert repo._tracking.ready.wait(5)
        with monkeypatch.context() as patch:

            def fail(*args: object, **kwargs: object) -> None:
                raise CaptureConflict("busy")

            with repo._lock:
                patch.setattr(_index, "index", fail)
                write(root, {"a.py": "def new(): pass\n"})
            eventually(lambda: repo._tracking.error is not None)
            with repo._lock:
                assert repo.store.view().snapshot == first.snapshot
            with pytest.raises(CaptureConflict):
                repo.search("new")
        assert repo.search("new").snapshot != first.snapshot
        assert repo._tracking.error is None


def test_unclosed_repository_is_collectable_and_stops_worker(tmp_path: Path) -> None:
    import gc
    import weakref

    root = write(tmp_path / "repo", {"a.py": "def old(): pass\n"})
    repo = oxide.open_repository(root)
    repo.index()
    worker = repo._tracking.thread
    reference = weakref.ref(repo)
    del repo
    gc.collect()
    eventually(lambda: reference() is None and not worker.is_alive())
    with oxide.open_repository(root) as reopened:
        assert reopened.snapshot


def test_failed_updates_do_not_retain_the_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import gc
    import weakref

    from oxide import _index

    root = write(tmp_path / "repo", {"a.py": "def old(): pass\n"})
    repo = oxide.open_repository(root)
    repo.index()
    assert repo._tracking.ready.wait(5)

    def fail(*args: object, **kwargs: object) -> None:
        raise CaptureConflict("busy")

    monkeypatch.setattr(_index, "index", fail)
    write(root, {"a.py": "def new(): pass\n"})
    tracker = repo._tracking
    eventually(lambda: tracker.error is not None)
    worker = tracker.thread
    reference = weakref.ref(repo)
    del repo
    gc.collect()
    eventually(lambda: reference() is None and not worker.is_alive(), timeout=2)


def test_automatic_updates_equal_full_rebuilds(tmp_path: Path) -> None:
    from test_incremental import BASE, STEPS, apply, dump

    root = write(tmp_path / "repo", BASE)
    with oxide.open_repository(root, data_dir=tmp_path / "incremental") as repo:
        repo.index()
        assert repo._tracking.ready.wait(5)
        for i, (step, edits) in enumerate(STEPS):
            apply(root, edits)
            expected = source.capture(root).snapshot_id

            def published(expected: str = expected) -> bool:
                with repo._lock:
                    return repo.store.view().snapshot.snapshot_id == expected

            eventually(published)
            with oxide.open_repository(root, data_dir=tmp_path / f"full-{i}") as full:
                full.index()
                with repo._lock:
                    assert dump(repo) == dump(full), step


def test_reads_pin_one_generation_while_updates_wait(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from oxide.storage.ladybug import View

    root = write(tmp_path / "repo", {"a.py": "def old(): pass\n"})
    with oxide.open_repository(root) as repo, ThreadPoolExecutor(1) as pool:
        first = repo.index()
        assert repo._tracking.ready.wait(5)
        entered, release = threading.Event(), threading.Event()
        original = repo._view

        def pinned() -> View:
            view = original()
            entered.set()
            assert release.wait(15)
            return view

        monkeypatch.setattr(repo, "_view", pinned)
        reading = pool.submit(repo.search, "old")
        try:
            assert entered.wait(5)
            write(root, {"a.py": "def new(): pass\n"})
            eventually(repo._tracking.dirty.is_set)
            assert repo.store.view().snapshot == first.snapshot
        finally:
            release.set()
        assert reading.result().snapshot == first.snapshot
        eventually(lambda: repo.snapshot != first.snapshot)


def test_reconciliation_keeps_captured_bytes_and_explicit_index_rereads(tmp_path: Path) -> None:
    root = write(tmp_path / "repo", {"a.py": "def a(): pass\n"})
    with oxide.open_repository(root) as repo:
        repo.index()
        before = repo._cache["a.py"]
        repo._update(True)
        assert repo._cache["a.py"] is before
        repo.index()
        assert repo._cache["a.py"] is not before
