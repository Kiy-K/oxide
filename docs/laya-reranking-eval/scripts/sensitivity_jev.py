#!/usr/bin/env python3
"""Post-hoc sensitivity analysis (NOT pre-registered; does not change Q1):
re-score Stage 1 dev-plain orderings on the 28 Jev-judged tasks with
relevance = commit gold ∪ {Jev noul >= 0.5}, to test whether commit-gold
incompleteness explains Laya's Stage 1 loss.

Two views, because Jev judged exactly production's (fused) top-5 plus the
lexical/semantic top-5, so "unjudged = non-relevant" is biased toward
production by construction:
  full       unjudged candidates count as non-relevant (biased view)
  condensed  unjudged candidates are dropped from every ordering before
             nDCG@10 (judged-only condensed list)
Also reports the judged fraction of each ordering's top-10. Stdlib only."""
import json, math, random, statistics, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from evaluate import reorder, boot_ci  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
RFE = ROOT / "docs/ranking-fusion-eval/results"
R = ROOT / "docs/laya-reranking-eval/results"
tasks = {json.loads(l)["id"]: json.loads(l) for l in open(RFE / "tasks.jsonl")}
jev = {}
for j in map(json.loads, open(RFE / "judgments.jsonl")):
    jev.setdefault(j["task"], {})[j["cand"]] = j["noul"]
sl = {json.loads(l)["id"]: json.loads(l) for l in open(R / "inputs/dev-plain-shortlists.jsonl")}
sc = {json.loads(l)["id"]: json.loads(l) for l in open(R / "quality/stage1/scores-english-noul-plain.jsonl")}


def ndcg(ranked, rel, k=10):
    dcg = sum(1 / math.log2(i + 2) for i, s in enumerate(ranked[:k]) if s in rel)
    ideal = sum(1 / math.log2(i + 2) for i in range(min(len(rel & set(ranked)), k)))
    return dcg / ideal if ideal else None


out = {"judged_fraction_of_top10": {}}
jf = {"production": [], "C1": [], "C2": []}
for tid in sorted(jev):
    if tid not in sl:
        continue
    sids = [c["sid"] for c in sl[tid]["cands"]]
    p = sc[tid]["scores"]["noul_ab"]
    for k, o in (("production", sids), ("C1", reorder(sids, p, "C1")), ("C2", reorder(sids, p, "C2"))):
        jf[k].append(sum(x in jev[tid] for x in o[:10]) / 10)
out["judged_fraction_of_top10"] = {k: round(statistics.fmean(v), 3) for k, v in jf.items()}
for view, label in [(v, l) for v in ("full", "condensed") for l in ("commit_gold", "gold_or_jev_relevant", "jev_relevant_only")]:
    rows = []
    for tid in sorted(jev):
        if tid not in sl:
            continue
        sids = [c["sid"] for c in sl[tid]["cands"]]
        gold = set(tasks[tid]["gold"])
        jrel = {s for s, v in jev[tid].items() if v >= 0.5}
        rel = {"commit_gold": gold, "gold_or_jev_relevant": gold | jrel, "jev_relevant_only": jrel}[label]
        p = sc[tid]["scores"]["noul_ab"]
        v = {"production": sids, "C1": reorder(sids, p, "C1"), "C2": reorder(sids, p, "C2")}
        if view == "condensed":
            v = {k: [x for x in o if x in jev[tid]] for k, o in v.items()}
        m = {k: ndcg(o, rel) for k, o in v.items()}
        if m["production"] is not None:
            rows.append(m)
    res = {"tasks": len(rows)}
    for k in ("production", "C1", "C2"):
        res[k] = round(statistics.fmean(r[k] for r in rows), 3)
    for k in ("C1", "C2"):
        d = [r[k] - r["production"] for r in rows]
        res[f"d_{k}"] = round(statistics.fmean(d), 3)
        res[f"d_{k}_ci95"] = [round(x, 3) for x in boot_ci(d)]
        res[f"wins_losses_{k}"] = (sum(x > 0 for x in d), sum(x < 0 for x in d))
    out[f"{view}/{label}"] = res
print(json.dumps(out, indent=1))
