#!/usr/bin/env python3
"""Bounded, deterministic, confidence-aware reranker: fit on dev dumps
(non-leaky signals only), apply within the fused top-N, evaluate variants
on any dump with the same metrics as fusion_eval, plus ablations, hard
negatives, and per-repo macro averages with bootstrap CIs.

usage:
  rerank_eval.py fit-dump tasks.jsonl dump.jsonl [tasks2 dump2 ...] > weights.json
  rerank_eval.py eval weights.json tasks.jsonl dump.jsonl [--judgments j.jsonl]
"""
import json, math, random, sys, statistics
from collections import defaultdict

FEATS = ["neg_log_lex_rank", "neg_log_sem_rank", "in_both", "struct_strong", "struct_uses"]

def feats_for(sid, lex, sem, strong_support, uses_support):
    lr = lex.get(sid); sr = sem.get(sid)
    return [
        -math.log1p(lr if lr is not None else 200),
        -math.log1p(sr if sr is not None else 200),
        1.0 if (lr is not None and sr is not None) else 0.0,
        min(strong_support.get(sid, 0.0), 2.0),
        min(uses_support.get(sid, 0.0), 2.0),
    ]

def supports(rec, lex, sem, fused_rank):
    """Structural support restricted to what the audit found informative:
    sibling/child/parent edges from a *confident* seed (fused rank < 3 and
    present in both channels). `uses` kept separately as a control."""
    strong = defaultdict(float); uses = defaultdict(float)
    for s in rec["neighbors"][:3]:
        seed = s["seed"]
        confident = seed in lex and seed in sem and fused_rank.get(seed, 99) < 3
        seen = set()
        for rel, nid in s["neighbors"]:
            if (rel, nid) in seen: continue
            seen.add((rel, nid))
            if rel in ("sibling", "child", "parent") and confident: strong[nid] += 1.0
            if rel == "uses": uses[nid] += 1.0
    return strong, uses

def build_rows(tasks, dump):
    rows = []
    for rec in dump:
        gold = set(tasks[rec["id"]]["gold"])
        lex = {sid: r for r, (sid, _) in enumerate(rec["lexical"]) if sid}
        sem = {sid: r for r, (sid, _) in enumerate(rec["semantic"]) if sid}
        fused = [sid for sid, _, _ in rec["fused"]]
        fr = {sid: i for i, sid in enumerate(fused)}
        strong, uses = supports(rec, lex, sem, fr)
        for sid in set(lex) | set(sem):
            rows.append((feats_for(sid, lex, sem, strong, uses), 1 if sid in gold else 0))
    return rows

def fit(rows, l2=1e-3, iters=400, lr=0.05, neg_cap=8000):
    random.seed(11)
    pos = [r for r in rows if r[1]]; neg = [r for r in rows if not r[1]]
    neg = random.sample(neg, min(len(neg), neg_cap))
    data = pos + neg
    wpos = len(neg) / max(1, len(pos))
    n = len(FEATS); w = [0.0] * n; b = 0.0
    for _ in range(iters):
        gw = [0.0] * n; gb = 0.0
        for x, y in data:
            z = b + sum(wi * xi for wi, xi in zip(w, x))
            p = 1 / (1 + math.exp(-max(-30, min(30, z))))
            g = (wpos if y else 1.0) * (p - y)
            for i in range(n): gw[i] += g * x[i]
            gb += g
        m = len(data)
        for i in range(n): w[i] -= lr * (gw[i] / m + l2 * w[i])
        b -= lr * gb / m
    return {"features": FEATS, "weights": [round(x, 2) for x in w], "bias": round(b, 2)}

def apply(weights, rec, base_order, top_n=20, drop=()):
    lex = {sid: r for r, (sid, _) in enumerate(rec["lexical"]) if sid}
    sem = {sid: r for r, (sid, _) in enumerate(rec["semantic"]) if sid}
    fr = {sid: i for i, sid in enumerate(base_order)}
    strong, uses = supports(rec, lex, sem, fr)
    w = list(weights["weights"])
    for d in drop: w[FEATS.index(d)] = 0.0
    head = []
    for sid in base_order[:top_n]:
        x = feats_for(sid, lex, sem, strong, uses)
        head.append((sid, sum(wi * xi for wi, xi in zip(w, x))))
    head.sort(key=lambda t: (-t[1], IDS.get(t[0], 0), t[0]))
    return [sid for sid, _ in head] + base_order[top_n:]

import struct
def _f32(x):
    return struct.unpack("f", struct.pack("f", x))[0]

def rrf(lex, sem, k, wl=0.6, ws=0.4):
    # f32 arithmetic, term by term, exactly as `RetrievalEngine::search`
    # accumulates `weight / (K + rank + 1)` — f64 would merge ties that f32
    # keeps distinct, and production breaks the resulting near-ties by id.
    s = defaultdict(float)
    for rank, (sid, _) in enumerate(lex):
        s[sid] = _f32(s[sid] + _f32(_f32(wl) / _f32(k + rank + 1)))
    for rank, (sid, _) in enumerate(sem):
        s[sid] = _f32(s[sid] + _f32(_f32(ws) / _f32(k + rank + 1)))
    return s

IDS = {}  # symbol path -> numeric FNV id (production's tie-break), per task

def order(scores):
    return [sid for sid, _ in sorted(scores.items(), key=lambda kv: (-kv[1], IDS.get(kv[0], 0), kv[0]))]

def metrics(ranked, gold):
    gold = set(gold); r = {}
    for k in (5, 10, 20): r[f"R@{k}"] = len(gold & set(ranked[:k])) / len(gold)
    dcg = sum(1 / math.log2(i + 2) for i, sid in enumerate(ranked[:10]) if sid in gold)
    idcg = sum(1 / math.log2(i + 2) for i in range(min(len(gold), 10)))
    r["nDCG@10"] = dcg / idcg if idcg else 0.0
    r["MRR"] = next((1 / (i + 1) for i, sid in enumerate(ranked) if sid in gold), 0.0)
    return r

def boot_ci(deltas, n=2000):
    random.seed(3); ms = []
    for _ in range(n):
        ms.append(statistics.fmean(random.choice(deltas) for _ in deltas))
    ms.sort(); return ms[int(0.025 * n)], ms[int(0.975 * n)]

if __name__ == "__main__" and sys.argv[1] == "fit-dump":
    rows = []
    args = sys.argv[2:]
    for i in range(0, len(args), 2):
        tasks = {t["id"]: t for t in map(json.loads, open(args[i]))}
        dump = [json.loads(l) for l in open(args[i + 1])]
        rows += build_rows(tasks, dump)
    print(json.dumps(fit(rows))); sys.exit(0)

# ---- eval ----
def _main():
    weights = json.load(open(sys.argv[2]))
    tasks = {t["id"]: t for t in map(json.loads, open(sys.argv[3]))}
    dump = [json.loads(l) for l in open(sys.argv[4])]
    judg = {}
    if "--judgments" in sys.argv:
        for l in open(sys.argv[sys.argv.index("--judgments") + 1]):
            j = json.loads(l); judg[(j["task"], j["cand"])] = j["noul"]
    variants = defaultdict(list); per_repo = defaultdict(lambda: defaultdict(list))
    hard = defaultdict(list); hard_sib = defaultdict(list)
    for rec in dump:
        t = tasks[rec["id"]]; gold = set(t["gold"]); repo = t.get("repo", "?")
        IDS.clear(); IDS.update({k: int(v) for k, v in rec.get("ids", {}).items()})
        lex = [(sid, sc) for sid, sc in rec["lexical"] if sid]; sem = [(sid, sc) for sid, sc in rec["semantic"] if sid]
        k60 = order(rrf(lex, sem, 60)); k10 = order(rrf(lex, sem, 10))
        V = {
            "RRF K=60 (production)": k60,
            "RRF K=10": k10,
            "K=60 + rerank": apply(weights, rec, k60),
            "K=10 + rerank": apply(weights, rec, k10),
            "K=10 + rerank (no struct)": apply(weights, rec, k10, drop=("struct_strong", "struct_uses")),
            "K=10 + rerank (no in_both)": apply(weights, rec, k10, drop=("in_both",)),
            "K=10 + rerank (no sem)": apply(weights, rec, k10, drop=("neg_log_sem_rank",)),
            "K=10 + rerank top10": apply(weights, rec, k10, top_n=10),
            "lexical only": [sid for sid, _ in lex],
        }
        for name, ranked in V.items():
            m = metrics(ranked, gold); variants[name].append(m); per_repo[name][repo].append(m)
            for sid, _ in lex[:5]:
                nj = judg.get((rec["id"], sid))
                if nj is not None and nj < 0.3 and sid not in gold:
                    hard[name].append(ranked.index(sid) + 1 if sid in ranked else 999)
            for g in gold:
                if g not in ranked: continue
                f, q = g.split("#", 1); parent = q.rsplit(".", 1)[0] if "." in q else None
                if not parent: continue
                gi = ranked.index(g)
                for sid in ranked[:50]:
                    if sid in gold: continue
                    f2, q2 = sid.split("#", 1)
                    if f2 == f and q2.startswith(parent + "."):
                        hard_sib[name].append(int(ranked.index(sid) < gi))

    base = variants["RRF K=60 (production)"]
    repos = sorted({tasks[d["id"]].get("repo", "?") for d in dump})
    print(f"tasks: {len(dump)} | repos: {repos}")
    print("\n| variant | R@5 | R@10 | R@20 | nDCG@10 | MRR | ΔnDCG@10 vs K=60 [95% CI] | macro R@10 (repo-balanced) |")
    print("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    for name, ms in variants.items():
        mean = lambda k: statistics.fmean(m[k] for m in ms)
        d = [a["nDCG@10"] - b["nDCG@10"] for a, b in zip(ms, base)]
        lo, hi = boot_ci(d)
        macro = statistics.fmean(statistics.fmean(m["R@10"] for m in v) for v in per_repo[name].values())
        print(f"| {name} | {mean('R@5'):.3f} | {mean('R@10'):.3f} | {mean('R@20'):.3f} | {mean('nDCG@10'):.3f} | {mean('MRR'):.3f} | {statistics.fmean(d):+.3f} [{lo:+.3f}, {hi:+.3f}] | {macro:.3f} |")
    if hard:
        print("\nhard negatives (a): judged-not-relevant lexical top-5 items — mean rank (capped 50; higher = better demotion), fraction still in top-5")
        for name, rs in hard.items():
            print(f"  {name}: n={len(rs)} mean rank {statistics.fmean(min(r, 50) for r in rs):.1f}, in top-5 {sum(1 for r in rs if r <= 5)/len(rs):.2f}")
    if hard_sib:
        print("\nhard negatives (b): same-parent non-gold siblings in top-50 — fraction ranked above the gold symbol")
        for name, rs in hard_sib.items():
            print(f"  {name}: n={len(rs)} above-gold {statistics.fmean(rs):.2f}")


if __name__ == "__main__" and sys.argv[1] == "eval":
    _main()
