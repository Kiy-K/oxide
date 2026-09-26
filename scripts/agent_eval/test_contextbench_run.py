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


def test_main_validates_provenance_even_when_every_task_is_already_done(tmp_path, monkeypatch):
    """Closes the remaining resume gap: when every (task, condition) pair a
    run would touch is already recorded, the old control flow `continue`d
    before ever indexing/verifying anything, so a mismatched (or legacy,
    unknown-provider) results file was never actually checked. main() must
    still index+verify against the first task and hard-stop, instead of
    quietly finishing as a no-op.
    """
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    results_path = out_dir / "cb_results.jsonl"
    conditions = ["lexical", "vec", "hybrid", "budgeted"]
    # Every condition for the only task main() will see is already done,
    # under a provider this run does not use.
    _write_jsonl(
        results_path,
        [{"task": "fake-1", "condition": c, "embedder": "http:qwen3-Q8_0@u"} for c in conditions],
    )
    before = results_path.read_text()

    fake_task = {
        "instance_id": "fake-1",
        "repo": "acme/widget",
        "repo_url": "https://example.invalid/acme/widget.git",
        "base_commit": "deadbeef",
        "language": "python",
        "problem_statement": "n/a",
    }
    monkeypatch.setattr(cb, "load_tasks", lambda *a, **k: [fake_task])
    monkeypatch.setattr(cb, "ensure_repo_checkout", lambda *a, **k: tmp_path / "repo")
    monkeypatch.setattr(cb, "index_repo", lambda *a, **k: None)
    monkeypatch.setattr(cb, "verify_embedder_took_effect", lambda *a, **k: "native:bge-small-en-v1.5")
    monkeypatch.setenv("OXIDE_EMBED_NATIVE", "bge-small-en-v1.5")
    monkeypatch.delenv("OXIDE_EMBED_URL", raising=False)
    monkeypatch.setattr(
        sys,
        "argv",
        ["contextbench_run.py", "--out", str(out_dir), "--conditions", ",".join(conditions)],
    )

    with pytest.raises(SystemExit):
        cb.main()

    # Nothing was appended before (or instead of) the hard stop.
    assert results_path.read_text() == before


# --- gold path normalization (docs/contextbench-scorer-fix/) -------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("/workspace/darkreader__darkreader__0.1/src/utils/url.ts", "src/utils/url.ts"),
        ("/workspace/clap-rs__clap__0.1/src/build/app/mod.rs", "src/build/app/mod.rs"),
        ("/testbed/astropy/modeling/separable.py", "astropy/modeling/separable.py"),
        # Already repo-relative, including dot-directories upstream's
        # `lstrip("./")` would corrupt: unchanged.
        ("src/generators/dynamic-theme.ts", "src/generators/dynamic-theme.ts"),
        (".goreleaser.yml", ".goreleaser.yml"),
        (".github/workflows/ci.yml", ".github/workflows/ci.yml"),
        # Any other absolute path is not a known container prefix: unchanged,
        # so it still matches nothing rather than being guessed into the repo.
        ("/tmp/reproduce_test.sh", "/tmp/reproduce_test.sh"),
        # A file directly under /workspace (68 `full` entries, e.g.
        # /workspace/reproduce.cpp — a scratch file outside the repository)
        # has no repo segment to strip; unlike upstream it is left as is.
        ("/workspace/reproduce.js", "/workspace/reproduce.js"),
        ("/workspace", "/workspace"),
        ("", ""),
    ],
)
def test_normalize_gold_path(raw, expected):
    assert cb.normalize_gold_path(raw) == expected


def _row(gold_file: str) -> dict:
    return {
        "gold_context": json.dumps([{"file": gold_file, "start_line": 1, "end_line": 4}]),
        "repo_url": "https://example.invalid/o/r",
        "base_commit": "0" * 40,
    }


def test_evaluate_task_scores_workspace_gold_exactly_like_relative_gold(tmp_path):
    # Regression: before the fix a `/workspace/…` gold row scored line
    # coverage 0 and span/symbol gold empty (vacuous coverage 1.0), while
    # file coverage — normalized inside ContextBench's Gold.files() — was 1.
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.py").write_text("def f():\n    return 1\n\n\ndef g():\n    return 2\n")
    items = [{"file": "src/a.py", "start_line": 1, "end_line": 2}]

    relative = cb.evaluate_task(tmp_path, _row("src/a.py"), items)
    workspace = cb.evaluate_task(tmp_path, _row("/workspace/o__r__0.1/src/a.py"), items)

    assert workspace == relative
    assert relative["line"]["gold_size"] == 4
    assert relative["line"]["intersection"] == 2
    assert relative["span"]["gold_size"] > 0
    assert relative["symbol"]["gold_size"] > 0


def test_evaluate_task_does_not_rescue_unknown_absolute_gold(tmp_path):
    # Only the known container prefixes are stripped: an unrelated absolute
    # path must not be mapped into the repository.
    (tmp_path / "tmp").mkdir()
    (tmp_path / "tmp" / "reproduce_test.sh").write_text("echo 1\necho 2\necho 3\necho 4\n")
    items = [{"file": "tmp/reproduce_test.sh", "start_line": 1, "end_line": 4}]
    m = cb.evaluate_task(tmp_path, _row("/tmp/reproduce_test.sh"), items)
    assert m["line"]["intersection"] == 0
