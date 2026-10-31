"""Edges resolved now that were unresolved at HEAD: sample with call-site text for hand review."""

import json
import os
import sys
import tempfile
from pathlib import Path

import oxide


def edges(repo_path, data):
    with oxide.open_repository(repo_path, data_dir=data) as r:
        r.index()
        v = r._view()
        rows = v._query(
            "MATCH (a:Entity)-[r:Rel]->(b:Entity) WHERE r.resolution='resolved' "
            "AND r.kind IN ['calls','imports','references'] "
            "RETURN a.key, r.kind, b.key, r.ev_path, r.ev_start, r.ev_end"
        )
        return {tuple(x) for x in rows}


mode = sys.argv[1]
out = sys.argv[2]
paths = sys.argv[3:]
res = {}
with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as d:
    for p in paths:
        res[p] = sorted(edges(p, d))
Path(out).write_text(json.dumps(res))
