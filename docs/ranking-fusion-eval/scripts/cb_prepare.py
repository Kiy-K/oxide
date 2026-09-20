#!/usr/bin/env python3
"""ContextBench pinned-instance preparation for the ranking experiment:
for each of the 21 pinned instances, check out the repository at its base
commit (reusing scripts/agent_eval/contextbench_run.py's commit-keyed
worktrees), index it with the shipped native embedder, and write one
tasks line per instance ({id, query=problem statement, path}). Gold is
scored later by ContextBench's own metric code, not stored here.

usage: eval-agent/.venv/bin/python cb_prepare.py <out_tasks.jsonl>
"""
import json, os, subprocess, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3] if (Path(__file__).resolve().parents[3] / "Cargo.toml").exists() else Path(__import__("os").environ.get("OXIDE_ROOT", "."))
sys.path.insert(0, str(ROOT / "scripts/agent_eval"))
import contextbench_run as cb  # noqa: E402  (clones ContextBench if needed)

pinned = set((ROOT / "eval-agent/results/tier_a_instances.txt").read_text().split())
rows = [r for r in cb.load_tasks() if r["instance_id"] in pinned]
env = {k: v for k, v in os.environ.items() if k not in ("OXIDE_EMBED_NATIVE", "OXIDE_EMBED_URL", "OXIDE_EMBED_MODEL")}
out = open(sys.argv[1], "w")
for r in sorted(rows, key=lambda r: r["instance_id"]):
    d = cb.ensure_repo_checkout(r["repo_url"], r["base_commit"])
    if not (d / ".oxide/index.db").exists():
        print("indexing", r["instance_id"], d.name, flush=True)
        p = subprocess.run([str(ROOT / "target/release/oxide"), "index", "."], cwd=d, env=env,
                           capture_output=True, text=True, timeout=3600)
        if p.returncode != 0:
            print("  index failed:", p.stderr[-300:], flush=True); continue
    out.write(json.dumps({"id": r["instance_id"], "repo": r["repo"], "query": r["problem_statement"],
                          "gold": [], "path": str(d), "base_commit": r["base_commit"]}) + "\n"); out.flush()
    print("ready", r["instance_id"], flush=True)
