"""Regression coverage for Codex review finding 1: resuming a ContextBench
run into a results file previously written by a different embedding
provider must hard-stop before appending, instead of silently mixing
vector spaces. A legacy row with no `embedder` field is unknowable
provenance, not an assumed match.

Run with the project's eval venv:
    eval-agent/.venv/bin/python -m pytest scripts/agent_eval/test_contextbench_run.py
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import contextbench_run as cb  # noqa: E402


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")


def test_load_existing_progress_treats_missing_embedder_as_unknown(tmp_path):
    results_path = tmp_path / "cb_results.jsonl"
    _write_jsonl(
        results_path,
        [
            {"task": "t1", "condition": "hybrid", "embedder": "http:qwen3-Q8_0@u"},
            # Legacy row from before provenance tracking existed.
            {"task": "t2", "condition": "hybrid"},
        ],
    )
    done_keys, existing_embedders = cb.load_existing_progress(results_path)
    assert done_keys == {("t1", "hybrid"), ("t2", "hybrid")}
    assert existing_embedders == {"http:qwen3-Q8_0@u", cb.UNKNOWN_EMBEDDER}


def test_load_existing_progress_on_missing_file_is_empty(tmp_path):
    done_keys, existing_embedders = cb.load_existing_progress(tmp_path / "nope.jsonl")
    assert done_keys == set()
    assert existing_embedders == set()


def test_check_embedder_provenance_stops_on_different_provider(tmp_path):
    results_path = tmp_path / "cb_results.jsonl"
    with pytest.raises(SystemExit):
        cb.check_embedder_provenance(
            {"http:qwen3-Q8_0@u"}, "native:bge-small-en-v1.5", results_path
        )


def test_check_embedder_provenance_stops_on_legacy_unknown_provider(tmp_path):
    # A file whose only prior rows lack an `embedder` field must be treated
    # as ambiguous and rejected — never silently assumed compatible just
    # because there's nothing to explicitly contradict the current run.
    results_path = tmp_path / "cb_results.jsonl"
    with pytest.raises(SystemExit):
        cb.check_embedder_provenance(
            {cb.UNKNOWN_EMBEDDER}, "native:bge-small-en-v1.5", results_path
        )


def test_check_embedder_provenance_allows_same_provider_resume(tmp_path):
    results_path = tmp_path / "cb_results.jsonl"
    # Should not raise: resuming under the identical provider is exactly
    # the supported case.
    cb.check_embedder_provenance(
        {"native:bge-small-en-v1.5"}, "native:bge-small-en-v1.5", results_path
    )


def test_check_embedder_provenance_allows_fresh_file(tmp_path):
    results_path = tmp_path / "cb_results.jsonl"
    # No existing rows at all: nothing to be inconsistent with.
    cb.check_embedder_provenance(set(), "native:bge-small-en-v1.5", results_path)


def test_end_to_end_resume_into_mixed_provider_file_is_rejected(tmp_path):
    """Reproduces the exact reported failure path: a file already holds a
    completed task/condition under provider A; resuming with provider B
    must stop before that (or any other) row is appended under B."""
    results_path = tmp_path / "cb_results.jsonl"
    _write_jsonl(
        results_path,
        [{"task": "t1", "condition": "hybrid", "embedder": "http:qwen3-Q8_0@u"}],
    )
    done_keys, existing_embedders = cb.load_existing_progress(results_path)
    assert ("t1", "hybrid") in done_keys  # would otherwise be skipped as already-done
    with pytest.raises(SystemExit):
        cb.check_embedder_provenance(existing_embedders, "native:bge-small-en-v1.5", results_path)
