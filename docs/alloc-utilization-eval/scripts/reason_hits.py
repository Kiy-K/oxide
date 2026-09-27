#!/usr/bin/env python3
"""Hit rate of topped-up items by the baseline cap that had dropped them,
versus the baseline pack's own items (item hits gold if any delivered line is gold).

usage: reason_hits.py <tasks> <dump> <gold-args...> [--mode balanced] [--ch 1] [--blast 0]
"""
import sys
from collections import Counter
mode = sys.argv[sys.argv.index("--mode") + 1] if "--mode" in sys.argv else "balanced"
CH = int(sys.argv[sys.argv.index("--ch") + 1]) if "--ch" in sys.argv else 1
BL = bool(int(sys.argv[sys.argv.index("--blast") + 1])) if "--blast" in sys.argv else False
exec(open(__file__.replace("reason_hits.py", "score_alloc.py")).read().replace("\nmain()\n", "\n"))
rows = {(r["id"], r["mode"], r["ch"], r["blast"]): r for r in dump}
n, h, tok, htok = Counter(), Counter(), Counter(), Counter()
for (tid, m, ch, bl), r in rows.items():
    if m != mode or bl != BL or ch not in (0, CH):
        continue
    gl, _ = gold_lines(tasks[tid])
    om = {k: w for k, w in rows[(tid, m, 0, bl)]["trace"]["omitted"]}
    for p in r["trace"]["packed"]:
        if ch == 0:
            w = "baseline item"
        elif p["added"]:
            w = om.get(p["key"], "widened" if CH == 3 else "?")
        else:
            continue
        hit = bool(span_set(p["span"]) & gl.get(fkey(p["key"]), set()))
        n[w] += 1; tok[w] += p["est"]; h[w] += hit; htok[w] += p["est"] * hit
for w in n:
    print(f"  {w:25s} items {n[w]:4d} tok {tok[w]:6d}  hit {h[w]:3d} ({h[w] / n[w]:.1%})  hit-tok share {htok[w] / max(1, tok[w]):.1%}")
