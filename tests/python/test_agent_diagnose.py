"""How an agent engaged with OXIDE, classified from its trace. Behaviour only: each class is
decided by a fact of the trace, not by the wording of the report."""

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

diagnose: ModuleType = importlib.import_module("diagnose")

GOLD = {"pkg/models.py"}
OXIDE_HIT = "# oxide context: 10/20 oxide-units-v1, 1 items\n### pkg/models.py:1-9 f\ndef f(): ..."
OXIDE_MISS = "# oxide context: 10/20 oxide-units-v1, 1 items\n### pkg/other.py:1-9 g\ndef g(): ..."


def trace(*calls: tuple[str, dict[str, Any], str]) -> list[dict[str, Any]]:
    lines = [
        json.dumps(
            {"type": "tool_use", "part": {"tool": tool, "state": {"input": inp, "output": out}}}
        )
        for tool, inp, out in calls
    ]
    parsed: list[dict[str, Any]] = diagnose.calls_of("\n".join(lines))
    return parsed


def test_a_run_whose_tool_never_connected_is_not_counted_as_ignoring_it() -> None:
    calls = trace(("grep", {}, "x"))
    assert diagnose.classify(calls, GOLD, connected=0) == "not connected"


def test_a_connected_tool_that_is_never_called_is_ignored() -> None:
    calls = trace(("grep", {"pattern": "f"}, "x"), ("read", {"path": "pkg/models.py"}, "x"))
    assert diagnose.classify(calls, GOLD, connected=1) == "connected, never called"


def test_builtins_that_read_the_gold_file_first_make_the_later_call_redundant() -> None:
    calls = trace(
        ("read", {"path": "pkg/models.py"}, "x"),
        ("oxide_oxide_search", {"mode": "context"}, OXIDE_HIT),
    )
    assert diagnose.classify(calls, GOLD, connected=1) == "called after builtins read gold"


def test_a_builtin_read_after_the_oxide_call_does_not_count_as_prior_localization() -> None:
    calls = trace(
        ("oxide_oxide_search", {"mode": "context"}, OXIDE_HIT),
        ("read", {"path": "pkg/models.py"}, "x"),
    )
    assert diagnose.classify(calls, GOLD, connected=1) == "called first, gold surfaced"


def test_a_first_call_that_lists_no_gold_file_is_classed_as_missing_it() -> None:
    calls = trace(("oxide_oxide_search", {"mode": "context"}, OXIDE_MISS))
    assert diagnose.classify(calls, GOLD, connected=1) == "called first, gold not surfaced"


def test_degraded_notes_are_extracted_from_the_bundle_header() -> None:
    out = "# oxide context: 1/2\n# degraded: routing stopped by its depth bound\n### a.py:1-2 x"
    assert diagnose.degraded(out) == ["routing stopped by its depth bound"]
