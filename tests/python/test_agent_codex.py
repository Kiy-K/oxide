"""The Codex runner's parsers, on event lines captured from ``codex exec --json`` (codex-cli
0.165.0). No model is called: this checks how the harness reads a run, not the agent."""

from __future__ import annotations

import importlib
import json
import sys
import tomllib
from pathlib import Path
from types import ModuleType

import pytest

SCRIPTS = str(Path(__file__).resolve().parents[2] / "scripts" / "agent_eval")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

codex: ModuleType = importlib.import_module("codex")
protocol: ModuleType = importlib.import_module("protocol")
run: ModuleType = importlib.import_module("run")

FTS = Path("/cache/libfts.lbug_extension")

MCP_ITEM = {
    "id": "item_1",
    "type": "mcp_tool_call",
    "server": "oxide",
    "tool": "oxide_search",
    "arguments": {"query": "parse_budget", "mode": "search"},
    "result": {"content": [{"type": "text", "text": '{"entries_returned": 1}'}]},
    "error": None,
    "status": "completed",
}

OK_RUN = "\n".join(
    json.dumps(event)
    for event in (
        {"type": "thread.started", "thread_id": "t"},
        {"type": "turn.started"},
        {"type": "item.completed", "item": MCP_ITEM},
        {
            "type": "item.completed",
            "item": {"id": "item_2", "type": "agent_message", "text": "cfg.py"},
        },
        {
            "type": "turn.completed",
            "usage": {
                "input_tokens": 44326,
                "cached_input_tokens": 39936,
                "cache_write_input_tokens": 0,
                "output_tokens": 100,
                "reasoning_output_tokens": 0,
            },
        },
    )
)

MODEL_REFUSED = "\n".join(
    json.dumps(event)
    for event in (
        {"type": "thread.started", "thread_id": "t"},
        {"type": "turn.started"},
        {"type": "error", "message": "The 'no-such-model-xyz' model is not supported."},
        {
            "type": "turn.failed",
            "error": {"message": "The 'no-such-model-xyz' model is not supported."},
        },
    )
)


def test_cached_input_is_billed_once_and_the_spend_equals_the_reported_tokens() -> None:
    measured = codex.codex_measure(OK_RUN)
    assert measured["tokens"] == {
        "input": 44326 - 39936,
        "output": 100,
        "cache_read": 39936,
        "cache_write": 0,
    }
    assert protocol.spend(measured["tokens"]) == 44326 + 100


def test_only_completed_items_count_as_calls_and_messages_do_not() -> None:
    measured = codex.codex_measure(OK_RUN)
    assert measured["calls"] == {"oxide_search": 1}
    assert measured["steps"] == 1


def test_a_failed_turn_is_reported_as_the_agent_failure() -> None:
    assert codex.codex_agent_error(MODEL_REFUSED) == (
        "The 'no-such-model-xyz' model is not supported."
    )


def test_an_error_inside_a_turn_that_completes_is_not_a_failure() -> None:
    retried = OK_RUN.replace(
        '{"type": "turn.started"}',
        '{"type": "turn.started"}\n{"type": "error", "message": "reconnecting"}',
    )
    assert codex.codex_agent_error(retried) is None


def test_usage_completeness_requires_terminal_usage() -> None:
    assert codex.codex_measure(OK_RUN)["usage_complete"] is True
    assert codex.codex_measure('{"type":"thread.started"}')["usage_complete"] is False
    assert codex.codex_measure('{"type":"turn.completed"}')["usage_complete"] is False
    assert codex.codex_measure(OK_RUN + '\n{"type":"turn.failed"}')["usage_complete"] is False


def test_the_arm_config_names_only_the_oxide_server_and_control_names_none() -> None:
    command = ["/venv/bin/python", "-m", "oxide", "mcp", "--repo", "/w", "--data-dir", "/d"]
    parsed = tomllib.loads(codex.codex_config(command, FTS))
    assert parsed == {
        "mcp_servers": {
            "oxide": {
                "command": "/venv/bin/python",
                "args": ["-m", "oxide", "mcp", "--repo", "/w", "--data-dir", "/d"],
                "env": {"OXIDE_FTS_EXTENSION": str(FTS)},
            }
        }
    }
    assert codex.codex_config([], FTS) == ""


def test_a_tool_call_from_another_server_fails_isolation() -> None:
    foreign = OK_RUN.replace('"server": "oxide"', '"server": "github"')
    with pytest.raises(RuntimeError, match="isolation"):
        codex.codex_isolation("oxide", foreign)
    with pytest.raises(RuntimeError, match="isolation"):
        codex.codex_isolation("control", OK_RUN)
    codex.codex_isolation("oxide", OK_RUN)


POSIX = pytest.mark.skipif(sys.platform == "win32", reason="pinned directory descriptors are POSIX")


def test_codex_takes_the_bare_openai_model_id_and_refuses_other_providers() -> None:
    assert codex.codex_model("openai/gpt-6-luna") == "gpt-6-luna"
    with pytest.raises(SystemExit, match="openai"):
        codex.codex_model("opencode/gpt-6-luna")
    with pytest.raises(SystemExit, match="openai"):
        codex.codex_model("gpt-6-luna")


@POSIX
def test_the_copied_login_is_removed_after_the_run_even_when_it_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    login = tmp_path / "auth.json"
    login.write_text('{"token": "not-a-real-login"}', encoding="utf-8")
    monkeypatch.setattr(codex, "CODEX_AUTH", login)
    monkeypatch.setattr(run, "fts_location", lambda: FTS)
    monkeypatch.setattr(codex.confine, "confine", lambda argv, **_: list(argv))

    def launch_fails(*_: object) -> tuple[str, str]:
        raise RuntimeError("launch failed")

    monkeypatch.setattr(run, "launch", launch_fails)
    base = tmp_path / "run"
    with pytest.raises(RuntimeError, match="launch failed"):
        codex.codex_agent("openai/gpt-6-luna", "oxide", {"problem": "p"}, base, {}, 1, [])
    assert not (base / "codex" / "auth.json").exists()
    assert (base / "codex" / "config.toml").exists()


@pytest.mark.skipif(sys.platform == "win32", reason="requires symlinks")
def test_codex_cleanup_pins_login_when_parent_is_replaced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    login = tmp_path / "login.json"
    login.write_text('{"token": "secret-value"}', encoding="utf-8")
    monkeypatch.setattr(codex, "CODEX_AUTH", login)
    monkeypatch.setattr(run, "fts_location", lambda: FTS)
    monkeypatch.setattr(run, "hidden", lambda: [])
    monkeypatch.setattr(codex.confine, "confine", lambda argv, **_: argv)
    base = tmp_path / "run"
    moved = base / "moved"
    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / "auth.json").write_text("untouched", encoding="utf-8")

    def launch(*_: object) -> tuple[str, str]:
        (base / "codex").rename(moved)
        (base / "codex").symlink_to(victim, target_is_directory=True)
        return "ok", ""

    monkeypatch.setattr(run, "launch", launch)
    codex.codex_agent("openai/model", "control", {"problem": "p"}, base, {}, 1, [])
    assert (victim / "auth.json").read_text() == "untouched"
    assert not (moved / "auth.json").exists()


@POSIX
def test_codex_cleanup_does_not_truncate_replacement_hardlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    login = tmp_path / "login.json"
    login.write_text('{"token": "secret-value"}', encoding="utf-8")
    monkeypatch.setattr(codex, "CODEX_AUTH", login)
    monkeypatch.setattr(run, "fts_location", lambda: FTS)
    monkeypatch.setattr(run, "hidden", lambda: [])
    monkeypatch.setattr(codex.confine, "confine", lambda argv, **_: argv)
    base = tmp_path / "run"
    victim = tmp_path / "victim"
    victim.write_text("untouched", encoding="utf-8")
    moved = base / "codex" / "moved.json"

    def launch(*_: object) -> tuple[str, str]:
        auth = base / "codex" / "auth.json"
        auth.rename(moved)
        auth.hardlink_to(victim)
        return "ok", ""

    monkeypatch.setattr(run, "launch", launch)
    codex.codex_agent("openai/model", "control", {"problem": "p"}, base, {}, 1, [])
    assert victim.read_text() == "untouched"
    assert b"secret-value" not in moved.read_bytes()


@POSIX
def test_codex_removes_the_login_when_writing_it_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    login = tmp_path / "login.json"
    login.write_text('{"token": "secret-value"}', encoding="utf-8")
    monkeypatch.setattr(codex, "CODEX_AUTH", login)
    monkeypatch.setattr(run, "fts_location", lambda: FTS)

    def write_fails(*_: object, **__: object) -> None:
        raise OSError("write failed")

    monkeypatch.setattr(codex.os, "fdopen", write_fails)
    base = tmp_path / "run"
    with pytest.raises(OSError, match="write failed"):
        codex.codex_agent("openai/model", "control", {"problem": "p"}, base, {}, 1, [])
    assert not (base / "codex" / "auth.json").exists()


@POSIX
def test_a_planted_auth_link_is_refused_and_its_target_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    login = tmp_path / "login.json"
    login.write_text('{"token": "secret-value"}', encoding="utf-8")
    monkeypatch.setattr(codex, "CODEX_AUTH", login)
    monkeypatch.setattr(run, "fts_location", lambda: FTS)
    victim = tmp_path / "victim"
    victim.write_text("untouched", encoding="utf-8")
    real_mkdir = Path.mkdir

    def mkdir_then_plant(self: Path, *args: object, **kwargs: object) -> None:
        real_mkdir(self, *args, **kwargs)  # type: ignore[arg-type]
        if self.name == "codex":
            (self / "auth.json").symlink_to(victim)

    monkeypatch.setattr(Path, "mkdir", mkdir_then_plant)
    base = tmp_path / "run"
    with pytest.raises(FileExistsError):
        codex.codex_agent("openai/model", "control", {"problem": "p"}, base, {}, 1, [])
    assert victim.read_text(encoding="utf-8") == "untouched"
