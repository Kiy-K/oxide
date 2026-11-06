"""Agent-evaluation report validity rules."""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from typing import Any


def test_more_than_ten_percent_harness_errors_voids_the_verdict(
    tmp_path: Path, capsys: Any
) -> None:
    scripts = str(Path(__file__).resolve().parents[2] / "scripts" / "agent_eval")
    sys.path.insert(0, scripts)
    try:
        report = importlib.import_module("report").report
    finally:
        sys.path.remove(scripts)
    rows: list[dict[str, Any]] = [
        {
            "task": task,
            "arm": arm,
            "rep": 0,
            "resolved": arm == "oxide",
            "seconds": 1,
            "tokens": {"output": 1},
            "steps": 1,
            "calls": {},
            "index_seconds": 0,
        }
        for task in ("a", "b")
        for arm in ("control", "oxide")
    ]
    rows.append({"task": "c", "arm": "control", "rep": 0, "resolved": None})
    results = tmp_path / "results.jsonl"
    results.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    report(results)

    assert "verdict: invalid: more than 10% harness errors" in capsys.readouterr().out


def test_provider_auth_failure_invalidates_a_small_partial_run(tmp_path: Path, capsys: Any) -> None:
    scripts = str(Path(__file__).resolve().parents[2] / "scripts" / "agent_eval")
    sys.path.insert(0, scripts)
    try:
        report = importlib.import_module("report").report
    finally:
        sys.path.remove(scripts)
    rows: list[dict[str, Any]] = [
        {
            "task": task,
            "arm": arm,
            "rep": 0,
            "resolved": True,
            "seconds": 1,
            "tokens": {"output": 1},
            "steps": 1,
            "calls": {},
        }
        for task in range(5)
        for arm in ("control", "oxide")
    ]
    rows.append(
        {
            "task": "auth-failed",
            "arm": "oxide",
            "rep": 0,
            "resolved": None,
            "status": "harness-error",
            "error": "provider.auth: Request failed: 400",
        }
    )
    results = tmp_path / "results.jsonl"
    results.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    report(results)

    assert "verdict: invalid: provider authentication failure" in capsys.readouterr().out


def test_provider_error_event_is_not_scored_as_task_failure() -> None:
    scripts = str(Path(__file__).resolve().parents[2] / "scripts" / "agent_eval")
    sys.path.insert(0, scripts)
    try:
        run = importlib.import_module("run")
    finally:
        sys.path.remove(scripts)

    events = '{"type":"error","error":{"type":"provider.auth","message":"Request failed: 400"}}'
    assert run.agent_error(events) == "provider.auth: Request failed: 400"
    assert run.agent_failure("exit 1", events) == "provider.auth: Request failed: 400"


def test_report_keeps_only_latest_attempt_for_each_run(tmp_path: Path, capsys: Any) -> None:
    scripts = str(Path(__file__).resolve().parents[2] / "scripts" / "agent_eval")
    sys.path.insert(0, scripts)
    try:
        report = importlib.import_module("report").report
    finally:
        sys.path.remove(scripts)
    control = {
        "task": "task",
        "arm": "control",
        "rep": 0,
        "resolved": True,
        "seconds": 1,
        "tokens": {"output": 1},
        "steps": 1,
        "calls": {},
    }
    oxide = {**control, "arm": "oxide", "index_seconds": 0}
    failed = {**oxide, "resolved": None, "status": "harness-error"}
    results = tmp_path / "retries.jsonl"
    results.write_text(
        "".join(json.dumps(row) + "\n" for row in (failed, control, oxide)), encoding="utf-8"
    )

    report(results)

    assert "2 scored runs, 0 harness errors, 1 paired tasks" in capsys.readouterr().out
