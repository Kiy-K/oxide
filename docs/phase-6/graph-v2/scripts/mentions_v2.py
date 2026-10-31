"""Unresolved-count cost of Repository.impact: tail-keyed lookup (v2 store) against the v1 full
Opaque scan, on the same store and the same names, with the counts checked equal.

    uv run python docs/phase-6/graph-v2/scripts/mentions_v2.py REPO

Same sample as graph-v1 mentions_cost.py (40 non-test symbols, seed 1, depth 3, limit 200).
"""

import json
import os
import random
import sys
import tempfile
import time
from pathlib import Path

import oxide
from oxide.core import navigation

V1 = (
    "MATCH (:Entity)-[r:Opaque]->(:Unresolved) WHERE r.kind IN $kinds AND "
    "(r.name IN $names OR any(s IN $suffixes WHERE r.name ENDS WITH s)) RETURN count(*)"
)
KINDS = ("calls", "references")


def ms(xs: list[float]) -> dict[str, float]:
    xs = sorted(xs)
    return {
        "p50": round(1000 * xs[len(xs) // 2], 1),
        "p95": round(1000 * xs[int(len(xs) * 0.95) - 1], 1),
    }


def v1(view: object, names: list[str]) -> int:
    if not names:
        return 0
    suffixes = sorted({f"{sep}{n}" for n in names for sep in (".", "::")})
    params = {"kinds": list(KINDS), "names": sorted(set(names)), "suffixes": suffixes}
    return int(view._query(V1, params)[0][0])  # type: ignore[attr-defined]


with (
    tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as d,
    oxide.open_repository(sys.argv[1], data_dir=d) as r,
):
    t = time.perf_counter()
    r.index()
    index_seconds = time.perf_counter() - t
    store_mb = sum(p.stat().st_size for p in Path(d).rglob("*") if p.is_file()) / 2**20
    v = r._view()
    q = "MATCH (e:Entity) WHERE e.category='symbol' AND e.test=false RETURN e.key"
    keys = sorted(k for (k,) in v._query(q))
    (opaque,) = v._query("MATCH ()-[r:Opaque]->() RETURN count(*)")[0]
    (nodes,) = v._query("MATCH (u:Unresolved) RETURN count(*)")[0]
    sample = random.Random(1).sample(keys, min(40, len(keys)))
    new, old, total, counts = [], [], [], []
    for k in sample:
        found = navigation.walk(v, k, "upstream", 3, 200)
        names = [e.name for e in v.entities(found.roots).values() if e.category == "symbol"]
        t0 = time.perf_counter()
        a = v.mentions(names, KINDS)
        t1 = time.perf_counter()
        b = v1(v, names)
        t2 = time.perf_counter()
        r.impact(k)
        t3 = time.perf_counter()
        assert a == b, (k, names, a, b)
        new.append(t1 - t0)
        old.append(t2 - t1)
        total.append(t3 - t2)
        counts.append(a)
    print(
        json.dumps(
            {
                "corpus": os.path.basename(sys.argv[1]),
                "index_seconds": round(index_seconds, 1),
                "store_mb": round(store_mb, 1),
                "opaque_rows": opaque,
                "unresolved_nodes": nodes,
                "equal_counts": len(counts),
                "nonzero_counts": sum(c > 0 for c in counts),
                "mentions_v1_ms": ms(old),
                "mentions_v2_ms": ms(new),
                "impact_upstream_ms": ms(total),
            }
        )
    )
