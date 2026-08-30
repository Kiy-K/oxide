#!/usr/bin/env python3
"""Aggregate docs/evals/phase-3.1/results.jsonl into the tables used by
results.md / transport-selection.md / failures.md. Prints; does not write
markdown (kept separate so the write-up can quote exact numbers by hand and
add narrative around them, per this repo's existing phase reports)."""
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
RESULTS = ROOT / "docs/evals/phase-3.1/results.jsonl"


def load():
    return [json.loads(l) for l in RESULTS.read_text().splitlines() if l.strip()]


def pct(n, d):
    return "n/a" if d == 0 else f"{100*n/d:.0f}%"


def main():
    rows = load()
    nav = [r for r in rows if r["kind"] == "nav"]
    coding = [r for r in rows if r["kind"] == "coding"]

    print(f"total rows: {len(rows)} (nav={len(nav)}, coding={len(coding)})")
    print()

    print("=== infra failures (timed_out) ===")
    for cond in "ABCDE":
        n = [r for r in nav if r["condition"] == cond]
        t = sum(1 for r in n if r["timed_out"])
        print(f"  {cond}: {t}/{len(n)} nav timeouts")
    for cond in "ABCDE":
        c = [r for r in coding if r["condition"] == cond]
        t = sum(1 for r in c if r["timed_out"])
        print(f"  {cond}: {t}/{len(c)} coding timeouts")
    print()

    print("=== codegraph leakage check (should be 0 everywhere) ===")
    leaks = [r for r in rows if r.get("codegraph_seen")]
    print(f"  {len(leaks)} runs saw codegraph: {[ (r['task'],r['condition'],r['rep']) for r in leaks ]}")
    print()

    print("=== activation by bucket x condition (nav only, appropriate/missed/unnecessary) ===")
    by = defaultdict(list)
    for r in nav:
        if r["timed_out"]:
            continue
        by[(r["bucket"], r["condition"])].append(r)
    for bucket in "ABC":
        for cond in "ABCDE":
            grp = by.get((bucket, cond), [])
            if not grp:
                continue
            appropriate = sum(1 for r in grp if r["activation_appropriate"])
            used = sum(1 for r in grp if r["used_oxide"])
            print(f"  bucket={bucket} cond={cond} n={len(grp)} used_oxide={used}/{len(grp)} "
                  f"appropriate={appropriate}/{len(grp)} ({pct(appropriate,len(grp))})")
    print()

    print("=== transport distribution, condition E (the core question) ===")
    e_rows = [r for r in nav + coding if r["condition"] == "E" and not r["timed_out"]]
    dist = defaultdict(int)
    for r in e_rows:
        dist[r["transport"]] += 1
    for k, v in sorted(dist.items()):
        print(f"  {k}: {v}/{len(e_rows)} ({pct(v, len(e_rows))})")
    print()

    print("=== first_action distribution by condition (nav, bucket A only) ===")
    for cond in "ABCDE":
        grp = [r for r in nav if r["condition"] == cond and r["bucket"] == "A" and not r["timed_out"]]
        fa = defaultdict(int)
        for r in grp:
            fa[r["first_action"]] += 1
        print(f"  {cond}: {dict(fa)}")
    print()

    print("=== discovery efficiency: mean total_tool_calls, bucket A ===")
    for cond in "ABCDE":
        grp = [r for r in nav if r["condition"] == cond and r["bucket"] == "A" and not r["timed_out"]]
        if not grp:
            continue
        mean_calls = sum(r["total_tool_calls"] for r in grp) / len(grp)
        mean_native = sum(r["native_explore_calls"] for r in grp) / len(grp)
        print(f"  {cond}: n={len(grp)} mean_total_calls={mean_calls:.1f} mean_native_explore={mean_native:.1f}")
    print()

    print("=== coding outcome ===")
    for cond in "ABCDE":
        grp = [r for r in coding if r["condition"] == cond]
        for task in sorted(set(r["task"] for r in grp)):
            tg = [r for r in grp if r["task"] == task]
            succ = sum(1 for r in tg if r["outcome"] == "success")
            infra = sum(1 for r in tg if r["outcome"] == "infrastructure_failure")
            print(f"  {task} {cond}: {succ}/{len(tg)} success, {infra} infra_failure, "
                  f"transports={[r['transport'] for r in tg]}")
    print()

    print("=== token totals (opencode step_finish, whole-session, not OXIDE-only) ===")
    for cond in "ABCDE":
        grp = [r for r in nav if r["condition"] == cond and not r["timed_out"] and r.get("tokens_total")]
        if not grp:
            continue
        mean_tok = sum(r["tokens_total"] for r in grp) / len(grp)
        print(f"  {cond}: n={len(grp)} mean_tokens_total={mean_tok:.0f}")


if __name__ == "__main__":
    main()
