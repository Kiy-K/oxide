#!/usr/bin/env python3
"""Per-task admitted evidence for one challenger vs baseline (balanced, no blast):
improvements (gold coverage up) and efficiency regressions (rel/used down).

usage: examples.py <tasks> <dump> <gold-args...> --ch 5
"""
import sys
CH = int(sys.argv[sys.argv.index("--ch") + 1])
exec(open(__file__.replace("examples.py", "score_alloc.py")).read().replace("\nmain()\n", "\n"))
rows = {(r["id"], r["ch"]): r for r in dump if r["mode"] == "balanced" and not r["blast"]}
for tid in sorted({i for i, _ in rows}):
    b, c = rows[(tid, 0)], rows.get((tid, CH))
    if not c:
        continue
    t = tasks[tid]
    gl, _ = gold_lines(t)
    if not gl:
        continue
    mb, mc = pack_metrics(b, t, gl), pack_metrics(c, t, gl)
    d_cov, d_eff = mc["gold_line_cov"] - mb["gold_line_cov"], mc["rel_per_used"] - mb["rel_per_used"]
    if abs(d_cov) < 1e-9 and abs(d_eff) < 1e-9:
        continue
    om = {k: w for k, w in b["trace"]["omitted"]}
    tag = "IMPROVED" if d_cov > 1e-9 else "EFF-REGRESSED"
    print(f"{tag} {tid}  cov {mb['gold_line_cov']:.2f}->{mc['gold_line_cov']:.2f}  rel/used {mb['rel_per_used']:.3f}->{mc['rel_per_used']:.3f}  used {mb['used']}->{mc['used']}")
    print(f"    query: {t['query'][:110]!r}")
    for p in c["trace"]["packed"]:
        if p["added"]:
            hit = len(span_set(p["span"]) & gl.get(fkey(p["key"]), set()))
            print(f"    + {p['key']}  [{om.get(p['key'], 'widened')}]  {p['est']} tok  span {p['span']}  gold lines {hit}")
