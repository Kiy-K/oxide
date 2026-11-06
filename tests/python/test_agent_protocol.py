"""Protocol guards of the paired agent evaluation: spend cap, manifest identity, held-out set."""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

SCRIPTS = str(Path(__file__).resolve().parents[2] / "scripts" / "agent_eval")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

protocol: ModuleType = importlib.import_module("protocol")


def test_spend_counts_every_billed_token_kind_and_tolerates_missing_keys() -> None:
    assert protocol.spend({"input": 3, "output": 2, "cache_read": 5, "cache_write": 7}) == 17
    assert protocol.spend({"input": 4}) == 4


def test_without_a_cap_every_run_is_admitted() -> None:
    assert protocol.admit(10**12, 5, 10**9, None)


def test_the_first_wave_is_one_run_until_a_run_reports_its_cost() -> None:
    # Two runs started together overshot a 450k cap at 809k: nothing is known before the first ends.
    assert protocol.admit(0, 0, None, 450_000)
    assert not protocol.admit(0, 1, None, 450_000)


def test_a_run_is_admitted_only_if_every_run_in_flight_fits_at_the_largest_cost() -> None:
    assert not protocol.admit(283_167, 0, 283_167, 450_000)  # a second 283k run would not fit
    assert protocol.admit(0, 1, 200_000, 450_000)  # 0 + 2 * 200k fits
    assert not protocol.admit(0, 2, 200_000, 450_000)  # 0 + 3 * 200k does not


def test_a_row_without_spend_data_is_not_a_zero_cost_run() -> None:
    error_row = {"status": "harness-error", "resolved": None}
    assert not protocol.row_has_spend(error_row)
    assert protocol.row_spend({"tokens": {"input": 4, "output": 1}}) == 5  # rows before "spend"
    assert protocol.spent([{"spend": 7}, error_row]) == 7


def test_resume_under_a_changed_protocol_is_refused_and_names_the_field(tmp_path: Path) -> None:
    path = tmp_path / "run.manifest.json"
    protocol.check_manifest(path, {"tasks_sha256": "a", "model": "m"})
    protocol.check_manifest(path, {"tasks_sha256": "a", "model": "m"})  # same protocol: fine
    with pytest.raises(SystemExit, match="tasks_sha256"):
        protocol.check_manifest(path, {"tasks_sha256": "b", "model": "m"})
    assert json.loads(path.read_text())["model"] == "m"


def test_a_second_results_file_on_the_same_heldout_set_is_refused(tmp_path: Path) -> None:
    log = tmp_path / "heldout-log.jsonl"
    protocol.heldout_open(log, "abc", tmp_path / "first.jsonl")
    protocol.heldout_open(log, "abc", tmp_path / "first.jsonl")  # resuming the same file
    protocol.heldout_open(log, "other", tmp_path / "second.jsonl")  # a different set
    with pytest.raises(SystemExit, match="one look"):
        protocol.heldout_open(log, "abc", tmp_path / "second.jsonl")
