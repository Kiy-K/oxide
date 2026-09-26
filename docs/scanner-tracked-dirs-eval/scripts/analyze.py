#!/usr/bin/env python3
"""Summarize challenger_eval.py output against the pre-registered gate.

usage: analyze.py <eval.jsonl>
"""
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "scripts/agent_eval"))
sys.path.insert(0, str(HERE))
import contextbench_run as cb  # noqa: E402
from affected import denied_dirs  # noqa: E402

DEP = ("vendor", "node_modules", "third_party")


def gold_spans(row):
    out = defaultdict(list)
    for it in json.loads(row["gold_context"]):
        if it.get("file"):
            out[cb.normalize_gold_path(it["file"])].append((it.get("start_line", 1), it.get("end_line", 1)))
    return out


def skipped_gold_in(items, spans):
    """Non-module items in a formerly skipped directory overlapping gold."""
    hits = []
    for iid, kind, s, e, _ in items:
        f = iid.split("#")[0]
        if kind != "module" and denied_dirs(f) and any(s <= ge and gs <= e for gs, ge in spans.get(f, ())):
            hits.append(iid)
    return hits


def main():
    recs = [json.loads(l) for l in open(sys.argv[1])]
    rows = {r["instance_id"]: r for r in cb.load_tasks(langs=("python", "typescript", "javascript", "go",
                                                               "rust", "c", "cpp", "java"))}
    by = defaultdict(dict)
    for r in recs:
        by[r["instance_id"]][r["arm"]] = r
    table, ops = [], defaultdict(list)
    for iid, arms in sorted(by.items()):
        spans = gold_spans(rows[iid])
        base = arms.get("main")
        for arm in ("tracked", "narrow"):
            r = arms.get(arm)
            if r is None:
                continue
            if "identical_to" in r:
                r = arms.get(r["identical_to"])
                if r is None:
                    continue
            ok = bool(base) and "error" not in base and "error" not in r
            row = dict(task=iid[-8:], repo=rows[iid]["repo"], arm=arm,
                       identical_to=arms[arm].get("identical_to"), error=not ok)
            if ok:
                bc, rc = base["context"]["score"], r["context"]["score"]
                row.update(
                    added_files=len(r["index"]["files_under_skipped_dirs"]),
                    ctx_file=(bc["file"]["coverage"], rc["file"]["coverage"]),
                    ctx_line=(bc["line"]["coverage"], rc["line"]["coverage"]),
                    srch_file=(base["search"]["score"]["file"]["coverage"], r["search"]["score"]["file"]["coverage"]),
                    skipped_gold_in_pack=skipped_gold_in(r["context"]["items"], spans),
                    skipped_gold_in_search=[h for h, s, e in r["search"]["hits"]
                                            if denied_dirs(h.split("#")[0])
                                            and any(s <= ge and gs <= e for gs, ge in spans.get(h.split("#")[0], ()))],
                    dep_items=[i[0] for i in r["context"]["items"] if any(d in i[0].split("/") for d in DEP)]
                    + [h for h, _, _ in r["search"]["hits"] if any(d in h.split("/") for d in DEP)],
                    pack_changed=base["context"]["sha256"] != r["context"]["sha256"],
                    new_in_pack=[i[0] for i in r["context"]["items"] if denied_dirs(i[0].split("#")[0])],
                )
                if not arms[arm].get("identical_to"):
                    ops[arm].append(dict(
                        cold=r["cold_index"]["wall_s"] / base["cold_index"]["wall_s"] - 1,
                        rss=r["cold_index"]["peak_tree_rss_kb"] / base["cold_index"]["peak_tree_rss_kb"] - 1,
                        db=r["index"]["db_bytes"] / base["index"]["db_bytes"] - 1,
                        incr_regular_ms=1000 * (r["incremental_regular"]["wall_s"] - base["incremental_regular"]["wall_s"]),
                        ctx_ms=1000 * (r["context_latency"]["median_s"] - base["context_latency"]["median_s"]),
                        srch_ms=1000 * (r["search_latency"]["median_s"] - base["search_latency"]["median_s"]),
                        task=iid[-8:]))
            table.append(row)
    for row in table:
        print(json.dumps(row))
    for arm, xs in ops.items():
        print(f"== ops {arm} (n={len(xs)} re-indexed tasks)")
        for k in ("cold", "rss", "db"):
            v = [x[k] for x in xs]
            print(f"  {k:5s} median {statistics.median(v):+.1%}  max {max(v):+.1%}  ({max(xs, key=lambda x: x[k])['task']})")
        for k in ("incr_regular_ms", "ctx_ms", "srch_ms"):
            v = [x[k] for x in xs]
            print(f"  {k:15s} median {statistics.median(v):+.1f}  max {max(v):+.1f}")
    for arm in ("tracked", "narrow"):
        rs = [r for r in table if r["arm"] == arm and not r["error"]]
        if not rs:
            continue
        gain = [r for r in rs if r["skipped_gold_in_pack"]]
        print(f"== {arm}: tasks {len(rs)}; skipped gold enters pack in {len(gain)} "
              f"{sorted({r['repo'] for r in gain})}; packs changed {sum(r['pack_changed'] for r in rs)}; "
              f"ctx file cov mean {statistics.fmean(r['ctx_file'][0] for r in rs):.3f} -> "
              f"{statistics.fmean(r['ctx_file'][1] for r in rs):.3f}; line {statistics.fmean(r['ctx_line'][0] for r in rs):.3f} -> "
              f"{statistics.fmean(r['ctx_line'][1] for r in rs):.3f}; file-cov losses {sum(r['ctx_file'][1] < r['ctx_file'][0] for r in rs)}, "
              f"gains {sum(r['ctx_file'][1] > r['ctx_file'][0] for r in rs)}; dependency items {sum(len(r['dep_items']) for r in rs)}")


if __name__ == "__main__":
    main()
