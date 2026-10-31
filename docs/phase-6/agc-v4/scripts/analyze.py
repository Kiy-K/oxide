"""AGC v4's preregistered rules (preregistration.md § Decision rule) over runs.json.

python3 analyze.py stage1     # after rep 1: continue or stop -> results/stage1.json
python3 analyze.py            # after all reps -> results/report.json and the decision
python3 analyze.py invalid    # server-arm runs whose first step lacked the tool schema
"""

import json
import math
import random
import statistics
import sys
from pathlib import Path

RESULTS = Path(__file__).resolve().parents[1] / "results"
ARMS = ("B", "S", "T", "F")
# first-step tokens each arm adds to B's (dev task: schema, plus F's forcing line)
EXPECTED = {"S": 615, "T": 702, "F": 633}
NAV = {"callers", "callees", "outline"}
STRUCTURAL = {"callers", "impact", "callees", "overrides"}
STAGE = sys.argv[1] if len(sys.argv) > 1 else "final"

rows = [r for r in json.loads((RESULTS / "runs.json").read_text()) if r["rep"].startswith("r")]
if STAGE == "stage1":
    rows = [r for r in rows if r["rep"] == "r1"]
tasks = sorted({r["task"] for r in rows})
base_first = {t: min(r["first_prompt"] for r in rows if r["task"] == t and r["arm"] == "B")
              for t in tasks}  # fmt: skip
for r in rows:
    r["exposure"] = r["first_prompt"] - base_first[r["task"]]
    r["schema_ok"] = r["arm"] == "B" or r["exposure"] >= EXPECTED[r["arm"]] / 2
    r["nav"] = any(m in NAV for m in r["modes"])
if STAGE == "invalid":
    for r in rows:
        if not r["schema_ok"]:
            print(r["rep"], r["arm"], r["task"], r["first_prompt"])
    raise SystemExit


def runs(arm: str, task: str | None = None) -> list[dict]:
    return [r for r in rows if r["arm"] == arm and (task is None or r["task"] == task)]


def mean(rs: list[dict], key: str) -> float:
    return round(statistics.fmean(float(r[key]) for r in rs), 3) if rs else 0.0


def med(arm: str, task: str, key: str) -> float:
    return statistics.median(r[key] for r in runs(arm, task))


def ratio(arm: str, key: str, add: float = 0.0) -> float:
    logs = [math.log((med(arm, t, key) + add) / (med("B", t, key) + add)) for t in tasks]
    return round(math.exp(statistics.fmean(logs)), 3)


def cps(arm: str, pick: list[str]) -> float:
    rs = [r for t in pick for r in runs(arm, t)]
    wins = sum(r["success"] for r in rs)
    return sum(r["cost_units"] for r in rs) / wins if wins else math.inf


def cps_ci(arm: str, other: str = "B", n: int = 2000) -> list[float]:
    rng, out = random.Random(20261025), []
    for _ in range(n):
        pick = [rng.choice(tasks) for _ in tasks]
        out.append(cps(arm, pick) / cps(other, pick))
    out.sort()
    return [round(out[int(0.025 * n)], 3), round(out[int(0.975 * n)], 3)]


def vs_b(rs: list[dict]) -> float | None:
    logs = [math.log(r["cost_units"] / med("B", r["task"], "cost_units")) for r in rs]
    return round(math.exp(statistics.fmean(logs)), 3) if logs else None


def summary(arm: str) -> dict:
    rs = runs(arm)
    structural = [r for r in rs if r["type"] in STRUCTURAL]
    lookups = [r for r in rs if r["type"] == "lookup"]
    used = [r for r in rs if r["oxide_calls"]]
    return {
        "runs": len(rs),
        "technical_failures": sum(r["technical_failure"] for r in rs),
        "schema_missing": sum(not r["schema_ok"] for r in rs),
        "success_rate": mean(rs, "success"),
        "f1": round(statistics.fmean(mean(runs(arm, t), "f1") for t in tasks), 3),
        "false_claims": mean(rs, "false_claims"),
        "precision": mean(rs, "precision"),
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
        "nav_use_structural": mean(structural, "nav"),
        "nav_use_lookup": mean(lookups, "nav"),
        "oxide_use_lookup": round(sum(r["oxide_calls"] > 0 for r in lookups) / len(lookups), 3)
        if lookups else 0.0,
        "oxide_calls_mean": mean(rs, "oxide_calls"),
        "modes_total": {m: sum(r["modes"].get(m, 0) for r in rs)
                        for m in ("search", "context", "callers", "callees", "outline")},
        "first_call_oxide": sum(str(r["first_tool"]).startswith("agc_") for r in rs),
        "exposure_first_step_tokens": statistics.median(r["exposure"] for r in rs),
        "cost_vs_b_not_invoked": vs_b([r for r in rs if not r["oxide_calls"]]),
        "cost_vs_b_invoked": vs_b(used),
        "n_invoked": len(used),
        "per_type": {ty: {"success": mean([r for r in rs if r["type"] == ty], "success"),
                          "false": mean([r for r in rs if r["type"] == ty], "false_claims"),
                          "nav": mean([r for r in rs if r["type"] == ty], "nav")}
                     for ty in sorted({r["type"] for r in rows})},
        "per_lang": {lg: {"success": mean([r for r in rs if r["lang"] == lg], "success"),
                          "false": mean([r for r in rs if r["lang"] == lg], "false_claims")}
                     for lg in sorted({r["lang"] for r in rows})},
        "per_task": {t: {"success": mean(runs(arm, t), "success"),
                         "false": mean(runs(arm, t), "false_claims"),
                         "nav": sum(r["nav"] for r in runs(arm, t))} for t in tasks},
    }  # fmt: skip


a = {arm: summary(arm) for arm in ARMS}
b, s, t, f = a["B"], a["S"], a["T"], a["F"]
report: dict = {"stage": STAGE, "n_runs": len(rows), "tasks": tasks, "arms": a}
valid = all(x["technical_failures"] <= 0.10 * x["runs"] and not x["schema_missing"]
            for x in a.values()) and f["first_call_oxide"] >= 0.9 * f["runs"]  # fmt: skip
report["valid"] = valid
if STAGE == "stage1":
    adoption = max(s["nav_use_structural"], t["nav_use_structural"]) >= 0.5
    utility = (sum(r["success"] for r in runs("F")) >= sum(r["success"] for r in runs("B")) + 1
               and f["false_claims"] <= b["false_claims"])  # fmt: skip
    report["signals"] = {"adoption": adoption, "utility": utility}
    report["decision"] = "INVALID" if not valid else "CONTINUE" if adoption or utility else "STOP"
else:
    split_wins = (t["success_rate"] - s["success_rate"] >= 0.10 and t["cps"] <= s["cps"]) or (
        t["nav_use_structural"] - s["nav_use_structural"] >= 0.25
        and t["success_rate"] >= s["success_rate"] - 0.05)  # fmt: skip
    pick = "T" if split_wins else "S"
    x = a[pick]
    adoption = x["nav_use_structural"] >= 0.50
    premium = x["success_rate"] >= b["success_rate"] + 0.10 or (
        b["false_claims"] >= 0.25 and x["false_claims"] <= 0.5 * b["false_claims"]
    )
    gate = adoption and all((
        x["success_rate"] >= b["success_rate"] - 0.05, x["f1"] >= b["f1"] - 0.03,
        x["false_claims"] <= b["false_claims"], x["ratio_tokens"] <= 1.50,
        x["ratio_seconds"] <= 1.25, x["ratio_cps"] <= (1.20 if premium else 1.00)))  # fmt: skip
    utility = (f["success_rate"] >= b["success_rate"] + 0.10
               and f["false_claims"] <= b["false_claims"]) or (
        f["success_rate"] >= b["success_rate"] - 0.05 and f["ratio_cps"] <= 0.90)  # fmt: skip
    report["interface"] = {"pick": pick, "split_clear_advantage": split_wins,
                           "cps_T_over_S_ci95": cps_ci("T", "S")}  # fmt: skip
    report["criteria"] = {"adoption_A": adoption, "gate_P": gate, "utility_U": utility,
                          "premium": premium}  # fmt: skip
    if not valid:
        report["decision"] = "INVALID"
    elif gate:
        report["decision"] = f"PROMOTE {pick}: ADR-0013 may move to Accepted ({pick} shape)"
    elif utility and not adoption:
        report["decision"] = "ADOPTION-BOUND: useful when used, not picked; ADR-0013 stays Proposed"
    elif utility:
        report["decision"] = (
            "COST-BOUND: picked and useful, fails the gate; ADR-0013 stays Proposed"
        )
    else:
        report["decision"] = "STOP: no utility even when used; withdraw ADR-0013"
(RESULTS / ("stage1.json" if STAGE == "stage1" else "report.json")).write_text(
    json.dumps(report, indent=1) + "\n")  # fmt: skip
print(json.dumps({k: {m: v for m, v in x.items() if m != "per_task"} for k, x in a.items()},
                 indent=1))  # fmt: skip
print(json.dumps({k: report.get(k) for k in ("valid", "signals", "interface", "criteria",
                                             "decision")}))  # fmt: skip
