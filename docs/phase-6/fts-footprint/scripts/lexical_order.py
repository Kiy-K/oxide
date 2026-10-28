"""Tie order by node offset vs by key, on two builds of the same rows (README § 4).

    uv run python lexical_order.py BASE.lbug SORTED.lbug REPS

BASE is built in the product's row order and queried with ``View.lexical``'s statement
(ties by ``node.key``). SORTED has Entity rows in key order and is queried with ties by
``offset(id(node))``, fetching keys only for the returned rows. query.py's 400 queries,
on both. Prints the per-arm timings and how many ``(key, score)`` lists differ.
"""

from __future__ import annotations

import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any

import ladybug
from query import queries

from oxide.core.retrieval import LEXICAL_LIMIT
from oxide.storage.ladybug import fts_location

CALL = (
    "CALL QUERY_FTS_INDEX('Entity', 'lexical', '{q}', conjunctive := false, K := 1.2, B := 0.75) "
)
BY_KEY = CALL + "RETURN node.key, score ORDER BY score DESC, node.key LIMIT {n}"
BY_OFFSET = (
    CALL + "WITH node, score ORDER BY score DESC, offset(id(node)) LIMIT {n} RETURN node.key, score"
)


def connect(path: Path) -> tuple[ladybug.Database, ladybug.Connection]:
    db = ladybug.Database(str(path), read_only=True, max_num_threads=1)
    conn = ladybug.Connection(db)
    conn.execute(f"LOAD EXTENSION '{fts_location()}'")
    return db, conn


def run(conn: ladybug.Connection, qs: list[str], statement: str) -> tuple[list[float], list[Any]]:
    took, hits = [], []
    for q in qs:
        began = time.perf_counter()
        hits.append(conn.execute(statement.format(q=q, n=LEXICAL_LIMIT + 1)).get_all())  # type: ignore[union-attr]
        took.append(time.perf_counter() - began)
    return took, hits


def main() -> None:
    base, ordered, reps = Path(sys.argv[1]), Path(sys.argv[2]), int(sys.argv[3])
    arms = {"key": (connect(base), BY_KEY), "offset": (connect(ordered), BY_OFFSET)}
    qs = queries(arms["key"][0][1])
    times: dict[str, list[float]] = {arm: [] for arm in arms}
    hits: dict[str, Any] = {}
    for _ in range(reps):
        for arm, ((_, conn), statement) in arms.items():
            took, hits[arm] = run(conn, qs, statement)
            times[arm].extend(took)
    row: dict[str, Any] = {"queries": len(qs)}
    for arm, took in times.items():
        ms = sorted(t * 1000 for t in took)
        row[arm] = {
            "median_ms": round(statistics.median(ms), 3),
            "p95_ms": round(ms[int(len(ms) * 0.95)], 3),
            "total_s": round(sum(took) / reps, 3),
        }
    row["differs"] = sum(a != b for a, b in zip(hits["key"], hits["offset"], strict=True))
    print(json.dumps(row))
    for (db, conn), _ in arms.values():
        conn.close()
        db.close()


if __name__ == "__main__":
    main()
