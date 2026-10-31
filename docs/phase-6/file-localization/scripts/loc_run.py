"""loc_run.py {py|ts} OUT [ARMS]: preregistered localization arms on a dev split.

Runs the frozen harness (production retrieval, router, packer, scorer) on dev tasks
only, scoring every arm in ARMS (comma-separated; default: the stage-1 oracles) on the
same index. Arms change only what retrieval returns, in this process:
  none      production
  file      lexical hits restricted to the task's verified gold files G
  seed      G as `paths` hints, production lexical
  dir       lexical restricted to files in a parent directory of a file of G
  file@K    lexical restricted to G plus the first K non-gold files in P0 order
  loc:PN    lexical restricted to signal PN's top 10 files (no oracle)
  signals   no scoring; records each signal's file ranking (stage 2; P5 added by
            ../../change-classification, Part D)
Writes OUT/ARM/results.json, OUT/signals.json (when requested) and OUT/cost.json.
"""

import dataclasses
import json
import math
import posixpath
import resource
import shutil
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path.cwd() / "tests/python"))
import contextbench as cb

from oxide.core import retrieval
from oxide.storage import ladybug

bench, out = sys.argv[1], Path(sys.argv[2])
ARMS = (sys.argv[3] if len(sys.argv) > 3 else "none,file,seed,dir,file@3,file@10").split(",")
SIGNALS = ("P0", "P1", "P2", "P3", "P4", "P5")
state: dict = {"allowed": None, "paths": ()}
original_lexical, original_retrieve, original_score = (
    ladybug.View.lexical,
    retrieval.retrieve,
    cb.score,
)
_full: dict = {}  # the last query's full hit list
_corpus: dict = {}  # per snapshot: path BM25 statistics and the file import graph


def path_of(key: str) -> str | None:
    if key.startswith("file:"):
        return key[5:]
    if key.startswith("symbol:"):
        return key[7:].rsplit("#", 1)[0]
    return None


def full(view, text):
    k = (view.snapshot.snapshot_id, text)
    if k not in _full:
        _full.clear()
        _full[k] = original_lexical(view, text, 10**9)[0]
    return _full[k]


def lexical(self, text, limit):
    if state["allowed"] is None:
        return original_lexical(self, text, limit)
    hits = [h for h in full(self, text) if path_of(h[0]) in state["allowed"]]
    return hits[:limit], len(hits) > limit


def retrieve(view, query, hints, **kw):
    if state["paths"]:
        hints = dataclasses.replace(hints, paths=hints.paths + state["paths"])
    return original_retrieve(view, query, hints, **kw)


def corpus(view):
    sid = view.snapshot.snapshot_id
    if sid not in _corpus:
        docs = {p: Counter(retrieval.terms(p)) for p in view.files}
        df = Counter(t for d in docs.values() for t in d)
        rows = view._query(
            "MATCH (a:Entity)-[r:Rel]->(b:Entity) WHERE r.kind = 'imports' RETURN a.path, b.path"
        )
        near = defaultdict(set)
        for a, b in rows:
            if a and b and a != b:
                near[a].add(b)
                near[b].add(a)
        avg = sum(sum(d.values()) for d in docs.values()) / max(1, len(docs))
        _corpus.clear()
        _corpus[sid] = (docs, df, avg, near)
    return _corpus[sid]


def bm25(docs: dict[str, Counter], query: set[str]) -> dict[str, float]:
    df = Counter(t for d in docs.values() for t in d)
    n, avg = len(docs), sum(sum(d.values()) for d in docs.values()) / max(1, len(docs))
    out = {}
    for key, d in docs.items():
        norm = 1.2 * (0.25 + 0.75 * sum(d.values()) / avg)
        out[key] = sum(
            math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5)) * d[t] * 2.2 / (d[t] + norm)
            for t in query
            if t in d
        )
    return out


def ranked(scores: dict[str, float]) -> list[str]:
    return [p for p, s in sorted(scores.items(), key=lambda x: (-x[1], x[0])) if s > 0]


def signals(view, text) -> dict[str, list[str]]:
    p0: list[str] = []
    p1: dict[str, float] = defaultdict(float)
    for key, score in full(view, text):
        path = path_of(key)
        if path is None:
            continue
        if path not in p1:
            p0.append(path)
        p1[path] += score
    docs, df, avg, near = corpus(view)
    n, query = len(docs), set(text.split())  # each distinct query term counts once
    p2 = {}
    for path, d in docs.items():
        norm = 1.2 * (0.25 + 0.75 * sum(d.values()) / avg)
        p2[path] = sum(
            math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5)) * d[t] * 2.2 / (d[t] + norm)
            for t in query
            if t in d
        )
    p3 = {
        f: p1.get(f, 0.0) + 0.5 * max((p1.get(g, 0.0) for g in near[f]), default=0.0) for f in docs
    }
    by_dir: dict[str, float] = defaultdict(float)
    for f, s in p1.items():
        by_dir[posixpath.dirname(f)] += s
    p4 = sorted(p1, key=lambda f: (-by_dir[posixpath.dirname(f)], -p1[f], f))
    dirs: dict[str, Counter] = defaultdict(Counter)  # P5: directory path + file names
    for f in docs:
        d = posixpath.dirname(f)
        if d not in dirs:
            dirs[d].update(retrieval.terms(d))
        dirs[d].update(retrieval.terms(posixpath.basename(f)))
    by = bm25(dirs, query)
    p5 = sorted(
        (f for f in docs if by[posixpath.dirname(f)] > 0 or p2[f] > 0),
        key=lambda f: (-by[posixpath.dirname(f)], -p2[f], f),
    )
    return {"P0": p0, "P1": ranked(p1), "P2": ranked(p2), "P3": ranked(p3), "P4": p4, "P5": p5}


results: dict[str, list] = {a: [] for a in ARMS if a != "signals"}
found_signals: list = []


def score(repo, lines, task):
    view = repo._view()
    gold = sorted({g["file"] for g in task["gold"] if g["verified"]})
    text = " ".join(retrieval.terms(task["query"]))
    ranks = signals(view, text)
    if "signals" in ARMS:
        found_signals.append(
            {
                "task": task["id"],
                "corpus": task["corpus"],
                "gold": gold,
                "rank": {
                    s: {g: (ranks[s].index(g) + 1 if g in ranks[s] else None) for g in gold}
                    for s in SIGNALS
                },
            }
        )
    for arm in results:
        allowed, paths = None, ()
        if arm in ("file", "dir") or arm.startswith("file@"):
            allowed = set(gold)
            if arm == "dir":
                dirs = {posixpath.dirname(g) for g in gold}
                allowed = {p for p in view.files if posixpath.dirname(p) in dirs}
            elif arm.startswith("file@"):
                allowed |= set([p for p in ranks["P0"] if p not in allowed][: int(arm[5:])])
        elif arm == "seed":
            paths = tuple(gold)
        elif arm.startswith("loc:"):
            allowed = set(ranks[arm[4:]][:10])
        state.update(allowed=allowed, paths=paths)
        try:
            results[arm].append(original_score(repo, lines, task))
        finally:
            state.update(allowed=None, paths=())
    # The harness keeps (and, for ts, attributes) the production result.
    return results["none"][-1] if "none" in results else original_score(repo, lines, task)


ladybug.View.lexical, retrieval.retrieve, cb.score = lexical, retrieve, score

if bench == "ts":
    import contextbench_ts as harness
else:
    harness = cb
tasks = harness.load_tasks()
dev = [t for t in tasks["tasks"] if t["split"] == "dev"]
used = {t["corpus"] for t in dev}
tasks = {**tasks, "tasks": dev, "corpora": {k: v for k, v in tasks["corpora"].items() if k in used}}
data = Path(tempfile.mkdtemp(prefix=f"loc-{bench}-"))
try:
    evaluated = harness.evaluate(tasks, data)
finally:
    shutil.rmtree(data, ignore_errors=True)
for arm, rows in results.items():
    (out / arm).mkdir(parents=True, exist_ok=True)
    (out / arm / "results.json").write_text(json.dumps(rows, indent=1) + "\n")
if found_signals:
    out.mkdir(parents=True, exist_ok=True)
    (out / "signals.json").write_text(json.dumps(found_signals, indent=1) + "\n")
cost = {k: v for k, v in evaluated.items() if k != "results"}
cost["max_rss_mb"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024
(out / "cost.json").write_text(json.dumps(cost, indent=1) + "\n")
