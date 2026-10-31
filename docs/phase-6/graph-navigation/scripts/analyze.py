"""Macro metrics and the Stage 1 gate (preregistration § Stage 1 → Stage 2 gate).

python analyze.py   # reads results/rows.json, writes results/report.json
"""

import json
import statistics

from common import RESULTS

rows = json.loads((RESULTS / "rows.json").read_text())


def mean(values: list[float]) -> float:
    return round(statistics.fmean(values), 3)


def med(values: list[float]) -> float:
    return round(statistics.median(values), 1)


report: dict = {"n": len(rows)}
for subset, members in (
    ("all", rows),
    ("function", [r for r in rows if r["kind"] == "function"]),
    ("method", [r for r in rows if r["kind"] == "method"]),
):
    s = report.setdefault(subset, {"n": len(members)})
    for q, d in (("q1", 1), ("q2", 2)):
        for arm in "GIP":
            s[f"{arm}.{q}"] = {m: mean([r[arm][q][m] for r in members]) for m in ("p", "r", "f1")}
            s[f"{arm}.{q}"]["tok_med"] = med([r[arm][f"tok{d}"] for r in members])
            s[f"{arm}.{q}"]["tok_sum"] = sum(r[arm][f"tok{d}"] for r in members)
        s[f"C.{q}"] = {"r": mean([r["C"][f"r{d}"] for r in members])}
    s["C.tok_med"] = med([r["C"]["tok"] for r in members])
    s["G.q2.calls_med"] = med([r["G"]["calls2"] for r in members])
    s["ms_med"] = {
        "G.q1": med([r["G"]["ms1"] for r in members]),
        "C": med([r["C"]["ms"] for r in members]),
        "I.q1": med([r["I"]["ms1"] for r in members]),
        "I.q2": med([r["I"]["ms2"] for r in members]),
    }
    valid = [sum(r["I"]["path_valid"][i] for r in members) for i in (0, 1)]
    chain = [sum(r["I"]["chain_valid"][i] for r in members) for i in (0, 1)]
    s["I.path_precision"] = [*valid, round(valid[0] / valid[1], 3) if valid[1] else None]
    s["I.chain_precision"] = [*chain, round(chain[0] / chain[1], 3) if chain[1] else None]

incomplete = [r for r in rows if r["I"]["q1"]["r"] < 1]
complete = [r for r in rows if r["I"]["q1"]["r"] == 1]


def fires(r: dict) -> bool:
    return r["I"]["unresolved"] > 0 or r["I"]["stopped_by"] is not None


report["signal"] = {
    "incomplete": len(incomplete),
    "sensitivity": round(sum(map(fires, incomplete)) / len(incomplete), 3) if incomplete else None,
    "specificity": (
        round(sum(not fires(r) for r in complete) / len(complete), 3) if complete else None
    ),
    "empty_and_incomplete": sum(r["I"]["n1"] == 0 for r in incomplete),
}
a = report["all"]
gate = {}
for q in ("q1", "q2"):
    for arm in "IP":
        gain = round(a[f"{arm}.{q}"]["f1"] - a[f"G.{q}"]["f1"], 3)
        cheaper = a[f"{arm}.{q}"]["tok_sum"] <= a[f"G.{q}"]["tok_sum"]
        gate[f"{arm}.{q}"] = {
            "f1_gain": gain,
            "tokens_ok": cheaper,
            "pass": gain >= 0.10 and cheaper,
        }
gate["signal_ok"] = (report["signal"]["sensitivity"] or 0) >= 0.9
gate["PASS"] = gate["signal_ok"] and any(v["pass"] for k, v in gate.items() if "." in k)
report["gate"] = gate
(RESULTS / "report.json").write_text(json.dumps(report, indent=1))
print(json.dumps(report, indent=1))
