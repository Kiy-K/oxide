"""Do graph communities localize a task's gold files better than directories?
(preregistered in docs/phase-6/graph-v1/preregistration.md). Diagnostic only.

    uv run --with networkx==3.5 python \
        docs/phase-6/graph-v1/scripts/community_probe.py {py|ts} OUT.json

Per dev task: the file graph of resolved calls/references/imports between files
(undirected, weighted by edge count), Louvain (networkx, seed 0, resolution 1); the
anchor is the file of the top-1 search hit. For each verified gold file: does it share
the anchor's community, and the anchor's directory? Region size is the number of files.
"""

from __future__ import annotations

import json
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

import networkx as nx

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "tests" / "python"))

import oxide


def file_of(key: str) -> str | None:
    if key.startswith("symbol:"):
        return key[7:].split("#")[0]
    return key[5:] if key.startswith("file:") else None


def communities(repo: oxide.Repository) -> tuple[dict[str, int], dict[int, int]]:
    view = repo._view()
    rows = view._query(
        "MATCH (a:Entity)-[r:Rel]->(b:Entity) WHERE r.resolution = 'resolved' AND "
        "r.kind IN ['calls', 'references', 'imports'] RETURN a.key, b.key"
    )
    weights: Counter[tuple[str, str]] = Counter()
    for a, b in rows:
        fa, fb = file_of(a), file_of(b)
        if fa and fb and fa != fb:
            weights[tuple(sorted((fa, fb)))] += 1  # type: ignore[index]
    graph = nx.Graph()
    graph.add_nodes_from(sorted(view.files))
    for (a, b), w in sorted(weights.items()):
        graph.add_edge(a, b, weight=w)
    parts = nx.community.louvain_communities(graph, weight="weight", resolution=1, seed=0)
    member = {f: n for n, part in enumerate(sorted(parts, key=min)) for f in part}
    return member, Counter(member.values())


def main(language: str, out: Path) -> None:
    import contextbench as cb
    import contextbench_ts as ts

    harness: Any = cb if language == "py" else ts
    tasks = harness.load_tasks()
    dev = [t for t in tasks["tasks"] if t["split"] == "dev"]
    rows = []
    with tempfile.TemporaryDirectory(prefix="oxide-graph-v1-") as data:
        for name in sorted({t["corpus"] for t in dev}):
            root = harness.corpus_root(name, tasks["corpora"][name])
            with oxide.open_repository(root, data_dir=data) as repo:
                repo.index()
                member, size = communities(repo)
                files = sorted(repo._view().files)
                dirs = Counter(f.rpartition("/")[0] for f in files)
                for task in (t for t in dev if t["corpus"] == name):
                    hits = repo.search(task["query"], limit=1, symbols=task.get("symbols", []))
                    sources = [e.entity.source for e in hits.entries if e.entity.source]
                    anchor = sources[0].path if sources else None
                    gold = sorted({g["file"] for g in task["gold"]} & set(files))
                    if anchor is None or not gold:
                        continue
                    directory = anchor.rpartition("/")[0]
                    same = sum(member[g] == member[anchor] for g in gold)
                    near = sum(g.rpartition("/")[0] == directory for g in gold)
                    rows.append(
                        {
                            "task": task["id"],
                            "corpus": name,
                            "anchor": anchor,
                            "gold": gold,
                            "community_share": same / len(gold),
                            "directory_share": near / len(gold),
                            "community_files": size[member[anchor]],
                            "directory_files": dirs[directory],
                            "corpus_files": len(files),
                            "communities": len(size),
                        }
                    )
    keys = ("community_share", "directory_share", "community_files", "directory_files")
    summary: dict[str, float] = {k: round(sum(r[k] for r in rows) / len(rows), 4) for k in keys}
    summary["tasks"] = len(rows)
    out.write_text(json.dumps({"summary": summary, "rows": rows}, indent=1) + "\n")
    print(json.dumps(summary))


if __name__ == "__main__":
    main(sys.argv[1], Path(sys.argv[2]))
