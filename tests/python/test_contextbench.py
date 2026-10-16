"""The context-quality regression gate over the latest ``docs/phase-6/baseline-vN`` (protocol in its
README, harness in ``contextbench.py``), and regression cases for the scorer itself.

Quality, invariants and exact output compatibility are separate tests, so a changed
format is never read as a quality regression and a regression never hides behind a
format change. The gate needs the pinned corpora (``mise run bootstrap``) and the FTS
extension; the scorer cases need neither.
"""

from __future__ import annotations

import json
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

import contextbench as cb
import pytest
from conftest import FTS, write

from oxide.storage.ladybug import fts_unavailable

MANIFEST = cb.load("manifest.json")
METRICS = cb.load("metrics.json")
FROZEN = {r["task"]: r for r in cb.load("results.json")}
AUDIT = cb.load(cb.AUDIT.name, cb.AUDIT.parent)


@pytest.fixture(scope="module")
def current() -> dict[str, Any]:
    reason = fts_unavailable(FTS)
    assert reason is None, f"{reason} (the gate needs the pinned extension)"
    # Removed right after: the 38 stores take ~1 GB, and pytest keeps its last 3 runs.
    with tempfile.TemporaryDirectory(prefix="oxide-contextbench-") as data:
        try:
            return cb.evaluate(cb.load_tasks(), Path(data))
        except FileNotFoundError as error:
            pytest.fail(str(error))


def test_protocol_is_the_frozen_one() -> None:
    assert (MANIFEST["tolerance"], MANIFEST["gated"]) == (cb.TOLERANCE, list(cb.GATED))
    assert MANIFEST["budgets"] == list(cb.BUDGETS) and MANIFEST["scorer"] == cb.SCORER
    assert MANIFEST["tasks"].endswith(cb._sha(cb.TASKS.read_bytes()))
    assert MANIFEST["audit"].endswith(cb._sha(cb.AUDIT.read_bytes()))
    for task in json.loads(cb.TASKS.read_text())["tasks"]:  # gold only from the sources
        assert task["source"].startswith(("fixtures/", "ContextBench full.parquet#"))
        assert all(set(g) <= {"file", "start", "end", "sha256", "symbol"} for g in task["gold"])
        if task["source"].startswith("ContextBench"):  # a verdict on every annotated span
            verdicts = [(v["file"], v["start"], v["end"]) for v in AUDIT["spans"][task["id"]]]
            assert verdicts == [(g["file"], g["start"], g["end"]) for g in task["gold"]]


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


def test_gold_integrity_failures_stay_visible(current: dict[str, Any]) -> None:
    classes = Counter(v["class"] for spans in AUDIT["spans"].values() for v in spans)
    unverified = {c: n for c, n in classes.items() if c not in cb.VERIFIED}
    scored = Counter(g["class"] for r in current["results"] for g in r["gold"] if not g["verified"])
    assert scored == unverified
    assert cb.aggregate(current["results"])["all"]["unverified"]["spans"] == sum(scored.values())


def _metric(result: dict[str, Any], metric: str) -> float:
    return float(result["verified"]["metrics"][metric]) if result["verified"] else 0.0


@pytest.mark.parametrize("split", ["dev", "heldout"])
def test_quality_does_not_regress(current: dict[str, Any], split: str) -> None:
    now = cb.aggregate(current["results"])[split]["verified"]
    frozen = METRICS[split]["verified"]
    assert (now["tasks"], now["spans"]) == (frozen["tasks"], frozen["spans"])
    regressions = []
    for metric in cb.GATED:
        if now[metric] < frozen[metric] - cb.TOLERANCE:
            worse = [
                f"{r['task']} {_metric(FROZEN[r['task']], metric):.3f}->{_metric(r, metric):.3f}"
                for r in current["results"]
                if r["split"] == split and _metric(r, metric) < _metric(FROZEN[r["task"]], metric)
            ]
            regressions.append(
                f"{metric}: {frozen[metric]:.4f} -> {now[metric]:.4f} ({', '.join(worse)})"
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


# --- scorer regression cases: each a confirmed evaluation bug or an audit rule ---


def test_lines_split_on_newlines_only() -> None:
    # str.splitlines() also splits on \x0c and \u2028, renumbering lines OXIDE counts by \n.
    assert cb.code_lines("a\x0cb\n\n  c\u2028d  \n") == ["a\x0cb", "c\u2028d"]


def test_blank_lines_are_not_gold(tmp_path: Path) -> None:
    # No source view covers the blank lines between two declarations, so a span that
    # counted them could never be complete (scorer v1).
    write(tmp_path, {"m.py": "def a():\n    pass\n\n   \ndef b():\n    pass\n"})
    assert cb.Lines(tmp_path).code("m.py", 1, 6) == {1, 2, 5, 6}


def test_module_lines_are_outside_outermost_declarations() -> None:
    source = (
        b'"""doc"""\nimport os\nLIMIT = 3\n\n@decorate\ndef f():\n    x = 1\n\n'
        b"if os.name:\n    def g():\n        pass\nelse:\n    g = None\n"
        b"class C:\n    y = 2\n"
    )
    assert cb.module_lines(source) == {1, 2, 3, 4, 8, 9, 12, 13, 16}


def test_files_cover_gold_only_through_module_lines() -> None:
    # Scorer v1 counted symbols only, so module-level gold was unreachable by definition.
    imports = {"file": "m.py", "lines": frozenset({1, 2}), "module": frozenset({1, 2})}
    body = {"file": "m.py", "lines": frozenset({7}), "module": frozenset()}
    assert cb.covers("file", ("m.py", 1, 40), imports)
    assert not cb.covers("file", ("m.py", 1, 40), body)
    assert cb.covers("symbol", ("m.py", 6, 8), body)
    assert not cb.covers("symbol", ("m.py", 6, 8), imports)
    assert not cb.covers("symbol", ("n.py", 6, 8), body)


AUDITED = "def f(x):\n    '''Doc.'''\n    y = x\n    return y\n\n\ndef g():\n    return 1\n"
PATCH = "--- a/m.py\n+++ b/m.py\n@@ -7,2 +7,2 @@\n def g():\n-    return 1\n+    return 2\n"


@pytest.mark.parametrize(
    ("path", "start", "end", "content", "expected"),
    [
        ("m.py", 1, 4, "def f(x):\n'''Doc.'''\ny = x\nreturn y", "exact"),
        ("m.py", 1, 4, "def f(x):\n    ...\n    return y", "contained"),  # elided
        ("pkg/m.py", 1, 4, "def f(x):\n'''Doc.'''\ny = x\nreturn y", "path"),
        ("m.py", 2, 5, "def f(x):\n'''Doc.'''\ny = x\nreturn y", "range"),
        ("m.py", 7, 8, "def g():\n    return 2", "revision"),
        ("m.py", 1, 4, "def f(x):\n    return x + 1", "drift"),
        ("m.py", 1, 4, "return y\ndef f(x):", "unresolved"),
        ("m.py", 1, 4, "", "empty"),
    ],
)
def test_audit_judges_spans_by_their_own_content(
    tmp_path: Path, path: str, start: int, end: int, content: str, expected: str
) -> None:
    write(tmp_path, {"m.py": AUDITED})
    verdict = cb.classify(tmp_path, path, start, end, content, PATCH)
    assert verdict["class"] == expected
    assert (verdict["file"], verdict["start"], verdict["end"]) == (path, start, end)
    assert verdict.get("corrected_file") == ("m.py" if expected == "path" else None)


def test_audit_never_guesses_between_paths(tmp_path: Path) -> None:
    write(tmp_path, {"m.py": AUDITED, "b/m.py": AUDITED})
    verdict = cb.classify(tmp_path, "a/b/m.py", 1, 4, "def f(x):\nreturn y", PATCH)
    assert verdict["class"] == "unresolved" and "corrected_file" not in verdict
