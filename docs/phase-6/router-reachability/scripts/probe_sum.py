"""probe_sum.py PROBE.json: per variant, verified spans reached at depth<=1 / 2, gains and displacement vs base."""

import json
import sys
from collections import Counter, defaultdict

rep = json.load(open(sys.argv[1]))
variants = list(rep[0]["sizes"])
by = defaultdict(Counter)
for t in rep:
    repo = t["corpus"].split("@")[0]
    for s in t["spans"]:
        lostup = s["lost"].split(":")[0] in ("entry", "routing")
        b = s["reached"]["base"]
        for v in variants:
            d = s["reached"][v]
            c = by[v]
            c["le1"] += d is not None and d <= 1
            c["d2"] += d == 2
            if lostup:
                c["lost_now_le1"] += d is not None and d <= 1
                c["lost_now_d2"] += d == 2
            c["gain_le1"] += (d is not None and d <= 1) and not (b is not None and b <= 1)
            c["drop_le1"] += (b is not None and b <= 1) and not (d is not None and d <= 1)
            c[f"gain_le1:{repo}"] += (d is not None and d <= 1) and not (b is not None and b <= 1)
            c[f"drop_le1:{repo}"] += (b is not None and b <= 1) and not (d is not None and d <= 1)
n = sum(len(t["spans"]) for t in rep)
print("verified dev spans", n, "tasks", len(rep))
for v in variants:
    c = by[v]
    extra = {k: x for k, x in sorted(c.items()) if ":" in k and x}
    print(
        f"{v:15} le1 {c['le1']:3} d2 {c['d2']:3} | lost-upstream now le1 {c['lost_now_le1']:3} d2 {c['lost_now_d2']:3} | gain {c['gain_le1']:3} drop {c['drop_le1']:3} {extra}"
    )
