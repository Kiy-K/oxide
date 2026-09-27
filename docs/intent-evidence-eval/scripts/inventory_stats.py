#!/usr/bin/env python3
"""Phase-1 inventory statistics over extract_intent.py outputs (issue #30).

usage: inventory_stats.py <inventory_corpora.txt> <ev_dir> > inventory.md
inventory_corpora.txt: "<name> <repo_root>" per line; ev_dir holds inv-<name>.jsonl.
Code-size denominators come from the corpus's own OXIDE index (indexed files).
"""
import json
import re
import sqlite3
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

corpora = [l.split() for l in open(sys.argv[1]) if l.strip()]
evdir = Path(sys.argv[2])
TYPES = ["docstring", "leading_comment", "inline_comment", "module_doc", "readme", "doc", "changelog"]


def norm(t):
    return re.sub(r"\W+", " ", t.lower()).strip()


def pct(a, b):
    return f"{100 * a / b:.0f}%" if b else "–"


all_recs = {}
sym_stats = {}
for name, root in corpora:
    recs = [json.loads(l) for l in open(evdir / f"inv-{name}.jsonl")]
    all_recs[name] = recs
    con = sqlite3.connect(f"file:{root}/.oxide/index.db?mode=ro", uri=True)
    rows = con.execute("select file, qualified_name, kind, language, start_line, end_line from symbols").fetchall()
    code_chars = 0
    for f in {r[0] for r in rows}:
        try:
            code_chars += (Path(root) / f).stat().st_size
        except OSError:
            pass
    sym_stats[name] = (rows, code_chars)

print("## Counts by evidence type (all records / kept after filtering)\n")
print("| repo | lang | " + " | ".join(TYPES) + " | todo-tagged | kept tokens | code tokens | kept/code |")
print("|---|---|" + "---:|" * (len(TYPES) + 4))
tot = Counter()
for name, _ in corpora:
    recs = all_recs[name]
    rows, code_chars = sym_stats[name]
    lang = Counter(r[3] for r in rows).most_common(1)[0][0]
    c = Counter(r["type"] for r in recs)
    k = Counter(r["type"] for r in recs if r["keep"])
    todo = sum(1 for r in recs if "todo" in r["tags"])
    kt = sum(r["chars"] for r in recs if r["keep"]) / 4
    tot.update({f"all:{t}": c[t] for t in TYPES})
    tot.update({f"keep:{t}": k[t] for t in TYPES})
    tot["todo"] += todo
    print(f"| {name} | {lang} | " + " | ".join(f"{c[t]}/{k[t]}" for t in TYPES)
          + f" | {todo} | {kt/1000:.0f}k | {code_chars/4000:.0f}k | {kt/(code_chars/4):.2f} |")
print("| **total** | | " + " | ".join(f"{tot['all:'+t]}/{tot['keep:'+t]}" for t in TYPES)
      + f" | {tot['todo']} | | | |")

print("\n## Size per record (kept, chars): mean / median / p90\n")
print("| type | n | mean | median | p90 | share symbol-associated |")
print("|---|---:|---:|---:|---:|---:|")
for t in TYPES:
    xs = [r for recs in all_recs.values() for r in recs if r["type"] == t and r["keep"]]
    if not xs:
        continue
    ch = sorted(r["chars"] for r in xs)
    print(f"| {t} | {len(xs)} | {statistics.fmean(ch):.0f} | {statistics.median(ch):.0f} | "
          f"{ch[int(0.9 * (len(ch) - 1))]} | {pct(sum(r['assoc'] == 'symbol' for r in xs), len(xs))} |")

print("\n## Symbol coverage: share of non-module symbols with attached intent\n")
print("| repo | lang | symbols | docstring/leading | any attached (incl. inline) | attached symbols in test files |")
print("|---|---|---:|---:|---:|---:|")
for name, _ in corpora:
    rows, _ = sym_stats[name]
    syms = {(r[0], r[1]) for r in rows if r[2] not in ("module", "file")}
    lang = Counter(r[3] for r in rows).most_common(1)[0][0]
    recs = [r for r in all_recs[name] if r["keep"] and r["symbol"]]
    lead = {(r["file"], r["symbol"]) for r in recs if r["type"] in ("docstring", "leading_comment")} & syms
    anyx = {(r["file"], r["symbol"]) for r in recs} & syms
    testy = sum(1 for f, _ in anyx if re.search(r"(^|/)(tests?|testing|__tests__|spec)(/|_)|_test\.|\.test\.|test_", f))
    print(f"| {name} | {lang} | {len(syms)} | {pct(len(lead), len(syms))} | {pct(len(anyx), len(syms))} | {pct(testy, len(anyx))} |")

print("\n## Duplication (kept records)\n")
print("| repo | exact-dup texts | normalized-dup texts | doc chunks containing a >60-char code comment/docstring verbatim |")
print("|---|---:|---:|---:|")
for name, _ in corpora:
    recs = [r for r in all_recs[name] if r["keep"]]
    ex = Counter(r["text"] for r in recs)
    nm = Counter(norm(r["text"]) for r in recs)
    dup_ex = sum(v - 1 for v in ex.values() if v > 1)
    dup_nm = sum(v - 1 for v in nm.values() if v > 1)
    code_norms = [norm(r["text"]) for r in recs if r["type"] not in ("readme", "doc", "changelog") and len(r["text"]) > 60]
    docs = [r for r in recs if r["type"] in ("readme", "doc", "changelog")]
    if len(code_norms) * len(docs) < 3e7:
        cross = f"{sum(1 for r in docs if any(c in norm(r['text']) for c in code_norms))}/{len(docs)}"
    else:
        cross = "skipped (size)"
    print(f"| {name} | {pct(dup_ex, len(recs))} | {pct(dup_nm, len(recs))} | {cross} |")

print("\n## Flags (all records)\n")
flags = Counter()
by_type_flag = defaultdict(Counter)
for recs in all_recs.values():
    for r in recs:
        for f in r["flags"]:
            flags[f] += 1
            by_type_flag[f][r["type"]] += 1
n_all = sum(len(v) for v in all_recs.values())
print(f"records: {n_all}\n")
print("| flag | count | share | by type |")
print("|---|---:|---:|---|")
for f, v in flags.most_common():
    print(f"| {f} | {v} | {pct(v, n_all)} | " + ", ".join(f"{t}:{c}" for t, c in by_type_flag[f].most_common(4)) + " |")

print("\n## Secret-like hits (location and class only)\n")
for name, recs in all_recs.items():
    for r in recs:
        s = [f for f in r["flags"] if f.startswith("secret:")]
        if s:
            print(f"- {name} `{r['file']}:{r['start_line']}` {r['type']} {','.join(s)}")
