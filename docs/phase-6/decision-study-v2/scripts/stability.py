"""Stability investigation (README § Why v1's repeat and reversed runs drifted). No network.

    python docs/phase-6/decision-study-v2/scripts/stability.py JUDGMENTS_DIR OUT

JUDGMENTS_DIR holds v1's recorded ``compact``, ``compact-repeat``, ``compact-reversed``
and ``compact-norank`` judgments; OUT is a ``harness.py --stability`` run over them.
Separates (1) judgment variability: how JEV's probabilities move between calls, from
(2) packing sensitivity: how packed recall moves for a given change in values, under
the raw v1 mapping (judgment replaces value; the floor applies) and the v2 hybrid.
"""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path
from typing import Any

BUDGETS = ("256", "1024", "4096")
FLOOR = 0.4
TOLERANCE = 0.010  # v1's |ΔΔ@4096| limit


def sign(x: float, y: float) -> int:
    return (x > y) - (x < y)


def judgment_variability(jdir: Path, tasks: list[dict[str, Any]]) -> None:
    base = json.loads((jdir / "compact.json").read_text())["judgments"]
    print("(1) judgment variability vs compact, per candidate (707 asked, 15 dev requests)")
    print(
        f"{'other call':18}{'same':>6}{'p50':>6}{'p90':>6}{'p99':>6}{'max':>6}"
        f"{'floor x':>8}  admitted pairs reordered"
    )
    for other in ("compact-repeat", "compact-reversed", "compact-norank"):
        o = json.loads((jdir / f"{other}.json").read_text())["judgments"]
        d = sorted(abs(o[k] - p) for k, p in base.items() if k in o)
        q = statistics.quantiles(d, n=100)
        cross = sum((p >= FLOOR) != (o[k] >= FLOOR) for k, p in base.items() if k in o)
        pairs = flips = 0
        for t in tasks:  # pairs among production's admitted set, by pool position
            h = [c[0] for c in t["arms"]["heuristic"]["candidates"]]
            a = [c[0] for c in t["arms"]["compact"]["candidates"]]
            b = [c[0] for c in t["arms"][other]["candidates"]]
            adm = [i for i, v in enumerate(h) if v >= FLOOR]
            for x, i in enumerate(adm):
                for k in adm[x + 1 :]:
                    pairs += 1
                    flips += sign(a[i], a[k]) != sign(b[i], b[k])
        same = sum(x == 0 for x in d) / len(d)
        print(
            f"{other:18}{same:6.2f}{q[49]:6.2f}{q[89]:6.2f}{q[98]:6.2f}{d[-1]:6.2f}"
            f"{cross:8d}  {flips} of {pairs} ({flips / pairs:.1%})"
        )
    ties = 0
    for t in tasks:
        h = [c[0] for c in t["arms"]["heuristic"]["candidates"]]
        adm = [
            c[0] for c, v in zip(t["arms"]["compact"]["candidates"], h, strict=True) if v >= FLOOR
        ]
        ties += sum(v == w for x, v in enumerate(adm) for w in adm[x + 1 :])
    print(f"exact ties among those admitted pairs in compact: {ties} (JEV returns a 0.01 grid)\n")


def drift(tasks: list[dict[str, Any]], a: str, b: str) -> list[tuple[str, list[float]]]:
    out = []
    for t in tasks:
        d = [t["arms"][b][x]["line_recall"] - t["arms"][a][x]["line_recall"] for x in BUDGETS]
        if any(abs(v) > 1e-9 for v in d):
            out.append((t["task"], [round(v, 3) for v in d]))
    return out


def macro(tasks: list[dict[str, Any]], arm: str) -> dict[str, float]:
    return {b: statistics.fmean(t["arms"][arm][b]["line_recall"] for t in tasks) for b in BUDGETS}


def observed(tasks: list[dict[str, Any]], items: dict[str, Any]) -> None:
    print("(2a) observed drift per task (Δ line_recall @256/1024/4096), recorded calls")
    for base in ("compact", "compact~order"):
        for ctl in ("repeat", "reversed", "norank"):
            other = base.replace("compact", f"compact-{ctl}")
            if other not in tasks[0]["arms"]:
                continue
            rows = drift(tasks, base, other)
            dd = macro(tasks, other)["4096"] - macro(tasks, base)["4096"]
            print(f"  {other:24} |ΔΔ@4096| {abs(dd):.4f}  tasks changed {len(rows)}: {rows}")
            for task, d in rows:
                for b in (x for x, v in zip(BUDGETS, d, strict=True) if v):
                    x, y = items[task][base][b], items[task][other][b]
                    print(
                        f"      {task} @{b}: {len(x)}→{len(y)} items; "
                        f"out {[k for k in x if k not in y]}; in {[k for k in y if k not in x]}"
                    )
    print()


def simulated(tasks: list[dict[str, Any]]) -> None:
    print("(2b) packing sensitivity: 20 seeded perturbations of the recorded compact judgments")
    print(
        f"{'family':22}{'mean@4096':>10}{'sd@1024':>9}{'sd@4096':>9}{'P(|ΔΔ|>.010)':>14}"
        f"{'tasks moved':>12}{'max task |Δ|@4096':>19}"
    )
    names = tasks[0]["arms"]
    families = (
        ("tie~order", "compact~order"),
        ("repeatnoise~order", "compact~order"),
        ("reversednoise~order", "compact~order"),
        ("repeatnoise", "compact"),
        ("reversednoise", "compact"),
    )
    for fam, base in families:
        arms = sorted(n for n in names if n.rsplit("_s", 1)[0] == fam)
        if not arms:
            continue
        ref = macro(tasks, base)["4096"]
        m = [macro(tasks, a) for a in arms]
        moved = [len(drift(tasks, base, a)) for a in arms]
        worst = max((abs(d[2]) for a in arms for _, d in drift(tasks, base, a)), default=0.0)
        over = sum(abs(x["4096"] - ref) > TOLERANCE for x in m) / len(m)
        print(
            f"{fam:22}{statistics.fmean(x['4096'] for x in m):10.4f}"
            f"{statistics.pstdev(x['1024'] for x in m):9.4f}"
            f"{statistics.pstdev(x['4096'] for x in m):9.4f}{over:14.2f}"
            f"{statistics.fmean(moved):12.1f}{worst:19.3f}"
        )
    print(f"{'reference':22}" + "".join(f"{'LR@' + b:>9}" for b in BUDGETS))
    for arm in ("heuristic", "compact", "compact~order", "oracle_order"):
        print(f"  {arm:20}" + "".join(f"{macro(tasks, arm)[b]:9.4f}" for b in BUDGETS))


def main(jdir: Path, out: Path) -> None:
    tasks = json.loads((out / "results.json").read_text())["tasks"]
    items = json.loads((out / "items.json").read_text())
    judgment_variability(jdir, tasks)
    observed(tasks, items)
    simulated(tasks)


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
