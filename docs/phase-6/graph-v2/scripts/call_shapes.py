"""Tally unresolved and resolved Python call shapes by receiver form.

uv run python docs/phase-6/graph-v2/scripts/call_shapes.py OUT.json REPO...
"""

import collections
import json
import os
import sys
import tempfile

import oxide


def shape(name: str) -> str:
    head, _, rest = name.partition(".")
    if not rest:
        return "bare"
    if head in ("self", "cls"):
        return f"{head}.m" if "." not in rest else f"{head}.attr.m"
    if head.startswith("super()"):
        return "super().m"
    return "qualified"


def tally(path: str, data: str) -> dict[str, object]:
    with oxide.open_repository(path, data_dir=data) as repo:
        repo.index()
        q = repo._view()._query
        out: dict[str, collections.Counter[str]] = {}
        rows = q(
            "MATCH ()-[r:Opaque]->() WHERE r.kind = 'calls' AND r.ev_path ENDS WITH '.py' "
            "RETURN r.name"
        )
        out["unresolved"] = collections.Counter(shape(n) for (n,) in rows)
        rows = q(
            "MATCH ()-[r:Rel]->() WHERE r.kind = 'calls' AND r.ev_path ENDS WITH '.py' "
            "RETURN r.resolution, count(*)"
        )
        out["linked"] = collections.Counter(dict(rows))
        top = collections.Counter(
            n
            for (n,) in q(
                "MATCH ()-[r:Opaque]->() WHERE r.kind = 'calls' AND r.ev_path ENDS WITH '.py' "
                "AND (r.name STARTS WITH 'self.' OR r.name STARTS WITH 'cls.') RETURN r.name"
            )
        )
        return {
            "corpus": os.path.basename(path),
            **{k: dict(sorted(v.items())) for k, v in out.items()},
            "top_receiver": top.most_common(10),
        }


if __name__ == "__main__":
    rows = []
    for repo in sys.argv[2:]:
        with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as d:
            rows.append(tally(repo, d))
            print(json.dumps(rows[-1]), flush=True)
    with open(sys.argv[1], "w") as f:
        json.dump(rows, f, indent=1)
