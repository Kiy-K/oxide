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
