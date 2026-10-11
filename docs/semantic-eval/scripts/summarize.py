#!/usr/bin/env python3
"""Aggregate semantic_pilot.rs output (docs/semantic-eval/pilot-<space>.json).

usage: summarize.py docs/semantic-eval/pilot-lfm.json

Macro means over tasks, except gold reached by routing, which is pooled
(routed gold symbols / gold symbols). Entry metrics are over verified gold *symbols* in
the CandidateSet at 1,024 units; bundle recall is the Phase 4
required_symbol_recall over all verified gold; packed gained/lost count gold
symbols covered by the 1,024-unit bundle with a configuration but not with
lexical alone (and the reverse).
"""
import json
import statistics
import sys

run = json.load(open(sys.argv[1]))
tasks = run["tasks"]
mean = lambda xs: statistics.fmean(xs) if xs else float("nan")
labels = list(tasks[0]["configs"])

print(f"space {run['space']}  tasks {len(tasks)}  gold symbols {sum(t['gold_symbols'] for t in tasks)}"
      f" (embedded {sum(t['gold_symbols_embedded'] for t in tasks)})")
print("\nchannel alone (top 100):")
for ch in ("semantic", "control"):
    c = [t["channel"][ch] for t in tasks]
    u = sum(t["channel"]["unique_gold_at_20"][ch] for t in tasks)
    print(f"  {ch:9} R@10 {mean([x['r10'] for x in c]):.3f} R@20 {mean([x['r20'] for x in c]):.3f}"
          f" R@100 {mean([x['r100'] for x in c]):.3f} MRR {mean([x['mrr'] for x in c]):.3f}"
          f"  unique gold@20 vs lexical {u}")

print("\nconfig            entries R@10  R@20  R@40   MRR  routed-gold  recall@256 @1024 @4096  packed+ packed-")
for label in labels:
    rows = [t["configs"][label] for t in tasks]
    e = [r["entries"] for r in rows]
    routed = sum(r["routed_gold"] for r in rows) / max(1, sum(t["gold_symbols"] for t in tasks))
    rec = [mean([r[f"@{b}"]["required_symbol_recall"] for r in rows]) for b in (256, 1024, 4096)]
    d = [t["packed_deltas_at_1024"].get(label) for t in tasks]
    plus = sum(x["packed_gold_gained"] for x in d if x)
    minus = sum(x["packed_gold_lost"] for x in d if x)
    print(f"  {label:17} {mean([x['r10'] for x in e]):.3f} {mean([x['r20'] for x in e]):.3f}"
          f" {mean([x['r40'] for x in e]):.3f} {mean([x['mrr'] for x in e]):.3f}   {routed:.3f}"
          f"        {rec[0]:.3f} {rec[1]:.3f} {rec[2]:.3f}   {plus:5} {minus:6}")

print("\nper-task bundle recall @1024 (lexical -> lexical+semantic, rrf, lexical+control):")
for t in tasks:
    r = {l: t["configs"][l]["@1024"]["required_symbol_recall"] for l in labels}
    print(f"  {t['task'][-8:]} {r['lexical']:.3f} -> {r['lexical+semantic']:.3f} {r['rrf']:.3f} {r['lexical+control']:.3f}")

print("\ncost per task:")
for t in tasks:
    c = t["cost"]
    print(f"  {t['task'][-8:]} docs {c['documents']} embedded {c['embedded']} cached {c['cached']}"
          f" embed {c['seconds_embedding']}s query cold {c['query_ms_cold']:.0f}ms"
          f" warm p50 {statistics.median(c['query_ms_warm']):.0f}ms"
          f" lexical-during-build {c['lexical_context_ms_during_build']:.0f}ms"
          f" peak_rss {t['peak_rss_kib'] // 1024}MiB")
lat = {l: mean([t["configs"][l]["@1024"]["latency_ms"] for t in tasks]) for l in labels}
print("\ncontext latency @1024 (ms, excludes query embedding):", {k: round(v) for k, v in lat.items()})
