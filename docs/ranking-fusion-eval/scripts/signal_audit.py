#!/usr/bin/env python3
"""Signal audit over fusion_dump output: which per-candidate signals
predict commit-labeled relevance, how correlated they are (double
counting), how precise structural neighbors are by relation type and seed
confidence, and an autopsy of the flat structural bonus.

usage: signal_audit.py tasks.jsonl dump.jsonl
"""
import json, math, re, sys, statistics
from collections import defaultdict

tasks = {t["id"]: t for t in map(json.loads, open(sys.argv[1]))}
dump = [json.loads(l) for l in open(sys.argv[2])]
REL = ("uses", "imported-definition", "child", "parent", "sibling", "test")

def toks(s):
    return {t.lower() for t in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", s)}

def name_segs(qname):
    last = qname.split(".")[-1]
    segs = {last.lower()}
    for t in re.split(r"[_.]", last):
        for c in re.findall(r"[A-Z]?[a-z0-9]+|[A-Z]+(?![a-z])", t):
            if len(c) >= 3: segs.add(c.lower())
    return segs

def rrf(lexr, semr, k):
    return (0.6 / (k + lexr + 1) if lexr is not None else 0) + (0.4 / (k + semr + 1) if semr is not None else 0)

rows = []          # one per (task, candidate)
rel_stats = defaultdict(lambda: [0, 0])      # rel -> [gold, total]
rel_by_conf = defaultdict(lambda: [0, 0])    # (rel, seed_conf) -> [gold, total]
seed_stats = defaultdict(lambda: [0, 0])     # seed_conf -> [seed is gold, total]
bonus_autopsy = {"promoted_into_top10": 0, "promoted_gold": 0, "displaced_gold": 0, "tasks": 0}
for rec in dump:
    t = tasks[rec["id"]]; gold = set(t["gold"]); qt = toks(t["query"])
    lex = {sid: (r, sc) for r, (sid, sc) in enumerate(rec["lexical"]) if sid}
    sem = {sid: (r, sc) for r, (sid, sc) in enumerate(rec["semantic"]) if sid}
    pool = set(lex) | set(sem)
    fused = [sid for sid, _, _ in rec["fused"]]
    fused_score = {sid: sc for sid, sc, _ in rec["fused"]}
    # structural support from top-3 K=60 seeds, with seed confidence
    support = defaultdict(lambda: defaultdict(float))
    for si, s in enumerate(rec["neighbors"][:3]):
        seed = s["seed"]; conf = "both" if (seed in lex and seed in sem) else ("lex" if seed in lex else "sem")
        seed_stats[conf][0] += seed in gold; seed_stats[conf][1] += 1
        seen = set()
        for rel, nid in s["neighbors"]:
            if (rel, nid) in seen: continue
            seen.add((rel, nid))
            support[nid][rel] += 1.0
            rel_stats[rel][0] += nid in gold; rel_stats[rel][1] += 1
            key = (rel, conf, "seed-gold" if seed in gold else "seed-nongold")
            rel_by_conf[key][0] += nid in gold; rel_by_conf[key][1] += 1
    for sid in pool:
        lr = lex.get(sid, (None, None))[0]; sr = sem.get(sid, (None, None))[0]
        segs = name_segs(sid.split("#", 1)[1])
        rows.append({
            "task": rec["id"], "id": sid, "gold": sid in gold,
            "lex_rank": lr if lr is not None else 200, "sem_rank": sr if sr is not None else 200,
            "in_both": int(lr is not None and sr is not None),
            "lex_score": lex.get(sid, (None, 0.0))[1] or 0.0, "sem_score": sem.get(sid, (None, 0.0))[1] or 0.0,
            "rrf60": rrf(lr, sr, 60), "rrf10": rrf(lr, sr, 10),
            "name_in_query": int(bool(segs & qt)),
            "name_cov": len(segs & qt) / max(1, len(segs)),
            "is_module": int(sid.endswith(":__module__")),
            "is_test": int("test" in sid.split("#")[0].lower()),
            "is_method": int("." in sid.split("#", 1)[1]),
            "struct_any": int(sid in support),
            **{f"rel_{r}": support[sid].get(r, 0.0) if sid in support else 0.0 for r in REL},
        })
    # flat-bonus autopsy (beta=0.5, top20 as in fusion_eval)
    weights = {"uses": 1.0, "imported-definition": 1.0, "child": 0.5, "parent": 0.5, "sibling": 0.25, "test": 0.25}
    sup = defaultdict(float); seeds = set()
    for s in rec["neighbors"][:3]:
        seeds.add(s["seed"])
        for rel, nid in s["neighbors"]: sup[nid] += weights.get(rel, 0)
    scored = []
    for i, sid in enumerate(fused[:20]):
        sc = fused_score[sid]
        if sid not in seeds: sc *= 1 + 0.5 * min(sup.get(sid, 0), 2.0)
        scored.append((sid, sc))
    new_top10 = [sid for sid, _ in sorted(scored, key=lambda x: (-x[1], x[0]))[:10]]
    old_top10 = fused[:10]
    promoted = [sid for sid in new_top10 if sid not in old_top10]
    displaced = [sid for sid in old_top10 if sid not in new_top10]
    bonus_autopsy["tasks"] += 1
    bonus_autopsy["promoted_into_top10"] += len(promoted)
    bonus_autopsy["promoted_gold"] += sum(1 for s in promoted if s in gold)
    bonus_autopsy["displaced_gold"] += sum(1 for s in displaced if s in gold)

def auc(rows, key, higher_is_better=True):
    pos = [r[key] for r in rows if r["gold"]]; neg = [r[key] for r in rows if not r["gold"]]
    if not pos or not neg: return float("nan")
    import random; random.seed(1)
    neg_s = random.sample(neg, min(len(neg), 20000))
    wins = ties = 0
    for p in pos:
        for n in neg_s:
            if p > n: wins += 1
            elif p == n: ties += 1
    a = (wins + 0.5 * ties) / (len(pos) * len(neg_s))
    return a if higher_is_better else 1 - a

n_pos = sum(r["gold"] for r in rows)
print(f"candidate rows: {len(rows)} ({n_pos} gold, base rate {n_pos/len(rows):.4f})")
print("\n| signal | AUC for gold | mean gold | mean non-gold |")
print("| --- | ---: | ---: | ---: |")
for key, hib in [("lex_rank", False), ("sem_rank", False), ("in_both", True), ("lex_score", True), ("sem_score", True),
                 ("rrf60", True), ("rrf10", True), ("name_in_query", True), ("name_cov", True),
                 ("is_module", False), ("is_test", False), ("is_method", True), ("struct_any", True)] + [(f"rel_{r}", True) for r in REL]:
    g = [r[key] for r in rows if r["gold"]]; ng = [r[key] for r in rows if not r["gold"]]
    print(f"| {key} | {auc(rows, key, hib):.3f} | {statistics.fmean(g):.3f} | {statistics.fmean(ng):.3f} |")

def corr(a, b):
    ma, mb = statistics.fmean(a), statistics.fmean(b)
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    den = math.sqrt(sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b)) or 1
    return num / den
keys = ["lex_rank", "sem_rank", "in_both", "name_in_query", "name_cov", "struct_any", "rrf60"]
print("\ncorrelation matrix (double-counting check):")
print("| | " + " | ".join(keys) + " |"); print("| --- |" + " ---: |" * len(keys))
for a in keys:
    print(f"| {a} | " + " | ".join(f"{corr([r[a] for r in rows], [r[b] for r in rows]):.2f}" for b in keys) + " |")

print("\nstructural neighbor precision by relation (top-3 K=60 seeds):")
print("| relation | neighbors | gold | precision |")
print("| --- | ---: | ---: | ---: |")
for rel in REL:
    g, n = rel_stats[rel]; print(f"| {rel} | {n} | {g} | {g/n if n else 0:.3f} |")
print("\nseed correctness by confidence: " + ", ".join(f"{c}: {g}/{n} gold ({g/n if n else 0:.2f})" for c, (g, n) in seed_stats.items()))
print("\nneighbor precision by (relation, seed confidence, seed correctness):")
for key in sorted(rel_by_conf):
    g, n = rel_by_conf[key]
    if n >= 10: print(f"  {key}: {g}/{n} = {g/n:.3f}")
print(f"\nflat bonus autopsy (β=0.5, top-20): tasks {bonus_autopsy['tasks']}, candidates promoted into top-10 {bonus_autopsy['promoted_into_top10']} "
      f"(gold: {bonus_autopsy['promoted_gold']}), gold displaced out of top-10 {bonus_autopsy['displaced_gold']}")
json.dump(rows, open(sys.argv[2].replace(".jsonl", "-features.json"), "w"))
