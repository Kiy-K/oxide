"""The preregistered decision rule (preregistration.md § Decision rule) over runs.json.

python3 analyze.py     # reads results/runs.json, writes results/report.json
"""

import json
import math
import random
import statistics
from pathlib import Path

RESULTS = Path(__file__).resolve().parents[1] / "results"
ARMS = ("B", "Bcm", "Ad", "Acm")
rows = [r for r in json.loads((RESULTS / "runs.json").read_text()) if r["rep"] != "dev"]
tasks = sorted({r["task"] for r in rows})
kind = {r["task"]: r["type"] for r in rows}


def runs(arm: str, task: str | None = None) -> list[dict]:
    return [r for r in rows if r["arm"] == arm and (task is None or r["task"] == task)]


def per_task(arm: str) -> dict[str, dict[str, float]]:
    out = {}
    for t in tasks:
        rs = runs(arm, t)
        out[t] = {
            "f1": statistics.fmean(0.0 if r["technical_failure"] else r["f1"] for r in rs),
            "success": statistics.fmean(r["success"] for r in rs),
            "false": statistics.fmean(r["false_claims"] for r in rs),
            "tokens": statistics.median(r["tokens"] for r in rs),
            "cost_units": statistics.median(r["cost_units"] for r in rs),
            "calls": statistics.median(r["tool_calls"] for r in rs),
            "seconds": statistics.median(r["seconds"] for r in rs),
        }
    return out


def ratio(x: dict, b: dict, metric: str, add: float = 0.0) -> float:
    logs = [math.log((x[t][metric] + add) / (b[t][metric] + add)) for t in tasks]
    return math.exp(statistics.fmean(logs))


def cps(arm: str, pick: list[str]) -> float:
    """Cost units per successful run over the tasks in `pick` (with repeats)."""
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


table = {a: per_task(a) for a in ARMS}
b = table["B"]
report: dict = {"n_runs": len(rows), "tasks": tasks, "arms": {}}
for arm in ARMS:
    x, rs = table[arm], runs(arm)
    heavy = [r for r in rs if r["type"] != "lookup"]
    lookups = [r for r in rs if r["type"] == "lookup"]
    s = {
        "runs": len(rs),
        "technical_failures": sum(r["technical_failure"] for r in rs),
        "success_rate": mean(rs, "success"),
        "f1": round(statistics.fmean(x[t]["f1"] for t in tasks), 3),
        "false_claims": mean(rs, "false_claims"),
        "cost_units_total": round(sum(r["cost_units"] for r in rs)),
        "cps": round(cps(arm, tasks), 1),
        "ratio_cps": round(cps(arm, tasks) / cps("B", tasks), 3),
        "ratio_cps_ci95": cps_ci(arm) if arm != "B" else [1.0, 1.0],
        "ratio_tokens": round(ratio(x, b, "tokens"), 3),
        "ratio_cost_units": round(ratio(x, b, "cost_units"), 3),
        "ratio_calls": round(ratio(x, b, "calls", 1.0), 3),
        "ratio_seconds": round(ratio(x, b, "seconds"), 3),
        "first_prompt_median": statistics.median(r["first_prompt"] for r in rs),
        "startup_s_median": statistics.median(r["startup_s"] or 0 for r in rs),
        "tokens_median": statistics.median(r["tokens"] for r in rs),
        "seconds_median": statistics.median(r["seconds"] for r in rs),
        "builtin_calls_mean": mean(rs, "builtin_calls"),
        "grep_calls_mean": mean(rs, "grep_calls"),
        "read_calls_mean": mean(rs, "read_calls"),
        "repeated_reads_mean": mean(rs, "repeated_reads"),
        "oxide_calls_mean": mean(rs, "oxide_calls"),
        "graph_calls": sum(r["graph_calls"] for r in rs),
        "outline_calls": sum(r["outline_calls"] for r in rs),
        "oxide_chars_median_when_used": statistics.median(
            [r["oxide_chars"] for r in rs if r["oxide_calls"]] or [0]
        ),
        "used_oxide_heavy": f"{sum(r['oxide_calls'] > 0 for r in heavy)}/{len(heavy)}",
        "used_oxide_lookup": f"{sum(r['oxide_calls'] > 0 for r in lookups)}/{len(lookups)}",
        "cost_usd": round(sum(r["cost_usd"] or 0 for r in rs), 3),
        "per_type": {
            ty: {
                "f1": round(statistics.fmean(x[t]["f1"] for t in tasks if kind[t] == ty), 3),
                "success": round(
                    statistics.fmean(x[t]["success"] for t in tasks if kind[t] == ty), 3
                ),
                "false": round(statistics.fmean(x[t]["false"] for t in tasks if kind[t] == ty), 3),
                "cost_units": round(
                    statistics.median(x[t]["cost_units"] for t in tasks if kind[t] == ty)
                ),
            }
            for ty in sorted(set(kind.values()))
        },
        "per_task": {t: {k: round(v, 3) for k, v in x[t].items()} for t in tasks},
    }
    if arm in ("Ad", "Acm"):
        base = report["arms"]["B"]
        premium = s["success_rate"] >= base["success_rate"] + 0.10 or (
            base["false_claims"] >= 0.25 and s["false_claims"] <= 0.5 * base["false_claims"]
        )
        guard = s["ratio_tokens"] <= 1.50 and s["ratio_seconds"] <= 1.25
        heavy_used = sum(r["oxide_calls"] > 0 for r in heavy)
        s["premium_justified"] = premium
        s["criteria"] = {
            "V": max(s["technical_failures"], base["technical_failures"]) <= 0.10 * len(rs),
            "A": heavy_used >= 0.5 * len(heavy),
            "Q": s["success_rate"] >= base["success_rate"] - 0.05 and s["f1"] >= base["f1"] - 0.03,
            "F": s["false_claims"] <= base["false_claims"],
            "E": guard and s["ratio_cps"] <= (1.20 if premium else 1.00),
        }
        s["passes"] = all(s["criteria"].values())
    report["arms"][arm] = s

a = report["arms"]
passing = [x for x in ("Ad", "Acm") if a[x]["passes"]]
decision = "PROMOTE " + min(passing, key=lambda x: a[x]["ratio_cps"]) if passing else "REJECT"
report["decision"] = decision
report["overhead"] = {  # descriptive: the wrapper alone, and code mode against direct
    "Bcm/B": {k: round(a["Bcm"][k] / a["B"][k], 3) for k in ("first_prompt_median", "cps")},
    "Acm/Ad": {k: round(a["Acm"][k] / a["Ad"][k], 3) for k in ("first_prompt_median", "cps")},
}
(RESULTS / "report.json").write_text(json.dumps(report, indent=1) + "\n")
brief = {k: {m: v for m, v in s.items() if m != "per_task"} for k, s in a.items()}
print(json.dumps(brief, indent=1))
print(json.dumps(report["overhead"]))
print(decision)
