#!/usr/bin/env python3
"""Summarize ContextBench tier-A results (cb_results.jsonl)."""
import json
import sys
from collections import defaultdict
from pathlib import Path

path = Path(sys.argv[1] if len(sys.argv) > 1 else "eval-agent/results/cb_results.jsonl")
rows = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]

def f1(cov, prec):
    return 2 * cov * prec / (cov + prec) if (cov + prec) > 0 else 0.0

conds = sorted({r["condition"] for r in rows})
langs = sorted({r["language"] for r in rows})

print(f"tasks={len(rows)//len(conds)} langs={langs}")
granularities = ["file", "symbol", "line"]
print(f"\n{'condition':<10} " + " ".join(
    f"{g+'_R':>8} {g+'_P':>8} {g+'_F1':>8}" for g in granularities) + f" {'tok':>7} {'items':>6}")
for cond in conds:
    rs = [r for r in rows if r["condition"] == cond]
    cells = []
    for g in granularities:
        cov = sum(r["metrics"][g]["coverage"] for r in rs) / len(rs)
        prec = sum(r["metrics"][g]["precision"] for r in rs) / len(rs)
        cells += [cov, prec, f1(cov, prec)]
    tok = sum(r["used_tokens"] for r in rs) / len(rs)
    items = sum(r["items"] for r in rs) / len(rs)
    print(f"{cond:<10} " + " ".join(f"{c:>8.3f}" for c in cells) + f" {tok:>7.0f} {items:>6.1f}")

# per-language breakdown for the strongest contrast
for lang in langs:
    lr = [r for r in rows if r["language"] == lang]
    if len({(r["task"]) for r in lr}) < 2:
        continue
    print(f"\n-- {lang} ({len(lr)//len(conds)} tasks) --")
    for cond in conds:
        rs = [r for r in lr if r["condition"] == cond]
        lcov = sum(r["metrics"]["line"]["coverage"] for r in rs) / max(1, len(rs))
        lprec = sum(r["metrics"]["line"]["precision"] for r in rs) / max(1, len(rs))
        fcov = sum(r["metrics"]["file"]["coverage"] for r in rs) / max(1, len(rs))
        tok = sum(r["used_tokens"] for r in rs) / max(1, len(rs))
        print(f"  {cond:<9} file_R={fcov:.3f} line_R={lcov:.3f} line_P={lprec:.3f} line_F1={f1(lcov,lprec):.3f} tok={tok:.0f}")
