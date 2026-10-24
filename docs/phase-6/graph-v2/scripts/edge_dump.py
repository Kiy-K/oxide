"""Every Python calls/references edge with resolution, basis and target range, plus index cost.

    uv run python docs/phase-6/graph-v2/scripts/edge_dump.py OUT.json REPO...

Run once per version (the HEAD worktree for v4, the working tree for v5). Each repository
is indexed in a fresh child process so peak RSS is per repository.
"""

import json
import os
import resource
import subprocess
import sys
import tempfile
import time

Q = (
    "MATCH (a:Entity)-[r:Rel]->(b:Entity) WHERE r.kind IN ['calls','references'] "
    "AND r.ev_path ENDS WITH '.py' "
    "RETURN a.key, r.kind, b.key, b.path, b.start_byte, b.end_byte, "
    "r.ev_path, r.ev_start, r.ev_end, r.resolution, r.basis"
)


def one(path: str) -> dict[str, object]:
    import oxide

    with (
        tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as d,
        oxide.open_repository(path, data_dir=d) as repo,
    ):
        start = time.perf_counter()
        repo.index()
        seconds = time.perf_counter() - start
        rows = sorted({tuple(x) for x in repo._view()._query(Q)})
    return {
        "corpus": os.path.basename(path),
        "oxide": oxide.__file__,
        "index_seconds": round(seconds, 2),
        "peak_rss_mb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024,
        "edges": rows,
    }


if __name__ == "__main__":
    if sys.argv[1] == "--one":
        print(json.dumps(one(sys.argv[2])))
        sys.exit()
    out = []
    for repo in sys.argv[2:]:
        done = subprocess.run(
            [sys.executable, __file__, "--one", repo], check=True, capture_output=True, text=True
        )
        out.append(json.loads(done.stdout))
        last = out[-1]
        print(
            last["corpus"],
            last["index_seconds"],
            last["peak_rss_mb"],
            len(last["edges"]),
            file=sys.stderr,
            flush=True,
        )
    with open(sys.argv[1], "w") as f:
        json.dump(out, f)
