#!/usr/bin/env python3
"""Sample symbols for the blind code-first description set (BCD, issue #30).

Seeded random sample of non-test functions/methods (8-60 lines) from one
corpus, printed with every developer-intent line removed (all extracted
comment/docstring spans from the inventory, plus trailing '#'/'//' comments),
so the query author sees code only and cannot copy comment wording. Whether
the gold has attached intent is decided by the sample, not by the author.

usage: sample_blind.py <repo_root> <inv.jsonl> <n> <seed>
"""
import json
import random
import re
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

root, inv, n, seed = Path(sys.argv[1]), sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
TEST = re.compile(r"(^|/)(tests?|testing|__tests__|spec|benches|examples?|docs?)(/|$)|_test\.|\.test\.|\.spec\.|(^|/)test_")
intent_lines = defaultdict(set)
for l in open(inv):
    r = json.loads(l)
    if r["type"] not in ("readme", "doc", "changelog"):
        intent_lines[r["file"]].update(range(r["start_line"], r["end_line"] + 1))
con = sqlite3.connect(f"file:{root}/.oxide/index.db?mode=ro", uri=True)
rows = con.execute("select file, qualified_name, name, kind, start_line, end_line from symbols "
                   "where kind in ('function','method') order by file, start_line").fetchall()
pool = [r for r in rows if not TEST.search(r[0]) and 8 <= r[5] - r[4] + 1 <= 60 and not r[2].startswith("test")]
rng = random.Random(seed)
for f, qn, name, kind, s, e in rng.sample(pool, min(n, len(pool))):
    lines = (root / f).read_text(errors="replace").splitlines()
    out = []
    for i in range(s, e + 1):
        if i in intent_lines[f] or i > len(lines):
            continue
        t = re.sub(r"\s+(#|//)\s.*$", "", lines[i - 1])
        if t.strip():
            out.append(t)
    print(f"=== {f}#{qn}  ({kind}, {s}-{e})")
    print("\n".join(out))
    print()
