"""Lexical query latency on one graph.lbug: today's statement vs ``top := k`` (README § 3).

    uv run python query.py GRAPH.lbug REPS

Queries: digest.py's 300 (single terms and 3-term mixes) plus 100 agent-like 12-term
queries drawn by term frequency (seed 0). Arm ``full`` is ``View.lexical``'s statement;
arm ``top`` adds ``top := limit + 1``; arm ``guarded`` reruns ``full`` when the limit-th and
(limit+1)-th scores tie (``top`` breaks ties at its cut arbitrarily); arm ``escalate``
first retries with ``top := 8 * (limit + 1)``; arm ``wide`` asks ``top := 10 * (limit + 1)``
at once, sorts those by key, and reruns ``full`` only when the score at rank ``limit``
does not beat the lowest score ``top`` returned. An arm is exact when its first ``limit``
rows and its "more matched" flag equal ``full``'s (what ``View.lexical`` returns); both
that count and raw list differences are reported. Also times open + LOAD EXTENSION.
"""

from __future__ import annotations

import json
import random
import statistics
import sys
import time
from pathlib import Path
from typing import Any

import ladybug

from oxide.core.retrieval import LEXICAL_LIMIT
from oxide.storage.ladybug import fts_location

BASE = (
    "CALL QUERY_FTS_INDEX('Entity', 'lexical', '{q}', conjunctive := false, K := 1.2, "
    "B := 0.75{top}) RETURN node.key, score ORDER BY score DESC, node.key LIMIT {n}"
)


def queries(conn: ladybug.Connection) -> list[str]:
    terms = [t for (t,) in conn.execute("MATCH (e:Entity) RETURN e.terms").get_all()]  # type: ignore[union-attr]
    words = sorted({w for t in terms for w in t.split() if w.isalnum()})
    step = max(len(words) // 250, 1)
    out = words[::step][:250] + [" ".join(words[i::997][:3]) for i in range(50)]
    tokens = [w for t in terms for w in t.split()]
    rng = random.Random(0)
    return out + [" ".join(rng.choices(tokens, k=12)) for _ in range(100)]


def run(conn: ladybug.Connection, qs: list[str], arm: str) -> tuple[list[float], list[Any]]:
    limit = LEXICAL_LIMIT
    n = limit + 1
    wide = 10 * n
    extra = {"full": "", "wide": f", top := {wide}"}.get(arm, f", top := {n}")
    took, hits = [], []
    for q in qs:
        began = time.perf_counter()
        cut = wide if arm == "wide" else n
        rows = conn.execute(BASE.format(q=q, top=extra, n=cut)).get_all()  # type: ignore[union-attr]
        tied = len(rows) == n and rows[limit][1] == rows[limit - 1][1]
        if arm == "wide":
            tied = len(rows) == wide and rows[-1][1] >= rows[limit - 1][1]
            rows = rows[:n]
        if arm == "escalate" and tied:
            m = 8 * n
            rows = conn.execute(BASE.format(q=q, top=f", top := {m}", n=m)).get_all()  # type: ignore[union-attr]
            tied = len(rows) == m and rows[m - 1][1] == rows[limit - 1][1]
        if arm in ("guarded", "escalate", "wide") and tied:
            rows = conn.execute(BASE.format(q=q, top="", n=n)).get_all()  # type: ignore[union-attr]
        hits.append(rows)
        took.append(time.perf_counter() - began)
    return took, hits


def visible(rows: list[Any]) -> tuple[list[Any], bool]:
    """What ``View.lexical`` returns from these rows: the first ``limit`` and "more"."""
    return rows[:LEXICAL_LIMIT], len(rows) > LEXICAL_LIMIT


def main() -> None:
    graph, reps = Path(sys.argv[1]), int(sys.argv[2])
    began = time.perf_counter()
    db = ladybug.Database(str(graph), read_only=True, max_num_threads=1)
    conn = ladybug.Connection(db)
    conn.execute(f"LOAD EXTENSION '{fts_location()}'")
    opened = time.perf_counter() - began
    qs = queries(conn)
    arms: dict[str, list[float]] = {a: [] for a in ("full", "top", "guarded", "escalate", "wide")}
    hits: dict[str, Any] = {}
    for _ in range(reps):
        for arm in arms:
            took, hits[arm] = run(conn, qs, arm)
            arms[arm].extend(took)
    conn.close()
    db.close()
    row: dict[str, Any] = {"graph": str(graph), "queries": len(qs), "open_s": round(opened, 4)}
    for arm, took in arms.items():
        ms = sorted(t * 1000 for t in took)
        row[arm] = {
            "median_ms": round(statistics.median(ms), 3),
            "p95_ms": round(ms[int(len(ms) * 0.95)], 3),
            "total_s": round(sum(took) / reps, 3),
            "differs": sum(a != b for a, b in zip(hits["full"], hits[arm], strict=True)),
            "visible_differs": sum(
                visible(a) != visible(b) for a, b in zip(hits["full"], hits[arm], strict=True)
            ),
        }
    print(json.dumps(row))


if __name__ == "__main__":
    main()
