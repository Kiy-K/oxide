"""The preregistered criteria (preregistration.md § Criteria) over results/runs.json.

analyze.py RUNS.json OUT.json
"""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path


def mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def main() -> None:
    runs = [r for r in json.loads(Path(sys.argv[1]).read_text()) if r["rep"] != "dev"]
    arm = {a: [r for r in runs if r["arm"] == a] for a in ("A", "B")}
    shape = {a: [r for r in rs if r["kind"] == "shape"] for a, rs in arm.items()}
    control = {a: [r for r in rs if r["kind"] == "control"] for a, rs in arm.items()}
    tasks = sorted({r["task"] for r in runs})

    def task_f1(a: str, t: str) -> float:
        return mean([r["f1"] for r in arm[a] if r["task"] == t])

    failures = {a: sum(r["technical_failure"] for r in rs) for a, rs in arm.items()}
    adopted = sum(r["structural_calls"] > 0 for r in shape["B"])
    shape_f1 = {a: mean([r["f1"] for r in rs]) for a, rs in shape.items()}
    worse = [t for t in tasks if t.startswith("S") and task_f1("B", t) < task_f1("A", t) - 0.10]
    calls = sum(r["structural_calls"] for r in arm["B"])
    errors = sum(r["structural_errors"] for r in arm["B"])
    control_f1 = {a: mean([r["f1"] for r in rs]) for a, rs in control.items()}
    tokens = {a: statistics.median(r["tokens"] for r in rs) for a, rs in arm.items()}
    wall = {a: statistics.median(r["wall_s"] for r in rs) for a, rs in arm.items()}
    criteria = {
        "V": all(failures[a] <= 0.10 * len(arm[a]) for a in arm),
        "P1": adopted >= 0.5 * len(shape["B"]),
        "P2": shape_f1["B"] - shape_f1["A"] >= 0.10 and len(worse) <= 2,
        "P3": calls > 0 and (calls - errors) >= 0.75 * calls,
        "P4": control_f1["B"] >= control_f1["A"] - 0.10,
        "P5": tokens["B"] <= 1.25 * tokens["A"] and wall["B"] <= 1.25 * wall["A"],
    }
    if not criteria["V"]:
        verdict = "INCONCLUSIVE"
    elif all(criteria.values()):
        verdict = "PROMOTE"
    elif not all(criteria[k] for k in ("P1", "P2", "P4", "P5")):
        verdict = "REJECT"
    else:
        verdict = "INCONCLUSIVE"
    used = [r["f1"] for r in shape["B"] if r["structural_calls"]]
    unused = [r["f1"] for r in shape["B"] if not r["structural_calls"]]
    report = {
        "runs": {a: len(rs) for a, rs in arm.items()},
        "technical_failures": failures,
        "structural_adoption": f"{adopted}/{len(shape['B'])}",
        "shape_f1": shape_f1,
        "shape_f1_delta": shape_f1["B"] - shape_f1["A"],
        "tasks_where_B_worse_by_0.10": worse,
        "structural_calls": calls,
        "structural_errors": errors,
        "control_f1": control_f1,
        "median_tokens": tokens,
        "median_wall_s": wall,
        "criteria": criteria,
        "verdict": verdict,
        "per_task": {
            t: {
                a: {
                    "f1": [round(r["f1"], 3) for r in arm[a] if r["task"] == t],
                    "precision": [round(r["precision"], 3) for r in arm[a] if r["task"] == t],
                    "recall": [round(r["recall"], 3) for r in arm[a] if r["task"] == t],
                    "structural_calls": [r["structural_calls"] for r in arm[a] if r["task"] == t],
                    "tokens": [r["tokens"] for r in arm[a] if r["task"] == t],
                    "wall_s": [r["wall_s"] for r in arm[a] if r["task"] == t],
                }
                for a in arm
            }
            for t in tasks
        },
        "descriptive": {
            "B_shape_f1_with_structural": [mean(used), len(used)],
            "B_shape_f1_without_structural": [mean(unused), len(unused)],
            "B_control_runs_using_structural": sum(r["structural_calls"] > 0 for r in control["B"]),
            "mean_cost_usd": {a: mean([r["cost_usd"] or 0 for r in rs]) for a, rs in arm.items()},
        },
    }
    Path(sys.argv[2]).write_text(json.dumps(report, indent=1) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k != "per_task"}, indent=1))


if __name__ == "__main__":
    main()
