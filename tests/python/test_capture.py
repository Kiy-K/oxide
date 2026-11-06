"""Source capture: scope, explicit skips and snapshot identity (ADR-0010)."""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

import pytest
from conftest import write

import oxide
from oxide.capture import MAX_FILE_BYTES, capture
from oxide.models import CaptureConflict, CaptureLimit


def test_scope_follows_gitignore_and_reports_every_skip(tmp_path: Path) -> None:
    if sys.platform == "win32":
        pytest.skip("no FIFOs; symlinks need privileges")
    root = write(
        tmp_path / "repo",
        {
            ".gitignore": "*.log\nbuild/\n",
            "keep.py": "x = 1\n",
            "debug.log": "noise\n",
            "build/out.py": "y = 2\n",
            "sub/.gitignore": "!important.log\n",
            "sub/important.log": "kept\n",
            ".git/config": "[core]\n",
            "big.bin": b"0" * (MAX_FILE_BYTES + 1),
        },
    )
    os.symlink(root / "keep.py", root / "link.py")
    os.mkfifo(root / "pipe")
    snapshot = capture(root)
    assert [f.path for f in snapshot.files] == [
        ".gitignore",
        "keep.py",
        "sub/.gitignore",
        "sub/important.log",
    ]
    assert {(s.path, s.reason) for s in snapshot.skipped} == {
        ("big.bin", "too_large"),
        ("link.py", "symlink"),
        ("pipe", "special"),
    }


def test_snapshot_identity_is_content_addressed(tmp_path: Path) -> None:
    root = write(tmp_path / "a", {"m.py": "x = 1\n"})
    other = write(tmp_path / "b", {"m.py": "x = 1\n"})
    assert capture(root).snapshot_id == capture(other).snapshot_id
    write(other, {"m.py": "x = 2\n"})
    assert capture(root).snapshot_id != capture(other).snapshot_id
    write(other, {"m.py": "x = 1\n", "new.txt": ""})
    assert capture(root).snapshot_id != capture(other).snapshot_id


def test_undecodable_ignore_rules_fail_closed(tmp_path: Path) -> None:
    root = write(tmp_path, {".gitignore": b"\xff\xfe*.log\n", "a.py": ""})
    with pytest.raises(CaptureConflict, match="unreadable ignore rules"):
        capture(root)


def test_oversized_ignore_rules_fail_closed_before_they_are_loaded(tmp_path: Path) -> None:
    root = write(tmp_path, {".gitignore": b"#" * (MAX_FILE_BYTES + 1), "a.py": ""})
    with pytest.raises(CaptureLimit, match="ignore rules exceed"):
        capture(root)


def test_case_fold_collisions_are_diagnostics(tmp_path: Path) -> None:
    root = write(tmp_path, {"Readme.md": "a", "README.md": "b"})
    if len(os.listdir(root)) < 2:
        pytest.skip("case-insensitive filesystem: the two names are one file")
    assert capture(root).case_collisions == (("README.md", "Readme.md"),)


@pytest.mark.parametrize("where", ["state", "deep/nested/state", "alias"])
def test_a_data_location_inside_the_repository_is_never_captured(
    repo_copy: Path, tmp_path: Path, where: str
) -> None:
    outside = oxide.index_repository(repo_copy, data_dir=tmp_path / "outside")
    if where == "alias":  # reached through a symlink outside the repository
        (repo_copy / "real-state").mkdir()
        (tmp_path / "alias").symlink_to(repo_copy / "real-state")
        data = tmp_path / "alias"
    else:
        data = repo_copy / where
    statuses = []
    with oxide.open_repository(repo_copy, data_dir=data) as repo:
        for _ in range(3):
            report = repo.index()
            statuses.append(report.status)
            assert report.files == outside.files
    assert statuses == ["built", "unchanged", "unchanged"]


@pytest.mark.skipif(
    sys.platform == "win32" or os.geteuid() == 0,
    reason="mode 000 blocks reads only for non-root POSIX",
)
def test_a_file_that_cannot_be_opened_is_an_unreadable_skip(tmp_path: Path) -> None:
    write(tmp_path, {"a.py": "x = 1\n", "secret.py": "y = 2\n"})
    (tmp_path / "secret.py").chmod(0)
    try:
        captured = capture(tmp_path)
    finally:
        (tmp_path / "secret.py").chmod(0o644)
    assert [f.path for f in captured.files] == ["a.py"]
    assert [(s.path, s.reason) for s in captured.skipped] == [("secret.py", "unreadable")]


def test_a_file_that_grows_after_it_is_listed_is_read_only_to_its_listed_size(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from oxide import capture as module

    (tmp_path / "a.py").write_bytes(b"x" * 10)
    expected = module._key(os.stat(tmp_path / "a.py"), str(tmp_path / "a.py"))
    (tmp_path / "a.py").write_bytes(b"x" * 1_000_000)
    monkeypatch.setattr(module, "_key", lambda *_: expected)  # growth after fstat
    sizes: list[int] = []
    real = os.fdopen

    class Spy:
        def __init__(self, fd: int, mode: str) -> None:
            self.handle = real(fd, mode)

        def __enter__(self) -> Spy:
            return self

        def __exit__(self, *_: object) -> None:
            self.handle.close()

        def read(self, n: int = -1) -> bytes:
            sizes.append(n)
            data: bytes = self.handle.read(n)
            return data

    monkeypatch.setattr(os, "fdopen", Spy)
    assert module._read(tmp_path, "a.py", expected) is None
    assert sizes == [11]


def _windows_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """The Windows key on any platform. ChangeTime moves on every write and utime cannot restore
    it, so a digest of the content stands in for it. st_ctime cannot: it is the creation time on
    Windows, which is why this test failed there when it used st_ctime."""
    from oxide import capture as module

    def change(source: str | int) -> int:
        if isinstance(source, int):  # the open descriptor: read it, then restore its position
            position = os.lseek(source, 0, os.SEEK_CUR)
            os.lseek(source, 0, os.SEEK_SET)
            data = b"".join(iter(lambda: os.read(source, 65536), b""))
            os.lseek(source, position, os.SEEK_SET)
        else:
            data = Path(source).read_bytes()
        return int.from_bytes(hashlib.sha256(data).digest()[:8], "big")

    monkeypatch.setattr(module, "_WINDOWS", True)
    monkeypatch.setattr(module, "change_time", change)


def test_windows_key_sees_an_edit_that_restores_size_and_mtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from oxide import capture as module

    _windows_key(monkeypatch)
    write(tmp_path, {"a.py": "x = 1\n"})
    cache: module.CaptureCache = {}
    capture(tmp_path, cache=cache)
    before = (tmp_path / "a.py").stat()
    write(tmp_path, {"a.py": "x = 2\n"})
    os.utime(tmp_path / "a.py", ns=(before.st_atime_ns, before.st_mtime_ns))
    assert [f.data for f in capture(tmp_path, cache=cache).files] == [b"x = 2\n"]


def test_a_file_whose_change_time_is_unavailable_is_an_unreadable_skip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from oxide import capture as module

    def refuse(source: str | int) -> int:
        raise PermissionError("no change time")

    write(tmp_path, {"a.py": "x = 1\n"})
    monkeypatch.setattr(module, "_WINDOWS", True)
    monkeypatch.setattr(module, "change_time", refuse)
    captured = capture(tmp_path)
    assert captured.files == ()
    assert [(s.path, s.reason) for s in captured.skipped] == [("a.py", "unreadable")]
