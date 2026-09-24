#!/usr/bin/env python3
"""Score semantic-channel challengers on the 21 pinned ContextBench instances
with ContextBench's own metric code (scripts/agent_eval/contextbench_run.py
.evaluate_task). Each dump comes from examples/semantic_variant.rs: the
production BM25 top-200, the variant's semantic top-200, the production RRF
fusion (`fused`, no expansion) and the production context pack.

Per dump and instance:
  sem@50   — semantic top-50 as the prediction (candidate recall of the
             channel the variant changes), symbol/line coverage
  fused@10 — production-fused top-10 (file/symbol/line coverage, line precision)
  fused@5  — file coverage of the fused top-5
  pack     — the production context pack's items (file/symbol/line coverage,
             line precision) and gold lines per 1k pack tokens
Deltas vs the baseline dump carry a paired bootstrap 95% CI (2000 resamples,
seed 0). ContextBench instances are indexed at the issue's base commit, so,
unlike the commit-derived held-out set, the gold code is not yet written.

usage: eval-agent/.venv/bin/python cb_variants.py cb-tasks.jsonl baseline.jsonl [name=dump.jsonl ...]
"""
import json, random, statistics, sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/agent_eval"))
import contextbench_run as cb  # noqa: E402

tasks = {t["id"]: t for t in map(json.loads, open(sys.argv[1]))}
runs = [("baseline", sys.argv[2])] + [tuple(a.split("=", 1)) for a in sys.argv[3:]]
pinned = set((ROOT / "eval-agent/results/tier_a_instances.txt").read_text().split())
rows = {r["instance_id"]: r for r in cb.load_tasks() if r["instance_id"] in pinned}


def items(rec, ids):
    out, missing = [], 0
    for sid in ids:
        span = rec["spans"].get(sid)
        if not span:
            missing += 1
            continue
        out.append({"file": sid.split("#")[0], "start_line": span[0], "end_line": span[1]})
    return out, missing


def gold_lines(row):
    g = cb.Gold({"init_ctx": json.loads(row["gold_context"]) if isinstance(row["gold_context"], str)
                 else row["gold_context"], "repo_url": row["repo_url"], "commit": row["base_commit"]})
    lines = defaultdict(set)
    for it in g.init + g.add:
        if it.get("file"):
            lines[it["file"]].update(range(it.get("start_line", 1), it.get("end_line", 1) + 1))
    return lines


def score(path):
    out, unresolved = {}, 0
    for line in open(path):
        if not line.strip():
            continue
        rec = json.loads(line)
        t = tasks[rec["id"]]; row = rows[rec["id"]]; repo = Path(t["path"])
        sem = [s for s, _ in rec["semantic"] if s]
        fused = [s for s, _, _ in rec["fused"]]
        pack = [i["id"] for i in rec["pack"]["items"]]
        m = {}
        it, _ = items(rec, sem[:50]); e = cb.evaluate_task(repo, row, it)
        m["sem@50 symbol.cov"] = e["symbol"]["coverage"]; m["sem@50 line.cov"] = e["line"]["coverage"]
        it, _ = items(rec, fused[:10]); e = cb.evaluate_task(repo, row, it)
        for g in ("file", "symbol", "line"):
            m[f"fused@10 {g}.cov"] = e[g]["coverage"]
        m["fused@10 line.prec"] = e["line"]["precision"]
        it, _ = items(rec, fused[:5]); e = cb.evaluate_task(repo, row, it)
        m["fused@5 file.cov"] = e["file"]["coverage"]
        it, miss = items(rec, pack); unresolved += miss; e = cb.evaluate_task(repo, row, it)
        for g in ("file", "symbol", "line"):
            m[f"pack {g}.cov"] = e[g]["coverage"]
        m["pack line.prec"] = e["line"]["precision"]
        gl = gold_lines(row)
        hit = sum(len(gl.get(x["file"], set()) & set(range(x["start_line"], x["end_line"] + 1))) for x in it)
        m["_gold_lines"] = hit; m["_used"] = rec["pack"]["used_tokens"]
        out[rec["id"]] = {k: float(v) for k, v in m.items()}
    return out, unresolved


def ci(d, n=2000):
    rng = random.Random(0)
    bs = sorted(statistics.fmean(rng.choices(d, k=len(d))) for _ in range(n))
    return bs[int(0.025 * n)], bs[int(0.975 * n) - 1]


data = {}
for name, path in runs:
    data[name], unres = score(path)
    if unres:
        print(f"note: {name}: {unres} pack items had no span in the dump (scored as absent)", file=sys.stderr)
ids = sorted(set.intersection(*(set(d) for d in data.values())))
keys = [k for k in next(iter(data["baseline"].values())) if not k.startswith("_")]
print(f"ContextBench pinned instances scored: {len(ids)} of {len(pinned)}")
print("| variant | " + " | ".join(keys) + " | gold lines / 1k pack tok |")
print("| --- |" + " ---: |" * (len(keys) + 1))
for name, d in data.items():
    tok = sum(d[i]["_used"] for i in ids)
    print(f"| {name} | " + " | ".join(f"{statistics.fmean(d[i][k] for i in ids):.3f}" for k in keys)
          + f" | {1000 * sum(d[i]['_gold_lines'] for i in ids) / max(1, tok):.1f} |")
print(f"\npaired deltas vs baseline, 95% bootstrap CI (n={len(ids)}):")
for name, d in data.items():
    if name == "baseline":
        continue
    for k in ("sem@50 symbol.cov", "fused@10 symbol.cov", "fused@10 file.cov", "fused@5 file.cov", "pack line.cov"):
        diff = [d[i][k] - data["baseline"][i][k] for i in ids]
        lo, hi = ci(diff)
        print(f"  {name:18s} {k:22s} {statistics.fmean(diff):+.3f} [{lo:+.3f}, {hi:+.3f}]  "
              f"wins/losses {sum(x > 0 for x in diff)}/{sum(x < 0 for x in diff)}")
