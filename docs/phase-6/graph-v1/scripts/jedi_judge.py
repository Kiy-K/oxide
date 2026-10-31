"""Classify each OXIDE edge against jedi goto(follow_imports=True). Run with the scratch venv."""

import json
import os
import re
import sys
from pathlib import Path

import jedi

CORPORA = os.path.expanduser("~/.cache/oxide/contextbench/corpora")
DOTTED = re.compile(rb"[A-Za-z_]\w*(?:\s*\.\s*[A-Za-z_]\w*)*")
LAST = re.compile(rb"[A-Za-z_]\w*$")


def line_of(data, off):
    return data.count(b"\n", 0, off) + 1


def judge(corpus, rows):
    root = os.path.join(CORPORA, corpus)
    src = os.path.join(root, "src")
    project = jedi.Project(
        path=root,
        added_sys_path=[src] if os.path.isdir(src) else [],
        load_unsafe_extensions=False,
        smart_sys_path=True,
    )
    files, scripts, out = {}, {}, []

    def data(p):
        if p not in files:
            files[p] = Path(root, p).read_bytes()
        return files[p]

    for a, kind, b, bpath, bs, be, ev, es, ee in rows:
        row = {"corpus": corpus, "source": a, "kind": kind, "target": b, "ev": [ev, es, ee]}
        if not b.startswith("symbol:"):
            row["class"] = "non-symbol"
            out.append(row)
            continue
        d = data(ev)
        m = DOTTED.match(d, es, ee)
        if not m:
            row["class"] = "no-name"
            row["text"] = d[es : min(ee, es + 60)].decode("utf-8", "replace")
            out.append(row)
            continue
        text = m.group(0)
        row["text"] = text.decode("utf-8", "replace")
        off = m.start() + LAST.search(text).start()
        line = line_of(d, off)
        col = len(d[d.rfind(b"\n", 0, off) + 1 : off].decode("utf-8", "replace"))
        if ev not in scripts:
            scripts[ev] = jedi.Script(
                code=d.decode("utf-8", "replace"), path=os.path.join(root, ev), project=project
            )
        try:
            defs = scripts[ev].goto(line, col, follow_imports=True)
        except Exception as e:
            defs = []
            row["error"] = repr(e)
        chain = re.sub(r"~\d+", "", b.split("#", 1)[1])
        last = chain.split(".")[-1]
        td = data(bpath)
        lo, hi = line_of(td, bs), line_of(td, be)
        target_file = os.path.join(root, bpath)
        row["jedi"] = [
            f"{x.module_path and os.path.relpath(str(x.module_path), root)}:{x.line}:{x.full_name}"
            for x in defs
        ]
        ok = any(
            str(x.module_path) == target_file
            and (
                (x.full_name or "").endswith(chain)
                or (x.name == last and x.line is not None and lo <= x.line <= hi)
            )
            for x in defs
        )
        row["class"] = "agree" if ok else ("jedi-none" if not defs else "disagree")
        out.append(row)
    return out


edges = json.loads(Path(sys.argv[1]).read_text())
rows = [r for c, es in edges.items() for r in judge(c, es)]
Path(sys.argv[2]).write_text(json.dumps(rows, indent=0))
