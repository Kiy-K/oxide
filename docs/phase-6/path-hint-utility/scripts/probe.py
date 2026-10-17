"""probe.py {py|ts}: where a gold-path-hinted bundle spends its budget (dev only)."""

import collections
import re
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path.cwd() / "tests/python"))
import contextbench as cb

bench = sys.argv[1]
spend: collections.Counter = collections.Counter()
items_by: collections.Counter = collections.Counter()


def score(repo, lines, task):
    gold = sorted({g["file"] for g in task["gold"] if g["verified"]})
    b = repo.build_context(task["query"], 4096, symbols=task["symbols"], paths=gold)
    for i in b.items:
        r = i.entities[0].reason
        kind = "ancestor header" if r.startswith("header of") else re.sub(r"[\d.]+", "N", r.split(",")[-1].strip())
        where = "goldfile" if i.source.path in gold else "other"
        spend[(where, kind)] += i.units
        items_by[(where, kind)] += 1
    om = collections.Counter(
        o.reason for o in b.omissions if o.key.split(":", 1)[-1].split("#")[0] in gold
    )
    print(task["id"], b.used, len(b.items), dict(om), flush=True)
    return original(repo, lines, task)


original = cb.score
cb.score = score
harness = __import__("contextbench_ts") if bench == "ts" else cb
tasks = harness.load_tasks()
dev = [t for t in tasks["tasks"] if t["split"] == "dev"]
used = {t["corpus"] for t in dev}
tasks = {**tasks, "tasks": dev, "corpora": {k: v for k, v in tasks["corpora"].items() if k in used}}
data = Path(tempfile.mkdtemp(prefix=f"hint-{bench}-"))
try:
    harness.evaluate(tasks, data)
finally:
    shutil.rmtree(data, ignore_errors=True)
total = sum(spend.values())
for k, v in spend.most_common():
    print(f"{k}: {v} units ({v / total:.0%}), {items_by[k]} items")
