"""body_sum.py DIR BENCH: compare the arms in DIR/BENCH-{none,full,own} (dev only).

Prints: whether `none` reproduces the frozen dev results; dev macro and per-repository
gated metrics; tasks improved/regressed in packed metrics; gold spans in the lexical
top 20 and gold files in the top 20 files; costs. Applies criteria 1-3 of the
preregistration mechanically.
"""

import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd() / "tests/python"))
import contextbench as cb

TOL = cb.TOLERANCE
GATED = list(cb.GATED)
PACKED = [m for m in GATED if m.startswith(("span_recall", "line_recall"))]
SHOWN = [*GATED, *(f"file_recall@{b}" for b in cb.BUDGETS)]

root, bench = Path(sys.argv[1]), sys.argv[2]
if bench == "ts":
    import contextbench_ts as ts

    frozen_dir = ts.BASELINE
else:
    frozen_dir = cb.BASELINE
frozen = {r["task"]: r for r in json.loads((frozen_dir / "results.json").read_text())}


def read(arm):
    d = root / f"{bench}-{arm}"
    return {
        "results": {r["task"]: r for r in json.loads((d / "results.json").read_text())},
        "cost": json.loads((d / "cost.json").read_text()),
        "probe": json.loads((d / "probe.json").read_text()),
    }


arms = {a: read(a) for a in ("none", "full", "own")}
base = arms["none"]["results"]
same = [t for t in base if base[t]["compat"] == frozen[t]["compat"]]
print(f"none reproduces frozen compat on {len(same)}/{len(base)} dev tasks")


def repo(task):
    return base[task]["corpus"].split("@")[0]


def macro(results, tasks, m):
    vals = [results[t]["verified"]["metrics"][m] for t in tasks if results[t]["verified"]]
    return sum(vals) / len(vals) if vals else None


groups = {"ALL": sorted(base)}
for t in sorted(base):
    groups.setdefault(repo(t), []).append(t)

verdict = {}
for arm in ("full", "own"):
    res = arms[arm]["results"]
    print(f"\n== {arm} vs none (dev macro, verified) ==")
    fails = []
    for g, tasks in groups.items():
        row = []
        for m in SHOWN:
            a, b = macro(base, tasks, m), macro(res, tasks, m)
            if a is None:
                continue
            row.append(f"{m}={a:.3f}->{b:.3f}")
            if m in GATED and b < a - TOL:
                fails.append(f"{g}:{m} {a:.3f}->{b:.3f}")
        print(f"[{g}] ({len(tasks)} tasks)\n  " + "\n  ".join(row))
    up, down = set(), set()
    for t in base:
        if not base[t]["verified"]:
            continue
        for m in PACKED:
            a, b = base[t]["verified"]["metrics"][m], res[t]["verified"]["metrics"][m]
            if b > a:
                up.add(t)
            elif b < a:
                down.add(t)
    gain = max(
        macro(res, groups["ALL"], m) - macro(base, groups["ALL"], m)
        for m in ("line_recall@1024", "line_recall@4096")
    )
    det = all(b["deterministic"] for r in res.values() for b in r["bundles"].values())
    print(f"tasks improved {len(up)} {sorted(up)}\ntasks regressed {len(down)} {sorted(down)}")
    print(f"best line_recall gain {gain:+.4f}; deterministic bundles: {det}")
    print("criterion-3 failures:", fails or "none")
    verdict[arm] = {
        "gain": gain > TOL,
        "improved": len(up),
        "regressed": len(down),
        "no_regression": not fails,
        "deterministic": det,
    }

print("\n== entry (probe, real FTS, no cut) ==")
for g, tasks in groups.items():
    row = []
    for arm in ("none", "full", "own"):
        sp = [s for s in arms[arm]["probe"]["spans"] if s["task"] in tasks]
        top = sum(s["rank"] is not None and s["rank"] <= 20 for s in sp)
        files = {(s["task"], s["file"]): s["file_rank"] for s in sp}
        ftop = sum(r is not None and r <= 20 for r in files.values())
        unmatched = sum(s["rank"] is None for s in sp)
        row.append(
            f"{arm}: spans top20 {top}/{len(sp)} unmatched {unmatched}, "
            f"files top20 {ftop}/{len(files)}"
        )
    print(f"[{g}] " + " | ".join(row))

print("\n== cost ==")
for arm in ("none", "full", "own"):
    c = arms[arm]["cost"]
    score = list(c["score_s"].values())
    vs = [c["score_s"][t] for t in c["score_s"] if "vscode" in t]
    print(
        f"{arm}: rss {c['max_rss_mb']} MB, lexical median {c['lexical_s']['median_ms']} ms "
        f"max {c['lexical_s']['max_ms']} ms, score median {statistics.median(score):.2f} s"
        + (f", vscode score median {statistics.median(vs):.2f} s" if vs else "")
    )
    for i in c["index"]:
        print(
            f"   {Path(i['root']).name[:40]:40} index {i.get('index_s')} s load {i['load_s']} s "
            f"store {i['store_bytes'] / 2**20:.1f} MiB body {i['body_terms_chars'] / 2**20:.1f} MiB"
        )
    corpora = c["harness"].get("corpora", {})
    fail = {
        k: v["default_index_failure"] for k, v in corpora.items() if v.get("default_index_failure")
    }
    print("   default_index_failure:", fail or "none")
print("\n" + json.dumps(verdict))
