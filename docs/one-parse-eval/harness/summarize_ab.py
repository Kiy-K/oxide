#!/usr/bin/env python3
"""Research-only (issue #24): paired baseline-vs-challenger summary.

Baseline and challenger alternate inside every rep (same repo copy recipe,
same pinned cores), so each rep gives one paired challenger/baseline ratio;
the table reports the median paired ratio and its min–max over reps."""
import collections, json, statistics as st, sys

rows = [json.loads(l) for l in open(sys.argv[1])]
g = collections.defaultdict(lambda: collections.defaultdict(dict))
for r in rows:
    g[(r["repo"], r["scenario"])][r["rep"]][r["variant"]] = r

print("| repo | scenario | files | parses B→C | wall ms B | wall ms C | Δ wall (median paired) | paired range | CPU ms B | CPU ms C | Δ CPU | RSS MiB B→C |")
print("|---|---|--:|---|--:|--:|--:|---|--:|--:|--:|---|")
agg = collections.defaultdict(list)
for (repo, sc), reps in g.items():
    pairs = [(v["baseline"], v["challenger"]) for v in reps.values() if len(v) == 2]
    wr = [c["wall_ms"] / b["wall_ms"] - 1 for b, c in pairs]
    cr = [c["cpu_ms"] / b["cpu_ms"] - 1 for b, c in pairs]
    b0, c0 = pairs[0]
    med = lambda k, i: st.median(p[i][k] for p in pairs)
    files = b0["extra"]["report"]["reparsed_files"]
    agg[sc].append(st.median(wr))
    print(f"| {repo} | {sc} | {files} | {b0['parse_calls']}→{c0['parse_calls']} | {med('wall_ms', 0):.1f} | {med('wall_ms', 1):.1f} "
          f"| {st.median(wr):+.1%} | {min(wr):+.1%} … {max(wr):+.1%} | {med('cpu_ms', 0):.1f} | {med('cpu_ms', 1):.1f} "
          f"| {st.median(cr):+.1%} | {med('vmhwm_kb', 0) / 1024:.1f}→{med('vmhwm_kb', 1) / 1024:.1f} |")
print()
print("| scenario | median of per-repo median Δ wall | range over repos |")
print("|---|--:|---|")
for sc, v in agg.items():
    print(f"| {sc} | {st.median(v):+.1%} | {min(v):+.1%} … {max(v):+.1%} |")
