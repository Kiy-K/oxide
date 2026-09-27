# Dump ContextBench human gold (init+add line spans per file) for the pinned 21 tasks.
import json, sys
from collections import defaultdict
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]  # repo checkout holding eval-agent/ (ContextBench evaluator)
sys.path.insert(0, str(ROOT / "scripts/agent_eval"))
import contextbench_run as cb
pinned = set((ROOT / "eval-agent/results/tier_a_instances.txt").read_text().split())
out = {}
for r in cb.load_tasks():
    if r["instance_id"] not in pinned: continue
    g = cb.Gold({"init_ctx": json.loads(r["gold_context"]) if isinstance(r["gold_context"], str) else r["gold_context"],
                 "repo_url": r["repo_url"], "commit": r["base_commit"]})
    lines = defaultdict(set)
    for it in g.init + g.add:
        if it.get("file"):
            lines[cb.normalize_gold_path(it["file"])].update(range(it.get("start_line", 1), it.get("end_line", 1) + 1))
    out[r["instance_id"]] = {"files": sorted(set(g.files())), "lines": {f: sorted(v) for f, v in lines.items()}}
json.dump(out, open(Path.home() / ".cache/oxide-alloc-eval/cb_gold.json", "w"))
print(len(out), sum(len(v["lines"]) for v in out.values()))
