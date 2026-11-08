"""Codex exec: isolated login, arm config, and the JSON stream it prints."""

from __future__ import annotations

import contextlib
import json
import os
import shutil
from pathlib import Path
from typing import Any

import arms
import confine
import events

# Codex runs use the ChatGPT login in CODEX_HOME/auth.json, copied into each run's isolated home.
CODEX = Path(shutil.which("codex") or "codex")
CODEX_AUTH = Path.home() / ".codex" / "auth.json"


def codex_config(command: list[str], fts: Path) -> str:
    """Codex config for one arm: the OXIDE server alone, or no server for control. JSON strings
    are valid TOML basic strings for these paths, so the arrays are written as JSON."""
    if not command:
        return ""
    return (
        "[mcp_servers.oxide]\n"
        f"command = {json.dumps(command[0])}\n"
        f"args = {json.dumps(command[1:])}\n"
        f"env = {{ OXIDE_FTS_EXTENSION = {json.dumps(str(fts))} }}\n"
    )


def codex_model(model: str) -> str:
    """Codex takes the bare model id under a ChatGPT login; only the openai provider is routed
    through it, so any other provider is refused rather than silently remapped."""
    provider, _, name = model.partition("/")
    if provider != "openai" or not name:
        raise SystemExit(f"--runner codex needs an openai/<model> id, got {model!r}")
    return name


def codex_agent(
    model: str,
    arm: str,
    task: dict[str, Any],
    base: Path,
    env: dict[str, str],
    timeout: int,
    command: list[str],
) -> tuple[str, str]:
    """Runs ``codex exec`` with an isolated home: the ChatGPT login and this arm's MCP server only.
    User config, user MCP servers and user instructions are not read."""
    import run

    work, home = base / "work", base / "codex"
    prompt = arms.PROMPT.format(hint=arms.HINT[arm], problem=task["problem"].strip())
    home.mkdir(parents=True)
    (home / "config.toml").write_text(codex_config(command, run.fts_location()), encoding="utf-8")
    environment = confine.clean_env(
        {
            **env,
            "CODEX_HOME": str(home),
            "HOME": str(base / "home"),
            "OXIDE_FTS_EXTENSION": str(run.fts_location()),
        }
    )
    argv = [
        str(CODEX),
        "exec",
        "--json",
        "--ephemeral",
        "--skip-git-repo-check",
        "-m",
        codex_model(model),
        "-s",
        "workspace-write",
        "-C",
        str(work),
        prompt,
    ]
    if not CODEX_AUTH.exists():
        raise SystemExit(f"no codex login at {CODEX_AUTH}: run `codex login`")
    login = CODEX_AUTH.read_bytes()
    secrets = confine.json_secrets(CODEX_AUTH)  # the login's values, redacted from kept events
    directory = os.open(home, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    fd = None
    try:
        # Exclusive no-follow create in the pinned directory: a planted link cannot redirect it.
        fd = os.open(
            "auth.json",
            os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=directory,
        )
        with os.fdopen(os.dup(fd), "wb") as file:
            file.write(login)
        wrapped = confine.confine(argv, writable=base, hide=run.hidden(), net=True)
        status, raw = run.launch(wrapped, work, environment, timeout)
    finally:
        try:
            try:
                if fd is not None:
                    os.ftruncate(fd, 0)
            finally:
                with contextlib.suppress(FileNotFoundError):
                    os.unlink("auth.json", dir_fd=directory)
        finally:
            if fd is not None:
                os.close(fd)
            os.close(directory)
    return status, confine.redact(raw, secrets)


def codex_measure(raw: str) -> dict[str, Any]:
    """Tokens, steps and tool calls from ``codex exec --json``. Codex counts cached input inside
    input_tokens, so the uncached share is recorded as input and cache_read is not billed twice.
    Output tokens already include reasoning."""
    tokens = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
    calls: dict[str, int] = {}
    steps = 0
    complete = False
    for event in events._jsonl(raw):
        if event.get("type") in {"turn.started", "turn.failed"}:
            complete = False
        if event.get("type") == "item.completed":
            item = event.get("item", {})
            kind = item.get("type")
            if kind in {"agent_message", "reasoning", "error"}:
                continue
            name = item.get("tool") if kind == "mcp_tool_call" else kind
            calls[str(name)] = calls.get(str(name), 0) + 1
        elif event.get("type") == "turn.completed":
            steps += 1
            used = event.get("usage")
            if not isinstance(used, dict):
                used = {}
            complete = all(
                isinstance(used.get(key), int) and used[key] >= 0
                for key in ("input_tokens", "output_tokens")
            )
            total = used.get("input_tokens", 0)
            cached = used.get("cached_input_tokens", 0)
            output = used.get("output_tokens", 0)
            if not isinstance(total, int) or total < 0:
                total = 0
            if not isinstance(cached, int) or not 0 <= cached <= total:
                complete = False
                cached = 0
            if not isinstance(output, int) or output < 0:
                output = 0
            tokens["input"] += total - cached
            tokens["cache_read"] += cached
            tokens["output"] += output
    return {
        "tokens": tokens,
        "steps": steps,
        "calls": calls,
        "usage_complete": complete and not events.incomplete_json(raw),
    }


def codex_agent_error(raw: str) -> str | None:
    """A failed turn, or an error with no completed turn. An error inside a turn that then
    completed is a retry, not a failure."""
    stream = events._jsonl(raw)
    failed = [
        e.get("error", {}).get("message", "turn failed")
        for e in stream
        if e.get("type") == "turn.failed"
    ]
    if failed:
        return str(failed[-1])
    if any(e.get("type") == "turn.completed" for e in stream):
        return None
    errors = [str(e.get("message", "agent error")) for e in stream if e.get("type") == "error"]
    return errors[-1] if errors else None


def codex_isolation(arm: str, raw: str) -> None:
    """Reject runs whose MCP tool calls came from a server other than the arm's own."""
    servers = {
        str(e["item"].get("server"))
        for e in events._jsonl(raw)
        if e.get("type") == "item.completed" and e.get("item", {}).get("type") == "mcp_tool_call"
    }
    if not servers <= ({"oxide"} if arm in arms.OXIDE_ARMS else set()):
        raise RuntimeError(f"isolation: unexpected MCP servers {sorted(servers)}")


def codex_connected(arm: str) -> int:
    """OXIDE tools configured for this arm. Codex's JSON stream does not log MCP startup, so this
    is the configured count, not an observed one; the calls themselves are observed in events."""
    return 1 if arm in arms.OXIDE_ARMS else 0
