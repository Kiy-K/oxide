#!/usr/bin/env python3
"""Re-run the 21-instance ContextBench pin (eval-agent/results/
tier_a_instances.txt, identical to docs/ranking-fusion-eval's cb pin) at
the current binary and score every prediction twice: with the pre-fix
scorer (gold paths used raw) and the fixed one
(`contextbench_run.normalize_gold_path`).

Retrieval runs exactly once per (instance, condition); both scores are
computed from that one saved item list, and the list's sha256 is recorded,
so any metric difference can only come from scoring.

usage: eval-agent/.venv/bin/python rescore_pin.py <out.jsonl>
"""
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/agent_eval"))
import contextbench_run as cb  # noqa: E402

CONDITIONS = ("lexical", "vec", "hybrid", "budgeted")
FIXED = cb.normalize_gold_path


def score(repo, row, items, fixed):
    cb.normalize_gold_path = FIXED if fixed else (lambda p: p)
    try:
        return cb.evaluate_task(repo, row, items)
    finally:
        cb.normalize_gold_path = FIXED


def main():
    out = Path(sys.argv[1])
    pinned = set((ROOT / "eval-agent/results/tier_a_instances.txt").read_text().split())
    rows = sorted((r for r in cb.load_tasks(langs=("python", "typescript"))
                   if r["instance_id"] in pinned), key=lambda r: r["instance_id"])
    assert len(rows) == len(pinned), (len(rows), len(pinned))
    with out.open("w") as fh:
        for row in rows:
            repo = cb.ensure_repo_checkout(row["repo_url"], row["base_commit"])
            idx = cb.sh([str(ROOT / "target/release/oxide"), "index", "."], cwd=repo,
                        env={"OXIDE_EMBED_URL": ""}, timeout=3600)
            assert idx.returncode == 0, idx.stderr[-300:]
            for cond in CONDITIONS:
                items, tokens = cb.retrieve(repo, cond, row["problem_statement"])
                digest = hashlib.sha256(json.dumps(items, sort_keys=True).encode()).hexdigest()
                rec = dict(instance_id=row["instance_id"], repo=row["repo"], condition=cond,
                           items_sha256=digest, n_items=len(items), used_tokens=tokens,
                           old=score(repo, row, items, fixed=False),
                           new=score(repo, row, items, fixed=True))
                fh.write(json.dumps(rec) + "\n")
                fh.flush()
            print("done", row["instance_id"], flush=True)


if __name__ == "__main__":
    main()
