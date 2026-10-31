"""Apply the preregistered verdict to dispatch_arm.py outputs, plus the flip diagnostic.

    python docs/phase-6/graph-v2/scripts/arm_gate.py B_DIR T_DIR

B must reproduce baseline-v13's dev results (compat hashes), or the study is void.
"""

import json
import sys
from pathlib import Path

FROZEN = Path(__file__).resolve().parents[2] / "baseline-v13" / "results.json"
METRICS = (
    "line_recall@256",
    "line_recall@1024",
    "line_recall@4096",
    "span_recall@4096",
    "gold_unit_share@4096",
    "routed_coverage",
)


def stage(gold: dict) -> str:
    lost = gold["lost"].get("4096")
    return "complete" if lost is None else lost.split(":", 1)[0]


b_dir, t_dir = Path(sys.argv[1]), Path(sys.argv[2])
frozen = {r["task"]: r["compat"] for r in json.loads(FROZEN.read_text()) if r["split"] == "dev"}
b_results = json.loads((b_dir / "results.json").read_text())
t_results = json.loads((t_dir / "results.json").read_text())
b_compat = {r["task"]: r["compat"] for r in b_results}
assert b_compat == frozen, "B does not reproduce baseline-v13 dev: study void"
print(f"B reproduces baseline-v13 dev compat on {len(frozen)} tasks")

b = json.loads((b_dir / "metrics.json").read_text())["dev"]["verified"]
t = json.loads((t_dir / "metrics.json").read_text())["dev"]["verified"]
print(f"{'metric':24} {'B':>9} {'T':>9} {'T-B':>9}")
for m in METRICS:
    print(f"{m:24} {b[m]:9.6f} {t[m]:9.6f} {t[m] - b[m]:+9.6f}")
gain = t["line_recall@4096"] - b["line_recall@4096"]
drops = [t[f"line_recall@{n}"] - b[f"line_recall@{n}"] for n in (256, 1024, 4096)]
share = t["gold_unit_share@4096"] - b["gold_unit_share@4096"]
advance = gain >= 0.02 and min(drops) >= -0.01 and share >= -0.01
print("VERDICT:", "ADVANCE" if advance else "REJECT")

changed = sum(rb["compat"] != rt["compat"] for rb, rt in zip(b_results, t_results, strict=True))
print(f"tasks whose entries/routed/payload changed under T: {changed} of {len(b_results)}")
print("verified spans whose routed status flips B -> T (4096 stage under B -> T):")
for rb, rt in zip(b_results, t_results, strict=True):
    for gb, gt in zip(rb["gold"], rt["gold"], strict=True):
        if gb["verified"] and gb["routed"] != gt["routed"]:
            print(
                f"  {rb['task']} {gb['file']} routed {gb['routed']}->{gt['routed']} "
                f"stage {stage(gb)}->{stage(gt)}"
            )
print("verified spans whose 4096 stage changes:")
for rb, rt in zip(b_results, t_results, strict=True):
    for gb, gt in zip(rb["gold"], rt["gold"], strict=True):
        if gb["verified"] and stage(gb) != stage(gt):
            print(f"  {rb['task']} {gb['file']} {stage(gb)} -> {stage(gt)}")
