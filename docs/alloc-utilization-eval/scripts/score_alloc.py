#!/usr/bin/env python3
"""Score allocator-utilization dumps (examples/alloc_dump.rs).

usage: score_alloc.py <tasks.jsonl> <dump.jsonl> [--cb cb_gold.json | --gold heldout_gold.json]
                      [--json out.json]

Gold is line-level on both sets:
  held-out: `--gold` edit-locus lines (heldout_edit_gold.py); without it, the
            union of gold-symbol spans (parent-commit index, read-only);
  CB: ContextBench human gold lines (init+add), from cb_gold.json.
Held-out also reports gold_sym_cov (a gold symbol counts when a delivered line
falls inside its span).
Relevant tokens = chars of delivered gold lines / 4 (CONTEXT_CHARS_PER_TOKEN),
counting each file line once per pack. Delivered lines are the exact snippet
spans the allocator emitted (render_snippet), not full symbol spans.
"""
import json
import random
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

CPT = 4.0
tasks = {t["id"]: t for t in map(json.loads, open(sys.argv[1]))}
dump = [json.loads(l) for l in open(sys.argv[2]) if l.strip()]
CB = json.load(open(sys.argv[sys.argv.index("--cb") + 1])) if "--cb" in sys.argv else None
LG = CB or (json.load(open(sys.argv[sys.argv.index("--gold") + 1])) if "--gold" in sys.argv else None)
JSON_OUT = sys.argv[sys.argv.index("--json") + 1] if "--json" in sys.argv else None

_files, _db = {}, {}


def flines(root, f):
    k = (root, f)
    if k not in _files:
        try:
            _files[k] = (Path(root) / f).read_text(errors="replace").splitlines()
        except OSError:
            _files[k] = []
    return _files[k]


def gold_lines(t, symbols=False):
    """{file: set(lines)} and the gold unit list [(label, file, set(lines))]."""
    if LG is not None and not symbols:
        g = LG[t["id"]]
        gl = {f: set(v) for f, v in g["lines"].items()}
        return gl, [(f, f, s) for f, s in gl.items()]
    root = t["path"]
    if root not in _db:
        _db[root] = sqlite3.connect(f"file:{root}/.oxide/index.db?mode=ro", uri=True)
    gl, units = defaultdict(set), []
    for g in t["gold"]:
        f, qn = g.split("#", 1)
        row = _db[root].execute("select start_line,end_line from symbols where file=? and qualified_name=?",
                                (f, qn)).fetchone()
        if row:
            s = set(range(row[0], row[1] + 1))
            gl[f] |= s
            units.append((g, f, s))
    return dict(gl), units


_spans = {}


def db_span(root, key):
    """Span of `file#qualified_name` from the corpus index (read-only)."""
    k = (root, key)
    if k not in _spans:
        if root not in _db:
            _db[root] = sqlite3.connect(f"file:{root}/.oxide/index.db?mode=ro", uri=True)
        f, qn = key.split("#", 1)
        _spans[k] = _db[root].execute("select start_line,end_line from symbols where file=? and qualified_name=?",
                                      (f, qn)).fetchone()
    return _spans[k]


def fkey(k):
    return k.split("#", 1)[0]


def span_set(sp):
    return set(range(sp[0], sp[1] + 1)) if sp and sp[0] > 0 else set()


def chars(root, f, lines):
    fl = flines(root, f)
    return sum(len(fl[i - 1]) + 1 for i in lines if 0 < i <= len(fl))


def pack_metrics(rec, t, gl):
    root = t["path"]
    delivered = defaultdict(set)
    for p in rec["trace"]["packed"]:
        delivered[fkey(p["key"])] |= span_set(p["span"])
    gtot = sum(len(v) for v in gl.values())
    ghit = {f: delivered.get(f, set()) & v for f, v in gl.items()}
    rel_tok = sum(chars(root, f, s) for f, s in ghit.items()) / CPT
    files = []
    for p in rec["trace"]["packed"]:
        f = fkey(p["key"])
        if f not in files:
            files.append(f)
    gfiles = set(gl)
    m = {
        "used": rec["used"], "util": rec["used"] / rec["budget"], "items": len(rec["trace"]["packed"]),
        "files": len(files),
        "gold_line_cov": sum(len(s) for s in ghit.values()) / gtot if gtot else 0.0,
        "rel_tok": rel_tok, "rel_per_used": rel_tok / rec["used"] if rec["used"] else 0.0,
        "rel_per_budget": rel_tok / rec["budget"],
        "gold_file_cov": len(gfiles & set(files)) / len(gfiles) if gfiles else 0.0,
        "added_items": sum(p["added"] for p in rec["trace"]["packed"]),
        "added_tok": sum(p["est"] for p in rec["trace"]["packed"] if p["added"]),
        "added_hit_tok": sum(p["est"] for p in rec["trace"]["packed"]
                             if p["added"] and span_set(p["span"]) & gl.get(fkey(p["key"]), set())),
        "ms": min(rec["context_ms"]) if rec["context_ms"] else 0.0,
    }
    if CB is not None:
        gf = set(CB[t["id"]]["files"]) or gfiles
        for k in (5, 10):
            m[f"R@{k}"] = len(gf & set(files[:k])) / max(1, len(gf))
    else:
        packed_keys = {p["key"] for p in rec["trace"]["packed"]}
        units = gold_lines(t, symbols=True)[1]
        m["gold_sym_cov"] = (sum(1 for g, f, s in units if g in packed_keys or delivered.get(f, set()) & s)
                             / len(units)) if units else 0.0
    return m


def is_mod(k):
    return k.endswith(":__module__")


def attribute(rec, gl, root):
    """Baseline funnel: where each undelivered gold line was last available.

    Only concrete (non-module) symbols attribute a line: a module symbol's span
    is its whole file, so it would "cover" every gold line there. Lines only a
    module candidate spans get their own `M:` bucket.
    """
    tr = rec["trace"]
    delivered = defaultdict(set)
    for p in tr["packed"]:
        delivered[fkey(p["key"])] |= span_set(p["span"])
    packed_keys = {p["key"] for p in tr["packed"]}
    omitted = {k: w for k, w in tr["omitted"]}
    pool = {c["key"]: c for c in tr["pool"]}
    n_seeds = len(tr["seeds"])
    fused = {k: (i, sp) for i, (k, _, sp) in enumerate(rec["fused50"] or [])}
    exp_lost = {x["key"] for x in tr.get("expansion_lost", [])} | {x["key"] for x in tr.get("structural_lost", [])}
    spans = {k: sp for k, (i, sp) in fused.items()}
    for c in tr["pool"]:
        spans[c["key"]] = c["span"]
    everything = set(spans) | set(omitted) | packed_keys
    stage = Counter()
    for f, gls in gl.items():
        for ln in gls:
            if ln in delivered.get(f, set()):
                stage["delivered"] += 1
                continue
            cov = lambda k: fkey(k) == f and not is_mod(k) and ln in span_set(spans.get(k))
            if any(cov(k) for k in packed_keys):
                stage["D:truncated-snippet"] += 1
                continue
            why = next((omitted[k] for k in pool if cov(k) and k in omitted), None)
            if why:
                stage[f"pool-dropped:{why}"] += 1
                continue
            dedup = [k for k, w in omitted.items() if cov(k) and "subsumed" in w]
            if dedup:
                stage[f"dedup:{omitted[dedup[0]]}"] += 1
                continue
            in_fused = [i for k, (i, sp) in fused.items() if cov(k)]
            if in_fused and min(in_fused) >= n_seeds:
                stage["A:fused-rank>seed-limit"] += 1
                continue
            if any(fkey(k) == f and not is_mod(k) and ln in span_set(db_span(root, k)) for k in exp_lost):
                stage["A:expansion-cap"] += 1
                continue
            mods = [k for k in everything if is_mod(k) and fkey(k) == f]
            if mods:
                stage["M:packed-module" if any(k in packed_keys for k in mods) else "M:module-candidate-only"] += 1
                continue
            stage["A:not-retrieved"] += 1
    return stage


def classify(stage):
    """Primary A/B/C/D label (priority B > D > B-floor > A > C) plus all labels."""
    lab = set()
    if any(k.startswith("pool-dropped:") and "cap" in k for k in stage):
        lab.add("B")
    if any(k.startswith("pool-dropped:below relevance floor") for k in stage):
        lab.add("B-floor")
    if stage.get("D:truncated-snippet"):
        lab.add("D")
    if any(k.startswith(("A:", "dedup:", "M:")) for k in stage):
        lab.add("A")
    if not lab:
        lab.add("C")
    for p in ("B", "D", "B-floor", "A", "C"):
        if p in lab:
            return p, lab


def boot(xs, n=2000, seed=0):
    rnd = random.Random(seed)
    if not xs:
        return (0.0, 0.0, 0.0)
    mean = sum(xs) / len(xs)
    bs = sorted(sum(rnd.choice(xs) for _ in xs) / len(xs) for _ in range(n))
    return mean, bs[int(0.025 * n)], bs[int(0.975 * n)]


def main():
    by = defaultdict(dict)  # (mode, blast, ch) -> id -> metrics
    diag = defaultdict(list)
    for r in dump:
        t = tasks[r["id"]]
        gl, _ = gold_lines(t)
        if not gl:
            continue
        key = (r["mode"], r["blast"], r["ch"])
        by[key][r["id"]] = pack_metrics(r, t, gl)
        if r["ch"] == 0:
            st = attribute(r, gl, t["path"])
            diag[(r["mode"], r["blast"])].append((r["id"], st, *classify(st), r))
    out = {"arms": {}, "diag": {}, "labels": {}}
    for mb, rows in sorted(diag.items()):
        print(f"\n## funnel / classification  mode={mb[0]} blast={mb[1]}  n={len(rows)}")
        tot, labc, multi = Counter(), Counter(), Counter()
        for tid, st, lab, labs, _ in rows:
            tot.update(st)
            labc[lab] += 1
            multi.update(labs)
            out["labels"][f"{mb}|{tid}"] = [lab, sorted(labs), dict(st)]
        g = sum(tot.values())
        for k, v in tot.most_common():
            print(f"  {k:45s} {v:6d} gold lines  {v / g:6.1%}")
        print("  primary label:", dict(labc), " multi-label:", dict(multi))
        omit = Counter()
        c = Counter()
        for *_, r in rows:
            tr = r["trace"]
            omit.update(w for _, w in tr["omitted"])
            c["seeds"] += len(tr["seeds"])
            c["exp_add"] += len(tr.get("expansion_added", []))
            c["ev_add"] += len(tr.get("evidence_added", []))
            c["exp_lost"] += len(tr.get("expansion_lost", []))
            c["st_lost"] += len(tr.get("structural_lost", []))
            c["pool"] += len(tr["pool"])
            c["packed"] += len(tr["packed"])
            c["pool_full"] += sum(x["full_tok"] for x in tr["pool"])
            c["pool_cap"] += sum(x["cap_tok"] for x in tr["pool"])
            c["unused_cap"] += sum(x["cap_tok"] for x in tr["pool"] if x["key"] not in {p["key"] for p in tr["packed"]})
            c["trunc_packed"] += sum(p["truncated"] for p in tr["packed"])
        n = len(rows)
        print("  per task:", {k: round(v / n, 2) for k, v in c.items()})
        print("  omissions/task:", {k: round(v / n, 2) for k, v in omit.most_common()})
        out["diag"][f"{mb}"] = {"funnel": dict(tot), "labels": dict(labc), "multi": dict(multi),
                                "per_task": {k: v / n for k, v in c.items()},
                                "omit_per_task": {k: v / n for k, v in omit.items()}}
    keys = ["util", "items", "files", "gold_line_cov", "rel_tok", "rel_per_used", "rel_per_budget",
            "gold_file_cov", "added_items", "added_tok", "added_hit_tok", "ms"]
    keys += ["R@5", "R@10"] if CB is not None else ["gold_sym_cov"]
    print("\n## arms (mean over tasks)")
    print("mode blast ch | " + " | ".join(keys))
    for k in sorted(by):
        rows = by[k].values()
        vals = [sum(m[x] for m in rows) / len(rows) for x in keys]
        print(f"{k[0]} {int(k[1])} {k[2]} | " + " | ".join(f"{v:.3f}" for v in vals))
        out["arms"][f"{k}"] = dict(zip(keys, vals))
    print("\n## paired vs baseline (ch0, same mode/blast): mean [95% CI], improved/regressed tasks")
    for k in sorted(by):
        if k[2] == 0:
            continue
        base = by[(k[0], k[1], 0)]
        ids = sorted(set(base) & set(by[k]))
        for metric in ["gold_line_cov", "rel_per_used", "rel_tok"] + (["R@5", "R@10"] if CB else ["gold_sym_cov"]):
            d = [by[k][i][metric] - base[i][metric] for i in ids]
            mean, lo, hi = boot(d)
            up = sum(x > 1e-9 for x in d)
            dn = sum(x < -1e-9 for x in d)
            print(f"  {k[0]} blast={int(k[1])} ch{k[2]} {metric:14s} {mean:+.4f} [{lo:+.4f},{hi:+.4f}]"
                  f"  +{up}/-{dn} of {len(ids)}")
    if JSON_OUT:
        out["per_task"] = {f"{k}": v for k, v in by.items()}
        json.dump(out, open(JSON_OUT, "w"), indent=1)


main()
