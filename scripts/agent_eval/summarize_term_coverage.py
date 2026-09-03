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
has_file_relevance = any("gold_file_in_context" in r for r in rows)
header = f"{'alpha':>6} " + " ".join(f"{m:>16}" for m in metrics) + f" {'gold_in_ctx':>12}"
if has_file_relevance:
    header += f" {'gold_file_ctx':>13}"
header += f" {'p50_s':>8} {'p95_s':>8}"
print(header)
for alpha in sorted(by_alpha, key=float):
    group = by_alpha[alpha]
    n = len(group)
    means = {m: statistics.mean(r[m] for r in group) for m in metrics}
    gold_rate = sum(1 for r in group if r["gold_in_context"]) / n
    lat = sorted(r["search_seconds"] for r in group)
    p50 = lat[len(lat) // 2]
    p95 = lat[min(len(lat) - 1, int(len(lat) * 0.95))]
    line = (
        f"{alpha:>6} "
        + " ".join(f"{means[m]:>16.4f}" for m in metrics)
        + f" {gold_rate:>12.3f}"
    )
    if has_file_relevance:
        file_rate = sum(1 for r in group if r.get("gold_file_in_context")) / n
        line += f" {file_rate:>13.3f}"
    line += f" {p50:>8.3f} {p95:>8.3f}"
    print(line)

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

# Full per-task detail for every P@5 movement (win or loss), not just the
# regression-id list above — every reported number a reader would need to
# attribute a win/loss to a real ranking change vs. an artifact, without
# re-deriving it from the raw JSONL by hand.
print("\nper-task P@5 movement vs alpha=0.0 baseline (win or loss, every alpha):")
for alpha in sorted(by_alpha, key=float):
    if alpha == "0.0":
        continue
    for r in by_alpha[alpha]:
        base = baseline.get(r["instance_id"])
        if base is None or r["precision_at_5"] == base["precision_at_5"]:
            continue
        direction = "WIN " if r["precision_at_5"] > base["precision_at_5"] else "LOSS"
        # Heuristic, not a semantic classifier: a baseline top-5 whose gold
        # coverage rested on exactly one relevant pack item is the
        # structural signature of an exact-identifier-dominant query (one
        # narrow correct match, no natural corroboration) — the shape of
        # every real regression found in the first sweep (Section 3(a),
        # docs/term-coverage-eval/README.md). Flag it for manual read, not
        # as a guaranteed classification.
        exact_id_flag = " [exact-identifier-shaped]" if len(base.get("relevant_ids", [])) == 1 else ""
        print(
            f"  {direction} alpha={alpha} {r['instance_id']}: "
            f"P@5 {base['precision_at_5']:.2f}->{r['precision_at_5']:.2f}"
            f"  base_relevant={base.get('relevant_ids')}  new_relevant={r.get('relevant_ids')}"
            f"{exact_id_flag}"
        )
