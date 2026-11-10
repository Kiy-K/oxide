"""The ``oxide`` CLI in subprocesses, each with an explicit private data location."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

from conftest import FIXTURE, env

from oxide.models import InvalidArgument, NotIndexed, advice


def oxide(
    data: Path, *args: str, extra: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, "-m", "oxide", *args]
    environment = env(data) | (extra or {})
    return subprocess.run(
        command, env=environment, capture_output=True, encoding="utf-8", check=False
    )


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
    assert lines[2] == "sources: 0 parsed, 7 reused"
    assert second.stderr.splitlines()[0].startswith("oxide: capturing ")
    assert "oxide: publishing" in second.stderr


def test_output_is_utf_8_whatever_the_stream_encoding(tmp_path: Path) -> None:
    # Windows pipes default to the ANSI code page; latin-1 stands in for it here.
    repo = tmp_path / "東京 café"
    shutil.copytree(FIXTURE, repo)
    data = tmp_path / "data"
    run = oxide(
        data, "index", str(repo), "--data-dir", str(data), extra={"PYTHONIOENCODING": "latin-1"}
    )
    assert run.returncode == 0, run.stderr
    assert f"oxide: capturing {repo}" in run.stderr


def test_impact_lists_dependents_as_text_and_json(tmp_path: Path, fts: Path) -> None:
    data = tmp_path / "cli-data"
    target = (str(FIXTURE), "--data-dir", str(data))
    assert oxide(data, "index", *target).returncode == 0
    key = "symbol:oxidepy/auth.py#decode_claims"
    test = "symbol:tests/test_auth.py#test_decode_claims_parses_payload"
    text = oxide(data, "impact", *target, key)
    assert text.returncode == 0 and test in text.stdout, text.stderr
    dependents = json.loads(oxide(data, "impact", *target, key, "--json").stdout)
    assert test in {h["entity"]["key"] for h in dependents["hops"]}
    assert dependents["root"]["key"] == key


def test_impact_and_empty_query_failures_exit_2_with_advice(tmp_path: Path, fts: Path) -> None:
    data = tmp_path / "cli-data"
    target = (str(FIXTURE), "--data-dir", str(data))
    unindexed = oxide(data, "search", *target, "x")
    assert unindexed.returncode == 2 and "error[not_indexed]" in unindexed.stderr
    assert f"hint: run `oxide index {FIXTURE} --data-dir {data}` first" in unindexed.stderr
    assert oxide(data, "index", *target).returncode == 0
    unknown = oxide(data, "impact", *target, "symbol:nope")
    assert unknown.returncode == 2 and "error[invalid_argument]" in unknown.stderr
    assert "find keys with oxide search" in unknown.stderr
    empty = oxide(data, "search", *target, "")
    assert empty.returncode == 2 and "query is empty" in empty.stderr


def test_advice_is_a_copy_pasteable_recovery_command() -> None:
    error = NotIndexed("not indexed")
    assert advice(error, "/tmp/my repo") == 'run `oxide index "/tmp/my repo"` first'
    assert advice(error, "/repo", "/data") == "run `oxide index /repo --data-dir /data` first"
    assert advice(error, "/repo", private_data_dir=True) == (
        "run `oxide index /repo --data-dir <the server's data location>` first"
    )
    assert advice(InvalidArgument("bad"), "/repo") is None


def test_search_and_context_show_hint_issues(tmp_path: Path, fts: Path) -> None:
    data = tmp_path / "cli-data"
    target = (str(FIXTURE), "--data-dir", str(data))
    assert oxide(data, "index", *target).returncode == 0
    hinted = ("RetryPolicy", "--symbol", "NoSuchThing")
    found = oxide(data, "search", *target, *hinted)
    context = oxide(data, "context", *target, *hinted)
    issues = json.loads(oxide(data, "search", *target, *hinted, "--json").stdout)["hints"]
    assert found.returncode == 0 and context.returncode == 0
    shown = [line for line in found.stdout.splitlines() if line.startswith("hint: ")]
    assert shown == [f"hint: {i['kind']} {i['hint']}: {i['issue']}" for i in issues]
    assert shown == ["hint: symbol NoSuchThing: not_found"]
    notes = [line for line in context.stderr.splitlines() if line.startswith("hint: ")]
    assert notes == shown


def test_search_and_context_print_the_blast_radius_of_their_entries(
    tmp_path: Path, fts: Path
) -> None:
    data = tmp_path / "cli-data"
    target = (str(FIXTURE), "--data-dir", str(data))
    assert oxide(data, "index", *target).returncode == 0
    found = oxide(data, "search", *target, "RetryPolicy")
    assert "blast symbol:oxidepy/retry.py#RetryPolicy: " in found.stdout, found.stdout
    context = oxide(data, "context", *target, "RetryPolicy", "--budget", "300")
    assert "blast symbol:oxidepy/retry.py#RetryPolicy: " in context.stderr, context.stderr
