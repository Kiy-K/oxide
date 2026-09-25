#!/usr/bin/env python3
"""One-sided equivalence (non-inferiority) between the two binaries of one
interleaved sweep written by `scripts/corpus_load_baseline.py` with
`--challenger-*`. R = median(challenger units) / median(base units); its
90 % percentile bootstrap interval (10,000 resamples, each binary
resampled independently over its units, fixed seed) is a one-sided
α = 0.05 test of "challenger is at most `margin` slower".

Modes (the unit is always one independent process):
  noexpand  PG-3.1 — `search --no-expand` rows from stages.jsonl/cli.jsonl:
            stage warm = median of in-process reps 1..N, stage cold = rep
            0, CLI = one invocation. Rule (as pre-registered):
            equivalent if upper < 1+m; regression if R >= 1+m; else
            inconclusive.
  mcp       PG-3.2 — warm `oxide mcp` from mcp_servers.jsonl: unit = one
            server's median over its measured (non-warmup) calls, per
            (corpus, tool). Rule: equivalent if upper < 1+m; confirmed
            regression if lower > 1+m; else inconclusive. Also reports the
            across-server coefficient of variation and peak RSS.

A gate verdict also needs the pre-registered design: every expected row
present, each binary with at least the pre-registered number of units
(noexpand: 6 rows, n >= 60; mcp: 4 rows, n >= 80; override with
--expect-rows / --min-n). Anything less prints INCOMPLETE and exits 2; a
complete run that is not all-equivalent exits 1.

usage: equivalence.py <raw dir> [--mode noexpand|mcp] [--against challenger|base_aa]
                      [--margin 0.03] [--expect-rows N] [--min-n N] [--json]"""
import json
import random
import statistics
import sys
from collections import defaultdict
from pathlib import Path

args = sys.argv[1:]
raw = Path(args[0])
margin = float(args[args.index("--margin") + 1]) if "--margin" in args else 0.03
mode = args[args.index("--mode") + 1] if "--mode" in args else "noexpand"
# Which variant is compared against `base`: `challenger`, or `base_aa` for
# a same-schedule A/A validity check.
against = args[args.index("--against") + 1] if "--against" in args else "challenger"
RESAMPLES, SEED = 10_000, 20260925
PREREGISTERED = {"noexpand": (6, 60), "mcp": (4, 80)}  # (rows, units per binary)
expect_rows, min_n = PREREGISTERED.get(mode, (0, 0))
if "--expect-rows" in args:
    expect_rows = int(args[args.index("--expect-rows") + 1])
if "--min-n" in args:
    min_n = int(args[args.index("--min-n") + 1])


def rows_of(name):
    path = raw / name
    return [json.loads(l) for l in path.open()] if path.exists() else []


units = defaultdict(list)  # (corpus, row) -> [(binary, value)]
rss = defaultdict(list)  # (corpus, row) -> [(binary, vm_hwm_kb)]
if mode == "noexpand":
    by_process = defaultdict(list)
    for r in rows_of("stages.jsonl"):
        if r["stage"] == "search_noexpand":
            by_process[(r["corpus"], r["binary"], r["batch"], r["process"])].append(r)
    for (corpus, binary, _, _), reps in by_process.items():
        reps.sort(key=lambda r: r["rep"])
        units[(corpus, "stage cold")].append((binary, reps[0]["ms"]))
        units[(corpus, "stage warm")].append((binary, statistics.median(r["ms"] for r in reps[1:])))
    for r in rows_of("cli.jsonl"):
        if r["surface"] == "search-no-expand":
            units[(r["corpus"], "cli")].append((r["binary"], r["ms"]))
elif mode == "mcp":
    by_server = defaultdict(list)
    for r in rows_of("mcp_servers.jsonl"):
        if not r["warmup"]:
            by_server[(r["corpus"], r["tool"], r["binary"], r["batch"], r["server"])].append(r)
    for (corpus, tool, binary, _, _), calls in by_server.items():
        units[(corpus, tool)].append((binary, statistics.median(c["ms"] for c in calls)))
        rss[(corpus, tool)].append((binary, calls[0]["vm_hwm_kb"]))
else:
    raise SystemExit(f"unknown mode {mode}")

rng = random.Random(SEED)
out = []
for (corpus, row), samples in sorted(units.items()):
    base = [v for b, v in samples if b == "base"]
    chal = [v for b, v in samples if b == against]
    ratio = statistics.median(chal) / statistics.median(base)
    boot = sorted(
        statistics.median(rng.choices(chal, k=len(chal)))
        / statistics.median(rng.choices(base, k=len(base)))
        for _ in range(RESAMPLES)
    )
    lo, hi = boot[int(0.05 * RESAMPLES)], boot[int(0.95 * RESAMPLES) - 1]
    if hi < 1 + margin:
        verdict = "equivalent"
    elif (ratio >= 1 + margin) if mode == "noexpand" else (lo > 1 + margin):
        verdict = "regression"
    else:
        verdict = "inconclusive"
    o = {"corpus": corpus, "row": row, "n_base": len(base), "n_challenger": len(chal),
         "median_base": statistics.median(base), "median_challenger": statistics.median(chal),
         "ratio": ratio, "ci90": [lo, hi], "margin": margin, "verdict": verdict}
    if mode == "mcp":
        def cv(xs):
            return statistics.stdev(xs) / statistics.mean(xs)
        o["cv_base"], o["cv_challenger"] = cv(base), cv(chal)
        o["vm_hwm_kb_base"] = statistics.median(v for b, v in rss[(corpus, row)] if b == "base")
        o["vm_hwm_kb_challenger"] = statistics.median(v for b, v in rss[(corpus, row)] if b == against)
    out.append(o)

complete = len(out) == expect_rows and all(
    o["n_base"] >= min_n and o["n_challenger"] >= min_n for o in out)
equivalent = complete and all(o["verdict"] == "equivalent" for o in out)
if "--json" in args:
    print(json.dumps(out, indent=1))
else:
    for o in out:
        extra = ""
        if mode == "mcp":
            extra = (f"  cv {o['cv_base']:.1%}/{o['cv_challenger']:.1%}"
                     f"  hwm {o['vm_hwm_kb_base'] / 1024:.1f}/{o['vm_hwm_kb_challenger'] / 1024:.1f} MB")
        print(f"{o['corpus']:8} {o['row']:11} n={o['n_base']}/{o['n_challenger']}  "
              f"{o['median_base']:.3f}->{o['median_challenger']:.3f} ms  R={o['ratio']:.4f}  "
              f"90% CI [{o['ci90'][0]:.4f}, {o['ci90'][1]:.4f}]  {o['verdict']}{extra}")
    if not complete:
        print(f"INCOMPLETE: {len(out)} of {expect_rows} rows, need n >= {min_n} per binary — no gate verdict")
    else:
        print("ALL EQUIVALENT" if equivalent else "NOT ALL EQUIVALENT")
sys.exit(0 if equivalent else (2 if not complete else 1))
