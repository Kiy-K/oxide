"""Pinned shared reads, and ``outline`` / ``inspect`` / ``refs`` over indexed facts."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import FIXTURE, env, write

import oxide
from oxide.models import InvalidArgument, NotIndexed, StoreCorrupt
from oxide.storage import pins
from oxide.storage.reader import Reader

HEX = "a" * 64


def oxide_cli(data: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "oxide", *args],
        env=env(data),
        capture_output=True,
        encoding="utf-8",
        check=False,
    )


@pytest.fixture
def owned(repo_copy: Path) -> oxide.Repository:
    repo = oxide.open_repository(repo_copy)
    repo.index()
    return repo


def store_dir(data: Path) -> Path:
    (directory,) = (data / "repos").iterdir()
    return directory


# --- pins -----------------------------------------------------------------------


def test_a_live_pin_protects_its_generation_and_a_dead_one_does_not(tmp_path: Path) -> None:
    generations = tmp_path / "generations"
    for name in (HEX, "b" * 64, "c" * 64, "d" * 64 + ".staging"):
        (generations / name).mkdir(parents=True)
    fd, path = pins.pin(tmp_path, HEX)
    other_fd, other = pins.pin(tmp_path, "b" * 64)
    os.close(other_fd)  # a crashed reader: its lock dies with the descriptor
    pins.sweep(tmp_path, generations, "c" * 64)
    assert sorted(p.name for p in generations.iterdir()) == [HEX, "c" * 64]
    assert path.exists() and not other.exists()
    pins.unpin(fd, path)
    pins.sweep(tmp_path, generations, "c" * 64)
    assert [p.name for p in generations.iterdir()] == ["c" * 64]


def test_sweep_keeps_the_replaced_twin_of_a_pinned_generation(tmp_path: Path) -> None:
    generations = tmp_path / "generations"
    for name in (HEX, HEX + ".replaced", HEX + ".staging", "c" * 64):
        (generations / name).mkdir(parents=True)
    fd, path = pins.pin(tmp_path, HEX)
    pins.sweep(tmp_path, generations, "c" * 64)
    assert sorted(p.name for p in generations.iterdir()) == [HEX, HEX + ".replaced", "c" * 64]
    pins.unpin(fd, path)


def test_the_pin_scan_unlinks_stale_pins_and_fails_closed_on_unreadable_ones(
    tmp_path: Path,
) -> None:
    fd, path = pins.pin(tmp_path, HEX)
    assert pins.pinned(tmp_path) == {HEX} and path.exists()
    os.close(fd)
    assert pins.pinned(tmp_path) == set() and not path.exists()
    unreadable = tmp_path / "pins" / ("b" * 64 + ".x")
    unreadable.write_text("")
    unreadable.chmod(0)
    if os.access(unreadable, os.R_OK):  # running as root: permissions do not bite
        return
    assert pins.pinned(tmp_path) == {"b" * 64}


# --- readers --------------------------------------------------------------------


def test_a_reader_follows_the_owner_and_keeps_its_pinned_generation(
    owned: oxide.Repository, repo_copy: Path, private_data: Path
) -> None:
    reader = Reader(repo_copy, private_data)
    first = reader.view()
    before = first.snapshot
    write(repo_copy, {"oxidepy/extra.py": "def extra():\n    return 1\n"})
    owned.index()
    assert first.snapshot == before and "oxidepy/extra.py" not in first.files  # still readable
    assert first.directory.is_dir()
    second = reader.view()
    assert "oxidepy/extra.py" in second.files and second.snapshot != before
    write(repo_copy, {"oxidepy/more.py": "def more():\n    return 2\n"})
    owned.index()  # the next publication sweeps what no reader pins any more
    assert not first.directory.exists()
    reader.close()
    assert list((store_dir(private_data) / "pins").iterdir()) == []


def test_a_reader_notices_a_same_name_rebuild(
    owned: oxide.Repository, repo_copy: Path, private_data: Path
) -> None:
    reader = Reader(repo_copy, private_data)
    first = reader.view()
    generation = first.directory
    shutil.copytree(generation, generation.with_suffix(".copy"))
    generation.rename(generation.with_suffix(".replaced"))  # as Store.publish does
    generation.with_suffix(".copy").rename(generation)
    assert reader.view() is not first
    reader.close()


def test_a_reader_needs_a_published_store(repo_copy: Path, private_data: Path) -> None:
    with pytest.raises(NotIndexed):
        Reader(repo_copy, private_data)
    repo = oxide.open_repository(repo_copy)
    repo.index()
    (store_dir(private_data) / "repo.json").write_text("{")
    with pytest.raises(StoreCorrupt):
        Reader(repo_copy, private_data)


# --- shared repositories ----------------------------------------------------------


def test_a_shared_handle_reads_while_another_owns_and_cannot_write(
    owned: oxide.Repository, repo_copy: Path
) -> None:
    with pytest.raises(oxide.OxideError):
        oxide.open_repository(repo_copy)
    with oxide.open_repository(repo_copy, create=False, shared=True) as reader:
        assert reader.read_only and not owned.read_only
        assert reader.snapshot == owned.snapshot
        assert reader.inspect("RetryPolicy").status == "resolved"
        with pytest.raises(InvalidArgument, match="only reads"):
            reader.index()
        with pytest.raises(InvalidArgument, match="only reads"):
            reader.judge(True)
    assert not owned.read_only


def test_a_shared_handle_is_the_owner_when_nobody_is(repo_copy: Path) -> None:
    oxide.index_repository(repo_copy)
    with oxide.open_repository(repo_copy, create=False, shared=True) as repo:
        assert not repo.read_only


# --- outline --------------------------------------------------------------------


def test_outline_of_a_file_nests_members_with_lines(owned: oxide.Repository) -> None:
    result = owned.outline("oxidepy/retry.py")
    (file,) = result.files
    shape = [(d.depth, d.kind, d.name, d.location.start_line) for d in file.declarations]  # type: ignore[union-attr]
    assert shape[:3] == [
        (0, "class", "TooManyAttemptsError", 7),
        (1, "method", "__init__", 10),
        (0, "class", "RetryPolicy", 15),
    ]
    policy = next(d for d in file.declarations if d.name == "RetryPolicy")
    assert policy.signature and policy.signature.startswith("class RetryPolicy")
    assert result.snapshot == owned.snapshot and not result.truncated


def test_outline_of_a_directory_is_deterministic_and_bounded(owned: oxide.Repository) -> None:
    whole = owned.outline()
    assert [f.path for f in whole.files] == sorted(f.path for f in whole.files)
    assert whole == owned.outline(".") == owned.outline("")
    cut = owned.outline("oxidepy", limit=3)
    assert cut.truncated and sum(len(f.declarations) for f in cut.files) == 3
    assert owned.outline("oxidepy", limit=0).truncated
    with pytest.raises(InvalidArgument, match="no indexed file or directory"):
        owned.outline("nope")
    with pytest.raises(InvalidArgument):
        owned.outline(limit=-1)


def test_outline_counts_files_it_cannot_parse(repo_copy: Path) -> None:
    write(repo_copy, {"notes.xyz": "text\n"})
    with oxide.open_repository(repo_copy) as repo:
        repo.index()
        assert repo.outline().unsupported >= 1
        assert all(f.path != "notes.xyz" for f in repo.outline().files)


# --- inspect and refs -------------------------------------------------------------


def test_inspect_resolves_every_spelling_of_one_symbol(owned: oxide.Repository) -> None:
    key = "symbol:oxidepy/retry.py#RetryPolicy.should_retry"
    for query in (
        key,
        "RetryPolicy.should_retry",
        "retry.py#RetryPolicy.should_retry",
        "oxidepy/retry.py#RetryPolicy.should_retry",
        "oxidepy/retry.py:32",
    ):
        found = owned.inspect(query)
        assert found.status == "resolved", query
        assert found.candidates[0].declaration.key == key
    found = owned.inspect("RetryPolicy")
    assert found.parent and found.parent.kind == "file"
    assert [m.name for m in found.members][:2] == ["__init__", "exhausted"]
    assert any(
        link.kind == "calls" and link.direction == "in" and link.proven for link in found.links
    )
    assert owned.inspect("oxidepy/retry.py").candidates[0].why == "path"
    assert owned.inspect("oxidepy/retry.py:1000").status == "not_found"


def test_an_ambiguous_name_is_never_guessed(owned: oxide.Repository) -> None:
    found = owned.inspect("__init__")
    assert found.status == "ambiguous" and len(found.candidates) > 1 and not found.members
    keys = [c.declaration.key for c in found.candidates]
    assert keys == [c.declaration.key for c in owned.inspect("__init__").candidates]
    assert owned.refs("__init__").status == "ambiguous"
    assert owned.refs("__init__").references == ()
    capped = owned.inspect("__init__", limit=2)
    assert len(capped.candidates) == 2 and capped.truncated
    assert owned.inspect("definitely_missing").status == "not_found"
    with pytest.raises(InvalidArgument, match="empty query"):
        owned.inspect("  ")


def test_refs_labels_evidence_and_never_claims_completeness(owned: oxide.Repository) -> None:
    result = owned.refs("RetryPolicy")
    assert result.status == "resolved" and result.references
    assert {r.label for r in result.references} <= {
        "proven",
        "heuristic",
        "ambiguous",
        "unresolved",
    }
    callers = [r for r in result.references if r.kind == "calls" and r.direction == "in"]
    assert callers and all(r.at and r.text and r.other for r in callers)
    labels = [r.label for r in result.references]
    assert labels == sorted(labels, key=("proven", "heuristic", "ambiguous", "unresolved").index)
    outward = owned.refs("oxidepy/retry.py#RetryPolicy.wait")
    assert any(r.direction == "out" for r in outward.references)
    few = owned.refs("RetryPolicy", limit=1)
    assert few.truncated and len(few.references) == 1
    # locations are lines of the captured bytes
    for r in result.references:
        assert r.at is None or (r.at.start_line >= 1 and r.at.end_line >= r.at.start_line)


def test_navigation_reads_the_snapshot_not_the_worktree(
    owned: oxide.Repository, repo_copy: Path
) -> None:
    before = owned.inspect("RetryPolicy")
    path = repo_copy / "oxidepy" / "retry.py"
    path.write_text("\n" * 50 + path.read_text())
    owned.index()
    after = owned.inspect("RetryPolicy")
    assert after.candidates[0].declaration.location.start_line == (  # type: ignore[union-attr]
        before.candidates[0].declaration.location.start_line + 50  # type: ignore[union-attr]
    )


@pytest.mark.parametrize("repo", ["js_repo", "go_repo", "rust_repo", "java_repo", "c_repo"])
def test_navigation_works_in_every_language(repo: str, tmp_path: Path) -> None:
    copy = tmp_path / repo
    shutil.copytree(FIXTURE.parent / repo, copy)
    with oxide.open_repository(copy) as handle:
        handle.index()
        files = [f for f in handle.outline().files if f.declarations]
        assert files
        first = files[0].declarations[0]
        found = handle.inspect(first.key)
        assert found.status == "resolved" and found.candidates[0].declaration == first
        assert handle.refs(first.key).status == "resolved"
        assert handle.outline() == handle.outline()


# --- command line -----------------------------------------------------------------


def test_cli_navigation_round_trip_beside_a_running_owner(
    owned: oxide.Repository, repo_copy: Path, private_data: Path
) -> None:
    target = (str(repo_copy), "--data-dir", str(private_data))
    listed = oxide_cli(private_data, "outline", *target, "oxidepy/retry.py")
    assert listed.returncode == 0, listed.stderr
    assert "RetryPolicy" in listed.stdout and "reading its latest snapshot" in listed.stderr
    found = oxide_cli(private_data, "inspect", *target, "RetryPolicy.should_retry", "--json")
    assert found.returncode == 0, found.stderr
    data = json.loads(found.stdout)
    assert data["status"] == "resolved"
    assert data["candidates"][0]["declaration"]["location"]["path"] == "oxidepy/retry.py"
    refs = oxide_cli(private_data, "refs", *target, "RetryPolicy", "--limit", "2")
    assert refs.returncode == 0 and "not an exhaustive list" in refs.stdout
    ambiguous = oxide_cli(private_data, "inspect", *target, "__init__")
    assert ambiguous.returncode == 1 and "ambiguous" in ambiguous.stdout
    assert oxide_cli(private_data, "refs", *target, "nothing_here").returncode == 1
    # the owner was not disturbed
    assert owned.inspect("RetryPolicy").status == "resolved"


def test_cli_navigation_errors_are_typed(repo_copy: Path, private_data: Path) -> None:
    target = (str(repo_copy), "--data-dir", str(private_data))
    missing = oxide_cli(private_data, "inspect", *target, "x")
    assert missing.returncode == 2 and "error[not_indexed]" in missing.stderr
    assert oxide_cli(private_data, "index", *target).returncode == 0
    bad = oxide_cli(private_data, "outline", *target, "nope")
    assert bad.returncode == 2 and "error[invalid_argument]" in bad.stderr
