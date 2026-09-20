#!/usr/bin/env python3
"""Offline fusion/reranking evaluation over `fusion_dump` output.

Reads tasks.jsonl (gold labels) and dump.jsonl (exact per-channel top-200
inputs, production fused list, top-seed neighbors, production pack), then:
  * verifies the offline RRF reproduces production's fused scores exactly,
  * partitions each task's loss: route (gold in no channel's top-200),
    ordering (in a channel but outside fused top-10), allocation (in
    fused top-10 but not in the pack),
  * scores challengers: RRF K/weight sweep, min-max / z-score CombSUM,
    CombMNZ, and a bounded deterministic evidence-aware rerank of the
    fused top-N (structural support from the top seeds' neighbors),
  * reports Recall@5/10/20, nDCG@10, MRR, gold-in-pack, utility/token.

usage: fusion_eval.py tasks.jsonl dump.jsonl [--md]
"""
import json, math, sys, statistics
from collections import defaultdict

tasks = {t["id"]: t for t in map(json.loads, open(sys.argv[1]))}
dump = [json.loads(l) for l in open(sys.argv[2])]
K_RRF, W_LEX, W_SEM = 60.0, 0.6, 0.4

import struct
def _f32(x):
    return struct.unpack("f", struct.pack("f", x))[0]

def rrf(lex, sem, k=K_RRF, wl=W_LEX, ws=W_SEM):
    # f32 arithmetic, term by term, exactly as `RetrievalEngine::search`
    # accumulates `weight / (K + rank + 1)` — f64 would merge ties that f32
    # keeps distinct, and production breaks the resulting near-ties by id.
    s = defaultdict(float)
    for rank, (sid, _) in enumerate(lex):
        s[sid] = _f32(s[sid] + _f32(_f32(wl) / _f32(k + rank + 1)))
    for rank, (sid, _) in enumerate(sem):
        s[sid] = _f32(s[sid] + _f32(_f32(ws) / _f32(k + rank + 1)))
    return s

def normalized(lex, sem, how, wl=W_LEX, ws=W_SEM, mnz=False):
    def norm(ch):
        vals = [v for _, v in ch]
        if not vals:
            return {}
        if how == "minmax":
            lo, hi = min(vals), max(vals)
            return {sid: (v - lo) / (hi - lo) if hi > lo else 1.0 for sid, v in ch}
        if how == "zscore":
            mu = statistics.fmean(vals); sd = statistics.pstdev(vals) or 1.0
            return {sid: (v - mu) / sd for sid, v in ch}
        raise ValueError(how)
    nl, ns = norm(lex), norm(sem)
    s = defaultdict(float); hits = defaultdict(int)
    for sid, v in nl.items(): s[sid] += wl * v; hits[sid] += 1
    for sid, v in ns.items(): s[sid] += ws * v; hits[sid] += 1
    if mnz:
        for sid in s: s[sid] *= hits[sid]
    return s

IDS = {}  # symbol path -> numeric FNV id, filled per task from the dump

def order(scores):
    # Production's `cmp_score_id`: score desc, then numeric symbol id asc.
    return [sid for sid, _ in sorted(scores.items(), key=lambda kv: (-kv[1], IDS.get(kv[0], 0), kv[0]))]

def evidence_rerank(fused_order, base_scores, neighbors, top_n=20, beta=0.5, seeds=3, rel_weights=None):
    """Bounded, deterministic: within the fused top-N, multiply a candidate's
    score by (1 + beta * support) where support sums relation weights over
    the top-`seeds` seeds that list the candidate as a structural neighbor
    (capped at 2). Candidates outside top-N and the seeds themselves are
    untouched, so direct hits are never displaced below rank N."""
    rel_weights = rel_weights or {"uses": 1.0, "imported-definition": 1.0, "child": 0.5, "parent": 0.5, "sibling": 0.25, "test": 0.25}
    support = defaultdict(float)
    seed_ids = set()
    for s in neighbors[:seeds]:
        seed_ids.add(s["seed"])
        for rel, nid in s["neighbors"]:
            support[nid] += rel_weights.get(rel, 0.0)
    out = []
    for i, sid in enumerate(fused_order):
        sc = base_scores[sid]
        if i < top_n and sid not in seed_ids:
            sc = sc * (1.0 + beta * min(support.get(sid, 0.0), 2.0))
        out.append((sid, sc))
    head = sorted(out[:top_n], key=lambda x: (-x[1], IDS.get(x[0], 0), x[0]))
    return [sid for sid, _ in head] + [sid for sid, _ in out[top_n:]]

def metrics(ranked, gold):
    gold = set(gold)
    r = {}
    for k in (5, 10, 20):
        r[f"R@{k}"] = len(gold & set(ranked[:k])) / len(gold)
    dcg = sum(1 / math.log2(i + 2) for i, sid in enumerate(ranked[:10]) if sid in gold)
    idcg = sum(1 / math.log2(i + 2) for i in range(min(len(gold), 10)))
    r["nDCG@10"] = dcg / idcg if idcg else 0.0
    r["MRR"] = next((1 / (i + 1) for i, sid in enumerate(ranked) if sid in gold), 0.0)
    return r

def mean(rows, key):
    return statistics.fmean(r[key] for r in rows) if rows else 0.0

variants = {}
partition = {"route": 0, "ordering@10": 0, "allocation": 0, "hit@pack": 0, "n": 0}
per_task_prod = []
for rec in dump:
    t = tasks[rec["id"]]; gold = set(t["gold"])
    lex = [(sid, sc) for sid, sc in rec["lexical"] if sid]
    sem = [(sid, sc) for sid, sc in rec["semantic"] if sid]
    prod = [sid for sid, _, _ in rec["fused"]]
    prod_scores = {sid: sc for sid, sc, _ in rec["fused"]}
    # --- reproduce production RRF from the dumped channels: scores AND order ---
    IDS.clear(); IDS.update({k: int(v) for k, v in rec.get("ids", {}).items()})
    mine = rrf(lex, sem)
    assert set(prod_scores) == set(mine) and all(abs(prod_scores[k] - mine[k]) < 1e-6 for k in mine), \
        f"offline RRF != production for {rec['id']}"
    assert order(mine) == prod, f"offline RRF order != production order for {rec['id']}"
    # --- loss partition (K=10) ---
    partition["n"] += 1
    in_channels = gold & ({sid for sid, _ in lex} | {sid for sid, _ in sem})
    pack_ids = {i["id"] for i in rec["pack"]["items"]}
    if not in_channels:
        partition["route"] += 1
    elif not (gold & set(prod[:10])):
        partition["ordering@10"] += 1
    elif not (gold & pack_ids):
        partition["allocation"] += 1
    else:
        partition["hit@pack"] += 1
    # --- variants ---
    def add(name, ranked):
        m = metrics(ranked, gold); m["id"] = rec["id"]
        variants.setdefault(name, []).append(m)
    add("production RRF (K=60, 0.6/0.4)", prod)
    add("lexical only", [sid for sid, _ in lex])
    add("semantic only", [sid for sid, _ in sem])
    for k in (10, 20, 40, 60, 100, 200):
        add(f"RRF K={k}", order(rrf(lex, sem, k=k)))
    for wl in (0.3, 0.4, 0.5, 0.6, 0.7, 0.8):
        add(f"RRF w_lex={wl}", order(rrf(lex, sem, wl=wl, ws=1 - wl)))
    add("minmax CombSUM 0.6/0.4", order(normalized(lex, sem, "minmax")))
    add("minmax CombSUM 0.5/0.5", order(normalized(lex, sem, "minmax", 0.5, 0.5)))
    add("minmax CombMNZ", order(normalized(lex, sem, "minmax", mnz=True)))
    add("zscore CombSUM 0.6/0.4", order(normalized(lex, sem, "zscore")))
    add("zscore CombSUM 0.5/0.5", order(normalized(lex, sem, "zscore", 0.5, 0.5)))
    for beta in (0.25, 0.5, 1.0):
        add(f"RRF + evidence rerank top20 beta={beta}", evidence_rerank(prod, prod_scores, rec["neighbors"], beta=beta))
    add("RRF + evidence rerank top10 beta=0.5", evidence_rerank(prod, prod_scores, rec["neighbors"], top_n=10, beta=0.5))
    add("production expanded (search default)", [sid for sid, _, _ in rec["expanded"]])
    # pack utility
    items = rec["pack"]["items"]
    rel_tok = sum(i["est_tokens"] for i in items if i["id"] in gold)
    per_task_prod.append({"gold_in_pack": 1.0 if gold & pack_ids else 0.0,
                          "used": rec["pack"]["used_tokens"], "rel_tok": rel_tok,
                          "items": len(items), "rel_items": sum(1 for i in items if i["id"] in gold)})

md = "--md" in sys.argv
rows = []
for name, ms in variants.items():
    rows.append((name, mean(ms, "R@5"), mean(ms, "R@10"), mean(ms, "R@20"), mean(ms, "nDCG@10"), mean(ms, "MRR")))
if md:
    print(f"tasks: {partition['n']} | loss partition @10: route {partition['route']}, ordering {partition['ordering@10']}, allocation {partition['allocation']}, hit-in-pack {partition['hit@pack']}")
    print()
    print("| variant | R@5 | R@10 | R@20 | nDCG@10 | MRR |")
    print("| --- | ---: | ---: | ---: | ---: | ---: |")
    for name, r5, r10, r20, nd, mrr in rows:
        print(f"| {name} | {r5:.3f} | {r10:.3f} | {r20:.3f} | {nd:.3f} | {mrr:.3f} |")
    print()
    n = len(per_task_prod)
    print(f"production pack: gold-in-pack {sum(p['gold_in_pack'] for p in per_task_prod)/n:.3f}, "
          f"mean used tokens {statistics.fmean(p['used'] for p in per_task_prod):.0f}, "
          f"relevant tokens per 1k pack tokens {1000*sum(p['rel_tok'] for p in per_task_prod)/max(1,sum(p['used'] for p in per_task_prod)):.1f}, "
          f"relevant items/pack {statistics.fmean(p['rel_items'] for p in per_task_prod):.2f} of {statistics.fmean(p['items'] for p in per_task_prod):.1f}")
else:
    json.dump({"partition": partition, "variants": variants, "pack": per_task_prod}, sys.stdout)
