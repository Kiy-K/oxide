"""Is ``CREATE_FTS_INDEX`` deterministic across database thread counts? (README § 9)

    uv run python fts_threads.py GRAPH.lbug SCRATCH REPS DB[:EXEC]...

Copies the generation's ``graph.lbug`` REPS times per setting, drops and rebuilds the
FTS index with exactly the statement ``Store._load`` runs, on a database of DB threads
whose connection runs it on EXEC threads (``set_max_threads_for_exec``; default DB), then
records the exact ``(key, score)`` lists of digest.py's 300 queries. Prints one JSON line
per build: the digest, the rebuild time, and how many queries differ from the first build
in ranking (keys) or in scores only.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
import time
from pathlib import Path
from typing import Any

import ladybug

from oxide.storage.ladybug import fts_location

QUERY = (
    "CALL QUERY_FTS_INDEX('Entity', 'lexical', '{}', conjunctive := false, K := 1.2, "
    "B := 0.75) RETURN node.key, score ORDER BY score DESC, node.key"
)


def build(source: Path, copy: Path, threads: int, exec_threads: int) -> tuple[list[Any], float]:
    for p in copy.parent.glob(copy.name + "*"):
        p.unlink()
    shutil.copyfile(source, copy)
    db = ladybug.Database(str(copy), max_num_threads=threads)
    conn = ladybug.Connection(db)
    conn.set_max_threads_for_exec(exec_threads)
    conn.execute(f"LOAD EXTENSION '{fts_location()}'")
    conn.execute("CALL DROP_FTS_INDEX('Entity', 'lexical')")
    began = time.perf_counter()
    conn.execute("CALL CREATE_FTS_INDEX('Entity', 'lexical', ['terms'], stemmer := 'porter')")
    took = time.perf_counter() - began
    terms = conn.execute("MATCH (e:Entity) RETURN e.terms").get_all()  # type: ignore[union-attr]
    words = sorted({w for (t,) in terms for w in t.split() if w.isalnum()})
    step = max(len(words) // 250, 1)
    queries = words[::step][:250] + [" ".join(words[i::997][:3]) for i in range(50)]
    hits = [conn.execute(QUERY.format(q)).get_all() for q in queries]  # type: ignore[union-attr]
    conn.close()
    db.close()
    return hits, took


def main() -> None:
    source, scratch, reps = Path(sys.argv[1]), Path(sys.argv[2]), int(sys.argv[3])
    for setting in sys.argv[4:]:
        threads, _, exec_threads = setting.partition(":")
        first: list[Any] | None = None
        for rep in range(reps):
            hits, took = build(
                source, scratch / "probe.lbug", int(threads), int(exec_threads or threads)
            )
            first = first or hits
            keys = sum(
                [k for k, _ in a] != [k for k, _ in b] for a, b in zip(first, hits, strict=True)
            )
            scores = sum(a != b for a, b in zip(first, hits, strict=True)) - keys
            sha = hashlib.sha256(repr(hits).encode()).hexdigest()[:16]
            row = {"threads": setting, "rep": rep, "digest": sha, "fts_s": round(took, 3)}
            print(json.dumps(row | {"ranking_differs": keys, "score_only_differs": scores}))
            sys.stdout.flush()


if __name__ == "__main__":
    main()
