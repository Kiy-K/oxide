"""End-to-end ``Repository.search`` latency and output digest (README § 3).

    uv run python search_ab.py CORPUS DATA_DIR REPS

Opens CORPUS's existing index read-only and runs query.py's 400 queries through
``repo.search`` REPS times. PYTHONPATH selects the code (HEAD or the working tree).
Prints the timings and a digest of every result's entries, scores and lexical status.
"""

from __future__ import annotations

import hashlib
import json
import statistics
import sys
import time
from pathlib import Path

import ladybug
from query import queries

import oxide
from oxide.storage.ladybug import fts_location


def main() -> None:
    corpus, data, reps = Path(sys.argv[1]), Path(sys.argv[2]), int(sys.argv[3])
    (store,) = (data / "repos").iterdir()
    graph = store / "generations" / (store / "CURRENT").read_text() / "graph.lbug"
    db = ladybug.Database(str(graph), read_only=True, max_num_threads=1)
    conn = ladybug.Connection(db)
    conn.execute(f"LOAD EXTENSION '{fts_location()}'")
    qs = queries(conn)
    conn.close()
    db.close()
    took: list[float] = []
    with oxide.open_repository(corpus, data_dir=data, create=False) as repo:
        for _ in range(reps):
            out = []
            for q in qs:
                began = time.perf_counter()
                found = repo.search(q)
                took.append(time.perf_counter() - began)
                entries = [(e.entity.key, e.lexical_rank, e.lexical_score) for e in found.entries]
                out.append((entries, found.lexical.state, found.lexical.truncated))
    ms = sorted(t * 1000 for t in took)
    print(
        json.dumps(
            {
                "corpus": corpus.name,
                "oxide": oxide.__file__,
                "median_ms": round(statistics.median(ms), 3),
                "p95_ms": round(ms[int(len(ms) * 0.95)], 3),
                "total_s": round(sum(took) / reps, 3),
                "digest": hashlib.sha256(repr(out).encode()).hexdigest()[:16],
            }
        )
    )


if __name__ == "__main__":
    main()
