"""The runner's job loop under a token cap, with agent runs replaced by fakes that report spend.
No model is called: this checks the gating and the ledger, not the agent."""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

SCRIPTS = str(Path(__file__).resolve().parents[2] / "scripts" / "agent_eval")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

run: ModuleType = importlib.import_module("run")
tasks: ModuleType = importlib.import_module("tasks")


def args(out: Path, cap: int | None) -> argparse.Namespace:
    return argparse.Namespace(
        split="dev",
        only=[tasks.load()[0]["id"]],  # one task, two arms, one repetition
        tasks=1,
        reps=1,
        jobs=3,
        max_tokens=cap,
        out=out,
        model="m",
        timeout=1,
        final=False,
        arms=list(run.ARMS),
        base_src=None,
        runner="opencode",
    )


@pytest.fixture
def fake_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    def run_one(task: dict[str, Any], arm: str, rep: int, _: Any, __: Any) -> dict[str, Any]:
        return {
            "task": task["id"],
            "arm": arm,
            "rep": rep,
            "status": "ok",
            "resolved": True,
            "spend": 300,
        }

    monkeypatch.setattr(run, "run_one", run_one)
    monkeypatch.setattr(run, "manifest", lambda *_: {"fixed": True})


def rows(out: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in out.read_text().splitlines()] if out.exists() else []


def test_the_cap_stops_the_second_run_once_the_first_cost_is_known(
    tmp_path: Path, fake_runs: None
) -> None:
    out = tmp_path / "r.jsonl"
    run.run_all(args(out, cap=500))
    assert len(rows(out)) == 1  # 300 spent; a second 300 run would reach 600 > 500
    assert rows(out)[0]["spend"] == 300


def test_resuming_under_the_same_cap_counts_the_recorded_spend(
    tmp_path: Path, fake_runs: None
) -> None:
    out = tmp_path / "r.jsonl"
    run.run_all(args(out, cap=500))
    run.run_all(args(out, cap=500))  # recorded 300 plus another 300 would exceed the cap
    assert len(rows(out)) == 1


def test_without_a_cap_every_planned_run_is_recorded(tmp_path: Path, fake_runs: None) -> None:
    out = tmp_path / "r.jsonl"
    run.run_all(args(out, cap=None))
    assert sorted(r["arm"] for r in rows(out)) == ["control", "oxide"]
