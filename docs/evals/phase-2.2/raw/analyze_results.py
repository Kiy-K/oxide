#!/usr/bin/env python3
"""Aggregate docs/evals/phase-2.2/results.jsonl into per-condition/bucket tables."""
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results.jsonl"
LOG_DIR = ROOT / "logs"
CONDITIONS = ["A", "B", "C", "D", "E"]
BUCKETS = ["A", "B", "C"]


def is_dead_run(log_name: str) -> bool:
    """opencode client bug (see failures.md): the model's first tool call is
    sometimes `read(filePath="/")`, which the permission layer auto-denies,
    and the session then ends with no further steps and no answer. This is
    a client/transport failure, not a real "did nothing" activation signal
    -- detected as exactly one tool_use event, which errored."""
    path = LOG_DIR / log_name
    if not path.exists():
        return False
    tool_events = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        part = d.get("part", {})
        if part.get("type") == "tool":
            tool_events.append(part)
    if len(tool_events) != 1:
        return False
    return tool_events[0].get("state", {}).get("status") == "error"


def load():
    recs = [json.loads(l) for l in RESULTS.read_text().splitlines() if l.strip()]
    for r in recs:
        r["dead_run"] = (not r["timed_out"]) and is_dead_run(r["log"])
    return recs


def pct(n, d):
    return f"{100*n/d:.0f}%" if d else "n/a"


def main():
    recs = load()
    print(f"{len(recs)} total records\n")

    infra = [r for r in recs if r["timed_out"]]
    dead = [r for r in recs if r["dead_run"]]
    valid = [r for r in recs if not r["timed_out"] and not r["dead_run"]]
    print(f"infrastructure failures (timed out after retry): {len(infra)}")
    for r in infra:
        print(f"  {r['task']} {r['condition']} r{r['rep']}")
    print(f"\ndead runs (single tool call, permission-denied read('/'), session ended): "
          f"{len(dead)}/{len(recs)} ({pct(len(dead), len(recs))})")
    by_cond = defaultdict(int)
    by_cond_total = defaultdict(int)
    for r in recs:
        if not r["timed_out"]:
            by_cond_total[r["condition"]] += 1
            if r["dead_run"]:
                by_cond[r["condition"]] += 1
    for c in CONDITIONS:
        print(f"  {c}: {by_cond[c]}/{by_cond_total[c]} ({pct(by_cond[c], by_cond_total[c])})")
    print(f"\n{len(valid)} valid runs remain after excluding timeouts + dead runs\n")

    # Activation rates per condition x bucket
    print("=== activation rate (used_oxide) by condition x bucket, n=valid runs ===")
    header = "cond " + " ".join(f"bucket{b:>6}" for b in BUCKETS)
    print(header)
    for c in CONDITIONS:
        row = [c]
        for b in BUCKETS:
            cell = [r for r in valid if r["condition"] == c and r["bucket"] == b]
            used = sum(1 for r in cell if r["used_oxide"])
            row.append(f"{used}/{len(cell)} ({pct(used, len(cell))})")
        print(f"{row[0]:<4} " + " ".join(f"{x:>12}" for x in row[1:]))
    print()

    print("=== appropriate / missed / unnecessary, by condition (all buckets) ===")
    for c in CONDITIONS:
        cell = [r for r in valid if r["condition"] == c]
        appropriate = sum(1 for r in cell if r["appropriate"])
        missed = sum(1 for r in cell if r["missed"])
        unnecessary = sum(1 for r in cell if r["unnecessary"])
        print(f"{c}: appropriate {appropriate}/{len(cell)} ({pct(appropriate, len(cell))})  "
              f"missed {missed}  unnecessary {unnecessary}")
    print()

    print("=== bucket A only: appropriate + missed, by condition ===")
    for c in CONDITIONS:
        cell = [r for r in valid if r["condition"] == c and r["bucket"] == "A"]
        appropriate = sum(1 for r in cell if r["appropriate"])
        missed = sum(1 for r in cell if r["missed"])
        print(f"{c}: n={len(cell)} appropriate={appropriate} ({pct(appropriate,len(cell))}) missed={missed}")
    print()

    print("=== bucket C only: unnecessary activation, by condition ===")
    for c in CONDITIONS:
        cell = [r for r in valid if r["condition"] == c and r["bucket"] == "C"]
        unnecessary = sum(1 for r in cell if r["unnecessary"])
        print(f"{c}: n={len(cell)} unnecessary={unnecessary} ({pct(unnecessary,len(cell))})")
    print()

    print("=== first repository-discovery action, by condition (bucket A tasks) ===")
    for c in CONDITIONS:
        cell = [r for r in valid if r["condition"] == c and r["bucket"] == "A"]
        counts = defaultdict(int)
        for r in cell:
            counts[r["first_action"]] += 1
        print(f"{c}: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    print()

    print("=== tool-call discipline (mean per run), by condition ===")
    for c in CONDITIONS:
        cell = [r for r in valid if r["condition"] == c]
        if not cell:
            continue
        n = len(cell)
        ctx = sum(r["oxide_context_calls"] for r in cell) / n
        srch = sum(r["oxide_search_calls"] for r in cell) / n
        native = sum(r["native_explore_calls"] for r in cell) / n
        total = sum(r["total_tool_calls"] for r in cell) / n
        wall = sum(r["wall_s"] for r in cell) / n
        tok = sum(r["tokens_total"] for r in cell) / n
        print(f"{c}: n={n} oxide_context={ctx:.2f} oxide_search={srch:.2f} native={native:.2f} "
              f"total_calls={total:.1f} wall={wall:.1f}s tokens={tok:.0f}")
    print()

    print("=== search-role: runs using BOTH context and search (any bucket) ===")
    for c in CONDITIONS:
        cell = [r for r in valid if r["condition"] == c]
        both = sum(1 for r in cell if r["oxide_context_calls"] > 0 and r["oxide_search_calls"] > 0)
        ctx_only = sum(1 for r in cell if r["oxide_context_calls"] > 0 and r["oxide_search_calls"] == 0)
        search_only = sum(1 for r in cell if r["oxide_search_calls"] > 0 and r["oxide_context_calls"] == 0)
        print(f"{c}: both={both} context_only={ctx_only} search_only={search_only}")


if __name__ == "__main__":
    main()
