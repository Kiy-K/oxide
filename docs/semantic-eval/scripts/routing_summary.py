"""Summarize docs/semantic-eval/routing.json and evaluate the preregistered gates.

Usage: python3 routing_summary.py routing.json
"""
import json
import random
import statistics
import sys

BUDGETS = ["@256", "@1024", "@4096"]


def boot_lower(diffs, n=10000, seed=1):
    rng = random.Random(seed)
    means = sorted(
        statistics.fmean(rng.choice(diffs) for _ in diffs) for _ in range(n)
    )
    return means[int(0.025 * n)], means[int(0.975 * n) - 1]


def table(tasks, labels):
    print("| config | tasks | recall @256 / @1024 / @4096 | routed cov @1024 | selection loss @1024 | routed gold (pooled) | packed gold @1024 gained/lost | entries | route p50 ms | ctx p50 ms @1024 |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    for label in labels:
        rows = [t for t in tasks if label in t["configs"]]
        if not rows:
            continue
        c = [t["configs"][label] for t in rows]
        rec = [statistics.fmean(x[b]["required_symbol_recall"] for x in c) for b in BUDGETS]
        cov = statistics.fmean(x["@1024"]["routed_gold_coverage"] for x in c)
        loss = statistics.fmean(x["@1024"]["selection_loss"] for x in c)
        routed = sum(x["@1024"]["stages"]["routed"] for x in c)
        gold = sum(t["gold_symbols"] for t in rows)
        g = sum(t["packed_deltas_at_1024"][label]["packed_gold_gained"] for t in rows)
        l = sum(t["packed_deltas_at_1024"][label]["packed_gold_lost"] for t in rows)
        entries = statistics.fmean(x["entry_count"] for x in c)
        route = statistics.median(x["route"]["route_ms"] for x in c)
        ctx = statistics.median(x["@1024"]["latency_ms"] for x in c)
        print(f"| {label} | {len(rows)} | {rec[0]:.3f} / {rec[1]:.3f} / {rec[2]:.3f} | {cov:.3f} | {loss:.3f} | {routed}/{gold} | +{g} / -{l} | {entries:.1f} | {route:.1f} | {ctx:.1f} |")


def diff(tasks, a, b, budget, key="required_symbol_recall"):
    rows = [t for t in tasks if a in t["configs"] and b in t["configs"]]
    return [t["configs"][a][budget][key] - t["configs"][b][budget][key] for t in rows]


def main(path):
    data = json.load(open(path))
    tasks = data["tasks"]
    semantic = [t for t in tasks if "semantic" in t["configs"]]
    lexical_labels = ["lexical", "lexical/ch", "lexical/ppr60", "lexical/ppr1"]
    print(f"## Lexical-only, {len(tasks)} dev tasks\n")
    table(tasks, lexical_labels)
    print(f"\n## Semantic repositories, {len(semantic)} tasks\n")
    labels = list(semantic[0]["configs"]) if semantic else []
    table(semantic, labels)

    print("\n## Gates\n")
    for v in ["lexical/ppr60", "lexical/ppr1"]:
        d = diff(tasks, v, "lexical", "@1024")
        lo, hi = boot_lower(d)
        other = [statistics.fmean(diff(tasks, v, "lexical", b)) for b in ["@256", "@4096"]]
        cov = statistics.fmean(diff(tasks, v, "lexical", "@1024", "routed_gold_coverage"))
        print(f"- {v} vs lexical: recall@1024 {statistics.fmean(d):+.3f} [{lo:+.3f}, {hi:+.3f}]; @256 {other[0]:+.3f}, @4096 {other[1]:+.3f}; routed coverage {cov:+.3f}")
    if semantic:
        p = "interleave40/ch"
        means = {b: statistics.fmean(diff(semantic, p, "lexical", b)) for b in BUDGETS}
        g = sum(t["packed_deltas_at_1024"][p]["packed_gold_gained"] for t in semantic)
        l = sum(t["packed_deltas_at_1024"][p]["packed_gold_lost"] for t in semantic)
        ctl = statistics.fmean(diff(semantic, p, "interleave40-control/ch", "@1024"))
        print(f"- {p} vs lexical: " + ", ".join(f"{b} {m:+.3f}" for b, m in means.items()) + f"; packed +{g}/-{l}; vs control @1024 {ctl:+.3f}")
        for t in semantic:
            c = t["semantic_cost"]["channel"]
            print(f"  - {t['task'][-8:]}: query first {c['query_ms_first']:.0f} ms, warm {[round(x) for x in c['query_ms_warm']]}, embedded {c['embedded']}/{c['unique_texts']}, vectors {c['vectors_mib']:.1f} MiB")


if __name__ == "__main__":
    main(sys.argv[1])
