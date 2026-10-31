"""body_run.py {py|ts} {none|full|own} OUT: one preregistered arm on a dev split.

Runs the frozen harness (production retrieval, router, packer, scorer) on dev tasks
only, with the FTS `terms` column of every entity replaced in this process:
  none  production document(e)
  full  document(e) + terms of the symbol's whole source text (symbols only)
  own   document(e) + terms of the entity's own text: its bytes minus every nested
        symbol's (symbols and files)
Writes results.json, metrics.json (the harness aggregate), cost.json and probe.json
(per verified span: gold rank and gold-file rank without the top-20 cut; per task: the
top-1000 lexical keys) to OUT.
"""

import json
import resource
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path.cwd() / "tests/python"))
import contextbench as cb

from oxide import api
from oxide.core import retrieval
from oxide.storage import ladybug

bench, arm, out = sys.argv[1], sys.argv[2], Path(sys.argv[3])
assert arm in ("none", "full", "own"), arm
cost: dict = {"index": [], "lexical_s": [], "score_s": {}}
_capture = None
_production = ladybug.document


def bodies(capture, entities) -> dict[str, str]:
    data = {f.path: f.data for f in capture.files}
    by_path: dict[str, list] = {}
    for e in entities:
        if e.source is not None and e.category in ("symbol", "file"):
            by_path.setdefault(e.source.path, []).append(e)
    found: dict[str, str] = {}
    for path, group in by_path.items():
        raw = data[path]
        symbols = [(e.source.start, e.source.end) for e in group if e.category == "symbol"]
        for e in group:
            s, t = e.source.start, e.source.end
            if arm == "full":
                if e.category != "symbol":
                    continue
                chunks = [raw[s:t]]
            else:  # own: drop every symbol strictly inside this entity
                inner = sorted((a, b) for a, b in symbols if s <= a and b <= t and (a, b) != (s, t))
                chunks, at = [], s
                for a, b in inner:
                    if a > at:
                        chunks.append(raw[at:a])
                    at = max(at, b)
                chunks.append(raw[at:t])
            text = b"\n".join(chunks).decode("utf-8", "replace")
            found[e.key] = " ".join(retrieval.terms(text))
    return found


original_publish, original_load = ladybug.Store.publish, ladybug.Store._load


def publish(self, capture, derived, components, lexical_unavailable):
    global _capture
    _capture = capture
    return original_publish(self, capture, derived, components, lexical_unavailable)


def load(self, staging, derived, lexical):
    began = time.perf_counter()
    body = {} if arm == "none" else bodies(_capture, derived.entities)
    ladybug.document = lambda e: " ".join(t for t in (_production(e), body.get(e.key)) if t)
    try:
        rows = original_load(self, staging, derived, lexical)
    finally:
        ladybug.document = _production
    size = sum(p.stat().st_size for p in staging.rglob("*") if p.is_file())
    cost["index"].append(
        {
            "root": str(_capture.root),
            "load_s": round(time.perf_counter() - began, 3),
            "store_bytes": size,
            "body_terms_chars": sum(len(v) for v in body.values()),
        }
    )
    return rows


original_lexical = ladybug.View.lexical


def lexical(self, text, limit):
    began = time.perf_counter()
    try:
        return original_lexical(self, text, limit)
    finally:
        cost["lexical_s"].append(time.perf_counter() - began)


original_index = api.Repository.index


def index(self, *args, **kwargs):
    began = time.perf_counter()
    report = original_index(self, *args, **kwargs)
    cost["index"][-1]["index_s"] = round(time.perf_counter() - began, 3)
    return report


probe: dict = {"spans": [], "tasks": {}}
_spans: dict = {}  # the open view's symbol and file spans
original_score = cb.score


def score(repo, lines, task):
    began = time.perf_counter()
    result = original_score(repo, lines, task)
    cost["score_s"][task["id"]] = round(time.perf_counter() - began, 3)
    view = repo._view()
    if id(view) not in _spans:
        keys = [r[0] for r in view._query("MATCH (e:Entity) RETURN e.key")]
        _spans.clear()
        _spans[id(view)] = {
            k: (e.category, lines.span(e.source))
            for k, e in view.entities(keys).items()
            if e.source is not None and e.category in ("symbol", "file")
        }
    spans = _spans[id(view)]
    hits, _ = original_lexical(view, " ".join(retrieval.terms(task["query"])), 1_000_000)
    rank = {k: i for i, (k, _) in enumerate(hits, 1)}
    file_rank: dict[str, int] = {}
    for k, _ in hits:
        if k in spans:
            file_rank.setdefault(spans[k][1][0], len(file_rank) + 1)
    probe["tasks"][task["id"]] = {"hits": len(hits), "top": [k for k, _ in hits[:1000]]}
    for g in result["gold"]:
        if not g["verified"]:
            continue
        path, (s, e) = g["file"], g["lines"]
        code = lines.code(path, s, e)
        holders = [
            k
            for k, (c, sp) in spans.items()
            if c == "symbol" and sp[0] == path and any(sp[1] <= n <= sp[2] for n in code)
        ]
        if g["module_lines"]:
            holders.append(f"file:{path}")
        probe["spans"].append(
            {
                "task": task["id"],
                "corpus": task["corpus"],
                "file": path,
                "lines": [s, e],
                "rank": min((rank[k] for k in holders if k in rank), default=None),
                "file_rank": file_rank.get(path),
                "holders": holders,
            }
        )
    return result


ladybug.Store.publish, ladybug.Store._load = publish, load
ladybug.View.lexical, api.Repository.index, cb.score = lexical, index, score

if bench == "ts":
    import contextbench_ts as harness
else:
    harness = cb
tasks = harness.load_tasks()
dev = [t for t in tasks["tasks"] if t["split"] == "dev"]
used = {t["corpus"] for t in dev}
tasks = {**tasks, "tasks": dev, "corpora": {k: v for k, v in tasks["corpora"].items() if k in used}}
data = Path(tempfile.mkdtemp(prefix=f"body-{bench}-{arm}-"))
try:
    evaluated = harness.evaluate(tasks, data)
finally:
    shutil.rmtree(data, ignore_errors=True)
lat = sorted(cost["lexical_s"])
cost["lexical_s"] = {
    "calls": len(lat),
    "median_ms": round(1000 * lat[len(lat) // 2], 2),
    "max_ms": round(1000 * lat[-1], 2),
}
cost["max_rss_mb"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024
cost["harness"] = {k: v for k, v in evaluated.items() if k != "results"}
out.mkdir(parents=True, exist_ok=True)
for name, value in (
    ("results.json", evaluated["results"]),
    ("metrics.json", harness.aggregate(evaluated["results"])),
    ("cost.json", cost),
    ("probe.json", probe),
):
    (out / name).write_text(json.dumps(value, indent=1, ensure_ascii=False) + "\n")
