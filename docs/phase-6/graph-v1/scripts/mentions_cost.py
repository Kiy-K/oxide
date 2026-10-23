"""Split Repository.impact's upstream cost into walk, entity reads and the unresolved count.

    uv run python docs/phase-6/graph-v1/scripts/mentions_cost.py REPO

Same sample as impact_perf.py (40 non-test symbols, seed 1, depth 3).
"""

import json
import os
import random
import statistics
import sys
import tempfile
import time

import oxide
from oxide.core import navigation


def ms(xs: list[float]) -> dict[str, float]:
    xs = sorted(xs)
    return {
        "p50": round(1000 * xs[len(xs) // 2], 1),
        "p95": round(1000 * xs[int(len(xs) * 0.95) - 1], 1),
    }


with (
    tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as d,
    oxide.open_repository(sys.argv[1], data_dir=d) as r,
):
    r.index()
    v = r._view()
    q = "MATCH (e:Entity) WHERE e.category='symbol' AND e.test=false RETURN e.key"
    keys = sorted(k for (k,) in v._query(q))
    (opaque,) = v._query("MATCH ()-[r:Opaque]->() RETURN count(*)")[0]
    sample = random.Random(1).sample(keys, min(40, len(keys)))
    walk, reads, mentions, total = [], [], [], []
    for k in sample:
        t0 = time.perf_counter()
        found = navigation.walk(v, k, "upstream", 3, 200)
        t1 = time.perf_counter()
        v.entities(h.key for h in found.hops)
        names = [e.name for e in v.entities(found.roots).values() if e.category == "symbol"]
        t2 = time.perf_counter()
        v.mentions(names, ("calls", "references"))
        t3 = time.perf_counter()
        r.impact(k)
        t4 = time.perf_counter()
        walk.append(t1 - t0)
        reads.append(t2 - t1)
        mentions.append(t3 - t2)
        total.append(t4 - t3)
    row = {
        "corpus": os.path.basename(sys.argv[1]),
        "opaque_rows": opaque,
        "walk_ms": ms(walk),
        "entity_reads_ms": ms(reads),
        "mentions_ms": ms(mentions),
        "impact_ms": ms(total),
        "mentions_share_of_sum": round(
            statistics.mean(m / (w + e + m) for w, e, m in zip(walk, reads, mentions, strict=True)),
            3,
        ),
    }
    print(json.dumps(row))
