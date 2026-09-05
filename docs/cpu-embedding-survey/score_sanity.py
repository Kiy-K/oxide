#!/usr/bin/env python3
"""Stage 4 cheap retrieval sanity: hit@5 + MRR over unique files, per mode.

Assumes the corpus at --corpus is ALREADY indexed with the embedder under test.
Runs `oxide search --json` per query, ranks unique files by first appearance,
and scores whether the gold file lands in the top 5.
"""
import json
import os
import subprocess
import sys

OXIDE = os.environ.get("OXIDE_BIN", "./target/release/oxide")


def ranked_files(corpus, query, mode, limit=25):
    out = subprocess.run(
        [OXIDE, "search", "--path", corpus, "--json", "--mode", mode, "--limit", str(limit), query],
        capture_output=True, text=True, timeout=300,
    )
    if out.returncode != 0:
        raise RuntimeError(f"oxide search failed ({mode}): {out.stderr[:400]}")
    data = json.loads(out.stdout)
    hits = data if isinstance(data, list) else data.get("hits", data.get("results", []))
    seen, files = set(), []
    for h in hits:
        f = h.get("file")
        if f and f not in seen:
            seen.add(f)
            files.append(f)
    return files


def main():
    corpus = sys.argv[1]
    spec = json.load(open(sys.argv[2]))
    label = sys.argv[3]
    results = {}
    for mode in ("semantic", "hybrid"):
        hits5, rr, misses = 0, 0.0, []
        for item in spec["queries"]:
            files = ranked_files(corpus, item["q"], mode)
            gold = item["gold"]
            rank = files.index(gold) + 1 if gold in files else None
            if rank and rank <= 5:
                hits5 += 1
            if rank:
                rr += 1.0 / rank
            else:
                misses.append((item["q"][:45], "not in top-25"))
            if rank and rank > 5:
                misses.append((item["q"][:45], f"rank {rank}"))
        n = len(spec["queries"])
        results[mode] = {
            "hit_at_5": round(hits5 / n, 3),
            "mrr": round(rr / n, 3),
            "n": n,
            "misses": misses,
        }
    print(json.dumps({"profile": label, **results}, indent=2))


if __name__ == "__main__":
    main()
