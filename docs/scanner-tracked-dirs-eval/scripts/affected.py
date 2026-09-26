#!/usr/bin/env python3
"""The ContextBench (`full`) instances whose gold includes a file under a
directory OXIDE's scanner skips by name (`scanner.rs::DENYLIST_DIRS`), and
their commit-keyed checkouts.

usage: eval-agent/.venv/bin/python affected.py [--checkout] > affected.jsonl
"""
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/agent_eval"))
import contextbench_run as cb  # noqa: E402

# Mirror of scanner.rs::DENYLIST_DIRS at d1333a9.
DENYLIST_DIRS = {".git", ".hg", ".svn", "node_modules", "target", "dist", "build", "out",
                 ".next", ".nuxt", ".turbo", "__pycache__", ".venv", "venv", ".tox",
                 ".mypy_cache", ".pytest_cache", ".ruff_cache", "coverage", ".nyc_output",
                 "vendor", ".idea", ".vscode"}


def denied_dirs(path):
    return [p for p in path.split("/")[:-1] if p in DENYLIST_DIRS]


def affected_rows():
    full = pd.read_parquet(cb.CB_DIR / "data" / "full.parquet")
    verified = set(pd.read_parquet(cb.CB_DIR / "data" / "contextbench_verified.parquet").instance_id)
    for row in full.sort_values("instance_id").to_dict("records"):
        files = sorted({cb.normalize_gold_path(it["file"]) for it in json.loads(row["gold_context"])
                        if it.get("file")})
        hit = [f for f in files if denied_dirs(f)]
        if hit:
            yield row, files, hit, row["instance_id"] in verified


def main():
    for row, files, hit, ver in affected_rows():
        rec = dict(instance_id=row["instance_id"], repo=row["repo"], language=row["language"],
                   base_commit=row["base_commit"], verified=ver, gold_files=files,
                   gold_in_denied_dirs=hit)
        if "--checkout" in sys.argv:
            rec["path"] = str(cb.ensure_repo_checkout(row["repo_url"], row["base_commit"]))
        print(json.dumps(rec), flush=True)


if __name__ == "__main__":
    main()
