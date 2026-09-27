#!/usr/bin/env python3
"""Update-frequency screen for developer-intent text (issue #30).

For the last N non-merge commits of a clone, classify every added/removed
diff line as code, comment (by leading comment marker in a code file) or
docs (a .md/.rst/.txt/.adoc file), and report how often each changes.
Line-prefix classification only: a block-comment continuation line without a
leading '*' and Python docstring bodies count as code (undercounts comments).

usage: churn.py <repo> [N=500]
"""
import re
import subprocess
import sys
from collections import Counter

repo = sys.argv[1]
n = int(sys.argv[2]) if len(sys.argv) > 2 else 500
CODE = re.compile(r"\.(py|rs|ts|tsx|js|jsx|mjs|go|java|c|h|cc|cpp|hpp|rb|php)$")
DOC = re.compile(r"\.(md|rst|txt|adoc)$", re.I)
out = subprocess.run(["git", "-C", repo, "log", "--no-merges", f"-n{n}", "-p", "-U0", "--format=@@COMMIT %H"],
                     capture_output=True, check=True).stdout.decode("utf-8", "replace")
lines = Counter()
commits = Counter()
cur = None
touched = set()
ext = ""
docfile = codefile = False


def flush():
    if cur is None:
        return
    commits["all"] += 1
    for k in touched:
        commits[k] += 1
    if "code" in touched and "comment" in touched:
        commits["code+comment"] += 1


for l in out.splitlines():
    if l.startswith("@@COMMIT "):
        flush()
        cur, touched = l.split()[1], set()
        continue
    if l.startswith("diff --git"):
        path = l.split(" b/")[-1]
        codefile = bool(CODE.search(path))
        docfile = bool(DOC.search(path))
        ext = path.rsplit(".", 1)[-1]
        continue
    if l.startswith(("+++", "---")) or not l.startswith(("+", "-")):
        continue
    body = l[1:].strip()
    if not body:
        continue
    if docfile:
        k = "docs"
    elif codefile:
        marks = ("#",) if ext in ("py", "rb") else ("//", "/*", "*")
        k = "comment" if body.startswith(marks) or body.startswith(('"""', "'''")) else "code"
    else:
        continue
    lines[k] += 1
    touched.add(k)
flush()
a = commits["all"]
print(f"{repo.rstrip('/').split('/')[-1]}: commits={a} "
      f"touch_code={commits['code']/a:.0%} touch_comment={commits['comment']/a:.0%} "
      f"touch_docs={commits['docs']/a:.0%} code_commits_also_comment={commits['code+comment']/max(1,commits['code']):.0%} "
      f"lines code/comment/docs={lines['code']}/{lines['comment']}/{lines['docs']} "
      f"comment:code churn={lines['comment']/max(1,lines['code']):.2f}")
