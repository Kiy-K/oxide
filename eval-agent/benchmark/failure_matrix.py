#!/usr/bin/env python3
"""Failure-overlap matrix + miss-cause classification over pinned Tier A set.

For every gold file: which conditions retrieved it? Misses by `budgeted` are
classified: retrieval_miss (no condition found it), ranking_error (vec or a
weaker condition ranked it in top-10 but budgeted dropped it), allocation_loss
(hybrid had it, pack omitted — reason pulled from pack JSON), stale_index not
applicable (indexes rebuilt per run)."""
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, "/home/khoi/Projects/oxide/scripts/agent_eval")
import contextbench_run as cb  # noqa: E402

ROOT = cb.ROOT
ENV = {
    "OXIDE_EMBED_URL": os.environ.get("OXIDE_EMBED_URL", ""),
    "OXIDE_EMBED_MODEL": os.environ.get("OXIDE_EMBED_MODEL", ""),
}
assert ENV["OXIDE_EMBED_URL"] and ENV["OXIDE_EMBED_MODEL"] == "qwen3-Q8_0"
PIN = ROOT / "eval-agent/results/tier_a_instances.txt"
ALLOW = {i.strip() for i in PIN.read_text().splitlines() if i.strip()}
CONDITIONS = ["lexical", "vec", "hybrid", "budgeted"]


def main():
    tasks = [t for t in cb.load_tasks() if t["instance_id"] in ALLOW]
    overlap = Counter()
    causes = Counter()
    examples = defaultdict(list)
    ox = str(ROOT / "target/release/oxide")
    for row in tasks:
        print(f"[{tasks.index(row)+1}/{len(tasks)}] {row['instance_id'][:44]}", flush=True)
        repo = cb.ensure_repo_checkout(row["repo_url"], row["base_commit"])
        problem = row["problem_statement"]
        gold = set(cb.Gold({
            "init_ctx": json.loads(row["gold_context"]),
            "repo_url": row["repo_url"],
            "commit": row["base_commit"],
        }).files())
        found = {}
        packs = {}
        cb.index_repo(repo, ENV["OXIDE_EMBED_URL"])
        for cond in CONDITIONS:
            items, _ = cb.retrieve(repo, cond, problem)
            found[cond] = {i["file"] for i in items}
            if cond == "budgeted":
                r = cb.sh(
                    [ox, "context", "--task", problem,
                     "--budget-tokens", "4096", "--json"],
                    cwd=repo, env=ENV,
                )
                packs[row["instance_id"]] = json.loads(r.stdout)
        for g in gold:
            key = tuple(c for c in CONDITIONS if g in found[c])
            overlap[key] += 1
        # classify budgeted misses
        om = {}
        p = packs.get(row["instance_id"], {})
        for o in p.get("omitted", []):
            om[o["id"].split("#")[0]] = o["why"]
        for g in gold - found["budgeted"]:
            if not any(g in found[c] for c in CONDITIONS):
                why = "retrieval_miss (no condition found it)"
            elif g in found["hybrid"]:
                why = f"allocation_loss ({om.get(g, 'not_in_candidates')})"
            elif g in found["vec"]:
                why = "semantic_only_dropped (vec found, hybrid/budgeted not)"
            else:
                why = "lexical_only_dropped"
            causes[why.split(" (")[0]] += 1
            examples[why].append(f"{row['instance_id'][:30]} {g.split('/')[-1]}")
    print("=== gold-file overlap across conditions ===")
    print("(lexical, vec, hybrid, budgeted) -> count")
    for k, v in sorted(overlap.items(), key=lambda x: -x[1]):
        present = ",".join(c for c, i in zip(CONDITIONS, [0, 1, 2, 3]) if True)
        marks = "".join(m for c, m in zip(CONDITIONS, "lvhb") if c in k)
        print(f"  {'{:<16}'.format(marks or 'NONE')} {v}")
    print("\n=== budgeted miss causes ===")
    for k, v in causes.most_common():
        print(f"  {v:>3}  {k}")
    print("\n=== examples ===")
    for why, exs in examples.items():
        for e in exs[:5]:
            print(f"  [{why.split(' (')[0]}] {e}")


if __name__ == "__main__":
    main()
