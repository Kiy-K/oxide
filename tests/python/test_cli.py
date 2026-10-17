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
    assert not data.exists()  # a read leaves no empty store behind


def test_expected_errors_never_print_a_traceback(tmp_path: Path, fts: Path) -> None:
    data = tmp_path / "cli-data"
    target = (str(FIXTURE), "--data-dir", str(data))
    assert oxide(data, "index", *target).returncode == 0
    for args, code in (
        (("search", *target, "x", "--limit", "-5"), "error[invalid_argument]:"),
        (("search", *target, "x", "--limit", "99999"), "error[invalid_argument]:"),
        (("context", *target, "x", "--budget", "-1"), "error[invalid_argument]:"),
        (("search", *target, "x", "--limit", "many"), "usage:"),
        (("index", str(tmp_path / "missing"), "--data-dir", str(data)), "error[invalid_path]:"),
    ):
        result = oxide(data, *args)
        assert result.returncode == 2, (args, result.stderr)
        assert code in result.stderr and "Traceback" not in result.stderr, result.stderr


def test_a_damaged_store_is_a_stable_error_then_rebuilt(tmp_path: Path, fts: Path) -> None:
    data = tmp_path / "cli-data"
    target = (str(FIXTURE), "--data-dir", str(data))
    assert oxide(data, "index", *target).returncode == 0
    (graph,) = data.glob("repos/*/generations/*/graph.lbug")
    graph.write_bytes(b"\0" * graph.stat().st_size)
    result = oxide(data, "search", *target, "RetryPolicy")
    assert result.returncode == 2 and result.stderr.startswith("error[store_corrupt]:")
    assert "Traceback" not in result.stderr
    rebuilt = oxide(data, "index", *target)
    assert rebuilt.returncode == 0 and rebuilt.stdout.startswith("built:"), rebuilt.stderr


def test_index_reports_progress_changes_and_reuse(tmp_path: Path, repo_copy: Path) -> None:
    data = tmp_path / "cli-data"
    target = (str(repo_copy), "--data-dir", str(data))
    first = oxide(data, "index", *target)
    assert first.returncode == 0, first.stderr
    assert "changes (no readable previous index): 11 added" in first.stdout
    (repo_copy / "oxidepy" / "cache.py").unlink()
    second = oxide(data, "index", *target)
    lines = second.stdout.splitlines()
    assert lines[0].startswith("built:")
    assert (
        lines[1]
        == "changes since the last index: 0 added; 0 modified; 1 deleted (oxidepy/cache.py)"
    )
    assert lines[2] == "python: 0 parsed, 7 reused"
    assert second.stderr.splitlines()[0].startswith("oxide: capturing ")
    assert "oxide: publishing" in second.stderr
