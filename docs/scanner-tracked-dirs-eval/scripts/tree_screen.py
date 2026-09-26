#!/usr/bin/env python3
"""Breadth screen for noise: for every distinct ContextBench (`full`)
repository at one of its base commits, list the Git-tracked paths each
policy would add, from the tree alone (bare blobless clone, no checkout, no
file contents). Path-level only: extension-based indexability and the
name-based file rules; no size/binary/content checks and no .gitignore of
tracked files (both can only remove files, so counts are upper bounds).

usage: tree_screen.py <cb_repos.json> <clone cache dir> > tree_screen.jsonl
"""
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

from policy_screen import (DENYLIST_DIRS, DENYLIST_FILES, DENYLIST_SUFFIXES, NARROW, VCS,
                           indexable_name)


def run(*args, cwd=None):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=1800)


def main():
    repos = json.load(open(sys.argv[1]))
    cache = Path(sys.argv[2])
    cache.mkdir(parents=True, exist_ok=True)
    for r in repos:
        name = r["repo"].replace("/", "__")
        bare = cache / f"{name}.git"
        rec = dict(repo=r["repo"], language=r["lang"], commit=r["commit"], instances=r["n"])
        if not bare.exists():
            c = run("git", "clone", "-q", "--bare", "--filter=blob:none", r["url"], str(bare))
            if c.returncode:
                rec["error"] = c.stderr[-300:]
                print(json.dumps(rec), flush=True)
                continue
        t = run("git", "ls-tree", "-r", "--name-only", "-z", r["commit"], cwd=bare)
        if t.returncode:
            run("git", "fetch", "-q", "--filter=blob:none", "origin", r["commit"], cwd=bare)
            t = run("git", "ls-tree", "-r", "--name-only", "-z", r["commit"], cwd=bare)
        if t.returncode:
            rec["error"] = t.stderr[-300:]
            print(json.dumps(rec), flush=True)
            continue
        paths = [p for p in t.stdout.split("\0") if p]
        rec["tracked_files"] = len(paths)
        for policy, allowed in (("tracked", DENYLIST_DIRS - VCS), ("narrow", NARROW)):
            add = []
            for rel in paths:
                parts = rel.split("/")
                dirs, fname = parts[:-1], parts[-1]
                denied = [d for d in dirs if d in DENYLIST_DIRS]
                if (not denied or any(d.startswith(".") for d in dirs)
                        or not all(d in allowed for d in denied)):
                    continue
                if (fname.startswith(".") and not fname.startswith(".env")) or fname in DENYLIST_FILES \
                        or any(s in fname for s in DENYLIST_SUFFIXES) or not indexable_name(fname):
                    continue
                add.append((rel, denied[0],
                            any(d in ("vendor", "node_modules", "third_party") for d in dirs)))
            rec[policy] = dict(files=len(add), by_dir=Counter(d for _, d, _ in add),
                               dependency_path=sum(1 for *_, dep in add if dep),
                               sample=[p for p, _, _ in add][:12])
        print(json.dumps(rec), flush=True)


if __name__ == "__main__":
    main()
