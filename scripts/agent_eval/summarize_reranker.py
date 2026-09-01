#!/usr/bin/env python3
"""Summarize docs/reranker-eval/results/results.jsonl into the Pareto table
(quality vs tokens vs complementary-evidence loss) the experiment's keep/
reject decision is based on."""
import json
import sys
from collections import defaultdict
from pathlib import Path

path = Path(sys.argv[1] if len(sys.argv) > 1 else "docs/reranker-eval/results/results.jsonl")
rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]

arms = sorted({r["arm"] for r in rows})
by_arm = defaultdict(list)
for r in rows:
    by_arm[r["arm"]].append(r)

n = len(by_arm.get("baseline", []))
print(f"tasks={n}\n")


def f1(g):
    p, c = g["precision"], g["coverage"]
    return 2 * p * c / (p + c) if (p + c) else 0.0


print(f"{'arm':<20} {'file_F1':>8} {'sym_F1':>8} {'line_F1':>8} {'tok':>7} {'items':>6} {'rel_items':>9} {'rel_tok':>7}")
for arm in arms:
    rs = by_arm[arm]
    m = len(rs)
    file_f1 = sum(f1(r["granularity"]["file"]) for r in rs) / m
    sym_f1 = sum(f1(r["granularity"]["symbol"]) for r in rs) / m
    line_f1 = sum(f1(r["granularity"]["line"]) for r in rs) / m
    tok = sum(r["used_tokens"] for r in rs) / m
    items = sum(r["n_items"] for r in rs) / m
    rel_items = sum(r["relevant_items"] for r in rs) / m
    rel_tok = sum(r["relevant_tokens"] for r in rs) / m
    print(f"{arm:<20} {file_f1:>8.3f} {sym_f1:>8.3f} {line_f1:>8.3f} {tok:>7.0f} {items:>6.1f} {rel_items:>9.1f} {rel_tok:>7.0f}")

print("\ncomplementary-evidence loss (Jina-style: gold-relevant symbols baseline kept but the arm dropped)")
for arm in arms:
    if arm == "baseline":
        continue
    rs = by_arm[arm]
    total_lost = sum(len(r.get("complementary_evidence_lost", [])) for r in rs)
    total_gained = sum(len(r.get("complementary_evidence_gained", [])) for r in rs)
    tasks_with_loss = sum(1 for r in rs if r.get("complementary_evidence_lost"))
    print(f"  {arm:<20} lost={total_lost:>3} (in {tasks_with_loss}/{len(rs)} tasks)  gained={total_gained:>3}")

print("\nworst per-task loss examples:")
for arm in arms:
    if arm == "baseline":
        continue
    worst = sorted(by_arm[arm], key=lambda r: -len(r.get("complementary_evidence_lost", [])))[:2]
    for r in worst:
        lost = r.get("complementary_evidence_lost", [])
        if lost:
            print(f"  {arm} / {r['instance_id']}: lost {lost}")
