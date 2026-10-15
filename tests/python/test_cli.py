"""The ``oxide`` CLI in subprocesses, each with an explicit private data location."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from conftest import FIXTURE, env


def oxide(data: Path, *args: str) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, "-m", "oxide", *args]
    return subprocess.run(command, env=env(data), capture_output=True, text=True, check=False)


def test_index_search_context_round_trip(tmp_path: Path, fts: Path) -> None:
    data = tmp_path / "cli-data"
    target = (str(FIXTURE), "--data-dir", str(data))
    indexed = oxide(data, "index", *target, "--json")
    assert indexed.returncode == 0, indexed.stderr
    assert json.loads(indexed.stdout)["status"] == "built"
    found = oxide(data, "search", *target, "RetryPolicy", "--json")
    assert json.loads(found.stdout)["entries"][0]["entity"]["key"] == (
        "symbol:oxidepy/retry.py#RetryPolicy"
    )
    context = oxide(data, "context", *target, "RetryPolicy", "--budget", "300")
    assert context.returncode == 0 and context.stdout.startswith("### oxidepy/retry.py:")
    used, budget = context.stderr.split()[0].split("/")
    assert int(used) <= int(budget) == 300
    assert (data / "repos").is_dir()


def test_errors_exit_2_with_a_stable_code(tmp_path: Path) -> None:
    data = tmp_path / "cli-data"
    result = oxide(data, "search", str(FIXTURE), "x", "--data-dir", str(data))
    assert result.returncode == 2
    assert result.stderr.startswith("error[not_indexed]:")
