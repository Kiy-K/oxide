"""Dev-only router diagnostics: route_probe.py OUT.json [corpus-prefix]

For every dev task: the baseline route's region composition, and for every verified
gold span the depth at which each router variant reaches a symbol (or entry file)
covering it. Variants change only the router; retrieval and the store are shared.
"""

import json
import sys
import tempfile
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path.cwd() / "tests/python"))
import contextbench as cb
import contextbench_ts as ts

from oxide.core import routing

INF = 10**9


def adjacency(view, keys):
    found = {k: ([], []) for k in keys}
    if not keys:
        return found
    p = {"keys": sorted(set(keys))}
    m = "MATCH (a:Entity)-[r:Rel]->(b:Entity) WHERE "
    for d, q in enumerate(
        (
            m + "a.key IN $keys RETURN a.key, r.kind, b.key, r.resolution",
            m + "b.key IN $keys RETURN b.key, r.kind, a.key, r.resolution",
        )
    ):
        for key, kind, other, res in view._query(q, p):
            found[key][d].append((kind, other, res))
    return found


def neighbors(key, out, into, fanout, policy):
    children = sorted({b for k, b, _ in out if k == "contains"})
    scopes = sorted({a for k, a, _ in into if k == "contains" and a != "repo"})
    cross = {(b, f"{k}:out:{key}", r) for k, b, r in out if k in routing.FOLLOW}
    cross |= {(a, f"{k}:in:{key}", r) for k, a, r in into if k in routing.FOLLOW}
    if policy.get("no_ambiguous"):
        cross = {c for c in cross if c[2] != "ambiguous"}
    if policy.get("ambiguous_last"):
        cross = sorted(cross, key=lambda c: (c[2] == "ambiguous", c[0], c[1]))
    else:
        cross = sorted(cross)
    cross = [(b, o) for b, o, _ in cross]
    cut = len(children) > fanout or len(cross) > fanout
    children, cross = children[:fanout], cross[:fanout]
    found = [(c, f"child:{key}") for c in children] + [(s, f"scope:{key}") for s in scopes] + cross
    if policy.get("cross_first"):
        found = (
            cross + [(s, f"scope:{key}") for s in scopes] + [(c, f"child:{key}") for c in children]
        )
    unique = {}
    for n, o in found:
        unique.setdefault(n, o)
    return list(unique.items()), cut, len(children) + len(cross)


def vroute(view, entry_keys, regions_cap=64, fanout=16, edges_cap=2048, **policy):
    starts = list(dict.fromkeys(entry_keys)) or ["repo"]
    layer = [(k, "entry") for k in starts]
    seen = set(starts)
    depth_of = {}
    regions = edges = 0
    depth = 0
    while layer:
        adj = adjacency(view, [k for k, _ in layer[: max(0, regions_cap - regions)]])
        per_parent = []
        for key, _ in layer:
            if regions >= regions_cap or edges >= edges_cap:
                continue
            out, into = adj.get(key, ([], []))
            ns, cut, examined = neighbors(key, out, into, fanout, policy)
            regions += 1
            edges += examined
            depth_of[key] = depth
            if cut or len(ns) > fanout:
                ns = ns[:fanout]
            per_parent.append(ns)
        upcoming = []
        if policy.get("interleave"):
            order = [
                n
                for i in range(max(map(len, per_parent), default=0))
                for ns in per_parent
                if i < len(ns)
                for n in [ns[i]]
            ]
        else:
            order = [n for ns in per_parent for n in ns]
        for n, o in order:
            if n in seen:
                continue
            seen.add(n)
            if depth >= routing.MAX_DEPTH:
                continue
            upcoming.append((n, o))
        layer = upcoming
        depth += 1
    return depth_of


VARIANTS = {
    "base": {},
    "regions_inf": {"regions_cap": INF, "edges_cap": INF},
    "fanout_inf": {"fanout": INF},
    "both_inf": {"regions_cap": INF, "edges_cap": INF, "fanout": INF},
    "interleave": {"interleave": True},
    "cross_first": {"cross_first": True},
    "no_ambiguous": {"no_ambiguous": True},
    "ambiguous_last": {"ambiguous_last": True},
}


def main():
    out_path = Path(sys.argv[1])
    only = sys.argv[2] if len(sys.argv) > 2 else ""
    tasks = ts.load_tasks()
    frozen = {r["task"]: r for r in json.loads((ts.BASELINE / "results.json").read_text())}
    dev = [t for t in tasks["tasks"] if t["split"] == "dev" and t["corpus"].startswith(only)]
    report = []
    for name in dict.fromkeys(t["corpus"] for t in dev):
        corpus = tasks["corpora"][name]
        root = ts.corpus_root(name, corpus)
        print("probing", name, file=sys.stderr)
        with tempfile.TemporaryDirectory() as d:
            repo, _, _ = ts._indexed(root, Path(d))
            with repo:
                view = repo._view()
                lines = cb.Lines(root)
                for task in (t for t in dev if t["corpus"] == name):
                    found = repo.search(task["query"], symbols=task["symbols"])
                    entries = [e.entity.key for e in found.entries]
                    base = routing.route(view, entries)
                    mine = vroute(view, entries)
                    assert sorted(mine.items()) == sorted(
                        (c.key, c.depth) for c in base.candidates
                    ), task["id"]
                    routes = {v: vroute(view, entries, **kw) for v, kw in VARIANTS.items()}
                    comp = Counter()
                    for c in base.candidates:
                        kind = c.origins[0].split(":")[0] if c.depth else "entry"
                        comp[f"d{c.depth}:{kind}"] += 1
                    spans = []
                    for g in frozen[task["id"]]["gold"]:
                        if not g["verified"]:
                            continue
                        path, (s, e) = g["file"], g["lines"]
                        code = lines.code(path, s, e)
                        rows = (
                            view._query(
                                "MATCH (x:Entity) WHERE x.category='symbol' AND x.path=$p RETURN x.key",
                                {"p": path},
                            )
                            if path in view.files
                            else []
                        )
                        ents = view.entities(r[0] for r in rows)
                        holders = {
                            k
                            for k, en in ents.items()
                            if en.source
                            and any(
                                lines.span(en.source)[1] <= n <= lines.span(en.source)[2]
                                for n in code
                            )
                        }
                        hit = {}
                        for v, dep in routes.items():
                            ds = [dep[k] for k in holders if k in dep]
                            hit[v] = min(ds) if ds else None
                        spans.append(
                            {
                                "file": path,
                                "lines": [s, e],
                                "lost": g["lost"].get("4096", "complete"),
                                "cause": g.get("cause"),
                                "reached": hit,
                            }
                        )
                    report.append(
                        {
                            "task": task["id"],
                            "corpus": name,
                            "entries": len(entries),
                            "composition": dict(sorted(comp.items())),
                            "sizes": {v: len(r) for v, r in routes.items()},
                            "spans": spans,
                        }
                    )
    out_path.write_text(json.dumps(report, indent=1) + "\n")


if __name__ == "__main__":
    main()
