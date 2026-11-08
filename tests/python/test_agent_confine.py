"""The agent's process is confined: no host secrets in its environment, the answers and other runs
hidden from its filesystem, and its kept traces free of the credential it used. Behaviour is
checked with the real bubblewrap where it is installed, and the rest with pure functions."""

from __future__ import annotations

import importlib
import shutil
import sqlite3
import subprocess
import sys
import uuid
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

SCRIPTS = str(Path(__file__).resolve().parents[2] / "scripts" / "agent_eval")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

confine: ModuleType = importlib.import_module("confine")
run: ModuleType = importlib.import_module("run")
tasks: ModuleType = importlib.import_module("tasks")

BWRAP = pytest.mark.skipif(shutil.which("bwrap") is None, reason="bubblewrap not installed")


@contextmanager
def home_scratch() -> Generator[Path, None, None]:
    """A directory under $HOME, not /tmp: confine mounts a tmpfs over /tmp, which would hide it
    for the wrong reason."""
    path = Path.home() / ".cache" / f"oxide-confine-test-{uuid.uuid4().hex}"
    path.mkdir(parents=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def test_the_agent_environment_holds_no_host_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-0123456789abcdef")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_0123456789abcdef")
    env = confine.clean_env({"HOME": "/h"})
    assert "OPENAI_API_KEY" not in env and "GITHUB_TOKEN" not in env
    assert env["HOME"] == "/h"


def test_the_runner_passes_the_agent_only_the_clean_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-0123456789abcdef")
    seen: dict[str, Any] = {}

    def fake_launch(
        command: list[str], cwd: Path, environment: dict[str, str], timeout: int
    ) -> tuple[str, str]:
        seen["environment"] = environment
        return "ok", ""

    monkeypatch.setattr(run, "launch", fake_launch)
    monkeypatch.setattr(run, "credentials", lambda *_: None)
    monkeypatch.setattr(run, "credential_values", lambda *_: [])
    monkeypatch.setattr(run, "forget_credentials", lambda *_: None)
    base = tmp_path / "base"
    (base / "work").mkdir(parents=True)
    run.agent("openai/m", "control", {"problem": "p"}, base, {"PATH": "/venv/bin"}, 1)
    assert "ANTHROPIC_API_KEY" not in seen["environment"]


def test_without_bubblewrap_no_agent_is_started(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", "/nonexistent")
    with pytest.raises(SystemExit, match="bubblewrap"):
        confine.confine(["true"], writable=Path("/tmp"), hide=(), net=False)


@BWRAP
def test_a_hidden_path_cannot_be_read_and_the_writable_one_can() -> None:
    with home_scratch() as root:
        answers = root / "answers"
        answers.mkdir()
        (answers / "gold.patch").write_text("secret", encoding="utf-8")
        work = root / "work"
        work.mkdir()
        read = confine.confine(
            [sys.executable, "-c", "import sys; open(sys.argv[1]).read()", answers / "gold.patch"],
            writable=work,
            hide=(answers,),
            net=False,
        )
        assert subprocess.run(read, capture_output=True).returncode != 0
        write = confine.confine(
            [sys.executable, "-c", "import sys; open(sys.argv[1], 'w').write('ok')", work / "f"],
            writable=work,
            hide=(answers,),
            net=False,
        )
        assert subprocess.run(write, capture_output=True).returncode == 0
        assert (work / "f").read_text(encoding="utf-8") == "ok"


@BWRAP
def test_a_path_outside_the_writable_dir_cannot_be_written() -> None:
    with home_scratch() as root:
        work, other = root / "work", root / "other"
        work.mkdir()
        other.mkdir()
        argv = confine.confine(
            [sys.executable, "-c", "import sys; open(sys.argv[1], 'w')", other / "x"],
            writable=work,
            hide=(),
            net=False,
        )
        assert subprocess.run(argv, capture_output=True).returncode != 0


def test_redaction_removes_a_secret_from_kept_text() -> None:
    key = "sk-live-0123456789abcdef"
    assert confine.redact(f"auth {key} done", [key]) == "auth [redacted] done"


def test_forgetting_credentials_empties_the_kept_copy(tmp_path: Path) -> None:
    db = tmp_path / "opencode.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE credential (integration_id TEXT, value TEXT)")
        conn.execute("INSERT INTO credential VALUES ('openai', 'sk-live-0123456789abcdef')")
    run.forget_credentials(db)
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT count(*) FROM credential").fetchone()[0] == 0


def test_forgetting_credentials_leaves_no_key_bytes_in_the_kept_file(tmp_path: Path) -> None:
    key = "sk-live-0123456789abcdef-tail"
    db = tmp_path / "opencode.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE credential (integration_id TEXT, value TEXT)")
        conn.execute("INSERT INTO credential VALUES ('openai', ?)", (key,))
    run.forget_credentials(db)
    assert key.encode() not in db.read_bytes()


def test_the_hidden_paths_cover_git_history_and_every_checkout_task_files(tmp_path: Path) -> None:
    repo, other = tmp_path / "repo", tmp_path / "other"
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run([*git, "init", "-q", str(repo)], check=True)
    subprocess.run([*git, "-C", str(repo), "commit", "-q", "--allow-empty", "-m", "i"], check=True)
    subprocess.run([*git, "-C", str(repo), "worktree", "add", "-q", str(other)], check=True)
    hidden = {p.resolve() for p in run.hidden(other)}
    assert (repo / ".git").resolve() in hidden  # the shared history a linked checkout points at
    assert (repo / "fixtures" / "eval").resolve() in hidden
    assert (other / "fixtures" / "eval").resolve() in hidden


@BWRAP
def test_both_runners_host_credential_stores_are_unreadable_by_the_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with home_scratch() as root:
        work = root / "work"
        work.mkdir()
        stores = [
            root / "home" / ".codex" / "auth.json",
            root / "home" / ".local" / "share" / "opencode" / "opencode.db",
        ]
        for store in stores:
            store.parent.mkdir(parents=True)
            store.write_text("credential", encoding="utf-8")
        monkeypatch.setenv("HOME", str(root / "home"))
        for store in stores:
            probe = confine.confine(
                ["cat", str(store)], writable=work, hide=run.hidden(), net=False
            )
            assert subprocess.run(probe, capture_output=True).returncode != 0


@BWRAP
def test_the_answer_files_are_unreadable_by_a_confined_agent() -> None:
    with home_scratch() as work:
        probe = confine.confine(
            ["cat", str(tasks.TASKS)], writable=work, hide=run.hidden(), net=False
        )
        assert subprocess.run(probe, capture_output=True).returncode != 0


@BWRAP
def test_a_run_directory_under_the_hidden_cache_stays_writable() -> None:
    base = tasks.CACHE / "runs" / f"confine-test-{uuid.uuid4().hex}"
    base.mkdir(parents=True)
    try:
        write = confine.confine(
            ["sh", "-c", f"echo ok > {base / 'out.txt'}"],
            writable=base,
            hide=run.hidden(),
            net=False,
        )
        assert subprocess.run(write, capture_output=True).returncode == 0
        assert (base / "out.txt").read_text(encoding="utf-8").strip() == "ok"
    finally:
        shutil.rmtree(base, ignore_errors=True)
