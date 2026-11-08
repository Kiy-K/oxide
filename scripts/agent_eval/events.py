"""Parsers for an agent's JSON event stream: spend, tool calls, and failure text."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any


def _jsonl(events: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for line in events.splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def measure(events: str) -> dict[str, Any]:
    """Tokens, steps and tool calls from opencode's JSON event stream."""
    tokens = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
    calls: dict[str, int] = {}
    steps = 0
    for line in events.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        part = event.get("part", {})
        if event.get("type") == "tool_use":
            calls[part.get("tool", "?")] = calls.get(part.get("tool", "?"), 0) + 1
        elif event.get("type") == "step_finish":
            steps += 1
            used = part.get("tokens", {})
            tokens["input"] += used.get("input", 0)
            tokens["output"] += used.get("output", 0) + used.get("reasoning", 0)
            tokens["cache_read"] += used.get("cache", {}).get("read", 0)
            tokens["cache_write"] += used.get("cache", {}).get("write", 0)
    return {"tokens": tokens, "steps": steps, "calls": calls}


def agent_error(events: str) -> str | None:
    for line in events.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("type") == "error":
            error = event.get("error", {})
            return f"{error.get('type', 'agent error')}: {error.get('message', '')}"
    return None


def agent_failure(
    status: str, events: str, error: Callable[[str], str | None] = agent_error
) -> str | None:
    if message := error(events):
        return message
    return f"agent exited with {status}" if status not in {"ok", "timeout"} else None
