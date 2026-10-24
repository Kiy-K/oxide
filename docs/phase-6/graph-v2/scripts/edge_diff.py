"""v4 -> v5 edge diff: lost or changed edges, new edges by resolution/basis, candidates per site.

    python docs/phase-6/graph-v2/scripts/edge_diff.py BEFORE.json AFTER.json NEW_ROWS.json

BEFORE/AFTER come from edge_dump.py. NEW_ROWS (input to jedi_dispatch.py) holds every edge
whose (source, kind, target, evidence) key is absent before, with its resolution and basis.
"""

import collections
import json
import sys
from pathlib import Path


def keyed(dump: dict) -> dict[tuple, tuple]:
    """(source, kind, target, ev_path, ev_start, ev_end) -> (resolution, basis, full row)."""
    return {(e[0], e[1], e[2], e[6], e[7], e[8]): (e[9], e[10], e) for e in dump["edges"]}


before = {d["corpus"]: d for d in json.loads(Path(sys.argv[1]).read_text())}
after = {d["corpus"]: d for d in json.loads(Path(sys.argv[2]).read_text())}
rows: dict[str, list] = {}
for corpus, b in before.items():
    old, new = keyed(b), keyed(after[corpus])
    lost = [k for k in old if k not in new]
    changed = collections.Counter(
        f"{old[k][:2]}->{new[k][:2]}" for k in old if k in new and old[k][:2] != new[k][:2]
    )
    added = [new[k] for k in new if k not in old]
    sites = collections.defaultdict(list)
    for res, _, e in added:
        sites[(e[0], e[6], e[7], e[8])].append(res)
    width = collections.Counter(min(len(v), 6) for v in sites.values())
    print(f"## {corpus}")
    print(
        f"  edges {len(old)} -> {len(new)}; index {b['index_seconds']}s -> "
        f"{after[corpus]['index_seconds']}s; peak RSS {b['peak_rss_mb']} -> "
        f"{after[corpus]['peak_rss_mb']} MB"
    )
    print(f"  lost {len(lost)}; changed {dict(changed)}")
    for k in lost[:5]:
        print("    LOST", k, old[k][:2])
    print(f"  new {dict(collections.Counter((r, s) for r, s, _ in added))}")
    print(f"  new sites {len(sites)}; candidates per site (6 = 6+) {dict(sorted(width.items()))}")
    rows[corpus] = [e for _, _, e in added]
Path(sys.argv[3]).write_text(json.dumps(rows))
