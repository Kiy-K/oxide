#!/usr/bin/env python3
"""Aggregate docs/term-coverage-eval/results/results.jsonl into a decision
table: mean metrics per alpha, per-query win/loss/tie vs baseline (alpha=0.0)
on precision@5, and search latency percentiles.

Usage:
    python3 scripts/agent_eval/summarize_term_coverage.py [results.jsonl]
"""
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PATH = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "docs/term-coverage-eval/results/results.jsonl"

rows = [json.loads(line) for line in PATH.read_text().splitlines() if line.strip()]
by_alpha = defaultdict(list)
for r in rows:
    by_alpha[r["alpha"]].append(r)

metrics = ["precision_at_5", "recall_at_5", "mrr", "ndcg_at_10", "relevant_items", "used_tokens"]
print(f"{'alpha':>6} " + " ".join(f"{m:>16}" for m in metrics) + f" {'gold_in_ctx':>12} {'p50_s':>8} {'p95_s':>8}")
for alpha in sorted(by_alpha, key=float):
    group = by_alpha[alpha]
    n = len(group)
    means = {m: statistics.mean(r[m] for r in group) for m in metrics}
    gold_rate = sum(1 for r in group if r["gold_in_context"]) / n
    lat = sorted(r["search_seconds"] for r in group)
    p50 = lat[len(lat) // 2]
    p95 = lat[min(len(lat) - 1, int(len(lat) * 0.95))]
    print(
        f"{alpha:>6} "
        + " ".join(f"{means[m]:>16.4f}" for m in metrics)
        + f" {gold_rate:>12.3f} {p50:>8.3f} {p95:>8.3f}"
    )

# Per-query win/loss/tie vs baseline on precision@5 (the metric the task
# names first) and on the composite (precision_at_5, mrr) tuple.
baseline = {r["instance_id"]: r for r in by_alpha.get("0.0", [])}
print("\nper-alpha win/loss/tie vs alpha=0.0 baseline, precision@5:")
for alpha in sorted(by_alpha, key=float):
    if alpha == "0.0":
        continue
    wins = losses = ties = 0
    regressions = []
    for r in by_alpha[alpha]:
        base = baseline.get(r["instance_id"])
        if base is None:
            continue
        if r["precision_at_5"] > base["precision_at_5"]:
            wins += 1
        elif r["precision_at_5"] < base["precision_at_5"]:
            losses += 1
            regressions.append(r["instance_id"])
        else:
            ties += 1
    print(f"  alpha={alpha}: wins={wins} losses={losses} ties={ties}"
          + (f"  regressions={regressions}" if regressions else ""))

print("\nper-alpha win/loss/tie vs alpha=0.0 baseline, gold_in_context:")
for alpha in sorted(by_alpha, key=float):
    if alpha == "0.0":
        continue
    wins = losses = ties = 0
    for r in by_alpha[alpha]:
        base = baseline.get(r["instance_id"])
        if base is None:
            continue
        if r["gold_in_context"] and not base["gold_in_context"]:
            wins += 1
        elif not r["gold_in_context"] and base["gold_in_context"]:
            losses += 1
        else:
            ties += 1
    print(f"  alpha={alpha}: wins={wins} losses={losses} ties={ties}")
