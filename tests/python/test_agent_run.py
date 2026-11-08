"""Runner regressions: confined git, private traces, nested credentials, base-src, scoring."""

from __future__ import annotations

import argparse
import importlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

import pytest

SCRIPTS = str(Path(__file__).resolve().parents[2] / "scripts" / "agent_eval")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

arms: Any = importlib.import_module("arms")
batch: Any = importlib.import_module("batch")
events: Any = importlib.import_module("events")
run: Any = importlib.import_module("run")
session: Any = importlib.import_module("session")
tasks: Any = importlib.import_module("tasks")


def _git_missing(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess((), 1)


def test_base_src_package_snapshot_imports(tmp_path: Path) -> None:
    pkg = tmp_path / "oxide"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "__main__.py").write_text("print('base-ok')\n", encoding="utf-8")
    done = subprocess.run(
        arms.oxide_command("oxide-base", pkg, "--version"),
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert "base-ok" in done.stdout


def test_base_src_tree_containing_the_package_imports(tmp_path: Path) -> None:
    root = tmp_path / "src"
    pkg = root / "oxide"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "__main__.py").write_text("print('tree-ok')\n", encoding="utf-8")
    done = subprocess.run(
        arms.oxide_command("oxide-base", root), capture_output=True, text=True, check=False
    )
    assert done.returncode == 0, done.stderr
    assert "tree-ok" in done.stdout


def test_base_src_rejects_a_directory_that_is_not_the_package(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="src/oxide"):
        arms.check_base_src(tmp_path)


def test_the_manifest_builds_for_every_arm_without_a_base_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(batch, "runner_version", lambda runner: "test")
    args = argparse.Namespace(
        split="dev",
        reps=1,
        model="m",
        timeout=1,
        arms=list(arms.ARMS),
        base_src=None,
        runner="opencode",
    )
    assert batch.manifest(args, [{"id": "a"}])["tasks"] == 1


def test_nested_credential_json_is_extracted_for_redaction(tmp_path: Path) -> None:
    key, access, refresh = (
        "sk-live-0123456789abcdef",
        "access-token-0123",
        "refresh-token-0123",
    )
    db = tmp_path / "opencode.db"
    blob = json.dumps({"key": key, "access": access, "refresh": refresh})
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE credential (integration_id TEXT, value TEXT)")
        conn.execute("INSERT INTO credential VALUES ('openai', ?)", (blob,))
    secrets = run.credential_values(db)
    assert key in secrets and access in secrets and refresh in secrets
    assert key not in run.confine.redact(f"used {key} and {access}", secrets)


def test_a_plain_credential_string_is_still_extracted(tmp_path: Path) -> None:
    key = "sk-live-0123456789abcdef"
    db = tmp_path / "opencode.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE credential (integration_id TEXT, value TEXT)")
        conn.execute("INSERT INTO credential VALUES ('openai', ?)", (key,))
    assert key in run.credential_values(db)


def test_git_on_agent_tree_is_confined(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}
    ran: tuple[str, ...] = ()

    def confine_fn(argv: list[str], *, writable: Path, hide: object, net: bool) -> list[str]:
        seen["argv"] = list(argv)
        seen["net"] = net
        seen["writable"] = writable
        return ["bwrap", *map(str, argv)]

    def sh(*argv: str, **kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal ran
        ran = argv
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(session.confine, "confine", confine_fn)
    monkeypatch.setattr(run, "hidden", lambda: [])
    monkeypatch.setattr(session.tasks, "sh", sh)
    base = tmp_path / "base"
    work = base / "work"
    work.mkdir(parents=True)
    session.git_on_agent(base, "add", "-A", cwd=work)
    assert seen["argv"] == ["git", *session.GIT_SAFE, "add", "-A"]
    assert seen["net"] is False
    assert seen["writable"] == base
    assert ran[0] == "bwrap"


def test_score_confines_git_and_counts_a_missing_test_as_unresolved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, ...]] = []

    def git(base: Path, *args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(args, 0)

    def sh(*argv: str, **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert argv[0] != "git"
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(session, "git_on_agent", git)
    monkeypatch.setattr(session.tasks, "sh", sh)
    monkeypatch.setattr(session.tasks, "apply", lambda *args, **kwargs: None)
    monkeypatch.setattr(session.tasks, "tests", lambda *args, **kwargs: {"t": "PASSED"})
    task = {
        "test_files": ["t.py"],
        "test_patch": "",
        "fail_to_pass": ["t"],
        "pass_to_pass": ["missing"],
    }
    scored = session.score(task, tmp_path, {}, tmp_path)
    assert scored == {"resolved": False, "f2p_failed": 0, "p2p_broken": 1}
    assert calls[0][:2] == ("cat-file", "-e")
    assert "checkout" in calls[1]


def test_score_does_not_turn_a_test_runner_error_into_a_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(session, "git_on_agent", _git_missing)
    monkeypatch.setattr(session.tasks, "apply", lambda *args, **kwargs: None)

    def boom(*args: object, **kwargs: object) -> dict[str, str]:
        raise tasks.TestRunError("pytest timed out")

    monkeypatch.setattr(session.tasks, "tests", boom)
    task = {"test_files": [], "test_patch": "", "fail_to_pass": ["t"], "pass_to_pass": []}
    with pytest.raises(tasks.TestRunError, match="timed out"):
        session.score(task, tmp_path, {}, tmp_path)


def test_a_reported_test_failure_is_still_unresolved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(session, "git_on_agent", _git_missing)
    monkeypatch.setattr(session.tasks, "apply", lambda *args, **kwargs: None)
    monkeypatch.setattr(session.tasks, "tests", lambda *args, **kwargs: {"t": "FAILED"})
    task = {"test_files": [], "test_patch": "", "fail_to_pass": ["t"], "pass_to_pass": []}
    assert session.score(task, tmp_path, {}, tmp_path)["resolved"] is False


def test_trace_write_does_not_truncate_a_hardlink(tmp_path: Path) -> None:
    victim = tmp_path / "victim"
    victim.write_text("keep", encoding="utf-8")
    link = tmp_path / "patch.diff"
    os.link(victim, link)
    session.write_private(link, "diff")
    assert victim.read_text(encoding="utf-8") == "keep"
    assert link.read_text(encoding="utf-8") == "diff"


@pytest.mark.skipif(sys.platform == "win32", reason="creating a symlink needs extra privileges")
def test_run_one_confines_git_and_does_not_follow_trace_links(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    victim = tmp_path / "victim"
    victim.write_text("keep", encoding="utf-8")
    root = tmp_path / "runs"
    root.mkdir()
    monkeypatch.setattr(run.tasks, "CACHE", root)
    git_calls: list[tuple[str, ...]] = []

    def checkout(task: dict[str, str], work: Path) -> None:
        work.mkdir(parents=True)

    def agent(
        model: str, arm: str, task: dict[str, str], base: Path, env: dict[str, str], timeout: int
    ) -> tuple[str, str]:
        (base / "events.jsonl").symlink_to(victim)
        (base / "patch.diff").symlink_to(victim)
        return "ok", "trace-body"

    def git(base: Path, *args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
        git_calls.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="diff", stderr="")

    def sh(*argv: str, **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert argv[0] != "git"
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(run.tasks, "checkout", checkout)
    monkeypatch.setattr(run.tasks, "environment", lambda *args, **kwargs: {})
    monkeypatch.setattr(run, "credentials", lambda *args, **kwargs: None)
    monkeypatch.setattr(run, "agent", agent)
    monkeypatch.setattr(
        events,
        "measure",
        lambda raw: {
            "tokens": {"input": 1, "output": 0, "cache_read": 0, "cache_write": 0},
            "steps": 0,
            "calls": {},
        },
    )
    monkeypatch.setattr(session, "connected", lambda *args, **kwargs: 0)
    monkeypatch.setattr(session, "isolation", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        session,
        "score",
        lambda *args, **kwargs: {"resolved": True, "f2p_failed": 0, "p2p_broken": 0},
    )
    monkeypatch.setattr(session, "git_on_agent", git)
    monkeypatch.setattr(run.tasks, "sh", sh)
    args = argparse.Namespace(
        runner="opencode", model="openai/m", split="dev", base_src=None, timeout=1
    )
    row = run.run_one({"id": "t", "problem": "p"}, "control", 0, args, root)
    assert ("add", "-A") in git_calls
    assert victim.read_text(encoding="utf-8") == "keep"
    base = root / "t.control.0"
    assert (base / "events.jsonl").read_text(encoding="utf-8") == "trace-body"
    assert (base / "patch.diff").read_text(encoding="utf-8") == "diff"
    assert row["resolved"] is True


@pytest.mark.skipif(shutil.which("bwrap") is None, reason="bubblewrap not installed")
def test_a_planted_git_clean_filter_cannot_write_outside_the_confined_tree() -> None:
    # Under $HOME, not /tmp: confine mounts a tmpfs over /tmp, which would hide the marker.
    scratch = Path.home() / ".cache" / f"oxide-filter-test-{uuid.uuid4().hex}"
    base, marker = scratch / "base", scratch / "escaped"
    work = base / "work"
    work.mkdir(parents=True)
    try:
        env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}
        for argv in (["init", "-q"], ["config", "filter.plant.clean", f"touch {marker}; cat"]):
            subprocess.run(["git", *argv], cwd=work, check=True, env=env)
        (work / ".gitattributes").write_text("* filter=plant\n", encoding="utf-8")
        (work / "f.txt").write_text("x\n", encoding="utf-8")
        # Control: unconfined, the same tree runs the planted filter on the host.
        subprocess.run(["git", *session.GIT_SAFE, "add", "-A"], cwd=work, check=True, env=env)
        assert marker.exists()
        marker.unlink()
        (work / ".git" / "index").unlink()
        session.git_on_agent(base, "add", "-A", cwd=work)
        assert not marker.exists()
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


@pytest.mark.skipif(shutil.which("bwrap") is None, reason="bubblewrap not installed")
def test_a_planted_clean_filter_does_not_run_on_the_host_when_the_test_patch_is_applied() -> None:
    scratch = Path.home() / ".cache" / f"oxide-apply-test-{uuid.uuid4().hex}"
    base, marker = scratch / "base", scratch / "escaped"
    work = base / "work"
    work.mkdir(parents=True)
    patch = "--- a/f.txt\n+++ b/f.txt\n@@ -1 +1 @@\n-x\n+y\n"
    try:
        env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}
        for argv in (["init", "-q"], ["config", "filter.plant.clean", f"touch {marker}; cat"]):
            subprocess.run(["git", *argv], cwd=work, check=True, env=env)
        (work / ".gitattributes").write_text("* filter=plant\n", encoding="utf-8")
        (work / "f.txt").write_text("x\n", encoding="utf-8")
        tasks.apply(work, patch)  # control: unconfined, the filter runs on the host
        assert marker.exists()
        marker.unlink()
        (work / "f.txt").write_text("x\n", encoding="utf-8")
        tasks.apply(work, patch, wrap=session.confined(base, net=False), scratch=base)
        assert not marker.exists()
        assert (work / "f.txt").read_text(encoding="utf-8") == "y\n"
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def test_a_test_run_error_is_a_harness_error_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "runs"
    root.mkdir()
    monkeypatch.setattr(tasks, "CACHE", root)
    monkeypatch.setattr(tasks, "checkout", lambda task, work: work.mkdir(parents=True))
    monkeypatch.setattr(tasks, "environment", lambda *args, **kwargs: {})
    monkeypatch.setattr(run, "credentials", lambda *args, **kwargs: None)
    monkeypatch.setattr(run, "agent", lambda *args, **kwargs: ("ok", ""))
    monkeypatch.setattr(
        events,
        "measure",
        lambda raw: {
            "tokens": {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0},
            "steps": 0,
            "calls": {},
        },
    )
    monkeypatch.setattr(session, "connected", lambda *args, **kwargs: 0)
    monkeypatch.setattr(session, "isolation", lambda *args, **kwargs: None)

    def confined_git(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess((), 0, stdout="")

    monkeypatch.setattr(session, "git_on_agent", confined_git)

    def fail(*args: object, **kwargs: object) -> dict[str, object]:
        raise tasks.TestRunError("pytest timed out")

    monkeypatch.setattr(session, "score", fail)
    args = argparse.Namespace(
        runner="opencode", model="openai/m", split="dev", base_src=None, timeout=1
    )
    row = run.run_one({"id": "t", "problem": "p"}, "control", 0, args, root)
    assert row["status"] == "harness-error"
    assert row["resolved"] is None
    assert "timed out" in row["error"]
