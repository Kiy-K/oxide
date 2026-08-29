#!/usr/bin/env python3
"""Aggregate docs/evals/phase-2.3/results.jsonl into per-variant/bucket tables."""
import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results.jsonl"
VARIANTS = ["E0", "E1", "E2", "E3", "E4"]
BUCKETS = ["A", "C"]


def load():
    return [json.loads(l) for l in RESULTS.read_text().splitlines() if l.strip()]


def pct(n, d):
    return f"{100*n/d:.0f}%" if d else "n/a"


def main():
    recs = load()
    print(f"{len(recs)} total records\n")

    timeouts = [r for r in recs if r["timed_out"]]
    dead = [r for r in recs if not r["timed_out"] and r["dead_run"]]
    valid = [r for r in recs if not r["timed_out"] and not r["dead_run"]]
    print(f"infra timeouts: {len(timeouts)}   dead runs: {len(dead)}   valid: {len(valid)}\n")

    print("=== dead-run rate by variant ===")
    for v in VARIANTS:
        cell = [r for r in recs if r["variant"] == v and not r["timed_out"]]
        d = sum(1 for r in cell if r["dead_run"])
        print(f"{v}: {d}/{len(cell)} ({pct(d, len(cell))})")
    print()

    print("=== Bucket-A activation rate by variant (valid runs) ===")
    for v in VARIANTS:
        cell = [r for r in valid if r["variant"] == v and r["bucket"] == "A"]
        used = sum(1 for r in cell if r["used_oxide"])
        print(f"{v}: {used}/{len(cell)} ({pct(used, len(cell))})")
    print()

    print("=== Bucket-A activation rate by variant x task ===")
    tasks = sorted({r["task"] for r in valid if r["bucket"] == "A"})
    header = "var  " + " ".join(f"{t:>10}" for t in tasks)
    print(header)
    for v in VARIANTS:
        row = [v]
        for t in tasks:
            cell = [r for r in valid if r["variant"] == v and r["task"] == t]
            used = sum(1 for r in cell if r["used_oxide"])
            row.append(f"{used}/{len(cell)}")
        print(f"{row[0]:<4} " + " ".join(f"{x:>10}" for x in row[1:]))
    print()

    print("=== Bucket-C unnecessary activation by variant ===")
    for v in VARIANTS:
        cell = [r for r in valid if r["variant"] == v and r["bucket"] == "C"]
        used = sum(1 for r in cell if r["used_oxide"])
        print(f"{v}: {used}/{len(cell)} ({pct(used, len(cell))})")
    print()

    print("=== first-action-is-oxide + late-activation, Bucket A, by variant ===")
    for v in VARIANTS:
        cell = [r for r in valid if r["variant"] == v and r["bucket"] == "A"]
        first_oxide = sum(1 for r in cell if r["first_action_is_oxide"])
        late = sum(1 for r in cell if r["late_activation"])
        used = sum(1 for r in cell if r["used_oxide"])
        print(f"{v}: n={len(cell)} used={used} first_action_oxide={first_oxide} "
              f"late_activation={late} (of {used} that used oxide)")
    print()

    print("=== tool-call discipline (mean per valid run), Bucket A, by variant ===")
    for v in VARIANTS:
        cell = [r for r in valid if r["variant"] == v and r["bucket"] == "A"]
        if not cell:
            continue
        n = len(cell)
        ctx = sum(r["oxide_context_calls"] for r in cell) / n
        srch = sum(r["oxide_search_calls"] for r in cell) / n
        native = sum(r["native_explore_calls"] for r in cell) / n
        wall = sum(r["wall_s"] for r in cell) / n
        print(f"{v}: n={n} oxide_context={ctx:.2f} oxide_search={srch:.2f} native={native:.2f} wall={wall:.1f}s")
    print()

    print("=== gate check: Bucket-A activation vs Bucket-C false-positive, by variant ===")
    for v in VARIANTS:
        a_cell = [r for r in valid if r["variant"] == v and r["bucket"] == "A"]
        c_cell = [r for r in valid if r["variant"] == v and r["bucket"] == "C"]
        a_rate = sum(1 for r in a_cell if r["used_oxide"]) / len(a_cell) if a_cell else 0
        c_rate = sum(1 for r in c_cell if r["used_oxide"]) / len(c_cell) if c_cell else 0
        print(f"{v}: Bucket-A activation {a_rate:.0%}, Bucket-C false-positive {c_rate:.0%}")


if __name__ == "__main__":
    main()
