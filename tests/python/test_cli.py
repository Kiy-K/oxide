"""The ``oxide`` CLI in subprocesses, each with an explicit private data location."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from importlib.metadata import version
from pathlib import Path

import pytest
from conftest import FIXTURE, env

import oxide.storage.ladybug as storage
from oxide.cli import main
from oxide.models import InvalidArgument, NotIndexed, advice


def oxide(
    data: Path, *args: str, extra: dict[str, str] | None = None, cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, "-m", "oxide", *args]
    environment = env(data) | (extra or {})
    return subprocess.run(
        command, env=environment, capture_output=True, encoding="utf-8", check=False, cwd=cwd
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


def test_terminal_colors_respect_plain_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(sys.stderr, "isatty", lambda: True)
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm")
    args = ["status", str(tmp_path), "--data-dir", str(tmp_path / "data")]
    assert main(args) == 0
    assert "\x1b[" in capsys.readouterr().out
    assert main([*args, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "unindexed"
    monkeypatch.setenv("NO_COLOR", "1")
    assert main(args) == 0
    assert "\x1b[" not in capsys.readouterr().out
    monkeypatch.delenv("NO_COLOR")
    monkeypatch.setenv("TERM", "dumb")
    assert main(args) == 0
    assert "\x1b[" not in capsys.readouterr().out
    monkeypatch.setenv("TERM", "xterm")
    assert main(["status", str(tmp_path / "missing")]) == 2
    assert "\x1b[" in capsys.readouterr().err


def test_version_is_the_installed_distribution_version(tmp_path: Path) -> None:
    for flag in ("--version", "-V"):
        result = oxide(tmp_path / "data", flag)
        assert result.returncode == 0 and result.stdout == f"oxide {version('oxide-agent')}\n"


def test_bare_oxide_lists_the_commands_instead_of_failing(tmp_path: Path) -> None:
    result = oxide(tmp_path / "data")
    assert result.returncode == 0 and result.stderr == ""
    assert all(name in result.stdout for name in ("index", "search", "context", "mcp"))


def test_a_mistyped_command_names_the_closest_one(tmp_path: Path) -> None:
    result = oxide(tmp_path / "data", "serch", ".", "x")
    assert result.returncode == 2 and re.search(r"mean(?:t)? 'search'", result.stderr)
    unrelated = oxide(tmp_path / "data", "zzzz")
    assert unrelated.returncode == 2 and "mean" not in unrelated.stderr


def test_index_without_a_path_indexes_the_current_directory(
    tmp_path: Path, repo_copy: Path, fts: Path
) -> None:
    data = tmp_path / "cli-data"
    result = oxide(data, "index", "--data-dir", str(data), cwd=repo_copy)
    assert result.returncode == 0 and result.stdout.startswith("built:"), result.stderr
    found = oxide(data, "search", str(repo_copy), "RetryPolicy", "--data-dir", str(data))
    assert found.returncode == 0 and "RetryPolicy" in found.stdout


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
    expected = (
        "hint: run oxide index for the same repository and data location first"
        if sys.platform == "win32"
        else f"hint: run `oxide index {FIXTURE} --data-dir {data}` first"
    )
    assert expected in unindexed.stderr
    assert oxide(data, "index", *target).returncode == 0
    unknown = oxide(data, "impact", *target, "symbol:nope")
    assert unknown.returncode == 2 and "error[invalid_argument]" in unknown.stderr
    assert "find keys with oxide search" in unknown.stderr
    empty = oxide(data, "search", *target, "")
    assert empty.returncode == 2 and "query is empty" in empty.stderr


def test_a_full_read_pool_exits_2_with_its_own_code_and_a_retry_hint(
    repo_copy: Path,
    tmp_path: Path,
    fts: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # In process: the pool size is a module value, and a subprocess cannot shrink it.
    target = (str(repo_copy), "--data-dir", str(tmp_path / "cli-data"))
    assert main(["index", *target]) == 0
    capsys.readouterr()
    with monkeypatch.context() as patch:
        patch.setattr(storage, "BUFFER_POOL", 64 << 10)
        assert main(["search", *target, "RetryPolicy"]) == 2
    err = capsys.readouterr().err
    assert err.startswith("error[resource_exhausted]:") and "buffer pool is full" in err
    assert "hint: " in err and "retry" in err and "oxide index" not in err
    assert main(["search", *target, "RetryPolicy"]) == 0  # intact: the next run just works


def test_advice_is_a_copy_pasteable_recovery_command() -> None:
    error = NotIndexed("not indexed")
    assert advice(error, "/tmp/my repo") == 'run `oxide index "/tmp/my repo"` first'
    assert advice(error, "/repo", "/data") == "run `oxide index /repo --data-dir /data` first"
    assert advice(error, "/repo", private_data_dir=True) == (
        "run oxide index for the same repository using the server's data location first"
    )
    assert advice(InvalidArgument("bad"), "/repo") is None


@pytest.mark.parametrize("path", ["/tmp/repo;echo_pwn", "/tmp/$HOME", '/tmp/a"b', "-repo"])
def test_advice_does_not_emit_unsafe_shell_paths(path: str) -> None:
    hint = advice(NotIndexed("missing"), path, "/tmp/data")
    assert hint == "run oxide index for the same repository and data location first"


def test_advice_does_not_emit_unsafe_data_location() -> None:
    hint = advice(NotIndexed("missing"), "/tmp/repo", "/tmp/$(touch pwn)")
    assert hint == "run oxide index for the same repository and data location first"


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


def test_index_and_impact_actionable_hints() -> None:
    from oxide.cli import _impact_summary, _index_summary
    from oxide.models import Entity, Impact, IndexReport, Skip, Snapshot

    snap = Snapshot("r", "s", "d")
    report = IndexReport(
        snap,
        "unchanged",
        1,
        2,
        3,
        ("x.py",),
        0,
        (Skip("link", "symlink"),),
        (("A", "a"),),
        "ran",
        0.0,
    )
    assert _index_summary(report) == (
        "unchanged: 1 files, 2 entities, 3 relations in 0.00s; lexical ran\n"
        "partial parses: 1 (x.py)\n"
        "hint: inspect parse diagnostics with oxide status --verbose for the same repository "
        "and data location\n"
        "skipped: 1 (link [symlink])\n"
        "hint: review skipped files with oxide status --verbose for the same repository "
        "and data location\n"
        "case collisions: A = a\nhint: rename conflicting files, then re-index"
    )
    root = Entity("symbol:x#f", "symbol", "function", "f", None, None, False)
    for stop, hint in (
        ("depth", "hint: consider raising --depth"),
        ("limit", "hint: consider raising --limit"),
        ("edges", "hint: traversal hit an internal edge bound"),
        (None, ""),
    ):
        impact = Impact(snap, root, "upstream", (root.key,), (), 0, stop)
        expected = (
            f"upstream of {root.key}: 0 reached; stopped by {stop or 'none'}; "
            "unresolved call sites 0"
        )
        assert _impact_summary(impact, 3, 200) == expected + (f"\n{hint}" if hint else "")
    for stop, hint in (
        ("depth", "hint: --depth is already at its maximum (8)"),
        ("limit", "hint: --limit is already at its maximum (1000)"),
    ):
        impact = Impact(snap, root, "upstream", (root.key,), (), 0, stop)
        assert _impact_summary(impact, 8, 1000).endswith(hint)


def test_search_truncation_and_context_degradations(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from dataclasses import replace

    from oxide.models import ChannelStatus, ContextBundle, RouteSummary, SearchResult, Snapshot

    snap = Snapshot("r", "s", "d")
    channel = ChannelStatus("ran", "fts", 0, True)
    search = SearchResult(snap, "q", (), channel, ())
    context = ContextBundle(
        snap,
        "q",
        100,
        0,
        "tokens",
        (),
        (),
        channel,
        (),
        RouteSummary(False, 0, 0, 0, 0, None),
        ("degraded", "judge fallback", "degraded"),
    )

    class Repo:
        read_only = False

        def __enter__(self) -> Repo:
            return self

        def __exit__(self, *_args: object) -> None:
            pass

        def search(self, *_args: object, **_kwargs: object) -> SearchResult:
            return search

        def build_context(self, *_args: object, **_kwargs: object) -> ContextBundle:
            return context

    monkeypatch.setattr("oxide.cli.open_repository", lambda *_a, **_kw: Repo())
    assert main(["search", ".", "q"]) == 0
    assert capsys.readouterr().out == (
        "lexical ran: fts\nhint: lexical results truncated; consider raising --limit\n"
    )
    assert main(["search", ".", "q", "--limit", "1000"]) == 0
    assert capsys.readouterr().out == (
        "lexical ran: fts\nhint: lexical results truncated at maximum --limit; narrow the query\n"
    )
    search = replace(search, lexical=replace(channel, truncated=False))
    assert main(["search", ".", "q"]) == 0
    assert capsys.readouterr().out == "lexical ran: fts\n"
    assert main(["context", ".", "q"]) == 0
    human = capsys.readouterr()
    assert human.err == "0/100 tokens\nwarning: degraded\njudge fallback\n"
    assert main(["context", ".", "q", "--json"]) == 0
    machine = capsys.readouterr()
    assert machine.err == "0/100 tokens\njudge fallback\n"
    assert json.loads(machine.out)["degradations"] == ["degraded", "judge fallback", "degraded"]
