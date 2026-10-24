"""Coverage precondition for the parent-precedence order (preregistration.md).

    python docs/phase-6/graph-v3/scripts/order_precondition.py TRACE.json [TRACE.json ...]

Dev tasks where a gold-holding depth-1 candidate is not packed whole at 4,096 while a
non-gold depth-1 candidate reached from a worse-ranked parent (larger ``p``) is packed.
``p`` here is the parent's position among the pool's entry points.
"""

import json
import sys
from pathlib import Path


def parent(origin: str) -> str:
    parts = origin.split(":")
    cross = len(parts) > 2 and parts[1] in ("in", "out")
    return origin.split(":", 2)[-1] if cross else origin.split(":", 1)[-1]


tasks = set()
for path in sys.argv[1:]:
    for r in json.loads(Path(path).read_text()):
        if not r["trace"]:
            continue
        cs = r["trace"]["candidates"]
        entry = [c["key"] for c in cs if c["depth"] == 0]
        where = {k: i for i, k in enumerate(entry)}

        def p(c: dict, where: dict = where, last: int = len(entry)) -> int:
            return min((where[parent(o)] for o in c["parents"] if parent(o) in where), default=last)

        d1 = [c for c in cs if c["depth"] == 1 and c["value"] >= 0.4 and c["units"]]
        for g in (c for c in d1 if c["gold"] and c["state"] != "full"):
            packed = [c for c in d1 if not c["gold"] and c["state"] in ("full", "header")]
            worse = [c for c in packed if p(c) > p(g)]
            if worse:
                tasks.add(r["task"])
                print(
                    f"  {r['task']:34} {g['key'][-55:]:55} gold={g['gold']} p={p(g)} "
                    f"u={g['units']} {g['state']} worse={len(worse)}"
                )
print(f"affected tasks: {len(tasks)}")
