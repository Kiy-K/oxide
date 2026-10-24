"""Gate for the parent-precedence order arm (preregistration.md).

    python docs/phase-6/graph-v3/scripts/order_gate.py {dev|heldout} py=B_DIR,R_DIR ts=B_DIR,R_DIR

Checks B against the frozen baseline (compat hashes), prints per-language and pooled
metrics (verified gold, macro over tasks) and the stage's verdict.
"""

import json
import sys
from pathlib import Path

PHASE6 = Path(__file__).resolve().parents[2]
FROZEN = {"py": "baseline-v13", "ts": "typescript-v10"}
METRICS = [f"{m}@{b}" for m in ("line_recall", "span_recall") for b in (256, 1024, 4096)]
METRICS += ["gold_unit_share@4096"]

split = sys.argv[1]
runs = {}
for arg in sys.argv[2:]:
    lang, dirs = arg.split("=")
    b, r = (json.loads((Path(d) / "results.json").read_text()) for d in dirs.split(","))
    frozen = {
        x["task"]: x["compat"]
        for x in json.loads((PHASE6 / FROZEN[lang] / "results.json").read_text())
    }
    same = sum(x["compat"] == frozen[x["task"]] for x in b)
    assert same == len(b), f"{lang} B reproduces {same}/{len(b)} frozen tasks: study void"
    print(f"{lang}: B reproduces {FROZEN[lang]} compat on {same} {split} tasks")
    runs[lang] = [(x, y) for x, y in zip(b, r, strict=True) if x["verified"]]


def mean(pairs, arm, metric):
    return sum(p[arm]["verified"]["metrics"][metric] for p in pairs) / len(pairs)


rows = {**runs, "pooled": [p for v in runs.values() for p in v]}
worst = share = 0.0
print(f"{'metric':22}" + "".join(f"{k + ' B':>10}{k + ' R':>10}{'R-B':>8}" for k in rows))
for m in METRICS:
    line = f"{m:22}"
    for k, pairs in rows.items():
        b, r = mean(pairs, 0, m), mean(pairs, 1, m)
        line += f"{b:10.4f}{r:10.4f}{r - b:+8.4f}"
        if k != "pooled" and m.startswith("line_recall"):
            worst = min(worst, r - b)
        if k != "pooled" and m.startswith("gold_unit"):
            share = min(share, r - b)
    print(line)
for k, pairs in rows.items():
    moved = [x["task"] for x, y in pairs if x["verified"]["metrics"] != y["verified"]["metrics"]]
    print(f"{k}: {len(moved)} of {len(pairs)} tasks change: {moved}")

gain = mean(rows["pooled"], 1, "line_recall@4096") - mean(rows["pooled"], 0, "line_recall@4096")
need = 0.010 if split == "dev" else 0.005
ok = gain >= need and worst >= -0.005 and (split == "dev" or share >= -0.01)
print(
    f"pooled line_recall@4096 R-B {gain:+.4f} (need >= +{need}); "
    f"worst language line_recall R-B {worst:+.4f} (>= -0.005)"
)
if split != "dev":
    print(f"worst gold_unit_share@4096 R-B {share:+.4f} (>= -0.01)")
print(f"{split.upper()} GATE: {'PASS' if ok else 'FAIL'}")
