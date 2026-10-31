"""Parent-precedence order arm (preregistered in docs/phase-6/graph-v3/preregistration.md).

    uv run python docs/phase-6/graph-v3/scripts/order_arm.py {py|ts} {dev|heldout} {B|R} OUT

B: production (must reproduce the frozen compat hashes). R: non-entry pool candidates are
ordered by (depth, p, key), ``p`` the best position in ``found.entries`` of a region the
candidate was reached from. Patched in-process; src/ is unchanged.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "tests" / "python"))

import contextbench as cb

from oxide.core import packing, retrieval, routing

real_pack, real_route, real_retrieve = packing.pack, routing.route, retrieval.retrieve
last: dict[str, Any] = {}


def parent(origin: str) -> str:
    """The region an origin names: ``calls:in:<key>`` / ``child:<key>`` -> ``<key>``."""
    parts = origin.split(":")
    cross = len(parts) > 2 and parts[1] in ("in", "out")
    return origin.split(":", 2)[-1] if cross else origin.split(":", 1)[-1]


def retrieve(view, query, hints, **kw):  # type: ignore[no-untyped-def]
    last["entries"] = out = real_retrieve(view, query, hints, **kw)
    return out


def route(view, entry_keys):  # type: ignore[no-untyped-def]
    last["route"] = out = real_route(view, entry_keys)
    return out


def pack(pool, scopes, budget, source, declarations):  # type: ignore[no-untyped-def]
    entries = [e.key for e in last["entries"].entries]
    where = {k: i for i, k in enumerate(entries)}
    origins = {r.key: r.origins for r in last["route"].candidates}

    def p(key: str) -> int:
        return min(
            (where[parent(o)] for o in origins[key] if parent(o) in where), default=len(entries)
        )

    head = [c for c in pool if c.entity.key in where]
    tail = [c for c in pool if c.entity.key not in where]
    assert pool == head + tail  # entries lead the pool, in entry order
    tail.sort(key=lambda c: (c.depth, p(c.entity.key), c.entity.key))
    return real_pack(head + tail, scopes, budget, source, declarations)


def main(language: str, split: str, variant: str, out: Path) -> None:
    if variant == "R":
        retrieval.retrieve, routing.route, packing.pack = retrieve, route, pack
    harness: Any = cb
    if language == "ts":
        import contextbench_ts

        harness = contextbench_ts
    tasks = harness.load_tasks()
    tasks["tasks"] = [t for t in tasks["tasks"] if t["split"] == split]
    used = {t["corpus"] for t in tasks["tasks"]}
    tasks["corpora"] = {k: v for k, v in tasks["corpora"].items() if k in used}
    with tempfile.TemporaryDirectory(prefix="oxide-graph-v3-") as data:
        evaluated = harness.evaluate(tasks, Path(data))
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps(evaluated["results"], indent=1) + "\n")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3], Path(sys.argv[4]))
