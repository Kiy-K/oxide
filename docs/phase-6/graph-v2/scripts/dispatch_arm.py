"""Routing over dispatch edges on ContextBench Python dev (preregistered in
docs/phase-6/graph-v2/preregistration.md). Patches the policy in-process; src/ is unchanged.

    uv run python docs/phase-6/graph-v2/scripts/dispatch_arm.py {B|T} OUT

B: unpatched (must reproduce baseline-v13's dev results). T: the router follows heuristic
``calls``/``references`` edges (receiver-aware method dispatch) like resolved ones.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "tests" / "python"))

import contextbench as cb

from oxide.core import routing


def main(variant: str, out: Path) -> None:
    if variant == "T":
        routing.follows = lambda kind, heuristic: kind in routing.FOLLOW
    tasks = cb.load_tasks()
    tasks["tasks"] = [t for t in tasks["tasks"] if t["split"] == "dev"]
    used = {t["corpus"] for t in tasks["tasks"]}
    tasks["corpora"] = {k: v for k, v in tasks["corpora"].items() if k in used}
    with tempfile.TemporaryDirectory(prefix="oxide-graph-v2-") as data:
        evaluated = cb.evaluate(tasks, Path(data))
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps(evaluated["results"], indent=1) + "\n")
    metrics = cb.aggregate(evaluated["results"])
    (out / "metrics.json").write_text(json.dumps(metrics, indent=1) + "\n")


if __name__ == "__main__":
    main(sys.argv[1], Path(sys.argv[2]))
