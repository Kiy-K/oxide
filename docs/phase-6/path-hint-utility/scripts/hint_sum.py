"""hint_sum.py DIR BENCH: preregistered path-hint criteria over hint_run.py output.

Prints per arm the macro and per-repository packed metrics, the criterion checks
against prod.H / prod.U / prod.N, carriers, determinism, budget adherence, median
build_context time, and whether each arm reproduces the frozen compat hashes (only
expected of *.U arms). The criteria block needs the dev arms of hint_run.py.
"""

import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd() / "tests/python"))
import contextbench as cb

root, bench = Path(sys.argv[1]), sys.argv[2]
frozen_dir = __import__("contextbench_ts").BASELINE if bench == "ts" else cb.BASELINE
frozen = {r["task"]: r for r in json.loads((frozen_dir / "results.json").read_text())}
GAIN = ("line_recall@1024", "line_recall@4096")
SHOWN = ("span_recall@1024", "line_recall@256", *GAIN, "file_recall@4096", "gold_unit_share@4096")
PACKED = [f"{m}@{b}" for m in ("span_recall", "line_recall") for b in cb.BUDGETS]
arms = {
    p.name: {r["task"]: r for r in json.loads((p / "results.json").read_text())}
    for p in sorted(root.iterdir())
    if (p / "results.json").exists()
}
tasks = sorted(t for t in arms["prod.U"] if arms["prod.U"][t]["verified"])
groups = {"ALL": tasks}
for t in tasks:
    groups.setdefault(arms["prod.U"][t]["corpus"].split("@")[0], []).append(t)


def m(arm, t, k):
    return arms[arm][t]["verified"]["metrics"][k]


def macro(arm, group, k):
    return sum(m(arm, t, k) for t in group) / len(group)


def gain(x, y):
    best = max(GAIN, key=lambda k: macro(x, tasks, k) - macro(y, tasks, k))
    d = macro(x, tasks, best) - macro(y, tasks, best)
    carriers = sum(m(x, t, best) > m(y, t, best) for t in tasks)
    return round(d, 4), best, carriers


for arm in arms:
    rows = arms[arm].values()
    same = sum(r["compat"] == frozen[r["task"]]["compat"] for r in rows)
    det = all(
        b["deterministic"] and b["within_budget"] for r in rows for b in r["bundles"].values()
    )
    secs = statistics.median(r["seconds"] for r in rows)
    print(
        f"\n== {arm} == frozen compat {same}/{len(rows)}; deterministic+within {det}; "
        f"median build_context {secs * 1000:.1f} ms"
    )
    for g, group in groups.items():
        print(
            f"  [{g} n={len(group)}] " + " ".join(f"{k}={macro(arm, group, k):.3f}" for k in SHOWN)
        )

if "tier.H" not in arms:
    sys.exit(0)
print("\n== criteria ==")
for v in ("tier", "outline"):
    h, n = f"{v}.H", f"{v}.N"
    c1 = gain(h, "prod.H")
    c2 = round(macro(h, tasks, "line_recall@4096") - macro("prod.U", tasks, "line_recall@4096"), 4)
    worst = min(
        (macro(h, g, k) - macro("prod.H", g, k), name, k)
        for name, g in groups.items()
        if name != "ALL"
        for k in GAIN
    )
    c4 = min(macro(n, tasks, k) - macro("prod.N", tasks, k) for k in GAIN)
    up = sum(any(m(h, t, k) > m("prod.H", t, k) for k in PACKED) for t in tasks)
    down = sum(any(m(h, t, k) < m("prod.H", t, k) for k in PACKED) for t in tasks)
    t_h = statistics.median(r["seconds"] for r in arms[h].values())
    t_p = statistics.median(r["seconds"] for r in arms["prod.H"].values())
    print(
        f"  {v}: c1 gain {c1}; c2 vs prod.U @4096 {c2:+.4f}; c3 worst repo "
        f"({worst[0]:+.4f}, {worst[1]}, {worst[2]}); c4 noisy {c4:+.4f}; "
        f"tasks up {up} down {down}; time ratio {t_h / t_p:.3f}"
    )
print(f"  prod.H vs prod.U: {gain('prod.H', 'prod.U')}")
