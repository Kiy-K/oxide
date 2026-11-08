"""Why agents engage with or ignore OXIDE, from the retained traces of a results file.

    uv run python scripts/agent_eval diagnose RESULTS.jsonl

Each OXIDE-arm run falls in one class, decided by its trace and the gold patch's files:

- not connected: the OXIDE tool never reached the agent's session (no `mcp connected` line)
- connected, never called: the agent had the tool and did not use it
- called after builtins read gold: builtin tools had already opened a file the gold patch changes
- called first, gold surfaced: the first OXIDE call listed a gold file that builtins had not opened
- called first, gold not surfaced: the OXIDE calls listed no gold file

Rows without a `trace` field (older results) use the newest run directory holding their key.
"""

from __future__ import annotations

import collections
import json
import re
from pathlib import Path
from typing import Any

import run
import tasks

CLASSES = (
    "not connected",
    "connected, never called",
    "called after builtins read gold",
    "called first, gold surfaced",
    "called first, gold not surfaced",
)


def calls_of(events: str) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    for line in events.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("type") == "tool_use":
            state = event["part"].get("state", {})
            calls.append(
                {
                    "tool": event["part"].get("tool", "?"),
                    "input": state.get("input") or {},
                    "output": str(state.get("output", "")),
                }
            )
        elif event.get("type") == "item.completed" and event["item"].get("type") == "mcp_tool_call":
            # Codex: MCP calls only. Its shell calls are not mapped to the builtin "read" class.
            item = event["item"]
            content = (item.get("result") or {}).get("content") or []
            calls.append(
                {
                    "tool": item.get("tool", "?"),
                    "input": item.get("arguments") or {},
                    "output": "".join(str(part.get("text", "")) for part in content),
                }
            )
    return calls


def gold_paths(gold_patch: str) -> set[str]:
    return set(re.findall(r"^\+\+\+ b/(\S+)", gold_patch, re.MULTILINE))


def read_path(call: dict[str, Any]) -> str:
    return str(call["input"].get("path") or call["input"].get("filePath") or "")


def surfaced(output: str) -> set[str]:
    """File paths an OXIDE context bundle lists, from its `### path:start-end` headers."""
    return set(re.findall(r"^### (\S+?):\d+", output, re.MULTILINE))


def degraded(output: str) -> list[str]:
    return [
        line.removeprefix("# degraded: ") for line in output.splitlines() if "# degraded:" in line
    ]


def classify(calls: list[dict[str, Any]], gold: set[str], connected: int) -> str:
    if connected == 0:
        return CLASSES[0]
    oxide = [i for i, c in enumerate(calls) if c["tool"].startswith("oxide")]
    if not oxide:
        return CLASSES[1]
    builtin_read = any(
        calls[i]["tool"] == "read"
        and any(read_path(calls[i]) == g or read_path(calls[i]).endswith("/" + g) for g in gold)
        for i in range(oxide[0])
    )
    if builtin_read:
        return CLASSES[2]
    seen = set().union(*(surfaced(calls[i]["output"]) for i in oxide))
    return CLASSES[3] if gold & seen else CLASSES[4]


def trace_dir(row: dict[str, Any]) -> Path:
    if "trace" in row:
        return tasks.CACHE / str(row["trace"])
    key = f"{row['task']}.{row['arm']}.{row['rep']}"
    roots = sorted(
        (d for d in (tasks.CACHE / "runs").iterdir() if (d / key / "events.jsonl").exists()),
        key=lambda d: d.name,
    )
    if not roots:
        raise FileNotFoundError(f"no trace for {key}")
    return roots[-1] / key


def diagnose(results: Path, arm: str = "oxide") -> str:
    rows = [json.loads(line) for line in results.read_text(encoding="utf-8").splitlines()]
    latest: dict[tuple[str, str, int], dict[str, Any]] = {}
    for row in rows:
        latest[(row["task"], row["arm"], row["rep"])] = row  # the last attempt is the one scored
    scored = [r for r in latest.values() if r.get("status") == "ok"]
    splits = {r.get("split", "dev") for r in scored}
    by_split = {s: {t["id"]: t for t in tasks.load(s)} for s in splits}
    counts: collections.Counter[str] = collections.Counter()
    resolved: collections.Counter[str] = collections.Counter()
    reasons: collections.Counter[str] = collections.Counter()
    calls_per_run: list[int] = []
    control = [r for r in scored if r["arm"] == "control"]
    for row in (r for r in scored if r["arm"] == arm):
        base = trace_dir(row)
        calls = calls_of((base / "events.jsonl").read_text(encoding="utf-8"))
        task = by_split[row.get("split", "dev")][row["task"]]
        cls = classify(
            calls,
            gold_paths(task["gold_patch"]),
            row.get("oxide_tools_connected", run.connected(base / "data", arm)),
        )
        counts[cls] += 1
        resolved[cls] += bool(row["resolved"])
        oxide_outputs = [c["output"] for c in calls if c["tool"].startswith("oxide")]
        calls_per_run.append(len(oxide_outputs))
        for output in oxide_outputs:
            reasons.update(degraded(output))
    lines = [
        f"control resolved {sum(bool(r['resolved']) for r in control)}/{len(control)}",
        f"{arm} arm: {sum(r['arm'] == arm for r in scored)} scored latest attempts",
        "",
        "| class | runs | resolved |",
        "|---|---:|---:|",
    ]
    lines += [f"| {c} | {counts[c]} | {resolved[c]} |" for c in CLASSES if counts[c]]
    lines += ["", f"oxide tool calls per run: {sorted(collections.Counter(calls_per_run).items())}"]
    lines += [f"degraded {n}x: {reason}" for reason, n in reasons.most_common(8)]
    return "\n".join(lines)
