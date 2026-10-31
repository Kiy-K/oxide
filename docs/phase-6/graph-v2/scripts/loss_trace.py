"""Where gold newly routed by one frozen baseline is lost: per-span 4,096-unit stage.

    python docs/phase-6/graph-v2/scripts/loss_trace.py baseline-v11 baseline-v12

Verified spans routed in NEW but not in OLD, with their stage in NEW (``entry``,
``routing``, ``selection`` with the omission reason, ``budget``, ``complete``). Routed but
lost at selection or budget is a selection-policy loss; never routed is discovery.
"""

import collections
import json
import sys
from pathlib import Path

PHASE6 = Path(__file__).resolve().parents[2]


def results(name: str) -> list[dict]:
    return json.loads((PHASE6 / name / "results.json").read_text())


old, new = results(sys.argv[1]), results(sys.argv[2])
tally: collections.Counter[str] = collections.Counter()
for ro, rn in zip(old, new, strict=True):
    assert ro["task"] == rn["task"]
    for go, gn in zip(ro["gold"], rn["gold"], strict=True):
        if gn["verified"] and gn["routed"] and not go["routed"]:
            lost = gn["lost"].get("4096")
            stage = "complete" if lost is None else lost
            tally[f"{rn['split']} {stage.split(':', 1)[0]}"] += 1
            print(f"{rn['split']:8} {rn['task']:34} {gn['file']:44} {stage}")
print(dict(sorted(tally.items())))
