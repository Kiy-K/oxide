"""The agent's environment is built from the task's recipe: dev tasks keep the frozen REPOS
recipe, and a held-out task's ``build`` block replaces it, so the tests validated under that
recipe are the ones the agent runs under."""

from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

SCRIPTS = str(Path(__file__).resolve().parents[2] / "scripts" / "agent_eval")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

tasks: ModuleType = importlib.import_module("tasks")


@pytest.fixture(autouse=True)
def require_sandbox(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tasks.confine, "require", lambda: None)


def task(**extra: Any) -> dict[str, Any]:
    return {
        "id": "psf__requests-1",
        "repo": "psf/requests",
        "version": "2.0",
        "created_at": "2026-08-01T00:00:00Z",
        "dated": True,
        **extra,
    }


@pytest.fixture
def commands(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    seen: list[list[str]] = []

    def fake_sh(*argv: str, **_: Any) -> subprocess.CompletedProcess[str]:
        seen.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(tasks, "sh", fake_sh)
    monkeypatch.setattr(tasks.confine, "require", lambda: None)
    return seen


def test_a_dev_task_keeps_the_frozen_recipe(commands: list[list[str]]) -> None:
    tasks.environment(task(), Path("/w"), Path("/v"))
    assert ["uv", "venv", "-q", "--python", "3.9", Path("/v")] in commands
    assert any("setuptools<70" in argv for argv in commands)


def test_a_held_out_build_replaces_python_and_setuptools(commands: list[list[str]]) -> None:
    build = {"python": "3.12", "setuptools": "setuptools>=77"}
    tasks.environment(task(build=build), Path("/w"), Path("/v"))
    assert ["uv", "venv", "-q", "--python", "3.12", Path("/v")] in commands
    assert any("setuptools>=77" in argv for argv in commands)
    assert not any("setuptools<70" in argv for argv in commands)


def test_the_editable_install_cannot_run_build_hooks_on_the_host(
    commands: list[list[str]],
) -> None:
    tasks.environment(task(), Path("/w"), Path("/v"))
    editable = next(argv for argv in commands if "-e" in argv)
    assert editable[0] == "bwrap"
    assert "--unshare-net" not in editable
    die = editable.index("--die-with-parent")
    assert editable[die - 3 : die] == ["--bind", str(Path("/v")), str(Path("/v"))]
    bound_work = ["--bind", str(Path("/w")), str(Path("/w"))]
    assert bound_work in [editable[i : i + 3] for i in range(len(editable) - 2)]
    assert str(tasks.ROOT / "fixtures" / "eval") in editable
    installs = [argv for argv in commands if "install" in argv]
    assert all(argv[0] == "bwrap" for argv in installs)


def test_verification_confines_patch_and_pytest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen: list[tuple[list[str], dict[str, Any]]] = []

    def fake_sh(*argv: str, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        seen.append((list(argv), kwargs))
        return subprocess.CompletedProcess(argv, 0, "PASSED test_a.py::test_a\n", "")

    monkeypatch.setattr(tasks, "sh", fake_sh)
    monkeypatch.setattr(tasks.confine, "require", lambda: None)
    work = tmp_path / "work"
    work.mkdir()
    tasks.apply(work, "patch")
    assert tasks.tests(work, {}, ["test_a.py"]) == {"test_a.py::test_a": "PASSED"}
    for argv, _ in seen:
        assert argv[0] == "bwrap"
        assert "--unshare-net" in argv
        assert ["--bind", str(work), str(work)] in [argv[i : i + 3] for i in range(len(argv) - 2)]


def test_checkout_git_commands_are_confined(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen: list[list[str]] = []
    monkeypatch.setattr(tasks, "_mirror", lambda *_: tmp_path / "mirror")
    monkeypatch.setattr(tasks.confine, "require", lambda: None)
    monkeypatch.setattr(tasks, "build_hides", lambda: [])

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[Any]:
        seen.append(argv)
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    monkeypatch.setattr(tasks.subprocess, "run", fake_run)
    work = tmp_path / "work"
    tasks.checkout(task(base_commit="abc"), work)
    assert len(seen) == 5
    assert all(argv[0] == "bwrap" and "--unshare-net" in argv for argv in seen)
    assert ["--ro-bind", str(tmp_path / "mirror"), "/tmp/eval-mirror"] in [
        seen[0][i : i + 3] for i in range(len(seen[0]) - 2)
    ]


def test_a_timeout_is_not_a_test_result(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_sh(*argv: str, **_: Any) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(list(argv), 1)

    monkeypatch.setattr(tasks, "sh", fake_sh)
    with pytest.raises(tasks.TestRunError, match="timed out"):
        tasks.tests(Path("/w"), {}, ["tests/test_a.py"], timeout=1)


def test_a_startup_failure_is_not_a_test_result(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_sh(*argv: str, **_: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 1, "", "bwrap: Can't find source path")

    monkeypatch.setattr(tasks, "sh", fake_sh)
    with pytest.raises(tasks.TestRunError, match="without a scoreable session"):
        tasks.tests(Path("/w"), {}, ["tests/test_a.py"])


def test_a_collection_error_stays_an_agent_result(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_sh(*argv: str, **_: Any) -> subprocess.CompletedProcess[str]:
        stdout = "ERROR tests/test_a.py\n!!!! Interrupted: 1 error during collection !!!!\n"
        return subprocess.CompletedProcess(argv, 2, stdout, "")

    monkeypatch.setattr(tasks, "sh", fake_sh)
    # The agent broke the module: its expected ids are absent, which score() counts as failed.
    assert tasks.tests(Path("/w"), {}, ["tests/test_a.py"]) == {"tests/test_a.py": "ERROR"}


def test_a_pytest_usage_error_is_not_a_test_result(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_sh(*argv: str, **_: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 4, "ERROR: file or directory not found\n", "")

    monkeypatch.setattr(tasks, "sh", fake_sh)
    with pytest.raises(tasks.TestRunError, match="without a scoreable session"):
        tasks.tests(Path("/w"), {}, ["tests/test_a.py"])


def test_an_agent_that_hangs_or_breaks_pytest_is_unresolved_not_a_harness_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def hang(*argv: str, **_: Any) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(list(argv), 1)

    monkeypatch.setattr(tasks, "sh", hang)
    assert tasks.tests(Path("/w"), {}, ["t.py"], timeout=1, agent_tree=True) == {}

    def usage(*argv: str, **_: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 4, "ERROR: bad addopts\n", "")

    monkeypatch.setattr(tasks, "sh", usage)
    assert tasks.tests(Path("/w"), {}, ["t.py"], agent_tree=True) == {}

    def launch_failure(*argv: str, **_: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 1, "", "bwrap: Can't find source path")

    monkeypatch.setattr(tasks, "sh", launch_failure)
    with pytest.raises(tasks.TestRunError):
        tasks.tests(Path("/w"), {}, ["t.py"], agent_tree=True)


def test_failed_tests_stay_scoreable(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_sh(*argv: str, **_: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            argv,
            1,
            "FAILED tests/test_a.py::test_x\nPASSED tests/test_a.py::test_y\n",
            "",
        )

    monkeypatch.setattr(tasks, "sh", fake_sh)
    assert tasks.tests(Path("/w"), {}, ["tests/test_a.py"]) == {
        "tests/test_a.py::test_x": "FAILED",
        "tests/test_a.py::test_y": "PASSED",
    }


def test_a_missing_heldout_set_names_the_generator(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(tasks, "HELDOUT", tmp_path / "heldout-tasks.json")
    with pytest.raises(FileNotFoundError, match=r"heldout\.py"):
        tasks.load("heldout")
