#!/usr/bin/env python3
"""Reranking experiment, phase 1: candidate-pool ceiling gate.

Before spending any effort on a reranker, check whether relevant evidence is
even reachable:

  - discovery ceiling: R@5/10/20/50 over `oxide search --mode hybrid --limit
    50` (fused lexical+semantic, pre-cap) — what widening the pool could buy.
  - rerankable ceiling: gold presence in the actual candidate pool a
    reranker would see, i.e. `context.rs`'s `kept` right before the
    `rerank_candidates` hook. Captured exactly via `OXIDE_DEBUG_DUMP_KEPT`
    (an env-gated debug dump added for this experiment) — a huge token
    budget alone is NOT faithful here: it only defeats the "over token
    budget" drop, while the per-file and role diversity caps are
    budget-independent and still remove `kept` members regardless.

Gate: if the rerankable ceiling is low, reranking cannot help — the problem
is discovery/allocation, not ordering. Stop before touching any model.

Run with the pinned Tier A set (same 21 instances as docs/retrieval-ceiling.md):
    eval-agent/.venv/bin/python scripts/agent_eval/reranker_ceiling.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import contextbench_run as cb  # noqa: E402

ROOT = cb.ROOT
OX = str(ROOT / "target/release/oxide")
ENV = {"OXIDE_EMBED_URL": os.environ.get("OXIDE_EMBED_URL", "")}
PIN = ROOT / "eval-agent/results/tier_a_instances.txt"
ALLOW = {i.strip() for i in PIN.read_text().splitlines() if i.strip()}
KS = (5, 10, 20, 50)


def gold_files(row) -> set[str]:
    gold_data = {
        "init_ctx": json.loads(row["gold_context"]) if isinstance(row["gold_context"], str) else row["gold_context"],
        "repo_url": row["repo_url"],
        "commit": row["base_commit"],
    }
    return set(cb.Gold(gold_data).files())


def discovery_hits(repo, problem) -> list[dict]:
    r = cb.sh([OX, "search", problem, "--mode", "hybrid", "--limit", "50",
               "--retrieval-mode", "quality", "--json"], cwd=repo, env=ENV)
    return json.loads(r.stdout)


def rerankable_pool(repo, problem) -> list[dict]:
    """The real pre-rerank `kept` pool, via OXIDE_DEBUG_DUMP_KEPT.

    A huge token budget only defeats the "over token budget" drop — the
    per-file and role diversity caps in context.rs's greedy-fill loop are
    budget-independent and still remove `kept` members regardless of budget
    size, so `pack["items"]` at any budget is NOT the same set `rerank_
    candidates` actually sees. The dump captures it exactly, pre-rerank.
    """
    with tempfile.NamedTemporaryFile(suffix=".json") as tmp:
        env = {**ENV, "OXIDE_DEBUG_DUMP_KEPT": tmp.name}
        cb.sh([OX, "context", "--task", problem, "--budget-tokens", "4096",
               "--retrieval-mode", "quality", "--json"], cwd=repo, env=env)
        return json.loads(Path(tmp.name).read_text())


def rank_of_first_gold(items: list[dict], gold: set[str], file_key) -> int | None:
    for i, it in enumerate(items):
        if file_key(it) in gold:
            return i
    return None


def main():
    tasks = [t for t in cb.load_tasks() if t["instance_id"] in ALLOW]
    missing = ALLOW - {t["instance_id"] for t in tasks}
    assert not missing, f"pinned instances missing: {sorted(missing)}"

    rows = []
    for row in tasks:
        repo = cb.ensure_repo_checkout(row["repo_url"], row["base_commit"])
        cb.index_repo(repo, ENV["OXIDE_EMBED_URL"])
        problem = row["problem_statement"]
        gold = gold_files(row)
        disc = discovery_hits(repo, problem)
        pool = rerankable_pool(repo, problem)
        rows.append({
            "instance_id": row["instance_id"],
            "gold_files": sorted(gold),
            "discovery_rank": rank_of_first_gold(disc, gold, lambda it: it["file"]),
            "discovery_n": len(disc),
            "pool_rank": rank_of_first_gold(pool, gold, lambda it: it["symbol"]["file"]),
            "pool_size": len(pool),
        })
        print(f"  {row['instance_id']}: discovery_rank={rows[-1]['discovery_rank']} "
              f"pool_rank={rows[-1]['pool_rank']} pool_size={len(pool)}", flush=True)

    out = ROOT / "docs/reranker-eval/results/ceiling.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")

    n = len(rows)
    print(f"\n=== ceiling summary (n={n} pinned Tier A tasks) ===")
    for k in KS:
        hit = sum(1 for r in rows if r["discovery_rank"] is not None and r["discovery_rank"] < k)
        print(f"discovery  R@{k:<3} = {hit}/{n} ({hit/n:.0%})")
    hit_pool = sum(1 for r in rows if r["pool_rank"] is not None)
    print(f"rerankable ceiling (gold anywhere in kept pool) = {hit_pool}/{n} ({hit_pool/n:.0%})")
    avg_pool = sum(r["pool_size"] for r in rows) / n
    print(f"avg kept-pool size = {avg_pool:.1f}")
    ranks = [r["pool_rank"] for r in rows if r["pool_rank"] is not None]
    if ranks:
        print(f"pool_rank of first gold item (when present): {sorted(ranks)}")


if __name__ == "__main__":
    main()
