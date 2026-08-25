#!/usr/bin/env python3
"""Fast aggregate: budgeted-vs-hybrid over the pinned Tier A set (warm indexes)."""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, "/home/khoi/Projects/oxide/scripts/agent_eval")
import contextbench_run as cb  # noqa: E402

ROOT = cb.ROOT
ENV = {
    "OXIDE_EMBED_URL": os.environ.get("OXIDE_EMBED_URL", ""),
    "OXIDE_EMBED_MODEL": os.environ.get("OXIDE_EMBED_MODEL", ""),
}
PIN = ROOT / "eval-agent/results/tier_a_instances.txt"
ALLOW = {i.strip() for i in PIN.read_text().splitlines() if i.strip()}


def f1(g):
    p, c = g["precision"], g["coverage"]
    return 2 * p * c / (p + c) if p + c else 0.0


tasks = [t for t in cb.load_tasks() if t["instance_id"] in ALLOW]
rows = []
for row in tasks:
    repo = cb.ensure_repo_checkout(row["repo_url"], row["base_commit"])
    problem = row["problem_statement"]
    hy_items, hy_tok = cb.retrieve(repo, "hybrid", problem)
    bd_items, bd_tok = cb.retrieve(repo, "budgeted", problem)
    hy_m = cb.evaluate_task(repo, row, hy_items)
    bd_m = cb.evaluate_task(repo, row, bd_items)
    gold = cb.Gold({"init_ctx": json.loads(row["gold_context"]), "repo_url": row["repo_url"], "commit": row["base_commit"]})
    g_f = set(gold.files())
    hy_f = {i["file"] for i in hy_items}
    bd_f = {i["file"] for i in bd_items}
    lost = len((g_f & hy_f) - bd_f)
    noise = len(bd_f - g_f - hy_f)
    rows.append(
        (
            f1(hy_m["file"]), f1(bd_m["file"]),
            f1(hy_m["line"]), f1(bd_m["line"]),
            bd_tok, hy_tok, lost, noise,
        )
    )

n = len(rows)
mean = lambda i: sum(r[i] for r in rows) / n
print(f"rows={n}")
print(f"file-F1  hybrid={mean(0):.3f} budgeted={mean(1):.3f}")
print(f"line-F1  hybrid={mean(2):.3f} budgeted={mean(3):.3f}")
print(f"tokens   budgeted={mean(4):.0f} hybrid={mean(5):.0f}")
print(f"lost_total={sum(r[6] for r in rows)} noise_mean={mean(7):.2f}")
w = sum(1 for r in rows if r[1] > r[0])
t = sum(1 for r in rows if r[1] == r[0])
l = sum(1 for r in rows if r[1] < r[0])
print(f"file-F1 win/tie/loss = {w}/{t}/{l}")
