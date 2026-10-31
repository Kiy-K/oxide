"""Source capture: scope, explicit skips and snapshot identity (ADR-0010)."""

from __future__ import annotations

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
