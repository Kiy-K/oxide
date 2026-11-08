"""The runner's job loop under a token cap, with agent runs replaced by fakes that report spend.
No model is called: this checks the gating and the ledger, not the agent."""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

SCRIPTS = str(Path(__file__).resolve().parents[2] / "scripts" / "agent_eval")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

arms: ModuleType = importlib.import_module("arms")
batch: ModuleType = importlib.import_module("batch")
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
        arms=list(arms.ARMS),
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
    monkeypatch.setattr(run.confine, "require", lambda: None)
    monkeypatch.setattr(batch, "manifest", lambda *_: {"fixed": True})


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


@pytest.mark.parametrize("resume", [False, True])
def test_unknown_usage_stops_capped_admissions(
    tmp_path: Path, fake_runs: None, monkeypatch: pytest.MonkeyPatch, resume: bool
) -> None:
    out = tmp_path / "r.jsonl"

    def incomplete(task: dict[str, Any], arm: str, rep: int, _: Any, __: Any) -> dict[str, Any]:
        return {
            "task": task["id"],
            "arm": arm,
            "rep": rep,
            "status": "timeout",
            "resolved": False,
            "spend": 12,
            "usage_unknown": True,
        }

    monkeypatch.setattr(run, "run_one", incomplete)
    run.run_all(args(out, cap=500))
    if resume:
        run.run_all(args(out, cap=500))
    assert len(rows(out)) == 1
    assert rows(out)[0]["spend"] == 12


def test_prelaunch_error_does_not_block_cap(
    tmp_path: Path, fake_runs: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "r.jsonl"

    def prelaunch(task: dict[str, Any], arm: str, rep: int, _: Any, __: Any) -> dict[str, Any]:
        return {
            "task": task["id"],
            "arm": arm,
            "rep": rep,
            "status": "harness-error",
            "resolved": None,
            "usage_unknown": False,
        }

    monkeypatch.setattr(run, "run_one", prelaunch)
    run.run_all(args(out, cap=500))
    assert len(rows(out)) == 2


@pytest.mark.parametrize("failure", ["cleanup", "timeout"])
def test_incomplete_run_keeps_observed_spend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    monkeypatch.setattr(run.tasks, "CACHE", tmp_path)
    monkeypatch.setattr(run.tasks, "checkout", lambda task, work: work.mkdir(parents=True))
    monkeypatch.setattr(run.tasks, "environment", lambda *args: {})
    monkeypatch.setattr(run, "credentials", lambda *args: None)
    raw = json.dumps(
        {
            "type": "step_finish",
            "part": {"reason": "stop", "tokens": {"input": 7, "output": 3}},
        }
    )

    def agent(*args: Any) -> tuple[str, str]:
        run._launch_state.started = True
        run._launch_state.raw = raw
        if failure == "cleanup":
            raise OSError("cleanup failed")
        return "timeout", raw

    monkeypatch.setattr(run, "agent", agent)
    monkeypatch.setattr(run.session, "write_private", lambda *args: None)
    monkeypatch.setattr(run.session, "connected", lambda *args: 0)
    monkeypatch.setattr(run.session, "isolation", lambda *args: None)
    monkeypatch.setattr(
        run.session, "git_on_agent", lambda *args, **kwargs: SimpleNamespace(stdout="")
    )
    monkeypatch.setattr(run.session, "score", lambda *args: {"resolved": False})
    arguments = argparse.Namespace(
        runner="opencode", model="m", split="dev", base_src=None, timeout=1
    )
    row = run.run_one({"id": "t", "problem": "p"}, "control", 0, arguments, tmp_path)
    assert row["spend"] == 10
    assert row["usage_unknown"] is True
    assert row["status"] == ("timeout" if failure == "timeout" else "harness-error")
