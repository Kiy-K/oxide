#!/usr/bin/env python3
"""Issue #15 offline evaluation: production order vs Laya-reordered shortlist.

  evaluate.py dev <laya_scores_plain.jsonl> <laya_scores_masked.jsonl>
      Candidate-level screen on the dev set (commit gold), all configurations.

A configuration is <question>/<combination>: question in {noul_ab, choice_ab},
combination C1 = pure Laya order, C2 = equal-weight RRF(K=60) of original rank
and Laya rank. Ties always break by original rank. A task whose Laya call
failed keeps production order (counted).
"""
import json
import math
import random
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
RFE = ROOT / "docs/ranking-fusion-eval/results"
INPUTS = ROOT / "docs/laya-reranking-eval/results/inputs"
QUESTIONS = ("noul_ab", "choice_ab")
COMBOS = ("C1", "C2")


def reorder(sids, probs, combo):
    n = len(sids)
    if combo == "C1":
        key = {i: (-probs[i], i) for i in range(n)}
    else:
        laya_rank = {i: r for r, i in enumerate(sorted(range(n), key=lambda i: (-probs[i], i)))}
        key = {i: (-(1 / (60 + i + 1) + 1 / (60 + laya_rank[i] + 1)), i) for i in range(n)}
    return [sids[i] for i in sorted(range(n), key=lambda i: key[i])]


def metrics(ranked, gold, k=10):
    rel = [1 if s in gold else 0 for s in ranked]
    dcg = sum(r / math.log2(i + 2) for i, r in enumerate(rel[:k]))
    ideal = sum(1 / math.log2(i + 2) for i in range(min(len(gold), k)))
    mrr = next((1 / (i + 1) for i, r in enumerate(rel) if r), 0.0)
    g = max(1, len(gold))
    return {"ndcg10": dcg / ideal if ideal else 0.0, "mrr": mrr,
            "r5": sum(rel[:5]) / g, "r10": sum(rel[:10]) / g}


def auc(scores, labels):
    pos = [s for s, l in zip(scores, labels) if l]
    neg = [s for s, l in zip(scores, labels) if not l]
    if not pos or not neg:
        return None
    wins = sum((p > q) + 0.5 * (p == q) for p in pos for q in neg)
    return wins / (len(pos) * len(neg))


def boot_ci(d, n=2000, seed=0):
    rng = random.Random(seed)
    m = sorted(statistics.fmean(rng.choices(d, k=len(d))) for _ in range(n))
    return m[int(0.025 * n)], m[int(0.975 * n) - 1]


def variants_for(sc, sids):
    """Production + every configuration + 10 seeded random permutations."""
    out = {"production": list(sids)}
    for q in QUESTIONS:
        if sc.get("scores") is not None and q not in sc["scores"]:
            continue  # question not asked in this run
        for c in COMBOS:
            if sc.get("scores") is None:
                out[f"{q}/{c}"] = list(sids)  # failure fallback: production order
            else:
                assert sc["sids"] == list(sids), "score rows must align with the shortlist"
                out[f"{q}/{c}"] = reorder(list(sids), sc["scores"][q], c)
    for seed in range(10):
        r = list(sids)
        random.Random(seed).shuffle(r)
        out[f"random#{seed}"] = r
    return out


def dev(plain_path, masked_path):
    out = {}
    names = ["production", "random"] + [f"{q}/{c}" for q in QUESTIONS for c in COMBOS]
    for regime, path in (("plain", plain_path), ("masked", masked_path)):
        tfile = "tasks.jsonl" if regime == "plain" else "tasks-masked.jsonl"
        tasks = {json.loads(l)["id"]: json.loads(l) for l in open(RFE / tfile)}
        shortl = [json.loads(l) for l in open(INPUTS / f"dev-{regime}-shortlists.jsonl")]
        scores = {j["id"]: j for j in map(json.loads, open(path))}
        import gzip
        union = {}
        for l in gzip.open(RFE / f"dump-{regime}.jsonl.gz", "rt"):
            d = json.loads(l)
            union[d["id"]] = {r[0] for r in d["lexical"] if r[0]} | {r[0] for r in d["semantic"] if r[0]}
        per_task, aucs, fails, absent = [], {"fused_rank": []}, 0, 0
        route = {"gold_in_top20": 0, "gold_only_in_top200_union": 0, "gold_absent_from_pool": 0}
        churn = {}
        for s in shortl:
            gold = set(tasks[s["id"]]["gold"])
            sids = [c["sid"] for c in s["cands"]]
            sc = scores[s["id"]]
            fails += sc.get("scores") is None
            absent += not (gold & set(sids))
            if gold & set(sids):
                route["gold_in_top20"] += 1
            elif gold & union[s["id"]]:
                route["gold_only_in_top200_union"] += 1
            else:
                route["gold_absent_from_pool"] += 1
            V = variants_for(sc, sids)
            m = {k: metrics(v, gold) for k, v in V.items()}
            base10 = set(V["production"][:10])
            for k, v in V.items():
                if k.startswith("random#") or k == "production":
                    continue
                top10 = set(v[:10])
                c = churn.setdefault(k, {"fp_promoted_into_top10": 0, "fn_gold_demoted_out_of_top10": 0,
                                         "gold_promoted_into_top10": 0, "regressed_tasks": []})
                c["fp_promoted_into_top10"] += len((top10 - base10) - gold)
                c["fn_gold_demoted_out_of_top10"] += len((base10 - top10) & gold)
                c["gold_promoted_into_top10"] += len((top10 - base10) & gold)
                if m[k]["ndcg10"] < m["production"]["ndcg10"]:
                    c["regressed_tasks"].append(s["id"])
            rnd = [m[k] for k in m if k.startswith("random#")]
            m["random"] = {x: statistics.fmean(r[x] for r in rnd) for x in rnd[0]}
            per_task.append(m)
            labels = [x in gold for x in sids]
            a = auc([-i for i in range(len(sids))], labels)
            if a is not None and sc.get("scores") is not None:
                aucs["fused_rank"].append(a)
                for q in QUESTIONS:
                    if q in sc["scores"]:
                        aucs.setdefault(q, []).append(auc(sc["scores"][q], labels))
        rows = {}
        names = [n for n in names if n in per_task[0]]
        for name in names:
            rows[name] = {x: statistics.fmean(t[name][x] for t in per_task) for x in per_task[0][name]}
            if name != "production":
                d = [t[name]["ndcg10"] - t["production"]["ndcg10"] for t in per_task]
                rows[name]["d_ndcg10"] = statistics.fmean(d)
                rows[name]["d_ndcg10_ci95"] = boot_ci(d)
                rows[name]["wins_losses"] = (sum(x > 0 for x in d), sum(x < 0 for x in d))
        out[regime] = {"n": len(per_task), "failures": fails, "gold_absent_from_top20": absent,
                       "route": route, "top10_churn_vs_production": churn,
                       "auc_n_tasks": len(aucs["fused_rank"]),
                       "auc": {k: statistics.fmean(v) for k, v in aucs.items()}, "rows": rows}
    return out


def parity(base):
    """Replay production allocation from each captured `kept` pool and require
    the exact baseline pack (item set, per-item est_tokens, used_tokens).
    Also reports which allocation branches the 21 tasks actually exercise."""
    import gzip
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import oxide_replay as R
    base = Path(base)
    dump = {json.loads(l)["id"]: json.loads(l) for l in gzip.open(RFE / "dump-contextbench.jsonl.gz", "rt")}  # == HEAD re-dump, see results/provenance/README.md
    ok, branches, rows = 0, {"below_floor": 0, "over_budget": 0, "per_file_cap": 0,
                             "primary_cap": 0, "test_cap": 0}, []
    for l in open(RFE / "cb-tasks.jsonl"):
        t = json.loads(l)
        kept = json.load(open(base / "kept" / f"{t['id']}.json"))
        pack = json.load(open(base / "kept" / f"{t['id']}.pack.json"))
        items, used = R.allocate(t["path"], t["query"], kept, dump[t["id"]]["fused"][0][1], R.ids_for(kept))
        want = sorted((f"{x['file']}#{x['qualified_name']}", x["est_tokens"]) for x in pack["items"])
        good = want == sorted((s, e) for s, _, e in items) and used == pack["used_tokens"]
        ok += good
        why = [o["why"] for o in pack["omitted"]]
        for k, needle in (("below_floor", "relevance floor"), ("over_budget", "token budget"),
                          ("per_file_cap", "per-file"), ("primary_cap", "primary cap"), ("test_cap", "test cap")):
            branches[k] += any(needle in w for w in why)
        rows.append({"id": t["id"], "match": good, "used_tokens": pack["used_tokens"]})
    return {"tasks": len(rows), "exact_matches": ok, "tasks_exercising_branch": branches,
            "max_used_tokens": max(r["used_tokens"] for r in rows), "rows": rows}


if __name__ == "__main__":
    if sys.argv[1] == "dev":
        print(json.dumps(dev(sys.argv[2], sys.argv[3]), indent=1))
    elif sys.argv[1] == "parity":
        print(json.dumps(parity(sys.argv[2]), indent=1))
