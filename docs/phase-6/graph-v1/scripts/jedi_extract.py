"""Resolved Python calls/references edges with evidence spans and target ranges (oxide env)."""

import json
import os
import sys
import tempfile
from pathlib import Path

import oxide

out, paths = sys.argv[1], sys.argv[2:]
print("oxide from", oxide.__file__, file=sys.stderr)
Q = (
    "MATCH (a:Entity)-[r:Rel]->(b:Entity) WHERE r.resolution='resolved' "
    "AND r.kind IN ['calls','references'] AND r.ev_path ENDS WITH '.py' "
    "RETURN a.key, r.kind, b.key, b.path, b.start_byte, b.end_byte, "
    "r.ev_path, r.ev_start, r.ev_end"
)
res = {}
with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as d:
    for p in paths:
        with oxide.open_repository(p, data_dir=d) as r:
            r.index()
            res[os.path.basename(p)] = sorted({tuple(x) for x in r._view()._query(Q)})
Path(out).write_text(json.dumps(res))
