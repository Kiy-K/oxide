"""Status reads only published generations, without capturing the worktree."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import env

import oxide
from oxide.models import InvalidPath, NotIndexed


def run(data: Path, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "oxide", "status", *args],
        env=env(data),
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


def test_unindexed_and_invalid_path(tmp_path: Path) -> None:
    data = tmp_path / "data"
    with pytest.raises(NotIndexed):
        oxide.repository_status(tmp_path, data_dir=data)
    result = run(data, "--json", cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    info = json.loads(result.stdout)
    assert info["status"] == "unindexed" and "oxide index" in info["hint"]
    assert "unindexed" in run(data, str(tmp_path)).stdout
    assert not data.exists()
    with pytest.raises(InvalidPath):
        oxide.repository_status(tmp_path / "missing", data_dir=data)
    error = run(data, str(tmp_path / "missing"), "--json")
    assert error.returncode == 2 and "error[invalid_path]" in error.stderr


def test_published_status_does_not_reindex(tmp_path: Path, fts: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "broken.py").write_text("def broken(:\n", encoding="utf-8")
    (root / "other.txt").write_text("hello", encoding="utf-8")
    data = tmp_path / "data"
    with oxide.open_repository(root, data_dir=data) as repo:
        report = repo.index()
        (root / "new.py").write_text("x = 1\n", encoding="utf-8")
        generation = next(data.glob("repos/*/generations/*"))
        before = {
            p.relative_to(generation): p.read_bytes() for p in generation.rglob("*") if p.is_file()
        }
        status = oxide.repository_status(root, data_dir=data)
        assert status.snapshot == report.snapshot
        assert {f.path for f in status.files} == {"broken.py", "other.txt"}
        assert any(f.coverage == "partial" and f.diagnostics for f in status.files)
        assert any(f.coverage == "unsupported" for f in status.files)
        result = run(data, str(root), "--data-dir", str(data), "--json")
        assert result.returncode == 0, result.stderr
        info = json.loads(result.stdout)
        assert info["snapshot"]["snapshot_id"] == report.snapshot.snapshot_id
        assert info["status"] == "indexed" and info["actions"]
        text = run(data, str(root), "--data-dir", str(data))
        assert text.returncode == 0 and "last published snapshot" in text.stdout
        assert "broken.py" in text.stdout and "partial" in text.stdout
        assert "Fix reported parse errors and re-index." in text.stdout
        assert before == {
            p.relative_to(generation): p.read_bytes() for p in generation.rglob("*") if p.is_file()
        }
        assert not list(data.glob("repos/*/pins/*"))
        assert not list(data.glob("repos/*/reader-views/*"))
    assert oxide.repository_status(root, data_dir=data).snapshot == report.snapshot


def test_status_problem_rendering_and_single_view(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from types import SimpleNamespace

    import oxide._status as status_module
    import oxide.cli as cli
    from oxide.models import Snapshot

    calls: list[str] = []
    view = SimpleNamespace(
        manifest={"skipped": [["link.py", "symlink"]], "case_collisions": [["A.py", "a.py"]]},
        snapshot=Snapshot("repo", "snapshot", "derivation"),
        files={},
        counts=(3, 4),
        lexical_unavailable="extension missing",
        directory=tmp_path,
        verify_sources=lambda: None,
    )

    class FakeReader:
        def __init__(self, *_args: object) -> None:
            pass

        def view(self) -> object:
            calls.append("view")
            return view

        def close(self) -> None:
            calls.append("close")

    monkeypatch.setattr(status_module, "Reader", FakeReader)
    result = status_module.repository_status(tmp_path)
    assert calls == ["view", "close"]
    assert result.skipped[0].path == "link.py" and result.case_collisions == (("A.py", "a.py"),)
    monkeypatch.setattr(cli, "repository_status", lambda *_a, **_kw: result)
    assert cli._status(str(tmp_path), None, False) == 0
    human = capsys.readouterr().out
    assert "link.py" in human and "A.py" in human and "extension missing" in human
    assert "re-index" in human
    assert cli._status(str(tmp_path), None, True) == 0
    document = json.loads(capsys.readouterr().out)
    assert len(document["actions"]) == 3
    assert document["skipped"][0]["reason"] == "symlink"
    assert document["case_collisions"] == [["A.py", "a.py"]]
    assert document["lexical_unavailable"] == "extension missing"


def test_status_does_not_hide_storage_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import oxide._status as status_module
    from oxide.models import IncompatibleIndex, StoreCorrupt

    class BrokenReader:
        def __init__(self, *_args: object) -> None:
            raise IncompatibleIndex("wrong version")

    monkeypatch.setattr(status_module, "Reader", BrokenReader)
    with pytest.raises(IncompatibleIndex, match="wrong version"):
        status_module.repository_status(tmp_path)

    class CorruptReader:
        def __init__(self, *_args: object) -> None:
            raise StoreCorrupt("bad manifest")

    monkeypatch.setattr(status_module, "Reader", CorruptReader)
    with pytest.raises(StoreCorrupt, match="bad manifest"):
        status_module.repository_status(tmp_path)


def test_real_store_problems_and_storage_errors(tmp_path: Path, fts: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "A.py").write_text("x = 1\n")
    (root / "a.py").write_text("y = 2\n")
    if (root / "A.py").samefile(root / "a.py"):
        pytest.skip("case-sensitive filesystem required")
    try:
        (root / "link.py").symlink_to("A.py")
    except OSError:
        pytest.skip("symlinks unavailable")
    data = tmp_path / "data"
    with oxide.open_repository(root, data_dir=data) as repo:
        repo.index()
    manifest = next(data.glob("repos/*/generations/*/MANIFEST.json"))
    original = manifest.read_bytes()
    generation = manifest.parent
    before = {
        p.relative_to(generation): p.read_bytes() for p in generation.rglob("*") if p.is_file()
    }
    status = oxide.repository_status(root, data_dir=data)
    assert before == {
        p.relative_to(generation): p.read_bytes() for p in generation.rglob("*") if p.is_file()
    }
    assert not list(data.glob("repos/*/pins/*"))
    assert any(s.path == "link.py" and s.reason == "symlink" for s in status.skipped)
    assert ("A.py", "a.py") in status.case_collisions
    result = run(data, str(root), "--json")
    assert result.returncode == 0, result.stderr
    info = json.loads(result.stdout)
    assert info["skipped"] and info["case_collisions"]
    document = json.loads(original)
    document["lexical_unavailable"] = "test extension unavailable"
    manifest.write_text(json.dumps(document))
    result = run(data, str(root), "--json")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["lexical_unavailable"] == "test extension unavailable"
    document["schema"] = "invalid schema"
    manifest.write_text(json.dumps(document))
    result = run(data, str(root), "--json")
    assert result.returncode == 2 and "incompatible_index" in result.stderr
    manifest.write_text("{")
    result = run(data, str(root), "--json")
    assert result.returncode == 2 and "store_corrupt" in result.stderr
    manifest.write_bytes(original)


def test_status_across_publication(
    tmp_path: Path, fts: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import oxide._status as status_module
    from oxide.storage.ladybug import View
    from oxide.storage.reader import Reader

    root, data = tmp_path / "repo", tmp_path / "data"
    root.mkdir()
    (root / "a.py").write_text("x = 1\n")
    with oxide.open_repository(root, data_dir=data) as owner:
        first = owner.index()
        (root / "b.py").write_text("def broken(:\n")

        class PublishingReader(Reader):
            calls = 0

            def view(self) -> View:
                view = super().view()
                self.calls += 1
                if self.calls == 2:
                    owner.index()
                return view

        monkeypatch.setattr(status_module, "Reader", PublishingReader)
        result = status_module.repository_status(root, data_dir=data)
        assert result.snapshot == first.snapshot
        assert [f.path for f in result.files] == ["a.py"]
        assert all(f.coverage == "complete" and not f.diagnostics for f in result.files)
        assert result.entities == first.entities and result.relations == first.relations
    assert not list(data.glob("repos/*/pins/*"))


def test_damaged_generation_and_copy_error(
    tmp_path: Path, fts: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import oxide.storage.reader as reader
    from oxide.models import ResourceExhausted, StoreCorrupt

    root, data = tmp_path / "repo", tmp_path / "data"
    root.mkdir()
    (root / "a.py").write_text("x = 1\n")
    with oxide.open_repository(root, data_dir=data) as owner:
        owner.index()
    store = next(data.glob("repos/*"))
    current = store / "CURRENT"
    original = current.read_text()
    current.write_text("0" * 64)
    with pytest.raises(StoreCorrupt):
        oxide.repository_status(root, data_dir=data)
    assert not list(store.glob("pins/*"))
    assert not list(store.glob("reader-views/*"))
    current.write_text(original)
    source = next(store.glob("generations/*/source/*"))
    original_source = source.read_bytes()
    source.unlink()
    with pytest.raises(StoreCorrupt, match="retained source"):
        oxide.repository_status(root, data_dir=data)
    source.write_text("corrupt")
    with pytest.raises(StoreCorrupt, match="retained source"):
        oxide.repository_status(root, data_dir=data)
    assert not list(store.glob("pins/*"))
    source.write_bytes(original_source)
    monkeypatch.setattr(reader, "_WINDOWS", True)

    def fail_copy(_src: object, dst: object, **_kwargs: object) -> None:
        cache = Path(str(dst))
        cache.mkdir()
        (cache / "partial").write_text("partial")
        raise OSError("simulated copy failure")

    monkeypatch.setattr("oxide.storage.reader.shutil.copytree", fail_copy)
    with pytest.raises(ResourceExhausted):
        oxide.repository_status(root, data_dir=data)
    assert not list(store.glob("pins/*"))
    assert not list(store.glob("reader-views/*"))


def test_changing_publication_stays_conflict(
    tmp_path: Path, fts: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from oxide.models import OwnershipConflict
    from oxide.storage.reader import Reader

    root, data = tmp_path / "repo", tmp_path / "data"
    root.mkdir()
    (root / "a.py").write_text("x = 1\n")
    with oxide.open_repository(root, data_dir=data) as owner:
        owner.index()
    monkeypatch.setattr(Reader, "_identity", lambda _self: ("f" * 64, 1))
    with pytest.raises(OwnershipConflict):
        oxide.repository_status(root, data_dir=data)
    assert not list(data.glob("repos/*/pins/*"))
