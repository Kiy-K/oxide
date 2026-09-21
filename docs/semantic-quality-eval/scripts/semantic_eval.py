#!/usr/bin/env python3
"""Score semantic-channel challengers against the frozen baseline.

usage: semantic_eval.py <tasks.jsonl> <baseline-dump.jsonl> [<name>=<dump.jsonl> ...] [--md]

Every dump comes from `examples/semantic_variant.rs` (or `fusion_dump.rs` for
the baseline): the production BM25 channel, the variant's semantic top-200,
the production RRF fusion of the two, and the production context pack. Metric
definitions are the ones in docs/ranking-fusion-eval/scripts/fusion_eval.py
(Recall@K, nDCG@10, MRR at symbol granularity; loss partition at K=10:
route = gold absent from both channels, ordering = present but outside the
fused top-10, allocation = in top-10 but not in the pack). Deltas vs the
baseline carry a paired bootstrap 95% CI (2000 resamples, seed 0).

Strata: `ident` tasks mention a gold symbol's own name in the query;
`desc` tasks do not — the description-style queries the semantic channel
is supposed to serve.
"""
import json, math, random, statistics, sys

tasks = {t["id"]: t for t in map(json.loads, open(sys.argv[1]))}
args = [a for a in sys.argv[2:] if not a.startswith("--")]
md = "--md" in sys.argv
runs = [("baseline", args[0])] + [tuple(a.split("=", 1)) for a in args[1:]]


def metrics(ranked, gold):
    gold = set(gold)
    r = {}
    for k in (5, 10, 20):
        r[f"R@{k}"] = len(gold & set(ranked[:k])) / len(gold)
    dcg = sum(1 / math.log2(i + 2) for i, sid in enumerate(ranked[:10]) if sid in gold)
    idcg = sum(1 / math.log2(i + 2) for i in range(min(len(gold), 10)))
    r["nDCG@10"] = dcg / idcg if idcg else 0.0
    r["MRR"] = next((1 / (i + 1) for i, sid in enumerate(ranked) if sid in gold), 0.0)
    return r


def stratum(t):
    q = t["query"].lower()
    names = {g.split("#", 1)[1].split(".")[-1].split("::")[-1].lower() for g in t["gold"]}
    return "ident" if any(len(n) > 2 and n in q for n in names) else "desc"


def load(path):
    out = {}
    for line in open(path):
        if not line.strip():
            continue
        rec = json.loads(line)
        t = tasks[rec["id"]]
        gold = set(t["gold"])
        lex = [sid for sid, _ in rec["lexical"] if sid]
        sem = [sid for sid, _ in rec["semantic"] if sid]
        fused = [sid for sid, _, _ in rec["fused"]]
        pack_ids = [i["id"] for i in rec["pack"]["items"]]
        row = {"id": rec["id"], "repo": t["repo"], "stratum": stratum(t)}
        for k in (10, 50, 200):
            row[f"sem R@{k}"] = len(gold & set(sem[:k])) / len(gold)
        row["lex R@200"] = len(gold & set(lex[:200])) / len(gold)
        row["union R@200"] = len(gold & (set(lex[:200]) | set(sem[:200]))) / len(gold)
        row.update({"fused " + k: v for k, v in metrics(fused, gold).items()})
        row["sem-only nDCG@10"] = metrics(sem, gold)["nDCG@10"]
        in_channels = bool(gold & (set(lex) | set(sem)))
        row["loss"] = ("route" if not in_channels else "ordering" if not gold & set(fused[:10])
                       else "allocation" if not gold & set(pack_ids) else "hit")
        items = rec["pack"]["items"]
        row["gold_in_pack"] = 1.0 if gold & set(pack_ids) else 0.0
        row["used"] = rec["pack"]["used_tokens"]
        row["rel_tok"] = sum(i["est_tokens"] for i in items if i["id"] in gold)
        row["timing"] = rec.get("timing_ms", {})
        out[rec["id"]] = row
    return out


data = {name: load(path) for name, path in runs}
ids = sorted(set.intersection(*(set(d) for d in data.values())))
if any(len(d) != len(ids) for d in data.values()):
    print(f"note: scoring the {len(ids)} tasks present in every dump", file=sys.stderr)
base = data["baseline"]
KEYS = ["sem R@10", "sem R@50", "sem R@200", "union R@200", "fused R@5", "fused R@10", "fused R@20",
        "fused nDCG@10", "fused MRR", "gold_in_pack"]


def mean(rows, key):
    return statistics.fmean(r[key] for r in rows) if rows else 0.0


def ci(rows_a, rows_b, key, n=2000):
    rng = random.Random(0)
    d = [a[key] - b[key] for a, b in zip(rows_a, rows_b)]
    if not d:
        return (0.0, 0.0)
    bs = sorted(statistics.fmean(rng.choices(d, k=len(d))) for _ in range(n))
    return bs[int(0.025 * n)], bs[int(0.975 * n) - 1]


def table(sel, title):
    rows = []
    for name, d in data.items():
        r = [d[i] for i in ids if sel(d[i])]
        b = [base[i] for i in ids if sel(base[i])]
        if not r:
            continue
        cells = [name, str(len(r))] + [f"{mean(r, k):.3f}" for k in KEYS]
        lo, hi = ci(r, b, "fused nDCG@10")
        cells.append("—" if name == "baseline" else f"{mean(r, 'fused nDCG@10') - mean(b, 'fused nDCG@10'):+.3f} [{lo:+.3f}, {hi:+.3f}]")
        lo, hi = ci(r, b, "sem R@50")
        cells.append("—" if name == "baseline" else f"{mean(r, 'sem R@50') - mean(b, 'sem R@50'):+.3f} [{lo:+.3f}, {hi:+.3f}]")
        used = sum(x["used"] for x in r)
        cells.append(f"{1000 * sum(x['rel_tok'] for x in r) / max(1, used):.1f}")
        losses = {k: sum(1 for x in r if x["loss"] == k) for k in ("route", "ordering", "allocation", "hit")}
        cells.append("/".join(str(losses[k]) for k in ("route", "ordering", "allocation", "hit")))
        repos = sorted({x["repo"] for x in r})
        cells.append(f"{statistics.fmean(mean([x for x in r if x['repo'] == rp], 'fused R@10') for rp in repos):.3f}")
        rows.append(cells)
    hdr = ["variant", "n"] + KEYS + ["ΔnDCG@10 [95% CI]", "Δsem R@50 [95% CI]", "rel tok/1k", "route/order/alloc/hit", "macro R@10"]
    if md:
        print(f"\n### {title}\n")
        print("| " + " | ".join(hdr) + " |")
        print("| " + " | ".join("---" if i == 0 else "---:" for i in range(len(hdr))) + " |")
        for r in rows:
            print("| " + " | ".join(r) + " |")
    else:
        print(f"\n== {title}")
        print("\t".join(hdr))
        for r in rows:
            print("\t".join(r))


table(lambda r: True, f"all tasks (n={len(ids)})")
table(lambda r: r["stratum"] == "desc", "description-style queries (no gold symbol name in the query)")
table(lambda r: r["stratum"] == "ident", "identifier-bearing queries")

# per-repo fused R@10 and semantic R@50
repos = sorted({base[i]["repo"] for i in ids})
if md:
    print("\n### Per repository (fused R@10 / semantic R@50)\n")
    print("| variant | " + " | ".join(f"{rp} (n={sum(1 for i in ids if base[i]['repo']==rp)})" for rp in repos) + " |")
    print("| --- | " + " | ".join("---:" for _ in repos) + " |")
    for name, d in data.items():
        print(f"| {name} | " + " | ".join(
            f"{mean([d[i] for i in ids if d[i]['repo']==rp], 'fused R@10'):.2f} / {mean([d[i] for i in ids if d[i]['repo']==rp], 'sem R@50'):.2f}"
            for rp in repos) + " |")
    print("\n### Query-path timing (ms, median over tasks; in-process, warm model)\n")
    print("| variant | embed_query | vector scan | hybrid search | context |")
    print("| --- | ---: | ---: | ---: | ---: |")
    for name, d in data.items():
        tm = [d[i]["timing"] for i in ids if d[i]["timing"]]
        if tm:
            print(f"| {name} | " + " | ".join(f"{statistics.median(t[k] for t in tm):.1f}" for k in ("embed_query", "scan", "search", "context")) + " |")
