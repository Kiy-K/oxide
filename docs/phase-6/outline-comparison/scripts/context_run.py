"""context_run.py {py|ts} SPLIT OUT: payload arms (stage 3) and navigation cases (stage 2).

Runs the frozen ContextBench harness (production retrieval, router, packer, scorer) on
the tasks of SPLIT. Every task is scored once per ARM.CONDITION on the same index:
  arms        A  production, unchanged
              A0 file entities get no view (the control for budget freed by B/C)
              B  a file entity's view is its ast-grep outline (outline.astgrep)
              C  a file entity's view is its native outline (outline.native)
  conditions  U  the task's own hints; H  plus its verified gold files as ``paths``
Candidates, routing, values, floor, item cap and order are the production ones in
every arm; only what a file entity shows changes. Outline items are packed under the
pseudo-path ``outline:<path>`` at lines (0, 0): they spend units, and the frozen scorer
cannot count them as gold source. Their navigational reach is reported apart, as
``pointer_recall`` (a verified span intersected by an entry of a packed outline).

Writes OUT/<arm>.<cond>.json (scorer rows, ``seconds`` = median build_context wall
time, ``pointer``), OUT/cases.json (navigation inputs, arm-independent) and
OUT/cost.json.
"""

from __future__ import annotations

import dataclasses
import os
import json
import resource
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path.cwd() / "tests/python"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import contextbench as cb
import outline

import oxide.api
from oxide.core import packing, retrieval
from oxide.models import ContextItem, ItemEntity, SourceRef

bench, split, out = sys.argv[1], sys.argv[2], Path(sys.argv[3])
ARMS = tuple(os.environ.get("ARMS", "A,A0,B,C").split(","))  # A alone before the freeze
CONDS = ("U", "H")
DISTRACTORS = 3  # non-gold candidate files per navigation case, in production order
state: dict = {"arm": "A", "paths": (), "bundles": {}}
cache: dict[tuple[str, str, str], tuple[list[outline.Entry] | None, str | None, str]] = {}
times: list[float] = []
orig_retrieve, orig_view, orig_item = retrieval.retrieve, packing._Packer.view, packing._Packer.item
orig_build, orig_score = oxide.api.Repository.build_context, cb.score


def outlined(view, arm: str, path: str):
    """(entries, failure, rendering) of one file under arm B or C, cached by digest."""
    info = view.files[path]
    key = (arm, path, info.digest)
    if key not in cache:
        data = view.source(SourceRef(path, 0, info.size, info.digest))
        entries, failure = outline.astgrep(data, path) if arm == "B" else outline.native(view, path)
        n = outline.Lines(data).count()
        cache[key] = (entries, failure, outline.render(path, n, entries))
    return cache[key]


def retrieve(view, query, hints, **kw):
    if state["paths"]:
        hints = dataclasses.replace(hints, paths=hints.paths + state["paths"])
    return orig_retrieve(view, query, hints, **kw)


def view(self, entity, header, reason):
    arm = state["arm"]
    if entity.category != "file" or arm == "A":
        return orig_view(self, entity, header, reason)
    if arm == "A0":
        return []
    entries, _, _ = outlined(state["view"], arm, entity.source.path)
    return [packing._Piece(entity, -1, -1, reason)] if entries else []


def item(self, path, span):
    if span[0].start >= 0:
        return orig_item(self, path, span)
    piece = span[0]
    _, _, text = outlined(state["view"], state["arm"], path)
    entity = ItemEntity(piece.entity.key, packing.display(piece.entity.key), False, piece.reason)
    ref = SourceRef(f"outline:{path}", 0, 0, piece.entity.source.digest)
    return ContextItem(ref, (0, 0), (entity,), packing.units(text), text)


def build_context(self, query, budget, **kw):
    started = time.perf_counter()
    try:
        bundle = orig_build(self, query, budget, **kw)
    finally:
        times.append(time.perf_counter() - started)
    state["bundles"][budget] = bundle
    return bundle


def flat(entries):
    for e in entries or ():
        yield e
        yield from e.members


def pointer(view, lines, task, arm: str) -> dict | None:
    """Per budget: the share of verified spans an entry of a packed outline intersects,
    and the median gold-line share of the smallest such entry (its tightness)."""
    if arm not in ("B", "C"):
        return None
    gold = [g for g in task["gold"] if g["verified"]]
    found: dict = {}
    for budget, bundle in sorted(state["bundles"].items()):
        packed = {i.source.path[8:] for i in bundle.items if i.source.path.startswith("outline:")}
        hits, tight = 0, []
        for g in gold:
            if g["file"] not in packed:
                continue
            code = lines.code(g["file"], g["start"], g["end"])
            entries, _, _ = outlined(view, arm, g["file"])
            spans = [
                e.last - e.first + 1
                for e in flat(entries)
                if code & set(range(e.first, e.last + 1))
            ]
            if spans:
                hits += 1
                tight.append(len(code) / min(spans))
        found[str(budget)] = {
            "pointer_recall": round(hits / len(gold), 6) if gold else None,
            "tightness": round(statistics.median(tight), 6) if tight else None,
        }
    return found


def nav_case(repo, view, lines, task) -> dict:
    gold = [g for g in task["gold"] if g["verified"]]
    gold_files = sorted({g["file"] for g in gold if g["file"] in view.files})
    found = repo.search(task["query"], symbols=task["symbols"])
    order = [e.entity.source.path for e in found.entries if e.entity.source is not None]
    others = [p for p in dict.fromkeys(order) if p not in gold_files][:DISTRACTORS]
    files = []
    for path in sorted([*gold_files, *others]):
        info = view.files[path]
        data = view.source(SourceRef(path, 0, info.size, info.digest))
        ls = outline.Lines(data)
        n = ls.count()
        head = f"### {path} ({n} lines)\n"
        parsed = info.coverage != "unsupported"
        texts = [ls.text(i) for i in range(1, n + 1)]
        record = {
            "path": path,
            "lines": n,
            "units": [packing.units(t + "\n") for t in texts],
            "gold": path in gold_files,
            "coverage": info.coverage,
            "overview": {
                "N": head,
                "A": head + (outline.header_view(view, path) if parsed else ""),
                # Agentless's baseline: the whole file, every line numbered
                "F": head + "".join(f"{i}: {t}\n" for i, t in enumerate(texts, 1)),
            },
            "failure": {},
            "entries": {},
        }
        for arm in ("B", "C"):
            entries, failure, text = outlined(view, arm, path)
            record["overview"][arm] = text
            record["failure"][arm] = failure
            record["entries"][arm] = [
                [e.role, e.kind, e.name, e.first, e.last] for e in flat(entries)
            ]
        files.append(record)
    spans = [
        {"file": g["file"], "lines": sorted(lines.code(g["file"], g["start"], g["end"]))}
        for g in gold
        if g["file"] in view.files
    ]
    return {
        "task": task["id"],
        "split": task["split"],
        "corpus": task["corpus"],
        "bench": bench,
        "query": task["query"],
        "files": files,
        "spans": spans,
        "dropped_spans": len(gold) - len(spans),
    }


results: dict[str, list] = {f"{a}.{c}": [] for a in ARMS for c in CONDS}
cases: list[dict] = []


def score(repo, lines, task):
    view = repo._view()
    state["view"] = view
    cases.append(nav_case(repo, view, lines, task))
    gold = tuple(sorted({g["file"] for g in task["gold"] if g["verified"]}))
    for arm in ARMS:
        for cond in CONDS:
            state.update(arm=arm, paths=gold if cond == "H" else (), bundles={})
            mark = len(times)
            try:
                row = orig_score(repo, lines, task)
            finally:
                state.update(arm="A", paths=())
            row["pointer"] = pointer(view, lines, task, arm)
            row["seconds"] = round(statistics.median(times[mark:]), 6)
            results[f"{arm}.{cond}"].append(row)
    return results[f"{ARMS[0]}.U"][-1]


retrieval.retrieve = retrieve
packing._Packer.view, packing._Packer.item = view, item
oxide.api.Repository.build_context, cb.score = build_context, score

outline.verify_tool()
if bench == "ts":
    import contextbench_ts as harness
else:
    harness = cb
tasks = harness.load_tasks()
chosen = [
    t
    for t in tasks["tasks"]
    if t["split"] == split and t["corpus"].startswith(os.environ.get("ONLY", ""))
]
used = {t["corpus"] for t in chosen}
tasks = {
    **tasks,
    "tasks": chosen,
    "corpora": {k: v for k, v in tasks["corpora"].items() if k in used},
}
data = Path(tempfile.mkdtemp(prefix=f"ctx-{bench}-", dir=outline.DATA))
try:
    evaluated = harness.evaluate(tasks, data)
finally:
    shutil.rmtree(data, ignore_errors=True)
out.mkdir(parents=True, exist_ok=True)
for name, rows in results.items():
    (out / f"{name}.json").write_text(json.dumps(rows, indent=1) + "\n")
(out / "cases.json").write_text(json.dumps(cases) + "\n")
cost = {k: v for k, v in evaluated.items() if k != "results"}
cost["max_rss_mb"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024
(out / "cost.json").write_text(json.dumps(cost, indent=1) + "\n")
