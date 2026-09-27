#!/usr/bin/env python3
"""Paired bootstrap Δ between two arms of a score_intent.py scores file.

usage: paired.py <dump.jsonl.scores.json> <armX> <armY> [metric ...]
Prints mean(Y - X) with 95% CI (2000 resamples, seed 0) per stratum.
"""
import json
import random
import statistics
import sys

d = json.load(open(sys.argv[1]))
per, strata = d["per_task"], d["strata"]
x, y = sys.argv[2], sys.argv[3]
metrics = sys.argv[4:] or ["@10 sym", "pack sym"]


def ci(v, n=2000):
    rng = random.Random(0)
    bs = sorted(statistics.fmean(rng.choices(v, k=len(v))) for _ in range(n))
    return bs[int(0.025 * n)], bs[int(0.975 * n) - 1]


for g in ["all"] + sorted(set(strata.values()) - {"all"}):
    ids = sorted(i for i in per if g == "all" or strata[i] == g)
    if len(ids) < 5:
        continue
    for m in metrics:
        v = [per[i][f"{y} {m}"] - per[i][f"{x} {m}"] for i in ids]
        lo, hi = ci(v)
        print(f"{g:5s} n={len(ids):3d} {y} - {x} {m:10s} {statistics.fmean(v):+.3f} [{lo:+.3f}, {hi:+.3f}] "
              f"w/l {sum(a > 0 for a in v)}/{sum(a < 0 for a in v)}")
