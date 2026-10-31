"""loc_sum.py DIR BENCH: compare the arms in DIR (loc_run.py output) with `none` (dev only).

Prints: whether `none` reproduces the frozen dev results; per arm the dev macro and
per-repository packed and entry metrics, the gain (best of line_recall@1024/@4096),
tasks improved/regressed, determinism, and the first-loss stage of every verified span
still missed at 4,096 units. With DIR/signals.json, also gold-file recall@k per signal.
"""

import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path.cwd() / "tests/python"))
import contextbench as cb

root, bench = Path(sys.argv[1]), sys.argv[2]
if bench == "ts":
    import contextbench_ts as ts

    frozen_dir = ts.BASELINE
else:
    frozen_dir = cb.BASELINE
frozen = {r["task"]: r for r in json.loads((frozen_dir / "results.json").read_text())}
SHOWN = (
    "entry_recall@20",
    "entry_mrr",
    "routed_coverage",
    "span_recall@1024",
    "line_recall@256",
    "line_recall@1024",
    "line_recall@4096",
    "file_recall@4096",
)
PACKED = [f"{m}@{b}" for m in ("span_recall", "line_recall") for b in cb.BUDGETS]
GAIN = ("line_recall@1024", "line_recall@4096")


def read(arm):
    return {r["task"]: r for r in json.loads((root / arm / "results.json").read_text())}


arms = sorted(p.name for p in root.iterdir() if (p / "results.json").exists())
arms.remove("none")
base = read("none")
same = sum(base[t]["compat"] == frozen[t]["compat"] for t in base)
print(f"none reproduces frozen compat on {same}/{len(base)} dev tasks")
tasks = sorted(t for t in base if base[t]["verified"])
groups = {"ALL": tasks}
for t in tasks:
    groups.setdefault(base[t]["corpus"].split("@")[0], []).append(t)


def metric(res, t, m):
    return res[t]["verified"]["metrics"][m]


def macro(res, group, m):
    return sum(metric(res, t, m) for t in group) / len(group)


def lost(res):
    return Counter(
        g["lost"]["4096"].split(":")[0]
        for t in tasks
        for g in res[t]["gold"]
        if g["verified"] and g["packed"]["4096"] != "complete"
    )


summary = {}
for arm in ["none", *arms]:
    res = read(arm)
    print(f"\n== {arm} ==")
    for g, group in groups.items():
        cells = []
        for m in SHOWN:
            a, b = macro(base, group, m), macro(res, group, m)
            cells.append(f"{m}={b:.3f}" + ("" if arm == "none" else f"({b - a:+.3f})"))
        print(f"  [{g} n={len(group)}] " + " ".join(cells))
    print(f"  missed@4096 by first-loss stage: {dict(lost(res))}")
    if arm == "none":
        continue
    up = {t for t in tasks if any(metric(res, t, m) > metric(base, t, m) for m in PACKED)}
    down = {t for t in tasks if any(metric(res, t, m) < metric(base, t, m) for m in PACKED)}
    best = max(GAIN, key=lambda m: macro(res, tasks, m) - macro(base, tasks, m))
    gain = macro(res, tasks, best) - macro(base, tasks, best)
    moved = sorted(((metric(res, t, best) - metric(base, t, best)) / len(tasks), t) for t in tasks)
    carriers = [t for d, t in moved if d > 0]
    det = all(b["deterministic"] for r in res.values() for b in r["bundles"].values())
    print(
        f"  gain {gain:+.4f} ({best}); improved {len(up)} regressed {len(down)} "
        f"(both {len(up & down)}); deterministic {det}"
    )
    top = [f"{t}:{d:+.3f}" for d, t in moved[::-1][:4]]
    print(f"  gain carried by {len(carriers)} tasks; largest shares {top}")
    summary[arm] = {
        "gain": round(gain, 4),
        "metric": best,
        "improved": len(up),
        "regressed": len(down),
        "carriers": len(carriers),
    }
print("\n" + json.dumps(summary))

sig = root / "signals.json"
if sig.exists():
    rows = [r for r in json.loads(sig.read_text()) if r["gold"]]
    print("\n== gold-file recall@k (macro over tasks with verified gold) ==")
    sgroups = {"ALL": rows}
    for r in rows:
        sgroups.setdefault(r["corpus"].split("@")[0], []).append(r)
    for g, rs in sgroups.items():
        for s in ("P0", "P1", "P2", "P3", "P4"):
            cells = []
            for k in (1, 5, 10, 20):
                hit = [
                    sum(x is not None and x <= k for x in r["rank"][s].values()) / len(r["gold"])
                    for r in rs
                ]
                cells.append(f"@{k}={sum(hit) / len(hit):.3f}")
            print(f"  [{g} n={len(rs)}] {s} " + " ".join(cells))
