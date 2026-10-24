# uv run python docs/phase-6/graph-v1/scripts/graph_audit.py OUT.json REPO...
"""Graph audit: relation provenance mix, unresolved-call shapes, degree and SCC structure."""

import collections
import json
import os
import resource
import sys
import tempfile
import time
from pathlib import Path

import oxide
from oxide.storage.ladybug import View


def tarjan(nodes, adj):
    index, low, on, stack, comps, counter = {}, {}, set(), [], [], [0]
    for root in nodes:
        if root in index:
            continue
        work = [(root, iter(adj.get(root, ())))]
        index[root] = low[root] = counter[0]
        counter[0] += 1
        stack.append(root)
        on.add(root)
        while work:
            v, it = work[-1]
            for w in it:
                if w not in index:
                    index[w] = low[w] = counter[0]
                    counter[0] += 1
                    stack.append(w)
                    on.add(w)
                    work.append((w, iter(adj.get(w, ()))))
                    break
                if w in on:
                    low[v] = min(low[v], index[w])
            else:
                work.pop()
                if work:
                    low[work[-1][0]] = min(low[work[-1][0]], low[v])
                if low[v] == index[v]:
                    comp = []
                    while True:
                        w = stack.pop()
                        on.discard(w)
                        comp.append(w)
                        if w == v:
                            break
                    comps.append(comp)
    return comps


def audit(path, data):
    t = time.perf_counter()
    with oxide.open_repository(path, data_dir=data) as repo:
        rep = repo.index()
        secs = time.perf_counter() - t
        view: View = repo._view()
        q = view._query
        rows = q("MATCH ()-[r:Rel]->() RETURN r.kind, r.resolution, r.basis, count(*)")
        rows += q("MATCH ()-[r:Opaque]->() RETURN r.kind, 'unresolved', r.basis, count(*)")
        mix = collections.Counter()
        for k, res, basis, n in rows:
            mix[f"{k}/{res}/{basis}"] += n
        # ambiguous sites and fan-out
        amb = q(
            "MATCH ()-[r:Rel]->() WHERE r.resolution='ambiguous' RETURN r.kind, r.site, count(*)"
        )
        ambfan = collections.Counter(n for _, _, n in amb)
        names = q("MATCH (a:Entity)-[r:Opaque]->() WHERE r.kind='calls' RETURN r.name, a.path")
        shape = collections.Counter()
        for name, p in names:
            lang = p.rsplit(".", 1)[-1]
            if name is None:
                shape[lang + ":none"] += 1
            elif name.startswith(("self.", "this.", "cls.")):
                shape[lang + ":self/this"] += 1
            elif "." in name or "::" in name:
                shape[lang + ":qualified"] += 1
            else:
                shape[lang + ":bare"] += 1
        top_unres = collections.Counter(
            n for n, _ in names if n and "." not in n and "::" not in n
        ).most_common(12)
        edges = q(
            "MATCH (a:Entity)-[r:Rel]->(b:Entity) WHERE r.resolution='resolved' "
            "AND r.kind IN ['calls','imports','references'] "
            "RETURN a.key, b.key, r.kind, a.category, b.category"
        )
        sym = collections.defaultdict(set)
        fil = collections.defaultdict(set)

        def file_of(k):
            return "file:" + k[7:].split("#")[0] if k.startswith("symbol:") else k

        indeg = collections.Counter()
        for a, b, kind, _, _ in edges:
            if kind == "calls":
                sym[a].add(b)
                indeg[b] += 1
            fa, fb = file_of(a), file_of(b)
            if fa != fb and fa.startswith("file:") and fb.startswith("file:"):
                fil[fa].add(fb)
        scc_sym = sorted(
            (
                len(c)
                for c in tarjan(
                    sorted(set(sym) | {b for v in sym.values() for b in v}),
                    {k: sorted(v) for k, v in sym.items()},
                )
                if len(c) > 1
            ),
            reverse=True,
        )
        fnodes = sorted(set(fil) | {b for v in fil.values() for b in v})
        scc_file = sorted(
            (len(c) for c in tarjan(fnodes, {k: sorted(v) for k, v in fil.items()}) if len(c) > 1),
            reverse=True,
        )
        return {
            "path": path,
            "files": rep.files,
            "entities": rep.entities,
            "relations": rep.relations,
            "index_s": round(secs, 2),
            "maxrss_mb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024,
            "mix": dict(sorted(mix.items())),
            "ambiguous_fanout": dict(sorted(ambfan.items())),
            "unresolved_call_shape": dict(sorted(shape.items())),
            "top_unresolved_bare": top_unres,
            "resolved_call_max_indegree": indeg.most_common(3),
            "call_scc_sizes_top": scc_sym[:5],
            "call_scc_count": len(scc_sym),
            "file_dep_scc_sizes_top": scc_file[:5],
            "file_dep_scc_count": len(scc_file),
            "file_dep_nodes": len(fnodes),
        }


if __name__ == "__main__":
    out = []
    with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as data:
        for p in sys.argv[2:]:
            r = audit(p, data)
            out.append(r)
            print(json.dumps(r)[:400], flush=True)
    Path(sys.argv[1]).write_text(json.dumps(out, indent=1))
