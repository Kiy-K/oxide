#!/usr/bin/env python3
"""Held-out edit-locus gold: parent-side lines the commit touched (`git diff -U0
HEAD <commit>` in the parent worktree; a pure insertion contributes its two
anchor lines), restricted to the task's gold-symbol spans (falls back to all
touched lines in gold files when none fall inside a gold symbol).

usage: heldout_edit_gold.py <tasks.jsonl> > heldout_gold.json
"""
import json
import re
import sqlite3
import subprocess
import sys
from collections import defaultdict

HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+")
out = {}
for t in map(json.loads, open(sys.argv[1])):
    root = t["path"]
    db = sqlite3.connect(f"file:{root}/.oxide/index.db?mode=ro", uri=True)
    gspan = defaultdict(set)
    for g in t["gold"]:
        f, qn = g.split("#", 1)
        row = db.execute("select start_line,end_line from symbols where file=? and qualified_name=?", (f, qn)).fetchone()
        if row:
            gspan[f] |= set(range(row[0], row[1] + 1))
    files = sorted(gspan)
    touched = defaultdict(set)
    if files:
        diff = subprocess.run(["git", "-C", root, "diff", "-U0", "--no-renames", "HEAD", t["commit"], "--", *files],
                              capture_output=True, text=True, check=True).stdout
        cur = None
        for line in diff.splitlines():
            if line.startswith("--- "):
                cur = line[6:] if line.startswith("--- a/") else None
            m = HUNK.match(line)
            if m and cur:
                start, cnt = int(m.group(1)), int(m.group(2) or 1)
                touched[cur] |= set(range(start, start + cnt)) if cnt else {start, start + 1}
    lines = {}
    for f in files:
        inside = touched[f] & gspan[f]
        lines[f] = sorted(inside or touched[f])
    out[t["id"]] = {"files": files, "lines": {f: v for f, v in lines.items() if v}}
json.dump(out, sys.stdout)
