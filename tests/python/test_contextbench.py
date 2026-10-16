"""The context-quality regression gate over ``docs/phase-6/baseline-v1`` (protocol in its
README, harness in ``contextbench.py``).

Quality, invariants and exact output compatibility are separate tests, so a changed
format is never read as a quality regression and a regression never hides behind a
format change. Needs the pinned corpora (``mise run bootstrap``) and the FTS extension.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import contextbench as cb
import pytest
from conftest import FTS

from oxide.storage.ladybug import fts_unavailable

MANIFEST = cb.load(name="manifest.json")
METRICS = cb.load(name="metrics.json")
FROZEN = {r["task"]: r for r in cb.load(name="results.json")}


@pytest.fixture(scope="module")
def current() -> dict[str, Any]:
    reason = fts_unavailable(FTS)
    assert reason is None, f"{reason} (the gate needs the pinned extension)"
    # Removed right after: the 38 stores take ~1 GB, and pytest keeps its last 3 runs.
    with tempfile.TemporaryDirectory(prefix="oxide-contextbench-") as data:
        try:
            return cb.evaluate(cb.load(), Path(data))
        except FileNotFoundError as error:
            pytest.fail(str(error))


def test_protocol_is_the_frozen_one() -> None:
    assert (MANIFEST["tolerance"], MANIFEST["gated"]) == (cb.TOLERANCE, list(cb.GATED))
    assert MANIFEST["budgets"] == list(cb.BUDGETS) and MANIFEST["scorer"] == cb.SCORER
    for task in cb.load()["tasks"]:  # gold only from the benchmark sources
        assert task["source"].startswith(("fixtures/", "ContextBench full.parquet#"))
        assert all(set(g) <= {"file", "start", "end", "sha256", "symbol"} for g in task["gold"])


def test_corpora_are_the_frozen_ones(current: dict[str, Any]) -> None:
    keys = ("url", "commit", "py_files", "tree_sha256")
    for name, corpus in current["corpora"].items():
        frozen = MANIFEST["corpora"][name]
        assert {k: corpus.get(k) for k in keys} == {k: frozen.get(k) for k in keys}, (
            f"corpus {name} changed: its results are not comparable, not a quality result"
        )


def test_bundles_are_bounded_and_deterministic(current: dict[str, Any]) -> None:
    results = current["results"]
    assert [r["task"] for r in results] == list(FROZEN)
    for result in results:
        for budget, bundle in result["bundles"].items():
            assert bundle["within_budget"], f"{result['task']}@{budget}: over budget"
            assert bundle["deterministic"], f"{result['task']}@{budget}: not repeatable"


@pytest.mark.parametrize("split", ["dev", "heldout"])
def test_quality_does_not_regress(current: dict[str, Any], split: str) -> None:
    now = cb.aggregate(current["results"])[split]
    regressions = []
    for metric in cb.GATED:
        if now[metric] < METRICS[split][metric] - cb.TOLERANCE:
            worse = [
                f"{r['task']} {FROZEN[r['task']]['metrics'][metric]:.3f}"
                f"->{r['metrics'][metric]:.3f}"
                for r in current["results"]
                if r["split"] == split
                and r["metrics"][metric] < FROZEN[r["task"]]["metrics"][metric]
            ]
            regressions.append(
                f"{metric}: {METRICS[split][metric]:.4f} -> {now[metric]:.4f} ({', '.join(worse)})"
            )
    assert not regressions, f"{split} quality regressed beyond {cb.TOLERANCE}:\n" + "\n".join(
        regressions
    )


def test_output_is_compatible(current: dict[str, Any]) -> None:
    versions = current["results"][0]["versions"]
    if versions != MANIFEST["versions"]:
        bumped = {k: (MANIFEST["versions"].get(k), v) for k, v in versions.items()}
        pytest.fail(
            "versioned output change, not a quality verdict (test_quality_does_not_regress "
            f"judges quality): { ({k: v for k, v in bumped.items() if v[0] != v[1]}) }; "
            "freeze a new baseline-vN after review"
        )
    changed: list[str] = []
    for result in current["results"]:
        frozen = FROZEN[result["task"]]["compat"]
        if diff := [k for k, v in result["compat"].items() if frozen.get(k) != v]:
            changed.append(f"{result['task']}: {', '.join(diff)}")
    assert not changed, "output changed without a version bump:\n" + "\n".join(changed)
