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
    measured = run.codex_measure(OK_RUN)
    assert measured["tokens"] == {
        "input": 44326 - 39936,
        "output": 100,
        "cache_read": 39936,
        "cache_write": 0,
    }
    assert protocol.spend(measured["tokens"]) == 44326 + 100


def test_only_completed_items_count_as_calls_and_messages_do_not() -> None:
    measured = run.codex_measure(OK_RUN)
    assert measured["calls"] == {"oxide_search": 1}
    assert measured["steps"] == 1


def test_a_failed_turn_is_reported_as_the_agent_failure() -> None:
    assert run.codex_agent_error(MODEL_REFUSED) == (
        "The 'no-such-model-xyz' model is not supported."
    )


def test_an_error_inside_a_turn_that_completes_is_not_a_failure() -> None:
    retried = OK_RUN.replace(
        '{"type": "turn.started"}',
        '{"type": "turn.started"}\n{"type": "error", "message": "reconnecting"}',
    )
    assert run.codex_agent_error(retried) is None


def test_the_arm_config_names_only_the_oxide_server_and_control_names_none() -> None:
    command = ["/venv/bin/python", "-m", "oxide", "mcp", "--repo", "/w", "--data-dir", "/d"]
    parsed = tomllib.loads(run.codex_config(command, FTS))
    assert parsed == {
        "mcp_servers": {
            "oxide": {
                "command": "/venv/bin/python",
                "args": ["-m", "oxide", "mcp", "--repo", "/w", "--data-dir", "/d"],
                "env": {"OXIDE_FTS_EXTENSION": str(FTS)},
            }
        }
    }
    assert run.codex_config([], FTS) == ""


def test_a_tool_call_from_another_server_fails_isolation() -> None:
    foreign = OK_RUN.replace('"server": "oxide"', '"server": "github"')
    with pytest.raises(RuntimeError, match="isolation"):
        run.codex_isolation("oxide", foreign)
    with pytest.raises(RuntimeError, match="isolation"):
        run.codex_isolation("control", OK_RUN)
    run.codex_isolation("oxide", OK_RUN)


def test_codex_takes_the_bare_openai_model_id_and_refuses_other_providers() -> None:
    assert run.codex_model("openai/gpt-6-luna") == "gpt-6-luna"
    with pytest.raises(SystemExit, match="openai"):
        run.codex_model("opencode/gpt-6-luna")
    with pytest.raises(SystemExit, match="openai"):
        run.codex_model("gpt-6-luna")


def test_the_copied_login_is_removed_after_the_run_even_when_it_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    login = tmp_path / "auth.json"
    login.write_text('{"token": "not-a-real-login"}', encoding="utf-8")
    monkeypatch.setattr(run, "CODEX_AUTH", login)
    monkeypatch.setattr(run, "fts_location", lambda: FTS)

    def launch_fails(*_: object) -> tuple[str, str]:
        raise RuntimeError("launch failed")

    monkeypatch.setattr(run, "launch", launch_fails)
    base = tmp_path / "run"
    with pytest.raises(RuntimeError, match="launch failed"):
        run.codex_agent("openai/gpt-6-luna", "oxide", {"problem": "p"}, base, {}, 1, [])
    assert not (base / "codex" / "auth.json").exists()
    assert (base / "codex" / "config.toml").exists()
