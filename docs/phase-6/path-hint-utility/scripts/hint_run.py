"""hint_run.py {py|ts} OUT [SPLIT [ARMS]]: preregistered path-hint arms on one split.

Runs the frozen harness (production retrieval, router, packer, scorer) on the tasks
of SPLIT only (default dev; held-out only for the one confirmation run).
Each arm is VARIANT.CONDITION, scored on the same index:
  variants   prod (production), tier (hinted-file candidates packed first),
             outline (tier, plus the hinted file's module-level view as its header)
  conditions U (no paths), H (verified gold files as paths), N (H plus the first
             non-gold file in production lexical order)
Writes OUT/ARM/results.json (frozen scorer rows plus `seconds`, the median
build_context wall time) and OUT/cost.json.
"""

import dataclasses
import json
import resource
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path.cwd() / "tests/python"))
import contextbench as cb

import oxide.api
from oxide.core import packing, retrieval

bench, out = sys.argv[1], Path(sys.argv[2])
split = sys.argv[3] if len(sys.argv) > 3 else "dev"
ARMS = (
    sys.argv[4] if len(sys.argv) > 4 else "prod.U,prod.H,tier.H,outline.H,prod.N,tier.N,outline.N"
).split(",")
state: dict = {"variant": "prod", "paths": ()}
orig_retrieve, orig_value, orig_view = retrieval.retrieve, packing.value, packing._Packer.view
orig_build, orig_score = oxide.api.Repository.build_context, cb.score
times: list[float] = []


def retrieve(view, query, hints, **kw):
    if state["paths"]:
        hints = dataclasses.replace(hints, paths=hints.paths + state["paths"])
    return orig_retrieve(view, query, hints, **kw)


def hinted(entity) -> bool:
    return (
        state["variant"] != "prod"
        and entity.source is not None
        and (entity.source.path in state["paths"])
    )


def value(c):
    score, basis = orig_value(c)
    if hinted(c.entity) and score >= packing.INCLUDE_FLOOR:
        return score + 1.0, f"hinted file, {basis}"  # a precedence tier above every value
    return score, basis


def view(self, entity, header, reason):
    if state["variant"] == "outline" and entity.category == "file" and hinted(entity):
        return orig_view(self, entity, True, reason) or orig_view(self, entity, False, reason)
    return orig_view(self, entity, header, reason)


def build_context(self, *a, **kw):
    started = time.perf_counter()
    try:
        return orig_build(self, *a, **kw)
    finally:
        times.append(time.perf_counter() - started)


results: dict[str, list] = {a: [] for a in ARMS}


def score(repo, lines, task):
    gold = tuple(sorted({g["file"] for g in task["gold"] if g["verified"]}))
    found = repo.search(task["query"], symbols=task["symbols"])
    ranked = [e.entity.source.path for e in found.entries if e.lexical_rank and e.entity.source]
    wrong = next((p for p in ranked if p not in gold), None)
    for arm in ARMS:
        variant, condition = arm.split(".")
        paths = {"U": (), "H": gold, "N": gold + ((wrong,) if wrong else ())}[condition]
        state.update(variant=variant, paths=paths)
        times.clear()
        try:
            row = orig_score(repo, lines, task)
        finally:
            state.update(variant="prod", paths=())
        row["seconds"] = statistics.median(times)
        row["hint_paths"] = list(paths)
        results[arm].append(row)
    return results[ARMS[0]][-1]


retrieval.retrieve, packing.value, packing._Packer.view = retrieve, value, view
oxide.api.Repository.build_context, cb.score = build_context, score

harness = __import__("contextbench_ts") if bench == "ts" else cb
tasks = harness.load_tasks()
dev = [t for t in tasks["tasks"] if t["split"] == split]
used = {t["corpus"] for t in dev}
tasks = {**tasks, "tasks": dev, "corpora": {k: v for k, v in tasks["corpora"].items() if k in used}}
data = Path(tempfile.mkdtemp(prefix=f"hint-{bench}-"))
try:
    evaluated = harness.evaluate(tasks, data)
finally:
    shutil.rmtree(data, ignore_errors=True)
for arm, rows in results.items():
    (out / arm).mkdir(parents=True, exist_ok=True)
    (out / arm / "results.json").write_text(json.dumps(rows, indent=1) + "\n")
cost = {k: v for k, v in evaluated.items() if k != "results"}
cost["max_rss_mb"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024
(out / "cost.json").write_text(json.dumps(cost, indent=1) + "\n")
