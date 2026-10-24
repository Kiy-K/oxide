"""Capsule v1 cost on real candidate pools (infrastructure only, no quality claim).

    uv run --with tiktoken python docs/phase-6/decision-seam/scripts/capsules.py OUT.json

Runs the Python ContextBench **dev** split (held-out stays sealed) with ``packing.pack``
patched to build, once per task, the capsules of the candidates a judge would be asked
about, and to time the CapsuleJudge round trip with an empty recorded adapter. Bundles
are unchanged (no judge reaches ``pack``). This script reads no gold.
"""

from __future__ import annotations

import json
import statistics
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "tests" / "python"))

import contextbench as cb
import tiktoken

from oxide.core import capsule, packing, retrieval

ENCODINGS = {name: tiktoken.get_encoding(name) for name in ("o200k_base", "cl100k_base")}
real_pack, real_retrieve = packing.pack, retrieval.retrieve
state: dict[str, Any] = {}
seen: set[tuple[str, str]] = set()
rows: list[dict[str, Any]] = []


def retrieve(view, query, hints, **kw):  # type: ignore[no-untyped-def]
    state["query"], state["derivation"] = query, view.snapshot.derivation_id
    return real_retrieve(view, query, hints, **kw)


def pack(pool, scopes, budget, source, declarations, judge=None):  # type: ignore[no-untyped-def]
    query, derivation = state["query"], state["derivation"]
    if (query, derivation) not in seen:
        seen.add((query, derivation))
        asked = [  # the candidates pack would ask a judge about
            c
            for c in pool
            if c.entity.source is not None
            and (c.entity.category == "symbol" or c.entity.key in declarations)
        ]
        start = time.perf_counter_ns()
        built = [capsule.build(query, derivation, c, source) for c in asked]
        build_ns = time.perf_counter_ns() - start
        empty = capsule.CapsuleJudge("empty", query, derivation, source, capsule.Recorded({}))
        start = time.perf_counter_ns()
        empty.judge(asked)
        judge_ns = time.perf_counter_ns() - start
        sent = [c for c in built if c is not None]
        rows.append(
            {
                "pool": len(pool),
                "asked": len(asked),
                "unsendable": len(asked) - len(sent),
                "build_ms": build_ns / 1e6,
                "judge_ms": judge_ns / 1e6,
                "bytes": [len(c.canonical) for c in sent],
                "tokens": {
                    n: [len(e.encode(c.canonical.decode())) for c in sent]
                    for n, e in ENCODINGS.items()
                },
                "truncated": [f for c in sent for f in c.truncated],
                "any_truncated": sum(1 for c in sent if c.truncated),
            }
        )
    return real_pack(pool, scopes, budget, source, declarations, judge)


def dist(values: list[float]) -> dict[str, float]:
    ordered = sorted(values)
    p95 = ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))]
    return {"median": statistics.median(ordered), "p95": p95, "max": ordered[-1]}


def main(out: Path) -> None:
    retrieval.retrieve, packing.pack = retrieve, pack
    tasks = cb.load_tasks()
    tasks["tasks"] = [t for t in tasks["tasks"] if t["split"] == "dev"]
    used = {t["corpus"] for t in tasks["tasks"]}
    tasks["corpora"] = {k: v for k, v in tasks["corpora"].items() if k in used}
    with tempfile.TemporaryDirectory(prefix="oxide-capsules-") as data:
        cb.evaluate(tasks, Path(data))
    capsules = [b for r in rows for b in r["bytes"]]
    summary = {
        "requests": len(rows),
        "capsules": len(capsules),
        "pool_per_request": dist([r["pool"] for r in rows]),
        "asked_per_request": dist([r["asked"] for r in rows]),
        "unsendable": sum(r["unsendable"] for r in rows),
        "capsules_with_truncation": sum(r["any_truncated"] for r in rows),
        "truncated_fields": dict(Counter(f for r in rows for f in r["truncated"]).most_common()),
        "build_ms_per_request": dist([r["build_ms"] for r in rows]),
        "judge_round_trip_ms_per_request": dist([r["judge_ms"] for r in rows]),
        "bytes_per_capsule": dist(capsules),
        "bytes_per_request": dist([sum(r["bytes"]) for r in rows]),
        **{f"{n}_per_capsule": dist([t for r in rows for t in r["tokens"][n]]) for n in ENCODINGS},
        **{f"{n}_per_request": dist([sum(r["tokens"][n]) for r in rows]) for n in ENCODINGS},
    }
    out.write_text(json.dumps(summary, indent=1) + "\n")
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main(Path(sys.argv[1]))
