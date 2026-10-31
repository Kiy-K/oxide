"""Post-hoc (not preregistered): why AGC v3's unguided arm D did better than guided G.

    python3 v3_dg.py      # reads ../../agc-v3/results/runs.json and the raw MCP logs

v3's verdict (PROMOTE G) is unchanged; this only explains the D-G difference.
"""

import json
import math
import random
import statistics
from pathlib import Path

V3 = Path(__file__).resolve().parents[2] / "agc-v3/results/runs.json"
RAW = Path.home() / "Projects/oxide-eval-data/oxide-agc3-eval/runs"
rows = [r for r in json.loads(V3.read_text()) if r["rep"].startswith("r")]
for r in rows:
    log = RAW / r["rep"] / r["arm"] / r["task"] / "mcp.jsonl"
    calls = [json.loads(x)["args"] for x in log.read_text().splitlines()] if log.exists() else []
    r["graph_targets"] = [c["target"] for c in calls if c["relation"]]
    r["depths"] = [c["depth"] for c in calls if c["relation"]]


def fisher(a: int, n: int, b: int, m: int) -> float:
    """Two-sided Fisher exact p for a/n vs b/m successes."""
    total, wins = n + m, a + b

    def p(x: int) -> float:
        return math.comb(n, x) * math.comb(m, wins - x) / math.comb(total, wins)

    observed = p(a)
    return sum(p(x) for x in range(max(0, wins - m), min(n, wins) + 1) if p(x) <= observed + 1e-12)


def arm(a: str) -> list[dict]:
    return [r for r in rows if r["arm"] == a]


def cps(rs: list[dict]) -> float:
    wins = sum(r["success"] for r in rs)
    return sum(r["cost_units"] for r in rs) / wins if wins else math.inf


out = {}
d, g = arm("D"), arm("G")
wins_d, wins_g = sum(r["success"] for r in d), sum(r["success"] for r in g)
out["successes"] = {"D": wins_d, "G": wins_g, "n": len(d),
                    "fisher_p": round(fisher(wins_d, len(d), wins_g, len(g)), 3)}  # fmt: skip
tasks = sorted({r["task"] for r in rows})
rng, ratios = random.Random(7), []
for _ in range(2000):
    pick = [rng.choice(tasks) for _ in tasks]
    ratios.append(cps([r for t in pick for r in g if r["task"] == t])
                  / cps([r for t in pick for r in d if r["task"] == t]))  # fmt: skip
ratios.sort()
out["cps_G_over_D"] = {"point": round(cps(g) / cps(d), 3),
                       "ci95": [round(ratios[50], 3), round(ratios[1949], 3)]}  # fmt: skip
for a in ("D", "G", "F"):
    rs = arm(a)
    out[a] = {
        "oxide_calls_mean": round(statistics.fmean(r["oxide_calls"] for r in rs), 2),
        "graph_calls_on_other_than_target_share": round(
            sum(len(r["graph_targets"][1:]) for r in rs)
            / max(1, sum(len(r["graph_targets"]) for r in rs)),
            3,
        ),
        "depth_2plus_share": round(
            sum(x >= 2 for r in rs for x in r["depths"])
            / max(1, sum(len(r["depths"]) for r in rs)),
            3,
        ),
        "steps_mean": round(statistics.fmean(r["steps"] for r in rs), 2),
        "tokens_mean": round(statistics.fmean(r["tokens"] for r in rs)),
        "answers_mean": round(statistics.fmean(r["answers"] for r in rs), 2),
        "grep_mean": round(statistics.fmean(r["grep_calls"] for r in rs), 2),
        "precision_mean": round(statistics.fmean(r["precision"] for r in rs), 3),
        "recall_mean": round(statistics.fmean(r["recall"] for r in rs), 3),
        "per_task_success": {t: sum(r["success"] for r in rs if r["task"] == t) for t in tasks},
    }
(Path(__file__).resolve().parents[1] / "results/v3_dg.json").write_text(
    json.dumps(out, indent=1) + "\n"
)
print(json.dumps(out, indent=1))
