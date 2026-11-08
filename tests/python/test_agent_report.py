"""Final report verdicts require the planned task/arm/repetition matrix to be scored."""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

SCRIPTS = str(Path(__file__).resolve().parents[2] / "scripts" / "agent_eval")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

report_mod: ModuleType = importlib.import_module("report")


def _row(task: str, arm: str, rep: int, *, resolved: bool | None) -> dict[str, Any]:
    row: dict[str, Any] = {
        "task": task,
        "arm": arm,
        "rep": rep,
        "resolved": resolved,
        "seconds": 1,
        "tokens": {"output": 1},
        "steps": 1,
        "calls": {},
        "status": "ok" if resolved is not None else "harness-error",
    }
    if resolved is None:
        row["error"] = "boom"
    return row


def _write(tmp_path: Path, rows: list[dict[str, Any]], manifest: dict[str, Any] | None) -> Path:
    results = tmp_path / "results.jsonl"
    results.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    if manifest is not None:
        side = results.with_name(results.stem + ".manifest.json")
        side.write_text(json.dumps(manifest), encoding="utf-8")
    return results


def _winning_pair() -> list[dict[str, Any]]:
    return [
        _row("only", "control", 0, resolved=False),
        _row("only", "oxide", 0, resolved=True),
    ]


def test_incomplete_plan_is_provisional_not_a_success(tmp_path: Path, capsys: Any) -> None:
    # The review reproduction: 20 tasks x 3 reps, one scored rep per arm, oxide wins.
    manifest = {"tasks": 20, "reps": 3, "arms": ["control", "oxide"]}
    report_mod.report(_write(tmp_path, _winning_pair(), manifest))
    out = capsys.readouterr().out
    assert "verdict: provisional: incomplete planned matrix (2/120 scored)" in out
    assert "improves task success" not in out
    assert "no demonstrated improvement" not in out


def test_scored_error_cell_blocks_a_final_verdict(tmp_path: Path, capsys: Any) -> None:
    # 1/10 errors does not trip the 10% rule, but that cell was planned.
    rows = [
        _row(str(task), arm, 0, resolved=arm == "oxide")
        for task in range(4)
        for arm in ("control", "oxide")
    ]
    rows.append(_row("4", "control", 0, resolved=False))
    rows.append(_row("4", "oxide", 0, resolved=None))
    manifest = {"tasks": 5, "reps": 1, "arms": ["control", "oxide"]}
    report_mod.report(_write(tmp_path, rows, manifest))
    out = capsys.readouterr().out
    assert "verdict: provisional: incomplete planned matrix (9/10 scored)" in out
    assert "improves task success" not in out


def test_complete_manifest_and_retried_runs_keep_the_final_verdict(
    tmp_path: Path, capsys: Any
) -> None:
    rows = [
        _row("only", "oxide", 0, resolved=None),
        *_winning_pair(),
    ]
    manifest = {"tasks": 1, "reps": 1, "arms": ["control", "oxide"]}
    report_mod.report(_write(tmp_path, rows, manifest))
    out = capsys.readouterr().out
    assert "verdict: oxide improves task success" in out
    assert "provisional" not in out


def test_results_without_a_manifest_are_not_a_final_verdict(tmp_path: Path, capsys: Any) -> None:
    report_mod.report(_write(tmp_path, _winning_pair(), None))
    out = capsys.readouterr().out
    assert "verdict: provisional" in out
    assert "improves task success" not in out


def test_unreadable_manifest_is_not_a_final_verdict(tmp_path: Path, capsys: Any) -> None:
    results = _write(tmp_path, _winning_pair(), None)
    results.with_name(results.stem + ".manifest.json").write_text("{", encoding="utf-8")
    report_mod.report(results)
    out = capsys.readouterr().out
    assert "verdict: invalid: unreadable manifest" in out
    assert "improves task success" not in out
