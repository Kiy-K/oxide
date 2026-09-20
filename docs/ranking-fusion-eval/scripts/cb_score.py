#!/usr/bin/env python3
"""Score fusion variants on the ContextBench pinned instances with
ContextBench's own metric code (via scripts/agent_eval/contextbench_run.py
.evaluate_task): identical candidate pools from fusion_dump, top-10 of each
variant as the prediction (file + line span), file/symbol/span/line
granularity metrics as ContextBench defines them.

usage: eval-agent/.venv/bin/python cb_score.py cb-tasks.jsonl cb-dump.jsonl weights.json
"""
import json, statistics, sys
from collections import defaultdict
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3] if (Path(__file__).resolve().parents[3] / "Cargo.toml").exists() else Path(__import__("os").environ.get("OXIDE_ROOT", "."))
sys.path.insert(0, str(ROOT / "scripts/agent_eval")); sys.path.insert(0, str(Path(__file__).resolve().parent))
import contextbench_run as cb  # noqa: E402
import rerank_eval as rr  # noqa: E402

tasks = {t["id"]: t for t in map(json.loads, open(sys.argv[1]))}
dump = [json.loads(l) for l in open(sys.argv[2])]
weights = json.load(open(sys.argv[3]))
pinned = set((ROOT / "eval-agent/results/tier_a_instances.txt").read_text().split())
rows = {r["instance_id"]: r for r in cb.load_tasks() if r["instance_id"] in pinned}

def items_for(rec, ranked, k=10):
    out = []
    for sid in ranked[:k]:
        span = rec["spans"].get(sid)
        if not span: continue
        out.append({"file": sid.split("#")[0], "start_line": span[0], "end_line": span[1]})
    return out

acc = defaultdict(lambda: defaultdict(list))
for rec in dump:
    t = tasks[rec["id"]]; row = rows[rec["id"]]; repo_dir = Path(t["path"])
    rr.IDS.clear(); rr.IDS.update({k: int(v) for k, v in rec.get("ids", {}).items()})
    lex = [(s, sc) for s, sc in rec["lexical"] if s]; sem = [(s, sc) for s, sc in rec["semantic"] if s]
    k60 = rr.order(rr.rrf(lex, sem, 60)); k10 = rr.order(rr.rrf(lex, sem, 10))
    V = {"RRF K=60 (production)": k60, "RRF K=10": k10,
         "K=60 + rerank": rr.apply(weights, rec, k60), "K=10 + rerank": rr.apply(weights, rec, k10),
         "K=10 + rerank (no struct)": rr.apply(weights, rec, k10, drop=("struct_strong", "struct_uses")),
         "lexical only": [s for s, _ in lex], "semantic only": [s for s, _ in sem]}
    for name, ranked in V.items():
        m = cb.evaluate_task(repo_dir, row, items_for(rec, ranked))
        for gran in ("file", "symbol", "span", "line"):
            for k in ("coverage", "precision"):
                acc[name][f"{gran}.{k}"].append(float(m[gran][k]))
        # top-5 file recall as a second cut (the 10-item prediction is the pack-sized one)
        m5 = cb.evaluate_task(repo_dir, row, items_for(rec, ranked, k=5))
        acc[name]["file.coverage@5"].append(float(m5["file"]["coverage"]))
        acc[name]["symbol.coverage@5"].append(float(m5["symbol"]["coverage"]))
keys = sorted({k for v in acc.values() for k in v})
print(f"ContextBench pinned instances scored: {len(dump)}")
print("| variant | " + " | ".join(keys) + " |"); print("| --- |" + " ---: |" * len(keys))
for name, m in acc.items():
    print(f"| {name} | " + " | ".join(f"{statistics.fmean(m[k]):.3f}" if m.get(k) else "-" for k in keys) + " |")
base = acc["RRF K=60 (production)"]
print("\npaired deltas vs K=60 with 95% bootstrap CI (n=21):")
for name, m in acc.items():
    if name == "RRF K=60 (production)": continue
    for k in ("file.coverage", "symbol.coverage", "file.coverage@5"):
        d = [a - b for a, b in zip(m[k], base[k])]
        lo, hi = rr.boot_ci(d)
        print(f"  {name:28s} {k:18s} {statistics.fmean(d):+.3f} [{lo:+.3f}, {hi:+.3f}]  wins/losses {sum(1 for x in d if x>0)}/{sum(1 for x in d if x<0)}")
