#!/usr/bin/env python3
"""Grep-baseline arm: term-occurrence file ranking over the pinned Tier A set.
No OXIDE involvement — represents plain lexical/grep retrieval dumping whole
files. Scored with ContextBench's own evaluator."""
import json
import os
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, "/home/khoi/Projects/oxide/scripts/agent_eval")
import contextbench_run as cb  # noqa: E402

ROOT = cb.ROOT
PIN = ROOT / "eval-agent/results/tier_a_instances.txt"
ALLOW = {i.strip() for i in PIN.read_text().splitlines() if i.strip()}
STOP = set("the this that with from into when what then they them their there these those have been will would could should your you're about which where while whose also more most some such only over under between because however therefore thus hence other another each every any all can cannot just like even ever never always often once twice here there does done doing being been was were has had having its it's don didn won isn aren were wasn weren".split())


def terms(problem):
    ws = [w.lower() for w in re.split(r"[^a-zA-Z0-9]+", problem)]
    return [w for w in dict.fromkeys(ws) if len(w) >= 4 and w not in STOP][:24]


def f1(p, c):
    return 2 * p * c / (p + c) if p + c else 0.0


def main():
    tasks = [t for t in cb.load_tasks() if t["instance_id"] in ALLOW]
    agg = defaultdict(float)
    n = 0
    for row in tasks:
        repo = cb.ensure_repo_checkout(row["repo_url"], row["base_commit"])
        problem = row["problem_statement"]
        ts = terms(problem)
        # count per-file matches across terms (git grep for speed/reproducibility)
        counts = defaultdict(int)
        for t in ts:
            r = subprocess.run(
                ["git", "grep", "-c", "-i", "-F", t, "--", "*.py", "*.ts", "*.tsx"],
                cwd=repo, capture_output=True, text=True,
            )
            for line in r.stdout.splitlines():
                fname, _, cnt = line.rpartition(":")
                counts[fname] += int(cnt) if cnt.isdigit() else 0
        ranked = [f for f, _ in sorted(counts.items(), key=lambda kv: -kv[1])[:10]]
        items = []
        tok = 0
        for f in ranked:
            src = (repo / f).read_text(errors="ignore")
            lines = src.count("\n") + 1
            tok += len(src) // 4
            items.append({"file": f, "start_line": 1, "end_line": lines})
        m = cb.evaluate_task(repo, row, items)
        gold = set(cb.Gold({
            "init_ctx": json.loads(row["gold_context"]),
            "repo_url": row["repo_url"],
            "commit": row["base_commit"],
        }).files())
        n += 1
        agg["file_f1"] += f1(m["file"]["precision"], m["file"]["coverage"])
        agg["line_f1"] += f1(m["line"]["precision"], m["line"]["coverage"])
        agg["file_cov"] += m["file"]["coverage"]
        agg["tok"] += tok
        print(f"[{n}/{len(tasks)}] {row['instance_id'][:40]} "
              f"fileF1={f1(m['file']['precision'], m['file']['coverage']):.2f} tok={tok}",
              flush=True)
    print(f"\n=== grep baseline (whole-file dumps, top-10) ===")
    print(f"file_F1={agg['file_f1']/n:.3f} line_F1={agg['line_f1']/n:.3f} "
          f"file_cov={agg['file_cov']/n:.3f} tokens={agg['tok']/n:.0f}")


if __name__ == "__main__":
    main()
