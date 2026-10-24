"""Test isolation: every test gets a private data location; real user data is never touched.

The pinned FTS extension is a read-only dependency resolved from the real environment
(``OXIDE_FTS_EXTENSION`` or the default cache) before anything is redirected.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterator, Mapping
from pathlib import Path

import pytest

from oxide.storage.ladybug import _fts_build, default_data_dir, fts_location, fts_unavailable

REPO = Path(__file__).resolve().parents[2]
FIXTURE = REPO / "fixtures" / "py_repo"
FTS = fts_location()
REAL_DATA = default_data_dir()


def _state(path: Path) -> object:
    return (
        sorted((str(p), p.stat().st_mtime_ns) for p in path.rglob("*")) if path.exists() else None
    )


@pytest.fixture(scope="session", autouse=True)
def real_data_untouched() -> Iterator[None]:
    before = _state(REAL_DATA)
    yield
    assert _state(REAL_DATA) == before, f"tests modified real user data at {REAL_DATA}"


@pytest.fixture(autouse=True)
def private_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data = tmp_path / "oxide-data"
    monkeypatch.setenv("OXIDE_FTS_EXTENSION", str(FTS))
    monkeypatch.setenv("OXIDE_DATA_DIR", str(data))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg-cache"))
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)  # a real key never reaches a test
    for name in [k for k in os.environ if k.lower().endswith("_proxy")]:
        monkeypatch.delenv(name)  # localhost judges are reached directly
    return data


@pytest.fixture
def fts() -> Path:
    if _fts_build() is None:
        pytest.skip("no pinned FTS build for this platform (docs/platforms.md)")
    reason = fts_unavailable(FTS)
    assert reason is None, f"{reason} (lexical tests need the pinned extension)"
    return FTS


@pytest.fixture
def repo_copy(tmp_path: Path) -> Path:
    """A private, writable copy of the Python fixture repository."""
    target = tmp_path / "py_repo"
    shutil.copytree(FIXTURE, target)
    return target


def write(root: Path, files: Mapping[str, str | bytes]) -> Path:
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        # Exact bytes: write_text would turn "\n" into "\r\n" on Windows.
        path.write_bytes(content if isinstance(content, bytes) else content.encode())
    return root


def env(data: Path) -> dict[str, str]:
    """Environment for a subprocess: explicit private data and cache locations.
    Fails closed unless ``data`` is test-owned (under the temporary root)."""
    if not data.resolve().is_relative_to(Path(tempfile.gettempdir()).resolve()):
        raise RuntimeError(f"{data} is not a private test location")
    return {
        **os.environ,
        "OXIDE_DATA_DIR": str(data),
        "OXIDE_FTS_EXTENSION": str(FTS),
        "XDG_DATA_HOME": str(data / "xdg"),
        "XDG_CACHE_HOME": str(data / "cache"),
    }


def python(data: Path, script: str, *args: str) -> subprocess.CompletedProcess[str]:
    """Runs ``script`` in a fresh interpreter with private locations: native probes that
    could crash, and crash injection, never run in the test process."""
    command = [sys.executable, "-c", script, *args]
    return subprocess.run(command, env=env(data), capture_output=True, text=True, check=False)
