"""Resource lifecycle: close, context managers and forgotten handles release the lock,
the read view and its native connection (descriptors counted in /proc or /dev/fd)."""

from __future__ import annotations

import gc
import os
import sys
import textwrap
import weakref
from pathlib import Path

import pytest
from conftest import FIXTURE, python

import oxide
from oxide.models import Closed, OwnershipConflict

OWNS = textwrap.dedent(
    """
    import sys, oxide
    try:
        oxide.open_repository(sys.argv[1], data_dir=sys.argv[2]).close()
    except oxide.OxideError as error:
        sys.exit(0 if error.code == "ownership_conflict" else 3)
    sys.exit(4)
    """
)


def descriptors() -> int:
    return len(os.listdir("/proc/self/fd" if os.path.isdir("/proc/self/fd") else "/dev/fd"))


def use(repo: oxide.Repository) -> None:
    repo.index()
    repo.search("RetryPolicy")
    repo.build_context("retry", 512)


def test_a_dropped_repository_releases_its_lock_on_collection(
    private_data: Path, fts: Path
) -> None:
    repo = oxide.open_repository(FIXTURE)
    use(repo)
    store = weakref.ref(repo.store)
    del repo
    gc.collect()
    assert store() is None  # the finalizer does not keep its store alive
    taken = python(private_data, OWNS, str(FIXTURE), str(private_data))
    assert taken.returncode == 4, taken.stderr  # another process can own it now
    with oxide.open_repository(FIXTURE) as again:
        assert again.search("RetryPolicy").entries


@pytest.mark.skipif(sys.platform == "win32", reason="no descriptor table to count on Windows")
def test_close_and_exceptions_release_everything(private_data: Path, fts: Path) -> None:
    with oxide.open_repository(FIXTURE) as repo:
        use(repo)  # warms imports and the native library before counting
    before = descriptors()
    for _ in range(10):
        with oxide.open_repository(FIXTURE) as repo:
            use(repo)
        with pytest.raises(RuntimeError), oxide.open_repository(FIXTURE) as repo:
            use(repo)
            raise RuntimeError("caller failure")
        repo = oxide.open_repository(FIXTURE)
        use(repo)
        del repo
        gc.collect()
    assert descriptors() == before


def test_close_is_idempotent_and_ownership_is_exclusive(private_data: Path) -> None:
    repo = oxide.open_repository(FIXTURE)
    with pytest.raises(OwnershipConflict):
        oxide.open_repository(FIXTURE)
    held = python(private_data, OWNS, str(FIXTURE), str(private_data))
    assert held.returncode == 0, held.stderr
    repo.close()
    repo.close()
    with pytest.raises(Closed):
        repo.files()
    oxide.open_repository(FIXTURE).close()


def test_a_failed_open_leaves_no_lock_behind(private_data: Path) -> None:
    oxide.index_repository(FIXTURE)
    (directory,) = (private_data / "repos").iterdir()
    (directory / "CURRENT").write_text("f" * 64)
    for _ in range(3):
        with pytest.raises(oxide.OxideError):
            oxide.open_repository(FIXTURE)
    (directory / "CURRENT").unlink()
    oxide.open_repository(FIXTURE).close()
