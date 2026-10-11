"""Tables and preregistered gates for docs/lexical-fields-eval/<split>.json.

Usage: python3 summary.py dev.json
"""
import json
import random
import statistics as st
import sys

LABELS = ["A", "A0", "B", "C", "C-rrf", "B512", "Bfull", "B-joined", "A0-qtf", "B-qtf", "C-qtf"]
BUDGETS = ["@256", "@1024", "@4096"]


def boot(diffs, n=10000, seed=1):
    rng = random.Random(seed)
    means = sorted(st.fmean(rng.choice(diffs) for _ in diffs) for _ in range(n))
    return means[int(0.025 * n)], means[int(0.975 * n) - 1]


def main(path):
    tasks = json.load(open(path))["tasks"]
    m = lambda xs: st.fmean(xs)
    print(f"{len(tasks)} tasks, {sum(t['gold'] for t in tasks)} gold ({sum(t['gold_symbols'] for t in tasks)} symbols)\n")
    print("| config | recall @256 / @1,024 / @4,096 | symbol recall @1,024 | entries R@10 / R@20 / MRR | channel R@100 | unique / lost gold vs A (top 20) | routed cov @1,024 | selection loss @1,024 | packing loss @1,024 | warm / cold ms (p50) |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    for l in LABELS:
        c = [t["configs"][l] for t in tasks]
        rec = [m(x[b]["required_symbol_recall"] for x in c) for b in BUDGETS]
        e = c[0]
        print(f"| {l} | {rec[0]:.3f} / {rec[1]:.3f} / {rec[2]:.3f} | {m(x['@1024']['symbol_recall'] for x in c):.3f} | "
              f"{m(x['entries']['r10'] for x in c):.3f} / {m(x['entries']['r20'] for x in c):.3f} / {m(x['entries']['mrr'] for x in c):.3f} | "
              f"{m(x['channel']['r100'] for x in c):.3f} | +{sum(x['unique_gold_vs_A'] for x in c)} / -{sum(x['lost_gold_vs_A'] for x in c)} | "
              f"{m(x['@1024']['routed_gold_coverage'] for x in c):.3f} | {m(x['@1024']['selection_loss'] for x in c):.3f} | {m(x['@1024']['packing_loss'] for x in c):.3f} | "
              f"{st.median(x['latency']['warm_ms'] for x in c):.1f} / {st.median(x['latency']['cold_ms'] for x in c):.1f} |")
    print("\n## Gates (vs A)\n")
    a_warm = st.median(t["configs"]["A"]["latency"]["warm_ms"] for t in tasks)
    a_index = st.median(t["cost"]["index_seconds"] for t in tasks)
    for l in ["B", "C", "A0", "C-rrf", "B512", "Bfull", "B-joined", "A0-qtf", "B-qtf", "C-qtf"]:
        d = {b: [t["configs"][l][b]["required_symbol_recall"] - t["configs"]["A"][b]["required_symbol_recall"] for t in tasks] for b in BUDGETS}
        lo, hi = boot(d["@1024"])
        cov = m(t["configs"][l]["@1024"]["routed_gold_coverage"] - t["configs"]["A"]["@1024"]["routed_gold_coverage"] for t in tasks)
        warm = st.median(t["configs"][l]["latency"]["warm_ms"] for t in tasks)
        g1 = m(d["@1024"]) >= 0.03 and lo > 0
        g2 = m(d["@256"]) >= -0.01 and m(d["@4096"]) >= -0.01 and cov >= -0.02
        g3 = warm <= max(2 * a_warm, a_warm + 50)
        print(f"- {l}: @1,024 {m(d['@1024']):+.3f} [{lo:+.3f}, {hi:+.3f}], @256 {m(d['@256']):+.3f}, @4,096 {m(d['@4096']):+.3f}, routed cov {cov:+.3f}, warm {warm:.1f} ms vs A {a_warm:.1f} -> gate1 {'pass' if g1 else 'FAIL'}, gate2 {'pass' if g2 else 'FAIL'}, gate3 latency {'pass' if g3 else 'FAIL'}")
    c = [t["cost"] for t in tasks]
    added = [(x["hydrate_ms"] + x["body_build_ms"]) / 1000 for x in c]
    print(f"\nCost: A index p50 {a_index:.1f} s; body hydrate+build p50 {st.median(added):.2f} s (max {max(added):.2f}); "
          f"meta build p50 {st.median(x['meta_build_ms'] for x in c):.0f} ms; RSS growth (all fields + body 2048) p50 {st.median(x['rss_growth_kib'] for x in c)/1024:.0f} MiB (max {max(x['rss_growth_kib'] for x in c)/1024:.0f}); "
          f"body index est p50 {st.median(x['body_index_bytes_2048'] for x in c)/2**20:.1f} MiB vs meta {st.median(x['meta_index_bytes'] for x in c)/2**20:.1f} MiB; store dir p50 {st.median(x['store_bytes'] for x in c)/2**20:.0f} MiB; peak RSS max {max(t['peak_rss_kib'] for t in tasks)/1024:.0f} MiB")
    b = [t["body"] for t in tasks]
    print(f"Body: symbols with body {sum(x['symbols_with_body'] for x in b)} of {sum(x['entities'] for x in b)} entities; median bytes p50 {st.median(x['body_bytes_median'] for x in b):.0f}; "
          f"truncated at 512/2048/65536: {m(x['truncated_share']['512'] for x in b):.2f}/{m(x['truncated_share']['2048'] for x in b):.2f}/{m(x['truncated_share']['65536'] for x in b):.3f}; "
          f"terms/body (2048) {m(x['body_terms_mean_2048'] for x in b):.0f}; query terms p50 {st.median(t['query_terms'] for t in tasks)}")


if __name__ == "__main__":
    main(sys.argv[1])
