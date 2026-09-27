#!/usr/bin/env python3
"""Preregistered ch5 validation scorer (PREREGISTRATION.md §3–4).

usage: validate_ch5.py <tasks.jsonl> <dump.jsonl> <gold.json> [--json out.json]

Arms: ch 0 (shipped) vs ch 5 (frozen). Paired task bootstrap, 10,000
resamples, seed 0, 95 % percentile intervals.
"""
import json
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

B, SEED, CPT = 10_000, 0, 4.0
tasks = {t["id"]: t for t in map(json.loads, open(sys.argv[1]))}
dump = [json.loads(l) for l in open(sys.argv[2]) if l.strip()]
GOLD = json.load(open(sys.argv[3]))
OUT = sys.argv[sys.argv.index("--json") + 1] if "--json" in sys.argv else None
_files = {}


def flines(root, f):
    if (root, f) not in _files:
        try:
            _files[(root, f)] = (Path(root) / f).read_text(errors="replace").splitlines()
        except OSError:
            _files[(root, f)] = []
    return _files[(root, f)]


fkey = lambda k: k.split("#", 1)[0]
span = lambda sp: set(range(sp[0], sp[1] + 1)) if sp and sp[0] > 0 else set()


def metrics(rec, t, gl):
    deliv = defaultdict(set)
    for p in rec["trace"]["packed"]:
        deliv[fkey(p["key"])] |= span(p["span"])
    hit = {f: deliv.get(f, set()) & v for f, v in gl.items()}
    tot = sum(len(v) for v in gl.values())
    fl = lambda f, ls: sum(len(flines(t["path"], f)[i - 1]) + 1 for i in ls if 0 < i <= len(flines(t["path"], f)))
    rel = sum(fl(f, s) for f, s in hit.items()) / CPT
    files = {fkey(p["key"]) for p in rec["trace"]["packed"]}
    return {"cov": sum(len(s) for s in hit.values()) / tot, "rel": rel, "used": rec["used"],
            "eff": rel / rec["used"] if rec["used"] else 0.0, "items": len(rec["trace"]["packed"]),
            "util": rec["used"] / rec["budget"], "files": len(files), "ms": min(rec["context_ms"] or [0]),
            "search_ms": rec["search_ms"]}


def pct(xs, q):
    return xs[min(len(xs) - 1, int(q * len(xs)))]


rows = defaultdict(dict)
for r in dump:
    if r["mode"] == "balanced" and not r["blast"]:
        rows[r["id"]][r["ch"]] = r
ids = sorted(i for i in rows if 0 in rows[i] and 5 in rows[i] and GOLD.get(i, {}).get("lines"))
M = {i: {ch: metrics(rows[i][ch], tasks[i], {f: set(v) for f, v in GOLD[i]["lines"].items()}) for ch in (0, 5)} for i in ids}
n = len(ids)
repos = Counter(tasks[i]["repo"] for i in ids)
print(f"primary tasks {n} from {len(repos)} repos: {dict(repos)}  max share {max(repos.values()) / n:.1%}")

# aggregates
for ch in (0, 5):
    a = {k: sum(M[i][ch][k] for i in ids) / n for k in ("cov", "rel", "used", "eff", "items", "util", "files", "ms")}
    print(f"arm ch{ch}: " + "  ".join(f"{k} {v:.4f}" for k, v in a.items()))

# paired bootstrap: coverage delta, relative efficiency change
rnd = random.Random(SEED)
d_cov = [M[i][5]["cov"] - M[i][0]["cov"] for i in ids]
e0 = [M[i][0]["eff"] for i in ids]
e5 = [M[i][5]["eff"] for i in ids]
rel_eff = (sum(e5) - sum(e0)) / sum(e0)
bc, br = [], []
for _ in range(B):
    s = [rnd.randrange(n) for _ in range(n)]
    bc.append(sum(d_cov[j] for j in s) / n)
    s0 = sum(e0[j] for j in s)
    br.append((sum(e5[j] for j in s) - s0) / s0 if s0 else 0.0)
bc.sort(); br.sort()
cov_mean = sum(d_cov) / n
print(f"Δ coverage {cov_mean:+.4f} [{pct(bc, .025):+.4f}, {pct(bc, .975):+.4f}]")
print(f"relative efficiency change {rel_eff:+.2%} [{pct(br, .025):+.2%}, {pct(br, .975):+.2%}]")
imp = sum(x > 1e-12 for x in d_cov); reg = sum(x < -1e-12 for x in d_cov)
eff_down = [i for i in ids if M[i][5]["eff"] < M[i][0]["eff"] - 1e-12]
print(f"coverage: improved {imp} / unchanged {n - imp - reg} / regressed {reg};  efficiency dropped on {len(eff_down)}")

# per added item
added = []
for i in ids:
    b, c = rows[i][0]["trace"], rows[i][5]["trace"]
    om = {k: w for k, w in b["omitted"]}
    fill = [p["key"] for p in b["pool"] if om.get(p["key"]) != "below relevance floor"]
    fused = {k: r for r, (k, _, _) in enumerate(rows[i][0]["fused50"] or [])}
    prim = sum(p["role"] == "primary" for p in b["packed"])
    gl = {f: set(v) for f, v in GOLD[i]["lines"].items()}
    for p in c["packed"][len(b["packed"]):]:
        crossed = p["role"] == "primary" and prim >= 5
        if p["role"] == "primary":
            prim += 1
        added.append({"task": i, "repo": tasks[i]["repo"], "symbol": p["key"], "file": fkey(p["key"]), "role": p["role"],
                      "reason": om.get(p["key"]), "fill_rank": fill.index(p["key"]) if p["key"] in fill else None,
                      "fused_rank": fused.get(p["key"]), "tokens": p["est"], "crossed_primary_cap": crossed,
                      "gold": bool(span(p["span"]) & gl.get(fkey(p["key"]), set())),
                      "task_d_cov": M[i][5]["cov"] - M[i][0]["cov"], "task_d_eff": M[i][5]["eff"] - M[i][0]["eff"]})
act = len({a["task"] for a in added})
print(f"ch5 activates on {act}/{n} tasks; added {len(added)} items; crossed primary cap {sum(a['crossed_primary_cap'] for a in added)}"
      f" (tasks {len({a['task'] for a in added if a['crossed_primary_cap']})}); gold-bearing {sum(a['gold'] for a in added)}"
      f" ({sum(a['gold'] for a in added) / max(1, len(added)):.1%}); mean tokens added/task {sum(a['tokens'] for a in added) / n:.1f}"
      f" (per activated task {sum(a['tokens'] for a in added) / max(1, act):.1f})")
print("reasons:", dict(Counter(a["reason"] for a in added)), " roles:", dict(Counter(a["role"] for a in added)))
# mechanism: hit rate by role × crossed, vs baseline items
bl = [(i, p) for i in ids for p in rows[i][0]["trace"]["packed"]]
bl_hit = sum(bool(span(p["span"]) & set(GOLD[i]["lines"].get(fkey(p["key"]), []))) for i, p in bl)
print(f"baseline items {len(bl)} gold-bearing {bl_hit} ({bl_hit / len(bl):.1%})")
for key, grp in (("primary crossed cap", [a for a in added if a["crossed_primary_cap"]]),
                 ("primary within cap", [a for a in added if a["role"] == "primary" and not a["crossed_primary_cap"]]),
                 ("dependency", [a for a in added if a["role"] == "dependency"])):
    if grp:
        print(f"  added {key}: {len(grp)} items, gold-bearing {sum(a['gold'] for a in grp)} ({sum(a['gold'] for a in grp) / len(grp):.1%})")
# operational
ms0 = sorted(M[i][0]["ms"] for i in ids); ms5 = sorted(M[i][5]["ms"] for i in ids)
dms = sorted(M[i][5]["ms"] - M[i][0]["ms"] for i in ids); sms = sorted(M[i][0]["search_ms"] for i in ids)
print(f"context ms median ch0 {pct(ms0, .5):.2f} ch5 {pct(ms5, .5):.2f}; median paired Δ {pct(dms, .5):+.3f} ms; median search {pct(sms, .5):.2f} ms"
      f" → Δ/search {pct(dms, .5) / pct(sms, .5):+.2%}")
gates = {"Q1": cov_mean > 0 and pct(bc, .025) > 0, "E1": pct(br, .025) >= -0.05,
         "validity": n >= 50 and len(repos) >= 6 and max(repos.values()) / n <= 0.20,
         "O1": pct(dms, .5) <= 0.05 * pct(sms, .5), "coverage_regressions": reg}
print("GATES:", gates)
if OUT:
    json.dump({"ids": ids, "metrics": M, "added": added, "gates": gates, "cov": [cov_mean, pct(bc, .025), pct(bc, .975)],
               "rel_eff": [rel_eff, pct(br, .025), pct(br, .975)], "eff_down": eff_down}, open(OUT, "w"), indent=1)
