"""retrieval_sum.py PROBE.json...: entry-failure taxonomy and lexical counterfactual ranks."""

import json
import sys
from collections import Counter


def at(r, k=20):
    return r is not None and r <= k


for path in sys.argv[1:]:
    rep = json.load(open(path))
    spans = [s for t in rep for s in t["spans"]]
    lost = [s for s in spans if s["entry_rank"] is None]
    tax = Counter()
    for s in lost:
        if s["fts"] is not None:
            b = "21-100" if s["fts"] <= 100 else "101-1000" if s["fts"] <= 1000 else ">1000"
            tax[
                f"A matched, ranked {b}"
                + (" (distinct term in doc)" if s["distinct_doc"] else " (generic terms only)")
            ] += 1
        elif s["distinct_span"]:
            tax["B unmatched; distinctive query term only in body"] += 1
        elif s["span_overlap"]:
            tax["C unmatched; only generic shared terms in body"] += 1
        else:
            tax["D no shared term at all (vocabulary mismatch)"] += 1
    print(f"== {path}: tasks {len(rep)}, verified spans {len(spans)}, lost at entry {len(lost)}")
    for k, v in sorted(tax.items()):
        print(f"  {v:4}  {k}")
    for name in ("fts", "replica", "body", "replica_pruned", "body_pruned", "leaf_body"):
        print(
            f"  {name:8} span@20 {sum(at(s[name]) for s in spans):3}  lost-now@20 {sum(at(s[name]) for s in lost):3}  @100 {sum(at(s[name], 100) for s in spans):3}"
        )
    print(
        "  replica@20 but body>20 (displaced):",
        sum(at(s["replica"]) and not at(s["body"]) for s in spans),
        " body@20 not replica@20:",
        sum(at(s["body"]) and not at(s["replica"]) for s in spans),
    )
    gained = Counter(
        t["task"]
        for t in rep
        for s in t["spans"]
        if at(s["body"]) and not at(s["fts"]) and s["entry_rank"] is None
    )
    print("  tasks with body gains over production:", len(gained), dict(gained))
    for a, b in (
        ("replica", "replica_pruned"),
        ("replica", "body_pruned"),
        ("fts", "body_pruned"),
        ("replica", "leaf_body"),
        ("fts", "leaf_body"),
    ):
        print(
            f"  {b} vs {a}: +{sum(at(s[b]) and not at(s[a]) for s in spans)} / -{sum(at(s[a]) and not at(s[b]) for s in spans)}",
            "tasks gaining",
            len({t["task"] for t in rep for s in t["spans"] if at(s[b]) and not at(s[a])}),
        )
    files = {(t["task"], s["file"]): s for t in rep for s in t["spans"]}
    lostf = {
        k: s
        for k, s in files.items()
        if all(
            x["entry_rank"] is None
            for t in rep
            if t["task"] == k[0]
            for x in t["spans"]
            if x["file"] == k[1]
        )
    }
    for name in ("file_by_entity_rank", "file_doc_rank"):
        print(
            f"  gold files {len(files)} ({len(lostf)} with no entry): {name} @5 {sum(at(s[name], 5) for s in files.values())} @10 {sum(at(s[name], 10) for s in files.values())} @20 {sum(at(s[name]) for s in files.values())}; no-entry files @10 {sum(at(s[name], 10) for s in lostf.values())}"
        )
    print(
        "  query terms median",
        sorted(len(t["query_terms"]) for t in rep)[len(rep) // 2],
        "pruned median",
        sorted(len(t.get("pruned_terms", [])) for t in rep)[len(rep) // 2],
    )
    print(
        "  queries with absent terms:",
        sum(bool(t["absent_query_terms"]) for t in rep),
        "/",
        len(rep),
    )
