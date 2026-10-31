"""Tables from pool_trace.py output.

    python docs/phase-6/graph-v3/scripts/pool_summary.py TRACE.json [TRACE.json ...]

1. macro line recall per counterfactual and budget (ceiling decomposition);
2. per candidate class at 4,096: count, gold holders, gold rate, packed, units spent;
3. every gold-holding candidate not fully packed at 4,096;
4. the region-cap sweep under the production policy.
"""

import collections
import json
import sys
from pathlib import Path


def klass(c: dict) -> str:
    if c["cat"] not in ("symbol", "file"):
        return "container"
    if c["cat"] == "file" and not c["seeded"] and c["rank"] is None:
        return "routed file"
    if c["seeded"]:
        return "seed"
    if c["rank"] is not None:
        return (
            "lexical r1-5"
            if c["rank"] <= 5
            else "lexical r6-10"
            if c["rank"] <= 10
            else "lexical r11+"
        )
    return f"routed d{c['depth']} {c['cat']}"


rows = [r for path in sys.argv[1:] for r in json.loads(Path(path).read_text()) if r["trace"]]
n = len(rows)
print(f"tasks with verified gold: {n}")
print("\n1. macro line_recall (verified)")
cfs = list(rows[0]["trace"]["cf"]["4096"])
print(f"{'cf':14}" + "".join(f"{b:>10}" for b in ("1024", "4096")))
for cf in cfs:
    print(
        f"{cf:14}"
        + "".join(
            f"{sum(r['trace']['cf'][b][cf] for r in rows) / n:10.3f}" for b in ("1024", "4096")
        )
    )
routed = sum(r["trace"]["routed_lines"] / r["trace"]["gold_lines"] for r in rows) / n
print(f"{'routed lines':14}{'':10}{routed:10.3f}  (gold lines with a holder in the pool)")

print("\n2. candidate classes at 4096")
stat: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
for r in rows:
    for c in r["trace"]["candidates"]:
        s = stat[klass(c)]
        s["n"] += 1
        s["gold"] += c["gold"] > 0
        s["packed"] += c["state"] in ("full", "header")
        s["gold_packed"] += c["gold"] > 0 and c["state"] in ("full", "header")
        s["units"] += c["units"] or 0
print(f"{'class':18}{'n':>6}{'gold':>6}{'rate':>7}{'packed':>8}{'g.pack':>8}{'mean u':>8}")
for k, s in sorted(stat.items(), key=lambda kv: -kv[1]["n"]):
    print(
        f"{k:18}{s['n']:6}{s['gold']:6}{s['gold'] / s['n']:7.3f}"
        f"{s['packed']:8}{s['gold_packed']:8}{s['units'] / s['n']:8.0f}"
    )

spent: collections.Counter = collections.Counter()
gold_spent: collections.Counter = collections.Counter()
for r in rows:
    for i in r["trace"]["items"]:
        reason = i["reasons"][0]
        k = (
            "prerequisite/header"
            if not reason.startswith("value")
            else reason.split(", ")[1].split(" rank")[0].split(" depth")[0]
        )
        spent[k] += i["units"]
        gold_spent[k] += i["units"] if i["gold"] else 0
print("\nunits packed at 4096 by first entity's basis (units, units in items touching gold)")
for k, u in spent.most_common():
    print(f"  {k:24}{u:8}{gold_spent[k]:8}  {gold_spent[k] / u:.2f}")

print("\n3. gold holders not fully packed at 4096")
for r in rows:
    for c in r["trace"]["candidates"]:
        if c["gold"] and c["state"] != "full":
            print(
                f"  {r['task'][:30]:30} {c['key'][-60:]:60} g={c['gold']:3} v={c['value']:.2f} "
                f"d={c['depth']} rank={c['rank']} u={c['units']} pos={c['position']} "
                f"{c['state']} {c['origins']}"
            )

print("\n4. cap sweep (production policy), macro line_recall@4096; regions median")
caps = list(rows[0]["trace"]["caps"])
base = sum(r["trace"]["cf"]["4096"]["base"] for r in rows) / n
for cap in caps:
    rec = sum(r["trace"]["caps"][cap]["line_recall"] for r in rows) / n
    regions = sorted(r["trace"]["caps"][cap]["regions"] for r in rows)[n // 2]
    moved = sum(
        r["trace"]["caps"][cap]["line_recall"] != r["trace"]["cf"]["4096"]["base"] for r in rows
    )
    print(
        f"  cap {cap:>4}: {rec:.3f} ({rec - base:+.3f}), "
        f"tasks changed {moved}, median regions {regions}"
    )
print(f"  cap   64: {base:.3f} (production)")
