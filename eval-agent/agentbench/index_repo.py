#!/usr/bin/env python3
"""Build (or reuse) the OXIDE index for each pilot task's repo, once per repo,
outside any timed agent run -- and record the one-time indexing cost.

Indexing happens directly in the permanent, commit-keyed ContextBench worktree
(`ensure_repo_checkout`'s `dst`), the same repo copy `contextbench_run.py`/
`tierb_agent_run.py` treat as an immutable source. `run_pilot.py` later copies
*from* that indexed worktree per run -- baseline copies exclude `.oxide`,
oxide-condition copies include it (see `run_pilot.py::stage_condition`).

Usage (needs the local embedder running -- see scripts/embedder.sh):
    OXIDE_EMBED_URL=http://127.0.0.1:8088 \
        eval-agent/.venv/bin/python eval-agent/agentbench/index_repo.py
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "agent_eval"))
from contextbench_run import ensure_repo_checkout  # noqa: E402

OXIDE_BIN = ROOT / "target" / "release" / "oxide"
TASKS_PATH = Path(__file__).parent / "tasks.json"
COST_PATH = Path(__file__).parent / "index_cost.json"


def sh(cmd, cwd=None, timeout=60):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)


def index_cost_for(repo_dir: Path, embedder_url: str) -> dict:
    """Times a fresh `oxide index .` via /usr/bin/time -v for peak RSS, then
    records index size and symbol/relation counts via `oxide status --json`.
    Assumes `.oxide` does not exist yet in `repo_dir` (caller's job to check)."""
    start = time.time()
    # No timeout here on purpose: a large repo (e.g. Deno, ~6k source files)
    # can take multiple hours against the deliberately-throttled local
    # embedder (scripts/embedder.sh's laptop-friendly --parallel 1). An
    # earlier fixed 3600s timeout killed `oxide index` mid-run, which lost
    # real progress (the DB itself survived intact -- WAL protected it --
    # but `root` meta, written only in the closing Finalize transaction,
    # never got set, so the index was correctly treated as incomplete).
    r = subprocess.run(
        ["/usr/bin/time", "-v", str(OXIDE_BIN), "index", "."],
        cwd=repo_dir, capture_output=True, text=True,
        env={**os.environ, "OXIDE_EMBED_URL": embedder_url},
    )
    wall = time.time() - start
    if r.returncode != 0:
        # Full stderr, not the tail -- /usr/bin/time -v appends a resource-
        # usage block AFTER the real error, and slicing the last N chars
        # was hiding the actual failure behind that trailer.
        print(f"--- oxide index stderr ({repo_dir}) ---\n{r.stderr}\n--- end stderr ---", flush=True)
        raise RuntimeError(f"oxide index failed in {repo_dir}: {r.stderr[-500:]}")
    peak_rss_kb = None
    for line in r.stderr.splitlines():
        if "Maximum resident set size" in line:
            peak_rss_kb = int(line.rsplit(":", 1)[-1].strip())
    db = repo_dir / ".oxide" / "index.db"
    status = sh([str(OXIDE_BIN), "status", "--json"], cwd=repo_dir)
    status_json = json.loads(status.stdout) if status.returncode == 0 else {}
    return {
        "wall_s": round(wall, 1),
        "peak_rss_kb": peak_rss_kb,
        "index_db_bytes": db.stat().st_size if db.exists() else None,
        "status": status_json,
    }


def index_is_complete(db_path: Path) -> bool:
    """`root` meta is written only in the closing Finalize transaction (see
    AGENTS.md's atomic-closing-meta-writes invariant) -- its presence is a
    real signal the index finished, unlike mere file existence. A prior bug
    here (file-existence-only) silently skipped re-indexing a repo whose
    `.oxide` was left behind by an interrupted run."""
    if not db_path.exists():
        return False
    import sqlite3
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute("SELECT value FROM meta WHERE key='root'").fetchone()
        return row is not None
    except sqlite3.DatabaseError:
        return False
    finally:
        conn.close()


def ensure_indexed_repo(task: dict, embedder_url: str) -> Path:
    """Returns the permanent, indexed worktree for `task`'s repo. Builds the
    index (and records its cost, keyed by repo, first time only) if missing
    or left incomplete by an interrupted prior run."""
    repo_dir = ensure_repo_checkout(task["repo_url"], task["base_commit"])
    if not index_is_complete(repo_dir / ".oxide" / "index.db"):
        print(f"  indexing {task['repo']} @ {repo_dir} ...", flush=True)
        cost = index_cost_for(repo_dir, embedder_url)
        costs = json.loads(COST_PATH.read_text()) if COST_PATH.exists() else {}
        costs[task["repo"]] = cost
        COST_PATH.write_text(json.dumps(costs, indent=1))
        print(f"    {cost['wall_s']}s, peak RSS {cost['peak_rss_kb']}KB, "
              f"index.db {cost['index_db_bytes']} bytes", flush=True)
    return repo_dir


def main() -> None:
    embedder_url = os.environ.get("OXIDE_EMBED_URL")
    assert embedder_url, "set OXIDE_EMBED_URL (see scripts/embedder.sh start)"
    tasks = json.loads(TASKS_PATH.read_text())
    seen = set()
    for task in tasks:
        if task["repo"] in seen:
            continue
        seen.add(task["repo"])
        ensure_indexed_repo(task, embedder_url)
    print(f"{len(seen)} repos indexed/verified; costs in {COST_PATH}")


if __name__ == "__main__":
    main()
