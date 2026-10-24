"""Tables and the preregistered verdict for Decision Study v2 (preregistration.md).

    python docs/phase-6/decision-study-v2/scripts/verdict.py OUT

Reads OUT/results.json from ``harness.py`` run with the recorded arms ``compact``,
``compact-norank``, ``compact-repeat`` and ``compact-reversed`` (any other recorded arm
is reported, never gated). Prints the preregistration digest, so a held-out run can check
that this verdict was reached under the frozen protocol.
"""

from __future__ import annotations

import hashlib
import json
import random
import statistics
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "decision-study-v1" / "scripts"))

from gate import auroc, spearman  # v1's tie-aware AUROC and Spearman

PREREGISTRATION = Path(__file__).resolve().parents[1] / "preregistration.md"
BUDGETS = ("256", "1024", "4096")
FLOOR = 0.4
PRIMARY = "compact~order"
CONTROLS = ("compact-repeat~order", "compact-reversed~order")
NORANK = "compact-norank~order"
RANDOM = [f"random~order_s{s}" for s in range(5)]
USD = 0.042e-6
DEADLINE = 2.0  # production's capsule.DEADLINE, seconds
Tasks = list[dict[str, Any]]


def recall(tasks: Tasks, arms: list[str], b: str, key: str = "line_recall") -> float:
    return statistics.fmean(statistics.fmean(t["arms"][a][b][key] for a in arms) for t in tasks)


def admitted(t: dict[str, Any], arm: str) -> list[tuple[float, bool]]:
    h = t["arms"]["heuristic"]["candidates"]
    c = t["arms"][arm]["candidates"]
    return [(v, y) for (v, y, _), (hv, *_) in zip(c, h, strict=True) if hv >= FLOOR]


def fell_back(t: dict[str, Any], arm: str) -> bool:
    """The request kept the heuristic: it has admitted candidates and judged none."""
    bases = [b for *_, b in t["arms"][arm]["candidates"]]
    return any(h >= FLOOR for h, *_ in t["arms"]["heuristic"]["candidates"]) and not any(
        b.startswith("judged by") for b in bases
    )


def bootstrap(deltas: list[float], n: int = 10_000) -> tuple[float, float]:
    """Paired percentile bootstrap over tasks, seed 0."""
    rng = random.Random(0)
    means = sorted(statistics.fmean(rng.choices(deltas, k=len(deltas))) for _ in range(n))
    return means[int(0.025 * n)], means[int(0.975 * n) - 1]


def table(tasks: Tasks, names: list[str]) -> dict[str, dict[str, float]]:
    groups = {g: [g] for g in ("heuristic", "oracle_order", "oracle_pick")}
    groups["random~order (mean of 5)"] = RANDOM
    groups |= {n: [n] for n in names if n.endswith("~order") and not n.startswith("random")}
    print(
        f"{'arm':28}"
        + "".join(f"{'LR@' + b:>9}" for b in BUDGETS)
        + "".join(f"{'used@' + b:>10}" for b in BUDGETS)
        + f"{'AUROC adm':>10}{'fallback':>9}"
    )
    out = {}
    for g, arms in groups.items():
        r = {b: recall(tasks, arms, b) for b in BUDGETS}
        used = {b: recall(tasks, arms, b, "used") / int(b) for b in BUDGETS}
        a = [x for t in tasks if (x := auroc(admitted(t, arms[0]))) is not None]
        fb = sum(fell_back(t, arms[0]) for t in tasks) if g.endswith("~order") else 0
        out[g] = r
        print(
            f"{g:28}"
            + "".join(f"{r[b]:9.4f}" for b in BUDGETS)
            + "".join(f"{used[b]:10.3f}" for b in BUDGETS)
            + f"{statistics.fmean(a):10.3f}{fb:9d}"
        )
    print("(used@B: mean packed units / budget; AUROC adm: within-task, admitted set only)")
    return out


def per_task(tasks: Tasks, arm: str) -> list[float]:
    print(f"\nper task, {arm} minus heuristic (Δ line_recall @256/1024/4096)")
    deltas = []
    for t in tasks:
        d = [
            t["arms"][arm][b]["line_recall"] - t["arms"]["heuristic"][b]["line_recall"]
            for b in BUDGETS
        ]
        deltas.append(d[2])
        mark = "+" if d[2] > 0.005 else "-" if d[2] < -0.005 else "="
        print(f"  {mark} {t['task']:36}" + "".join(f"{x:+8.3f}" for x in d))
    return deltas


def costs(results: dict[str, Any]) -> None:
    print()
    for name, data in results["recorded"].items():
        reqs = list(data["requests"].values())
        lat = sorted(r["latency_s"] for r in reqs)
        p95 = lat[min(len(lat) - 1, int(0.95 * len(lat)))]
        billed = [r.get("input_tokens") or 0 for r in reqs]
        failed = sum(r["status"] != 200 for r in reqs)
        print(
            f"{name}: {len(reqs)} requests, {failed} failed; latency median "
            f"{statistics.median(lat):.2f} s, p95 {p95:.2f} s, max {lat[-1]:.2f} s "
            f"(p95 {'within' if p95 <= DEADLINE else 'OVER'} the {DEADLINE} s deadline); "
            f"billed input tokens/request {statistics.fmean(billed):.0f}, "
            f"total ${sum(billed) * USD:.4f}"
        )


def verdict(results: dict[str, Any], r: dict[str, dict[str, float]], deltas: list[float]) -> None:
    tasks, split = results["tasks"], results["split"]
    base, p = r["heuristic"], r[PRIMARY]
    gain = p["4096"] - base["4096"]
    rnd = r["random~order (mean of 5)"]["4096"]
    lo, hi = bootstrap(deltas)
    wins = sum(d > 0.005 for d in deltas)
    losses = sum(d < -0.005 for d in deltas)
    reqs = results["recorded"]["compact"]["requests"].values()
    failed = sum(q["status"] != 200 for q in reqs)
    fallback = sum(fell_back(t, PRIMARY) for t in tasks)
    print(
        f"\n{PRIMARY}: Δ@4096 {gain:+.4f} (paired bootstrap 95% CI {lo:+.4f} … {hi:+.4f}); "
        f"tasks +{wins} -{losses} ={len(deltas) - wins - losses}; "
        f"failed requests {failed}, fallback requests {fallback}"
    )
    no_harm = all(p[b] >= base[b] - 0.005 for b in BUDGETS)
    norank = NORANK in r and r[NORANK]["4096"] - base["4096"] >= 0.5 * gain
    rhos: dict[str, float | None] = {}
    for c in CONTROLS:
        vals = []
        for t in tasks:
            x, y = admitted(t, PRIMARY), admitted(t, c) if c in r else []
            if y and (s := spearman([v for v, _ in x], [v for v, _ in y])) is not None:
                vals.append(s)
        rhos[c] = statistics.median(vals) if vals else None
        dd = {b: round(r[c][b] - p[b], 4) for b in BUDGETS} if c in r else None
        print(f"{c}: median within-task Spearman (admitted) {rhos[c]}, ΔΔ vs {PRIMARY} {dd}")
    if split == "dev":
        floor, tol = 0.030, 0.030
        checks = {
            "D1 recall": gain >= floor and p["1024"] >= base["1024"] and no_harm,
            "D2 random order": p["4096"] - rnd >= floor,
            "D3 rank": norank,
            "D4 regressions": losses <= 1,
            "D5 reproducibility": all(
                c in r
                and r[c]["4096"] - base["4096"] >= floor
                and all(abs(r[c][b] - p[b]) <= tol for b in ("1024", "4096"))
                and (rhos[c] or 0.0) >= 0.90
                for c in CONTROLS
            ),
            "D6 operational": failed <= 1,
        }
        for k, ok in checks.items():
            print(f"  {k}: {'pass' if ok else 'FAIL'}")
        science = all(
            checks[k] for k in ("D1 recall", "D2 random order", "D3 rank", "D4 regressions")
        )
        state = "PASS" if all(checks.values()) else "FAIL"
        print(f"DEV GATE: {state}")
        if state == "FAIL":
            print(
                "VERDICT: " + ("INCONCLUSIVE" if science else "REJECT") + " (held-out stays sealed)"
            )
        return
    checks = {
        "H1 recall": gain >= 0.020 and lo > 0,
        "H2 no harm": no_harm,
        "H3 random order": p["4096"] - rnd >= 0.020,
        "H4 rank": norank,
        "H5 regressions": losses <= 0.2 * len(deltas) and losses < wins,
        "H6 reproducibility": all(
            c in r and r[c]["4096"] - base["4096"] >= 0.020 for c in CONTROLS
        ),
        "H7 operational": failed <= 0.1 * len(deltas),
    }
    for k, ok in checks.items():
        print(f"  {k}: {'pass' if ok else 'FAIL'}")
    if all(checks.values()):
        print("VERDICT: PROMOTE (to an ADR proposal; no production integration)")
    elif gain < 0 or not checks["H5 regressions"]:
        print("VERDICT: REJECT")
    else:
        print("VERDICT: INCONCLUSIVE")


def main(out: Path) -> None:
    digest = "sha256:" + hashlib.sha256(PREREGISTRATION.read_bytes()).hexdigest()
    results = json.loads((out / "results.json").read_text())
    tasks = results["tasks"]
    print(f"preregistration {digest}")
    print(f"split {results['split']}: {len(tasks)} tasks with verified gold\n")
    r = table(tasks, list(tasks[0]["arms"]))
    deltas = per_task(tasks, PRIMARY)
    costs(results)
    verdict(results, r, deltas)


if __name__ == "__main__":
    main(Path(sys.argv[1]))
