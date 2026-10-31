"""soundness.py REPO CLASSES.json OUT.json: Part B, does a layout/body edit stay in its file?

Applies sampled edits from classify.py's output to REPO's worktree capture in memory (the
file's bytes replaced by its before, then its after version), derives both with cached
facts, and counts what changed outside the edited file and in FTS documents. See
../README.md, Part B.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections import Counter
from dataclasses import replace
from pathlib import Path
from typing import Any

from classify import Blobs

from oxide.capture import capture, digest
from oxide.core.indexing import derive
from oxide.core.retrieval import document

sys.path.insert(0, str(Path(__file__).parents[2] / "indexing-throughput/scripts"))
from drift import owner, rows

QUOTA = {"layout": 30, "interface": 10}


def entity_owner(e: Any) -> str:
    return e.source.path if e.source is not None else e.key


def changed(before: Any, after: Any, edited: str) -> dict[str, Any]:
    ra, rb = Counter(rows(before)), Counter(rows(after))
    edges = {r: e for d in (before, after) for e, r in zip(d.edges, rows(d), strict=True)}
    diff_edges = (ra - rb) + (rb - ra)
    ea, eb = set(before.entities), set(after.entities)
    others = {owner(edges[r]) for r in diff_edges} | {entity_owner(e) for e in ea ^ eb}
    others.discard(edited)
    da = Counter((e.key, document(e)) for e in before.entities)
    db = Counter((e.key, document(e)) for e in after.entities)
    example = [repr(r)[:300] for r in diff_edges if owner(edges[r]) != edited][:3]
    return {
        "other_files": sorted(others)[:10],
        "other_count": len(others),
        "documents": sum(((da - db) + (db - da)).values()),
        "rows": sum(diff_edges.values()) + len(ea ^ eb),
        "example": example,
    }


def spread(items: list[Any], k: int) -> list[Any]:
    return items if len(items) <= k else [items[(i * len(items)) // k] for i in range(k)]


def main() -> None:
    repo, classes, out = sys.argv[1], json.loads(Path(sys.argv[2]).read_text()), sys.argv[3]
    snap = capture(repo)
    index = {f.path: i for i, f in enumerate(snap.files)}
    edits = [(c["commit"], f) for c in classes for f in c["files"] if f["path"] in index]
    # Refined layout/body edits include every preregistered one (refine only blanks facts).
    picks = spread([e for e in edits if e[1]["refined"] != "interface"], QUOTA["layout"])
    picks += spread([e for e in edits if e[1]["refined"] == "interface"], QUOTA["interface"])
    cached = derive(snap, "sound").facts
    blobs, results = Blobs(repo), []
    head = subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"], capture_output=True, text=True)
    for sha, f in picks:
        i = index[f["path"]]
        derived = []
        for spec in (f"{sha}^:{f['path']}", f"{sha}:{f['path']}"):
            data = blobs.get(spec)
            edited = replace(snap.files[i], data=data, digest=digest(data))
            files = (*snap.files[:i], edited, *snap.files[i + 1 :])
            d = derive(replace(snap, files=files), "sound", cached)
            cached = {lang: {**cached.get(lang, {}), **v} for lang, v in d.facts.items()}
            derived.append(d)
        results.append({"commit": sha, **f, **changed(*derived, f["path"])})
        print(json.dumps(results[-1]), flush=True)
    Path(out).write_text(json.dumps({"head": head.stdout.strip(), "edits": results}, indent=1))


if __name__ == "__main__":
    main()
