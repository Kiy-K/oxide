#!/usr/bin/env python3
"""Ranking metrics over the pinned Tier A set: Recall@K, nDCG@10, MRR,
first-useful-hit, tokens. Read-only vs OXIDE."""
import json
import math
import os
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, "/home/khoi/Projects/oxide/scripts/agent_eval")
import contextbench_run as cb  # noqa: E402

ROOT = cb.ROOT
# Fail fast: partial embedder env silently re-labels vectors (http:@…) and
# wipes/rebuilds indexes under an incomparable provider identity.
ENV = {
    "OXIDE_EMBED_URL": os.environ.get("OXIDE_EMBED_URL", ""),
    "OXIDE_EMBED_MODEL": os.environ.get("OXIDE_EMBED_MODEL", ""),
}
assert ENV["OXIDE_EMBED_URL"], "set OXIDE_EMBED_URL"
assert ENV["OXIDE_EMBED_MODEL"] == "qwen3-Q8_0", (
    "set OXIDE_EMBED_MODEL=qwen3-Q8_0 — a different label wipes embeddings"
)
import json as _json
import urllib.request
_req = urllib.request.Request(
    ENV["OXIDE_EMBED_URL"].replace("/embeddings", "/embeddings"),
    data=_json.dumps({"input": "liveness"}).encode(),
    headers={"Content-Type": "application/json"},
)
try:
    urllib.request.urlopen(_req, timeout=10)
except Exception as e:  # noqa: BLE001
    raise SystemExit(f"embedder not answering at {ENV['OXIDE_EMBED_URL']}: {e}")
PIN = ROOT / "eval-agent/results/tier_a_instances.txt"
ALLOW = {i.strip() for i in PIN.read_text().splitlines() if i.strip()}
CONDITIONS = ["lexical", "vec", "hybrid", "budgeted"]
KS = [1, 3, 5, 10]


def ranked_files(items):
    """Unique files preserving first-occurrence order."""
    seen, out = set(), []
    for it in items:
        f = it["file"]
        if f not in seen:
            seen.add(f)
            out.append(f)
    return out


def main():
    tasks = [t for t in cb.load_tasks() if t["instance_id"] in ALLOW]
    agg = {c: defaultdict(float) for c in CONDITIONS}
    n = 0
    for row in tasks:
        repo = cb.ensure_repo_checkout(row["repo_url"], row["base_commit"])
        problem = row["problem_statement"]
        gold = set(cb.Gold({
            "init_ctx": json.loads(row["gold_context"]),
            "repo_url": row["repo_url"],
            "commit": row["base_commit"],
        }).files())
        n += 1
        for cond in CONDITIONS:
            items, tok = cb.retrieve(repo, cond, problem)
            files = ranked_files(items)
            a = agg[cond]
            a["tok"] += tok
            a["items"] += len(items)
            for k in KS:
                a[f"r@{k}"] += len(gold & set(files[:k])) / max(1, len(gold))
                a[f"hit@{k}"] += float(any(f in gold for f in files[:k]))
            rr = 0.0
            for i, f in enumerate(files):
                if f in gold:
                    rr = 1.0 / (i + 1)
                    break
            a["mrr"] += rr
            dcg = sum(
                1.0 / math.log2(i + 2)
                for i, f in enumerate(files[:10]) if f in gold
            )
            ideal = sum(1.0 / math.log2(i + 2) for i in range(min(len(gold), 10)))
            a["ndcg10"] += dcg / ideal if ideal else 0.0
    print(f"tasks={n} model={ENV['OXIDE_EMBED_MODEL']}")
    hdr = ("cond      " + "".join(f"{'R@'+str(k):>7}" for k in KS)
           + f" {'hit@5':>7} {'MRR':>7} {'nDCG@10':>8} {'tok':>6} {'items':>6}")
    print(hdr)
    for c in CONDITIONS:
        a = agg[c]
        cells = "".join(f"{a[f'r@{k}']/n:>7.3f}" for k in KS)
        print(f"{c:<10}{cells}{a['hit@5']/n:>7.2f}{a['mrr']/n:>7.3f}"
              f"{a['ndcg10']/n:>8.3f}{a['tok']/n:>6.0f}{a['items']/n:>6.1f}")


if __name__ == "__main__":
    main()
