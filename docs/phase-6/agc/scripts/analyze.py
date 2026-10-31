"""The preregistered decision rule (preregistration.md § Decision rule) over runs.json.

python3 analyze.py     # reads results/runs.json, writes results/report.json
"""

import json
import math
import random
import statistics
from pathlib import Path

RESULTS = Path(__file__).resolve().parents[1] / "results"
ARMS = ("B", "G", "T", "AGC")
rows = [r for r in json.loads((RESULTS / "runs.json").read_text()) if r["rep"] != "dev"]
tasks = sorted({r["task"] for r in rows})


def runs(arm: str, task: str | None = None) -> list[dict]:
    return [r for r in rows if r["arm"] == arm and (task is None or r["task"] == task)]


def per_task(arm: str) -> dict[str, dict[str, float]]:
    out = {}
    for t in tasks:
        rs = runs(arm, t)
        out[t] = {
            "f1": statistics.fmean(0.0 if r["technical_failure"] else r["f1"] for r in rs),
            "false": statistics.fmean(r["false_claims"] for r in rs),
            "tokens": statistics.median(r["tokens"] for r in rs),
            "tokens_uncached": statistics.median(r["tokens_uncached"] for r in rs),
            "calls": statistics.median(r["tool_calls"] for r in rs),
            "seconds": statistics.median(r["seconds"] for r in rs),
            "repeated": statistics.fmean(r["repeated_reads"] for r in rs),
            "oxide": statistics.fmean(r["oxide_calls"] for r in rs),
        }
    return out


def ratio(x: dict, b: dict, metric: str, add: float = 0.0) -> float:
    logs = [math.log((x[t][metric] + add) / (b[t][metric] + add)) for t in tasks]
    return math.exp(statistics.fmean(logs))


def boot(x: dict, b: dict, metric: str, n: int = 2000) -> list[float]:
    rng, deltas = random.Random(20261025), []
    for _ in range(n):
        pick = [rng.choice(tasks) for _ in tasks]
        deltas.append(statistics.fmean(x[t][metric] - b[t][metric] for t in pick))
    deltas.sort()
    return [round(deltas[int(0.025 * n)], 3), round(deltas[int(0.975 * n)], 3)]


table = {a: per_task(a) for a in ARMS}
b = table["B"]
report: dict = {"n_runs": len(rows), "tasks": tasks, "arms": {}}
for arm in ARMS:
    x, rs = table[arm], runs(arm)
    types = sorted({r["type"] for r in rs})
    s = {
        "runs": len(rs),
        "technical_failures": sum(r["technical_failure"] for r in rs),
        "used_oxide": sum(r["oxide_calls"] > 0 for r in rs),
        "f1": round(statistics.fmean(x[t]["f1"] for t in tasks), 3),
        "false_claims": round(statistics.fmean(r["false_claims"] for r in rs), 3),
        "tokens_median": statistics.median(r["tokens"] for r in rs),
        "tokens_uncached_median": statistics.median(r["tokens_uncached"] for r in rs),
        "calls_median": statistics.median(r["tool_calls"] for r in rs),
        "seconds_median": statistics.median(r["seconds"] for r in rs),
        "repeated_reads_mean": round(statistics.fmean(r["repeated_reads"] for r in rs), 2),
        "graph_calls": sum(r["graph_calls"] for r in rs),
        "tiers_calls": sum(r["tiers_calls"] for r in rs),
        "cost_usd": round(sum(r["cost_usd"] or 0 for r in rs), 3),
        "ratio_tokens": round(ratio(x, b, "tokens"), 3),
        "ratio_tokens_uncached": round(ratio(x, b, "tokens_uncached"), 3),
        "ratio_calls": round(ratio(x, b, "calls", 1.0), 3),
        "ratio_seconds": round(ratio(x, b, "seconds"), 3),
        "f1_delta_ci95": boot(x, b, "f1"),
        "per_type_f1": {
            ty: round(
                statistics.fmean(x[t]["f1"] for t in tasks if runs(arm, t)[0]["type"] == ty), 3
            )
            for ty in types
        },
        "per_task": {t: {k: round(v, 3) for k, v in x[t].items()} for t in tasks},
    }
    if arm != "B":
        base = report["arms"]["B"]
        worse = sum(x[t]["f1"] < b[t]["f1"] - 0.25 for t in tasks)
        e1 = s["ratio_tokens"] <= 0.85 and s["ratio_calls"] <= 1.0 and s["ratio_seconds"] <= 1.10
        e2 = s["f1"] >= base["f1"] + 0.10 and s["ratio_tokens"] <= 1.0
        s["tasks_worse_by_0.25"] = worse
        s["criteria"] = {
            "V": max(s["technical_failures"], base["technical_failures"]) <= 0.10 * len(rs),
            "A": s["used_oxide"] >= 0.5 * len(rs),
            "C": s["f1"] >= base["f1"] - 0.03 and worse <= 1,
            "F": s["false_claims"] <= base["false_claims"] + 0.25,
            "E": e1 or e2,
        }
        s["passes"] = all(s["criteria"].values())
    report["arms"][arm] = s

a = report["arms"]
singles = [s for s in ("G", "T") if a[s]["passes"]]
beats = all(
    a["AGC"]["ratio_tokens"] <= a[s]["ratio_tokens"] - 0.05 or a["AGC"]["f1"] >= a[s]["f1"] + 0.05
    for s in singles
)
if a["AGC"]["passes"] and beats:
    decision = "PROMOTE AGC"
elif singles:
    decision = "PROMOTE " + min(singles, key=lambda s: a[s]["ratio_tokens"])
else:
    decision = "REJECT"
report["decision"] = decision
(RESULTS / "report.json").write_text(json.dumps(report, indent=1) + "\n")
brief = {k: {m: v for m, v in s.items() if m != "per_task"} for k, s in a.items()}
print(json.dumps(brief, indent=1))
print(decision)
