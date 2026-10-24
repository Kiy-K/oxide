"""Summary and preregistered verdict for Decision Study v1 (preregistration.md).

    python docs/phase-6/decision-study-v1/scripts/gate.py OUT

Reads OUT/results.json, OUT/costs.json and OUT/truncation.json from ``study.py``. Model
arms are the recorded ones: ``canonical``, ``shared``, ``compact``, each optionally with
``-norank``, ``-repeat`` and ``-reversed``, and the ``shared-fullquery`` diagnostic.
"""

from __future__ import annotations

import json
import statistics
import sys
from collections import Counter
from pathlib import Path
from typing import Any

BUDGETS = ("256", "1024", "4096")
RENDERERS = ("canonical", "shared", "compact")
SEEDS = range(5)
Tasks = list[dict[str, Any]]


def ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    out = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            out[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return out


def auroc(pairs: list[tuple[float, bool]]) -> float | None:
    pos = sum(1 for _, y in pairs if y)
    neg = len(pairs) - pos
    if not pos or not neg:
        return None
    r = ranks([v for v, _ in pairs])
    hits = sum(x for x, (_, y) in zip(r, pairs, strict=True) if y)
    return (hits - pos * (pos + 1) / 2) / (pos * neg)


def spearman(a: list[float], b: list[float]) -> float | None:
    ra, rb = ranks(a), ranks(b)
    if len(set(ra)) < 2 or len(set(rb)) < 2:
        return None
    ma, mb = statistics.fmean(ra), statistics.fmean(rb)
    cov = sum((x - ma) * (y - mb) for x, y in zip(ra, rb, strict=True))
    return cov / (sum((x - ma) ** 2 for x in ra) * sum((y - mb) ** 2 for y in rb)) ** 0.5


def quality(tasks: Tasks, arm: str) -> dict[str, Any]:
    within, p5, pooled, failed = [], [], [], 0
    fallbacks: Counter[str] = Counter()
    for t in tasks:
        cands = t["arms"][arm]["candidates"]
        pairs = [(v, y) for v, y, _ in cands]
        pooled += pairs
        if (a := auroc(pairs)) is not None:
            within.append(a)
        top = sorted(range(len(pairs)), key=lambda i: -pairs[i][0])[:5]  # ties: pool order
        p5.append(sum(pairs[i][1] for i in top) / len(top))
        bases = [b for *_, b in cands]
        failed += any(w in b for b in bases for w in (" failed (", "no mapping", "unknown"))
        fallbacks.update(
            w for b in bases for w in ("missing", "abstained", "invalid") if b.endswith(w)
        )
    bins: list[list[tuple[float, bool]]] = [[] for _ in range(10)]
    for v, y in pooled:
        bins[min(9, int(v * 10))].append((v, y))
    ece = sum(
        len(b)
        / len(pooled)
        * abs(statistics.fmean(v for v, _ in b) - statistics.fmean(y for _, y in b))
        for b in bins
        if b
    )
    return {
        "auroc_within": statistics.fmean(within),
        "auroc_pooled": auroc(pooled),
        "p_at_5": statistics.fmean(p5),
        "brier": statistics.fmean((v - y) ** 2 for v, y in pooled),
        "ece": ece,
        "failed_requests": failed,
        "fallbacks": dict(fallbacks),
    }


def mean_over(tasks: Tasks, arms: list[str], key: str = "line_recall") -> dict[str, float]:
    return {
        b: statistics.fmean(statistics.fmean(t["arms"][a][b][key] for a in arms) for t in tasks)
        for b in BUDGETS
    }


def agreement(tasks: Tasks, a: str, b: str) -> float | None:
    rhos = []
    for t in tasks:
        x = [c[0] for c in t["arms"][a]["candidates"]]
        y = [c[0] for c in t["arms"][b]["candidates"]]
        if (r := spearman(x, y)) is not None:
            rhos.append(r)
    return statistics.median(rhos) if rhos else None


def controls(out: Path, tasks: Tasks) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    names = list(tasks[0]["arms"])
    groups = {g: [g] for g in ("heuristic", "oracle_order", "oracle_pick")}
    groups["random (mean of 5)"] = [f"random_s{s}" for s in SEEDS]
    if "random~order_s0" in names:  # posthoc.py's control for ``~order``
        groups["random~order (mean of 5)"] = [f"random~order_s{s}" for s in SEEDS]
    perms = sorted({n[5:].rsplit("_s", 1)[0] for n in names if n.startswith("perm-")})
    groups |= {f"perm-{b} (mean of 5)": [f"perm-{b}_s{s}" for s in SEEDS] for b in perms}
    models = [  # ``~`` arms come from posthoc.py: diagnostics, not gated
        n for n in names if n.split("-")[0].split("~")[0] in RENDERERS and not n.startswith("perm-")
    ]
    groups |= {m: [m] for m in models}
    head = "".join(f"{'LR@' + b:>9}" for b in BUDGETS)
    print(
        f"{'arm':30}{head}{'share@4096':>11}{'AUROC/task':>11}{'AUROC':>7}"
        f"{'P@5':>6}{'Brier':>7}{'ECE':>6}{'fail':>5}"
    )
    table = {}
    for g, arms in groups.items():
        r, q = mean_over(tasks, arms), quality(tasks, arms[0])
        share = mean_over(tasks, arms, "gold_unit_share")["4096"]
        table[g] = {"recall": r, **q}
        print(
            f"{g:30}" + "".join(f"{r[b]:9.4f}" for b in BUDGETS) + f"{share:11.4f}"
            f"{q['auroc_within']:11.3f}{q['auroc_pooled']:7.3f}{q['p_at_5']:6.2f}"
            f"{q['brier']:7.3f}{q['ece']:6.3f}{q['failed_requests']:5d}"
        )

    per: dict[str, list[dict[str, Any]]] = {}
    for c in json.loads((out / "costs.json").read_text()):
        per.setdefault(c["renderer"], []).append(c)
    print(
        "\nrendered tokens per request, tiktoken estimate (JEV default 64k/request, 32k/question)"
    )
    print(
        f"{'renderer':30}{'o200k med':>10}{'max':>8}{'cl100k med':>11}{'per-q med':>10}{'fits':>7}"
    )
    for name, cs in per.items():
        o = [c["o200k_base"] for c in cs]
        print(
            f"{name:30}{statistics.median(o):10.0f}{max(o):8d}"
            f"{statistics.median(c['cl100k_base'] for c in cs):11.0f}"
            f"{statistics.median(c['o200k_base_per_question'] for c in cs):10.0f}"
            f"{sum(c['fits_default'] for c in cs):>4d}/{len(cs)}"
        )

    trunc = json.loads((out / "truncation.json").read_text())
    beyond = [t["task"] for t in trunc if "beyond" in t["holders"].values()]
    held = Counter(v for t in trunc for v in t["holders"].values())
    other: Counter[str] = Counter()
    for t in trunc:
        other.update(t["others"])
    print(f"\nquery truncation: {sum(t['clipped'] for t in trunc)} of {len(trunc)} queries clipped")
    print(f"gold holders named: {dict(held)}; other candidates: {dict(other)}")
    print(f"tasks with a holder named only beyond the clip: {len(beyond)} {beyond}")
    print("full-query diagnostic " + ("RUNS" if len(beyond) >= 2 else "does not run") + " (>= 2)")
    return table, per, models


def verdict(results: dict[str, Any], table: dict[str, Any], per: dict[str, Any]) -> None:
    tasks, base = results["tasks"], table["heuristic"]["recall"]
    gains = {m: table[m]["recall"]["4096"] - base["4096"] for m in RENDERERS if m in table}
    for name, data in results["recorded"].items():
        reqs = list(data["requests"].values())
        lat = [r["latency_s"] for r in reqs]
        billed = statistics.fmean(r.get("input_tokens") or 0 for r in reqs)
        gain = table[name]["recall"]["4096"] - base["4096"] if name in table else 0.0
        point = f"{billed / (100 * gain):.0f}" if gain > 0 else "no improvement"
        print(
            f"{name}: latency median {statistics.median(lat):.2f} s, max {max(lat):.2f} s; "
            f"billed input tokens/request {billed:.0f} (${billed * 0.042e-6:.5f}); "
            f"tokens/request per +1 recall point: {point}"
        )
    if not gains:
        print("\nNO MODEL ARMS: the gate is not evaluable (harness and controls only)")
        return
    best = max(gains.values())
    tokens = {m: statistics.median(c["o200k_base"] for c in per[f"ds1-{m}-v1"]) for m in gains}
    gate = min((m for m in gains if gains[m] >= best - 0.010), key=lambda m: tokens[m])
    g, t, norank = table[gate], gains[gate], table.get(f"{gate}-norank")
    noise = max(
        table["random (mean of 5)"]["recall"]["4096"],
        table[f"perm-{gate} (mean of 5)"]["recall"]["4096"],
    )
    checks = {
        "1 recall": t >= 0.030
        and g["recall"]["1024"] >= base["1024"]
        and all(g["recall"][b] >= base[b] - 0.005 for b in BUDGETS),
        "2 controls": g["recall"]["4096"] - noise >= 0.030,
        "3 AUROC": g["auroc_within"] >= max(0.70, table["heuristic"]["auroc_within"] + 0.05),
        "4 rank": norank is not None
        and norank["recall"]["4096"] - base["4096"] >= 0.5 * t
        and norank["auroc_within"] >= 0.65,
    }
    ops = g["failed_requests"] <= 1
    for ctl in ("repeat", "reversed"):
        arm = f"{gate}-{ctl}"
        rho = agreement(tasks, gate, arm) if arm in table else None
        drift = abs(table[arm]["recall"]["4096"] - g["recall"]["4096"]) if arm in table else 1.0
        ops = ops and rho is not None and rho >= 0.90 and drift <= 0.010
        print(f"{ctl}: median within-task Spearman {rho}, |ΔΔ@4096| {drift:.4f}")
    checks["5 operational"] = ops
    print(f"\ngate renderer {gate}: Δ@4096 {t:+.4f}, {tokens[gate]:.0f} o200k tokens/request")
    for k, ok in checks.items():
        print(f"  {k}: {'pass' if ok else 'FAIL'}")
    print(f"{results['split'].upper()} GATE: {'PASS' if all(checks.values()) else 'FAIL'}")


def main(out: Path) -> None:
    results = json.loads((out / "results.json").read_text())
    print(f"split {results['split']}: {len(results['tasks'])} tasks with verified gold\n")
    table, per, _ = controls(out, results["tasks"])
    print()
    verdict(results, table, per)


if __name__ == "__main__":
    main(Path(sys.argv[1]))
