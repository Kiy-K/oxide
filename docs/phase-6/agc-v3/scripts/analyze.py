"""AGC v3's preregistered rules (preregistration.md § Decision rule) over runs.json.

    python3 analyze.py stage1     # after rep 1: continue or stop
    python3 analyze.py            # after all reps: results/report.json and the decision
    python3 analyze.py invalid    # runs whose first step lacked the tool schema

Reads results/runs.json (`trial.py grade`) and, for the first tool call, the raw records.
"""

import json
import math
import random
import statistics
import sys
from pathlib import Path

RESULTS = Path(__file__).resolve().parents[1] / "results"
DATA = Path.home() / "Projects/oxide-eval-data/oxide-agc3-eval"
ARMS = ("B", "D", "G", "F")
# system-prompt tokens each arm adds to B's first step (measured on the dev task), and
# the tool schema's own size: a server arm's first step must carry both
EXTRA, SCHEMA = {"D": 0, "G": 163, "F": 194}, 287
STAGE = sys.argv[1] if len(sys.argv) > 1 else "final"

rows = [r for r in json.loads((RESULTS / "runs.json").read_text()) if r["rep"] != "dev"]
if STAGE == "stage1":
    rows = [r for r in rows if r["rep"] == "r1"]
tasks = sorted({r["task"] for r in rows})
kind = {r["task"]: r["type"] for r in rows}
base_first = {t: min(r["first_prompt"] for r in rows if r["task"] == t and r["arm"] == "B")
              for t in tasks}  # fmt: skip
for r in rows:
    path = DATA / "runs" / r["rep"] / r["arm"] / r["task"] / "record.json"
    record = json.loads(path.read_text())
    r["first_tool"] = record["tools"][0][0] if record["tools"] else None
    r["exposure"] = r["first_prompt"] - base_first[r["task"]]
    r["schema_ok"] = r["arm"] == "B" or r["exposure"] - EXTRA[r["arm"]] >= SCHEMA // 2

if STAGE == "invalid":
    for r in rows:
        if not r["schema_ok"]:
            print(r["rep"], r["arm"], r["task"], r["first_prompt"])
    raise SystemExit


def runs(arm: str, task: str | None = None) -> list[dict]:
    return [r for r in rows if r["arm"] == arm and (task is None or r["task"] == task)]


def med(arm: str, task: str, key: str) -> float:
    return statistics.median(r[key] for r in runs(arm, task))


def ratio(arm: str, key: str, add: float = 0.0) -> float:
    logs = [math.log((med(arm, t, key) + add) / (med("B", t, key) + add)) for t in tasks]
    return round(math.exp(statistics.fmean(logs)), 3)


def cps(arm: str, pick: list[str]) -> float:
    cost = sum(r["cost_units"] for t in pick for r in runs(arm, t))
    wins = sum(r["success"] for t in pick for r in runs(arm, t))
    return cost / wins if wins else math.inf


def cps_ci(arm: str, n: int = 2000) -> list[float]:
    rng, out = random.Random(20261025), []
    for _ in range(n):
        pick = [rng.choice(tasks) for _ in tasks]
        out.append(cps(arm, pick) / cps("B", pick))
    out.sort()
    return [round(out[int(0.025 * n)], 3), round(out[int(0.975 * n)], 3)]


def mean(rs: list[dict], key: str) -> float:
    return round(statistics.fmean(r[key] for r in rs), 3) if rs else 0.0


def vs_b(rs: list[dict]) -> float | None:
    """Geometric mean of each run's cost units over B's median on the same task."""
    logs = [math.log(r["cost_units"] / med("B", r["task"], "cost_units")) for r in rs]
    return round(math.exp(statistics.fmean(logs)), 3) if logs else None


def summary(arm: str) -> dict:
    rs = runs(arm)
    used = [r for r in rs if r["oxide_calls"]]
    return {
        "runs": len(rs),
        "technical_failures": sum(r["technical_failure"] for r in rs),
        "schema_missing": sum(not r["schema_ok"] for r in rs),
        "success_rate": mean(rs, "success"),
        "f1": round(statistics.fmean(mean(runs(arm, t), "f1") for t in tasks), 3),
        "false_claims": mean(rs, "false_claims"),
        "cps": round(cps(arm, tasks), 1),
        "ratio_cps": round(cps(arm, tasks) / cps("B", tasks), 3),
        "ratio_cps_ci95": cps_ci(arm) if arm != "B" else [1.0, 1.0],
        "ratio_tokens": ratio(arm, "tokens"),
        "ratio_seconds": ratio(arm, "seconds"),
        "ratio_calls": ratio(arm, "tool_calls", 1.0),
        "grep_calls_mean": mean(rs, "grep_calls"),
        "read_calls_mean": mean(rs, "read_calls"),
        "repeated_reads_mean": mean(rs, "repeated_reads"),
        "oxide_use_rate": round(len(used) / len(rs), 3),
        "graph_use_rate": round(sum(r["graph_calls"] > 0 for r in rs) / len(rs), 3),
        "oxide_calls_mean": mean(rs, "oxide_calls"),
        "first_call_oxide": sum(str(r["first_tool"]).startswith("agc_") for r in rs),
        # exposure: the tool and its prompt are listed; invocation: the tool is called
        "exposure_first_step_tokens": statistics.median(r["exposure"] for r in rs),
        "exposure_cost_est_mean": round(
            statistics.fmean(r["exposure"] * (1 + 0.1 * (r["steps"] - 1)) for r in rs), 1
        ),
        "cost_vs_b_not_invoked": vs_b([r for r in rs if not r["oxide_calls"]]),
        "cost_vs_b_invoked": vs_b(used),
        "n_invoked": len(used),
        "oxide_output_tokens_est_mean_when_used": round(
            statistics.fmean(r["oxide_chars"] / 4 for r in used), 1
        )
        if used
        else 0,
        "oxide_ms_mean_when_used": mean(used, "oxide_ms"),
        "per_type": {
            ty: {
                "success": mean([r for r in rs if r["type"] == ty], "success"),
                "false": mean([r for r in rs if r["type"] == ty], "false_claims"),
                "oxide_use": round(
                    statistics.fmean(bool(r["oxide_calls"]) for r in rs if r["type"] == ty), 3
                ),
            }
            for ty in sorted(set(kind.values()))
        },
        "per_task": {
            t: {
                "success": mean(runs(arm, t), "success"),
                "f1": mean(runs(arm, t), "f1"),
                "false": mean(runs(arm, t), "false_claims"),
                "oxide": sum(r["oxide_calls"] > 0 for r in runs(arm, t)),
            }
            for t in tasks
        },
    }


a = {arm: summary(arm) for arm in ARMS}
b, d, g, f = a["B"], a["D"], a["G"], a["F"]
report: dict = {"stage": STAGE, "n_runs": len(rows), "tasks": tasks, "arms": a}
valid = all(x["technical_failures"] <= 0.10 * x["runs"] and not x["schema_missing"]
            for x in a.values()) and f["first_call_oxide"] >= 0.9 * f["runs"]  # fmt: skip
report["valid"] = valid
if STAGE == "stage1":
    adoption = g["oxide_use_rate"] >= 0.5 and g["oxide_use_rate"] > d["oxide_use_rate"]
    utility = (sum(r["success"] for r in runs("F")) >= sum(r["success"] for r in runs("B")) + 1
               and f["false_claims"] <= b["false_claims"])  # fmt: skip
    report["signals"] = {"adoption": adoption, "utility": utility}
    report["decision"] = "INVALID" if not valid else "CONTINUE" if adoption or utility else "STOP"
else:
    utility = (f["success_rate"] >= b["success_rate"] + 0.10
               and f["false_claims"] <= b["false_claims"]) or (
        f["success_rate"] >= b["success_rate"] - 0.05 and f["ratio_cps"] <= 0.90)  # fmt: skip
    adoption = g["oxide_use_rate"] >= 0.5 and g["oxide_use_rate"] >= d["oxide_use_rate"] + 0.2
    premium = g["success_rate"] >= b["success_rate"] + 0.10 or (
        b["false_claims"] >= 0.25 and g["false_claims"] <= 0.5 * b["false_claims"]
    )
    product = adoption and all((
        g["success_rate"] >= b["success_rate"] - 0.05, g["f1"] >= b["f1"] - 0.03,
        g["false_claims"] <= b["false_claims"], g["ratio_tokens"] <= 1.50,
        g["ratio_seconds"] <= 1.25, g["ratio_cps"] <= (1.20 if premium else 1.00)))  # fmt: skip
    report["criteria"] = {"utility_U": utility, "adoption_A": adoption, "product_P": product,
                          "premium": premium}  # fmt: skip
    if not valid:
        report["decision"] = "INVALID"
    elif product:
        report["decision"] = "PROMOTE G: propose an ADR for direct oxide_context with guidance"
    elif utility and not adoption:
        report["decision"] = "ADOPTION-BOUND: the tool helps when used; next is client guidance"
    elif utility:
        report["decision"] = "COST-BOUND: adopted and useful but fails the gate; next is output"
    else:
        report["decision"] = "STOP: no utility even when used; keep the graph API-only"
name = "stage1.json" if STAGE == "stage1" else "report.json"
(RESULTS / name).write_text(json.dumps(report, indent=1) + "\n")
brief = {k: {m: v for m, v in s.items() if m != "per_task"} for k, s in a.items()}
print(json.dumps(brief, indent=1))
print(json.dumps({k: report.get(k) for k in ("valid", "signals", "criteria", "decision")}))
