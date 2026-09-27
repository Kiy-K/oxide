#!/usr/bin/env python3
"""Research-only (issue #29): paired baseline-vs-challenger summary of
`bench.py ab` output. One paired ratio per rep (challenger/baseline - 1);
tables report the median paired ratio and its min–max over reps.

  summarize.py <ab.jsonl>
"""
import collections, json, statistics as st, sys

rows = [json.loads(l) for l in open(sys.argv[1])]
g = collections.defaultdict(lambda: collections.defaultdict(dict))
for r in rows:
    g[(r["repo"], r["scenario"])][r["rep"]][r["variant"]] = r


def stage(r, kind, name):
    return (r["extra"].get(kind) or {}).get(name, 0.0)


def workers(r):
    return r["extra"].get("parse_workers") or []


def util(r):
    """Busy share of the parse pool: summed worker thread CPU over
    (parse-stage wall x workers)."""
    w = workers(r)
    wall = stage(r, "stages_ms", "Parse")
    return sum(x["thread_cpu_ms"] for x in w) / (wall * len(w)) if w and wall else float("nan")


def spread(r):
    """Earliest-to-latest worker finish (ms): idle time of the first-done
    worker while the join waits for the last."""
    f = [x["finish_ms"] for x in workers(r) if x["finish_ms"] is not None]
    return max(f) - min(f) if len(f) > 1 else 0.0


def pairs(reps):
    return [(v["baseline"], v["challenger"]) for v in reps.values() if len(v) == 2]


def med(ps, f, i):
    return st.median(f(p[i]) for p in ps)


def rel(ps, f):
    rs = [f(c) / f(b) - 1 for b, c in ps if f(b)]
    return (st.median(rs), min(rs), max(rs)) if rs else (float("nan"),) * 3


order = [n for n in dict.fromkeys(r["repo"] for r in rows)]
scen = ["full", "noop", "edit_median", "edit_largest", "watch_median"]

print("## End to end\n")
print("| repo | scenario | files | parses B→C | wall ms B | wall ms C | Δ wall (median paired) | paired range | CPU ms B | CPU ms C | Δ CPU | RSS MiB B→C | Δ RSS |")
print("|---|---|--:|---|--:|--:|--:|---|--:|--:|--:|---|--:|")
agg = collections.defaultdict(lambda: collections.defaultdict(list))
for repo in order:
    for sc in scen:
        ps = pairs(g[(repo, sc)])
        if not ps:
            continue
        wall = rel(ps, lambda r: r["wall_ms"])
        cpu = rel(ps, lambda r: r["cpu_ms"])
        rss = rel(ps, lambda r: r["vmhwm_kb"])
        b0, c0 = ps[0]
        files = b0["extra"]["report"]["reparsed_files"]
        agg[sc]["wall"].append(wall[0])
        agg[sc]["cpu"].append(cpu[0])
        agg[sc]["rss"].append(rss[0])
        agg[sc]["all_wall"] += [c["wall_ms"] / b["wall_ms"] - 1 for b, c in ps]
        print(f"| {repo} | {sc} | {files} | {b0['parse_calls']}→{c0['parse_calls']} "
              f"| {med(ps, lambda r: r['wall_ms'], 0):.1f} | {med(ps, lambda r: r['wall_ms'], 1):.1f} "
              f"| {wall[0]:+.1%} | {wall[1]:+.1%} … {wall[2]:+.1%} "
              f"| {med(ps, lambda r: r['cpu_ms'], 0):.1f} | {med(ps, lambda r: r['cpu_ms'], 1):.1f} | {cpu[0]:+.1%} "
              f"| {med(ps, lambda r: r['vmhwm_kb'], 0) / 1024:.1f}→{med(ps, lambda r: r['vmhwm_kb'], 1) / 1024:.1f} | {rss[0]:+.1%} |")

print("\n| scenario | median of per-repo median Δ wall | range over repos | Δ CPU (median over repos) | Δ RSS (median over repos) | paired reps faster |")
print("|---|--:|---|--:|--:|--:|")
for sc in scen:
    a = agg[sc]
    if not a["wall"]:
        continue
    faster = sum(x < 0 for x in a["all_wall"])
    print(f"| {sc} | {st.median(a['wall']):+.1%} | {min(a['wall']):+.1%} … {max(a['wall']):+.1%} "
          f"| {st.median(a['cpu']):+.1%} | {st.median(a['rss']):+.1%} | {faster}/{len(a['all_wall'])} |")

print("\n## Full-index scheduling (medians over reps)\n")
print("| repo | workers | parse stage ms B→C | parse CPU ms B→C | worker util B→C | finish spread ms B→C "
      "| resolve gap ms B→C | store stage ms B→C | store CPU ms B→C | embed ms B→C | base ms B→C |")
print("|---|--:|---|---|---|---|---|---|---|---|---|")
for repo in order:
    ps = pairs(g[(repo, "full")])
    if not ps:
        continue
    f = lambda fn: f"{med(ps, fn, 0):.0f}→{med(ps, fn, 1):.0f}"
    print(f"| {repo} | {len(workers(ps[0][0]))} "
          f"| {f(lambda r: stage(r, 'stages_ms', 'Parse'))} | {f(lambda r: stage(r, 'stages_cpu_ms', 'Parse'))} "
          f"| {med(ps, util, 0):.0%}→{med(ps, util, 1):.0%} | {f(spread)} "
          f"| {f(lambda r: r['extra'].get('resolve_gap_ms') or 0)} "
          f"| {f(lambda r: stage(r, 'stages_ms', 'Store'))} | {f(lambda r: stage(r, 'stages_cpu_ms', 'Store'))} "
          f"| {f(lambda r: stage(r, 'stages_ms', 'Embed'))} | {f(lambda r: r['extra']['base_ms'])} |")
