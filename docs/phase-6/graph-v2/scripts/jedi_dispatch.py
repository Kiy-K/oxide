"""jedi oracle for the new v5 edges, per edge and per call site. Run with the scratch venv:

    JEDI_VENV/bin/python -I docs/phase-6/graph-v2/scripts/jedi_dispatch.py NEW_ROWS.json OUT.json

Same agreement rule as graph-v1 ``jedi_judge.py`` (a jedi definition in the target's file
whose full name ends with the target's name chain, or whose name matches and whose line lies
in the target's range), with the dotted name allowed to pass through ``super()``. A resolved
site is correct when its one edge agrees. An ambiguous site *covers* jedi when one of its
candidates agrees: jedi gives the static class of ``self``, one member of OXIDE's set.
"""

import collections
import json
import os
import re
import sys
from pathlib import Path

import jedi

CORPORA = os.path.expanduser("~/.cache/oxide/contextbench/corpora")
DOTTED = re.compile(rb"[A-Za-z_]\w*(?:\s*(?:\(\s*\))?\s*\.\s*[A-Za-z_]\w*)*")
LAST = re.compile(rb"[A-Za-z_]\w*$")


def line_of(data: bytes, off: int) -> int:
    return data.count(b"\n", 0, off) + 1


def judge(corpus: str, rows: list) -> list[dict]:
    root = os.path.join(CORPORA, corpus)
    src = os.path.join(root, "src")
    project = jedi.Project(
        path=root, added_sys_path=[src] if os.path.isdir(src) else [], smart_sys_path=True
    )
    files: dict[str, bytes] = {}
    scripts: dict[str, jedi.Script] = {}
    goto: dict[tuple, list] = {}
    out = []
    for a, _, b, bpath, bs, be, ev, es, ee, res, _ in rows:
        for p in (ev, bpath):
            if p not in files:
                files[p] = Path(root, p).read_bytes()
        d = files[ev]
        row = {"corpus": corpus, "source": a, "target": b, "ev": [ev, es, ee], "res": res}
        m = DOTTED.match(d, es, ee)
        if not m:
            out.append({**row, "class": "no-name"})
            continue
        text = m.group(0)
        row["text"] = text.decode("utf-8", "replace")
        off = m.start() + LAST.search(text).start()
        line = line_of(d, off)
        col = len(d[d.rfind(b"\n", 0, off) + 1 : off].decode("utf-8", "replace"))
        if (ev, off) not in goto:
            if ev not in scripts:
                scripts[ev] = jedi.Script(
                    code=d.decode("utf-8", "replace"), path=os.path.join(root, ev), project=project
                )
            try:
                goto[(ev, off)] = scripts[ev].goto(line, col, follow_imports=True)
            except Exception:  # jedi crash: counted as finding nothing
                goto[(ev, off)] = []
        defs = goto[(ev, off)]
        chain = re.sub(r"~\d+", "", b.split("#", 1)[1])
        lo, hi = line_of(files[bpath], bs), line_of(files[bpath], be)
        target_file = os.path.join(root, bpath)
        row["jedi"] = [
            f"{x.module_path and os.path.relpath(str(x.module_path), root)}:{x.line}:{x.full_name}"
            for x in defs
        ]
        ok = any(
            str(x.module_path) == target_file
            and (
                (x.full_name or "").endswith(chain)
                or (x.name == chain.split(".")[-1] and x.line is not None and lo <= x.line <= hi)
            )
            for x in defs
        )
        out.append({**row, "class": "agree" if ok else ("jedi-none" if not defs else "disagree")})
    return out


def sites(rows: list[dict]) -> dict[str, collections.Counter]:
    by: dict[tuple, list[dict]] = collections.defaultdict(list)
    for r in rows:
        by[(r["corpus"], r["source"], *r["ev"])].append(r)
    tally: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for group in by.values():
        classes = {r["class"] for r in group}
        verdict = (
            "agree" if "agree" in classes else "disagree" if "disagree" in classes else "jedi-none"
        )
        tally[f"{group[0]['corpus']} {group[0]['res']}"][verdict] += 1
    return tally


if __name__ == "__main__":
    edges = json.loads(Path(sys.argv[1]).read_text())
    rows = [r for c, es in edges.items() for r in judge(c, es)]
    Path(sys.argv[2]).write_text(json.dumps(rows, indent=0))
    for key, counts in sorted(sites(rows).items()):
        print(key, dict(counts))
