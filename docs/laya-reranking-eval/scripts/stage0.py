#!/usr/bin/env python3
"""Stage 0 (protocol §10): sanity, (checkpoint, question) selection and the
offline-judge comparison, on the Jev-judged dev pairs. Stdlib only.

  stage0.py build <out_shortlists.jsonl> <out_meta.json>
      One shortlist per judged dev task: the judged pairs that lie inside
      the dev-plain fused top-20 (in fused order), plus one unrelated
      control (a candidate from a task in a different repository). Model
      input is query + candidate code only; labels go to the meta file.
  stage0.py eval <meta.json> <scores_english.jsonl> <scores_multilingual.jsonl>
"""
import json
import random
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
RFE = ROOT / "docs/ranking-fusion-eval/results"
INPUTS = ROOT / "docs/laya-reranking-eval/results/inputs"
QS = ("noul_ab", "choice_ab", "choice_ba")


def build(out, meta_out):
    tasks = {json.loads(l)["id"]: json.loads(l) for l in open(RFE / "tasks.jsonl")}
    sl = {json.loads(l)["id"]: json.loads(l) for l in open(INPUTS / "dev-plain-shortlists.jsonl")}
    judged = {}
    for j in map(json.loads, open(RFE / "judgments.jsonl")):
        judged.setdefault(j["task"], {})[j["cand"]] = j["noul"]
    ids = sorted(t for t in judged if t in sl)
    meta = {"pairs": [], "controls": []}
    with open(out, "w") as fh:
        for n, tid in enumerate(ids):
            s = sl[tid]
            cands = [dict(c, rank=i) for i, c in enumerate(s["cands"]) if c["sid"] in judged[tid]]
            # control: first candidate of the next task (cyclic) from another repository
            for k in range(1, len(ids)):
                other = ids[(n + k) % len(ids)]
                if tasks[other]["repo"] != tasks[tid]["repo"]:
                    ctrl = dict(sl[other]["cands"][0], sid="CONTROL::" + sl[other]["cands"][0]["sid"])
                    break
            for c in cands:
                meta["pairs"].append({"task": tid, "sid": c["sid"], "rank": c["rank"],
                                      "gold": c["sid"] in tasks[tid]["gold"], "jev": judged[tid][c["sid"]]})
            meta["controls"].append({"task": tid, "sid": ctrl["sid"], "from_task": other})
            out_c = [{k: c[k] for k in ("sid", "path", "symbol", "source")} for c in cands + [ctrl]]
            fh.write(json.dumps({"id": tid, "query": s["query"], "cands": out_c}) + "\n")
    json.dump(meta, open(meta_out, "w"), indent=1)
    print(f"tasks {len(ids)} pairs {len(meta['pairs'])} gold {sum(p['gold'] for p in meta['pairs'])} "
          f"controls {len(meta['controls'])}", file=sys.stderr)


def auc(scores, labels):
    pos = [s for s, l in zip(scores, labels) if l]
    neg = [s for s, l in zip(scores, labels) if not l]
    if not pos or not neg:
        return None
    return sum((p > q) + 0.5 * (p == q) for p in pos for q in neg) / (len(pos) * len(neg))


def boot_auc(rows, key, label, n=2000, seed=0):
    """Task-level bootstrap of pooled AUC."""
    by_task = {}
    for r in rows:
        by_task.setdefault(r["task"], []).append(r)
    tids = sorted(by_task)
    rng = random.Random(seed)
    vals = []
    for _ in range(n):
        smp = [r for t in rng.choices(tids, k=len(tids)) for r in by_task[t]]
        a = auc([key(r) for r in smp], [label(r) for r in smp])
        if a is not None:
            vals.append(a)
    vals.sort()
    return vals[int(0.025 * len(vals))], vals[int(0.975 * len(vals)) - 1]


def spearman(a, b):
    def ranks(v):
        o = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(o):
            j = i
            while j + 1 < len(o) and v[o[j + 1]] == v[o[i]]:
                j += 1
            for k in range(i, j + 1):
                r[o[k]] = (i + j) / 2
            i = j + 1
        return r
    ra, rb = ranks(a), ranks(b)
    ma, mb = statistics.fmean(ra), statistics.fmean(rb)
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    den = (sum((x - ma) ** 2 for x in ra) * sum((y - mb) ** 2 for y in rb)) ** 0.5
    return num / den if den else 0.0


def evaluate(meta_path, *score_paths):
    meta = json.load(open(meta_path))
    rows = [dict(p) for p in meta["pairs"]]
    idx = {(r["task"], r["sid"]): r for r in rows}
    out = {"n_pairs": len(rows), "n_gold": sum(r["gold"] for r in rows),
           "n_tasks": len({r["task"] for r in rows})}
    gold = lambda r: r["gold"]  # noqa: E731
    out["fused_rank_auc_vs_gold"] = auc([-r["rank"] for r in rows], [gold(r) for r in rows])
    out["fused_rank_auc_ci95"] = boot_auc(rows, lambda r: -r["rank"], gold)
    out["jev_auc_vs_gold"] = auc([r["jev"] for r in rows], [gold(r) for r in rows])
    out["jev_auc_ci95"] = boot_auc(rows, lambda r: r["jev"], gold)
    out["configs"] = {}
    for path in score_paths:
        ck = "multilingual" if "multilingual" in path else "english"
        ctrl_sep = {q: [] for q in QS}
        fails = 0
        for line in open(path):
            j = json.loads(line)
            if j["scores"] is None:
                fails += 1
                continue
            for q in QS:
                if q not in j["scores"]:
                    continue
                vals = dict(zip(j["sids"], j["scores"][q]))
                judged_vals = []
                for sid, v in vals.items():
                    if sid.startswith("CONTROL::"):
                        continue
                    idx[(j["id"], sid)][f"{ck}/{q}"] = v
                    judged_vals.append(v)
                ctrl = next(v for sid, v in vals.items() if sid.startswith("CONTROL::"))
                ctrl_sep[q].append(ctrl < statistics.median(judged_vals))
        for q in QS:
            key = f"{ck}/{q}"
            if not all(key in r for r in rows):
                continue
            s = [r[key] for r in rows]
            jl = [r["jev"] >= 0.5 for r in rows]
            out["configs"][key] = {
                "auc_vs_gold": auc(s, [gold(r) for r in rows]),
                "auc_vs_gold_ci95": boot_auc(rows, lambda r, k=key: r[k], gold),
                "auc_vs_jev": auc(s, jl),
                "agree_with_jev_at_0.5": statistics.fmean((a >= 0.5) == b for a, b in zip(s, jl)),
                "frac_judged_relevant": statistics.fmean(a >= 0.5 for a in s),
                "controls_below_task_median": statistics.fmean(ctrl_sep[q]) if ctrl_sep[q] else None,
                "failures": fails,
            }
        if all(f"{ck}/choice_ab" in r and f"{ck}/choice_ba" in r for r in rows):
            out["configs"][f"{ck}/swap_spearman"] = spearman([r[f"{ck}/choice_ab"] for r in rows],
                                                             [r[f"{ck}/choice_ba"] for r in rows])
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    if sys.argv[1] == "build":
        build(sys.argv[2], sys.argv[3])
    else:
        evaluate(*sys.argv[2:])
