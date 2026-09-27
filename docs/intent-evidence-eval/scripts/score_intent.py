#!/usr/bin/env python3
"""Offline scorer for the developer-intent evidence experiment (issue #30).

Protocol — fixed before any challenger result was looked at:

Channels (from examples/intent_dump.rs)
  A          production `fused` list (frozen RRF K=60, 0.6/0.4, depth 200) and
             production context pack. Never modified.
  I          research-only intent channel: RRF(K=60) of intent BM25 top-200
             (weight 0.6) and intent semantic top-200 (weight 0.4) over kept
             evidence records — the production weights reused, not tuned.
             I-lex / I-sem: one channel only (ablation).

Representations
  B  separate evidence nodes: units = symbols ∪ evidence records;
     rank = RRF(K=60) of [A (w 1.0), I (w 0.5)]. An evidence unit "locates"
     a gold symbol if it is attached to it or its span lies inside it.
  C  symbol-attached: I mapped to attached symbols (docstring / leading /
     inline comments with a symbol; first occurrence wins) → symbol list;
     rank = RRF(K=60) of [A (w 1.0), I-symbols (w 0.5)]. Units stay symbols;
     no symbol id / name / embedding text changes.
  H  constrained hybrid: C ranking; pack gets C's insertions plus at most one
     file/repo-level evidence record (module_doc/readme/doc/changelog).
  Post-hoc sensitivity (added after the first BCD run showed that at w=0.5 an
  intent unit's best RRF score, 0.5/61, only ties production rank ~62, so B/C
  can never place a new unit in the top 10): B1 / C1 = the same with the
  intent list at equal weight w=1.0. Reported separately, never as primary.
  Type ablations: B-code (docstring/leading/inline only), B-docs
  (module_doc/readme/doc/changelog only). Channel ablations: C-lex, C-sem.

Packs are capped at the production budget (4096): A = production
pack; a challenger inserts at most 2 units (evidence cost = min(chars/4,
350)+12; symbol cost = min(body chars/4, 350)+12, the production per-item cap
and overhead), dropping A's lowest-scored items only if the budget would be
exceeded. An evidence unit whose span lies inside a symbol already in the
pack (or is attached to one) is a duplicate and is not inserted (counted).

Equal-token control (added with the B1/C1 sensitivity, before scoring the
held-out/CB sets): A+ = A's ranking, pack gets A's own next two top-10
symbols not already packed under the same insertion rule — a same-rule
insertion control, NOT token-matched (A+ inserts somewhat more tokens than C;
compare per-token metrics). The 4096 budget never binds in these runs, so no
pack item is ever replaced: the experiment tests two extra selections, not
reallocation within the production pack.

Pack items are scored on the lines production would deliver: symbols through a
port of context.rs::render_snippet (350-token cap, query-term window), evidence
records by their own span. Ranked-list @k metrics use full symbol spans.

Gold: symbol-gold sets use task["gold"] (file#qualified_name) with spans from
the corpus index; ContextBench uses its own evaluator (line/file/symbol
coverage of item spans) via scripts/agent_eval/contextbench_run.py.
Strata: desc = no gold symbol's name occurs as a query token; ident otherwise.
CIs: paired bootstrap, 2000 resamples, seed 0.

Controls from a sidecar (code_control.py): --extra <sidecar.jsonl> adds one
C-shaped arm per list (rank = RRF [A w1.0, list w0.5]; same pack rule).

usage: score_intent.py <tasks.jsonl> <dump.jsonl> [--cb] [--extra sidecar.jsonl]
"""
import json
import random
import re
import sqlite3
import statistics
import sys
from collections import defaultdict
from pathlib import Path

K = 60
W_INTENT = 0.5
M_INSERT = 2
CAP, OVERHEAD, BUDGET = 350, 12, 4096
EV = Path.home() / ".cache/oxide-intent-eval/ev"
SYMBOL_TYPES = {"docstring", "leading_comment", "inline_comment"}
DOC_TYPES = {"module_doc", "readme", "doc", "changelog"}
REPS = ["A", "A+", "B", "B-code", "B-docs", "C", "C-lex", "C-sem", "H", "B1", "C1"]

tasks = {t["id"]: t for t in map(json.loads, open(sys.argv[1]))}
dump = [json.loads(l) for l in open(sys.argv[2]) if l.strip()]
IS_CB = "--cb" in sys.argv
EXTRA = {}
if "--extra" in sys.argv:
    for l in open(sys.argv[sys.argv.index("--extra") + 1]):
        r = json.loads(l)
        EXTRA[r["id"]] = r["lists"]
    REPS += sorted(next(iter(EXTRA.values())))
if IS_CB:
    ROOT = Path(__file__).resolve().parents[3]
    sys.path.insert(0, str(ROOT / "scripts/agent_eval"))
    import contextbench_run as cb  # noqa: E402
    pinned = set((ROOT / "eval-agent/results/tier_a_instances.txt").read_text().split())
    cbrows = {r["instance_id"]: r for r in cb.load_tasks() if r["instance_id"] in pinned}

_ev_cache, _file_cache, _db_cache = {}, {}, {}


def evidence(path):
    name = Path(path).name
    if name not in _ev_cache:
        _ev_cache[name] = {r["eid"]: r for r in map(json.loads, open(EV / f"{name}.jsonl"))}
    return _ev_cache[name]


def file_lines(root, f):
    key = (root, f)
    if key not in _file_cache:
        try:
            _file_cache[key] = (Path(root) / f).read_text(errors="replace").splitlines()
        except OSError:
            _file_cache[key] = []
    return _file_cache[key]


def gold_spans(root, gold):
    if root not in _db_cache:
        _db_cache[root] = sqlite3.connect(f"file:{root}/.oxide/index.db?mode=ro", uri=True)
    out = {}
    for g in gold:
        f, qn = g.split("#", 1)
        row = _db_cache[root].execute("select start_line, end_line from symbols where file=? and qualified_name=?",
                                      (f, qn)).fetchone()
        if row:
            out[g] = (f, row[0], row[1])
    return out


def rrf(lists):
    """lists: [(weight, [unit,...])] -> fused order (deterministic tie-break on unit)."""
    score = defaultdict(float)
    for w, units in lists:
        for r, u in enumerate(units):
            score[u] += w / (K + r + 1)
    return [u for u, _ in sorted(score.items(), key=lambda x: (-x[1], x[0]))]


def intent_rank(rec, which="both"):
    lex = [e for e, _ in rec["intent_lex"]]
    sem = [e for e, _ in rec["intent_sem"]]
    if which == "lex":
        return lex
    if which == "sem":
        return sem
    return rrf([(0.6, lex), (0.4, sem)])


def unit_span(u, rec, evs):
    if u.startswith("ev:"):
        r = evs[u]
        return r["file"], r["start_line"], r["end_line"]
    sp = rec["spans"].get(u)
    return (u.split("#", 1)[0], sp[0], sp[1]) if sp else None


def locates(u, g, gspan, evs):
    """Does unit u identify gold symbol g? Symbols: exact. Evidence: attached or inside."""
    if not u.startswith("ev:"):
        return u == g
    r = evs[u]
    if r["file"] != gspan[0]:
        return False
    if r["symbol"] and f"{r['file']}#{r['symbol']}" == g:
        return True
    return gspan[1] <= r["start_line"] and r["end_line"] <= gspan[2]


def query_terms(q):
    out = []
    for w in re.split(r"[^0-9a-z]+", q.lower()):
        if len(w) >= 3 and w not in out:
            out.append(w)
    return out


def snippet(rec, u, root, query):
    """Port of context.rs::render_snippet at the production per-item cap:
    returns (file, first_line, last_line, est_tokens) of the lines production
    would actually deliver for symbol u (whole body if it fits, else a window
    around the first densest query-term line, else the head)."""
    sp = rec["spans"].get(u)
    f = u.split("#", 1)[0]
    if not sp:
        return None
    lines = file_lines(root, f)
    lo, hi = sp[0] - 1, min(sp[1], len(lines))
    if hi <= lo:
        return None
    body = lines[lo:hi]
    budget = CAP * 4
    if sum(len(l) + 1 for l in body) <= budget:
        a, b = 0, len(body) - 1
    else:
        terms = query_terms(query)
        scores = [sum(t in l.lower() for t in terms) for l in body]
        best = max(scores)
        if best == 0:
            used, b = 0, -1
            for i, l in enumerate(body):
                if used + len(l) + 1 > budget:
                    break
                used += len(l) + 1
                b = i
            a = 0
            b = max(b, 0)
        else:
            a = b = scores.index(best)
            used = len(body[a]) + 1
            while True:
                can_lo, can_hi = a > 0, b + 1 < len(body)
                if not can_lo and not can_hi:
                    break
                grow_lo = can_lo and (not can_hi or len(body[a - 1]) <= len(body[b + 1]))
                cost = len(body[a - 1]) + 1 if grow_lo else len(body[b + 1]) + 1
                if used + cost > budget:
                    break
                if grow_lo:
                    a -= 1
                else:
                    b += 1
                used += cost
    text = "\n".join(body[a:b + 1])
    return f, lo + a + 1, lo + b + 1, -(-len(text) // 4) + OVERHEAD


def sym_tokens(rec, u, root, query):
    sn = snippet(rec, u, root, query)
    return sn[3] if sn else CAP + OVERHEAD


def ev_tokens(r):
    return min(int(r["chars"] / 4), CAP) + OVERHEAD


def to_symbols(ev_list, evs):
    seen, syms = set(), []
    for e in ev_list:
        r = evs.get(e)
        if r and r["type"] in SYMBOL_TYPES and r["symbol"]:
            k = f"{r['file']}#{r['symbol']}"
            if k not in seen:
                seen.add(k)
                syms.append(k)
    return syms


def rankings(rec, evs):
    A = [s for s, _ in rec["fused"]]
    I = intent_rank(rec)
    return {
        "A": A,
        "A+": A,
        "B": rrf([(1.0, A), (W_INTENT, I)]),
        "B-code": rrf([(1.0, A), (W_INTENT, [e for e in I if evs[e]["type"] in SYMBOL_TYPES])]),
        "B-docs": rrf([(1.0, A), (W_INTENT, [e for e in I if evs[e]["type"] in DOC_TYPES])]),
        "C": rrf([(1.0, A), (W_INTENT, to_symbols(I, evs))]),
        "C-lex": rrf([(1.0, A), (W_INTENT, to_symbols(intent_rank(rec, "lex"), evs))]),
        "C-sem": rrf([(1.0, A), (W_INTENT, to_symbols(intent_rank(rec, "sem"), evs))]),
        "B1": rrf([(1.0, A), (1.0, I)]),
        "C1": rrf([(1.0, A), (1.0, to_symbols(I, evs))]),
        **{name: rrf([(1.0, A), (W_INTENT, lst)]) for name, lst in EXTRA.get(rec["id"], {}).items()},
        "_I": I,
    }


def packs(rec, R, evs, root, query):
    """Token-neutral packs: {name: [(unit, tokens, inserted, score)]}, duplicate counts."""
    base = [(i["id"], i["est_tokens"], False, i["score"]) for i in rec["pack"]["items"]]
    in_pack = {u for u, *_ in base}
    pack_spans = [s for s in (unit_span(u, rec, evs) for u in in_pack) if s]

    def inside_pack(r):
        return any(r["file"] == f and s <= r["start_line"] and r["end_line"] <= e for f, s, e in pack_spans)

    def build(inserts):
        items = list(base)
        used = sum(t for _, t, _, _ in items)
        for u, t in inserts:
            while used + t > BUDGET and any(not it[2] for it in items):
                j = min((k for k, it in enumerate(items) if not it[2]), key=lambda k: items[k][3])
                used -= items[j][1]
                items.pop(j)
            items.append((u, t, True, 0.0))
            used += t
        return items

    out, dups = {"A": base}, {}
    for name, pool in (("B", R["_I"]),
                       ("B-code", [e for e in R["_I"] if evs[e]["type"] in SYMBOL_TYPES]),
                       ("B-docs", [e for e in R["_I"] if evs[e]["type"] in DOC_TYPES])):
        ins, d = [], 0
        for e in pool:
            if len(ins) == M_INSERT:
                break
            r = evs[e]
            if inside_pack(r) or (r["symbol"] and f"{r['file']}#{r['symbol']}" in in_pack):
                d += 1
                continue
            ins.append((e, ev_tokens(r)))
        out[name], dups[name] = build(ins), d
    for name in ("A+", "C", "C-lex", "C-sem", "C1", *EXTRA.get(rec["id"], {})):
        ins = [(u, sym_tokens(rec, u, root, query)) for u in R[name][:10] if u not in in_pack][:M_INSERT]
        out[name] = build(ins)
    ins = [(u, sym_tokens(rec, u, root, query)) for u in R["C"][:10] if u not in in_pack][:M_INSERT]
    doc = next((e for e in R["_I"] if evs[e]["type"] in DOC_TYPES), None)
    if doc:
        ins.append((doc, ev_tokens(evs[doc])))
    out["H"] = build(ins)
    # B1: insert the evidence units that B1's fused top-10 contains (same cap/dup rule).
    ins, d = [], 0
    for e in [u for u in R["B1"][:10] if u.startswith("ev:")]:
        if len(ins) == M_INSERT:
            break
        r = evs[e]
        if inside_pack(r) or (r["symbol"] and f"{r['file']}#{r['symbol']}" in in_pack):
            d += 1
            continue
        ins.append((e, ev_tokens(r)))
    out["B1"], dups["B1"] = build(ins), d
    return out, dups


def ci(d, n=2000):
    rng = random.Random(0)
    bs = sorted(statistics.fmean(rng.choices(d, k=len(d))) for _ in range(n))
    return bs[int(0.025 * n)], bs[int(0.975 * n) - 1]


def span_lines(sp):
    return set(range(sp[1], sp[2] + 1))


CAMEL = re.compile(r"[A-Z]+(?=[A-Z][a-z0-9])|[A-Z]?[a-z0-9]+|[A-Z]+")
STOP = {"the", "and", "for", "with", "this", "that", "from", "into", "self", "none", "null", "undefined",
        "true", "false", "fn", "func", "def", "let", "var", "const", "return", "import"}


def subtokens(text):
    """Python mirror of embeddings::tokenize (camel/snake split, lowercase, len>=2, stopwords)."""
    return [p.lower() for raw in re.split(r"[^\w]+", text) for part in raw.split("_")
            for p in CAMEL.findall(part) if len(p) >= 2 and p.lower() not in STOP]


per, strata, strict = {}, {}, {}
for rec in dump:
    t = tasks[rec["id"]]
    root = t["path"]
    evs = evidence(root)
    R = rankings(rec, evs)
    R["H"] = R["C"]
    P, dups = packs(rec, R, evs, root, t["query"])
    m = {}
    if IS_CB:
        row = cbrows[rec["id"]]

        def delivered(u):
            """Lines an agent actually receives: evidence span, or the symbol's snippet window."""
            if u.startswith("ev:"):
                return unit_span(u, rec, evs)
            sn = snippet(rec, u, root, t["query"])
            return sn[:3] if sn else None

        def cbeval(units, pack=False):
            items = []
            for u in units:
                sp = delivered(u) if pack else unit_span(u, rec, evs)
                if sp:
                    items.append({"file": sp[0], "start_line": sp[1], "end_line": sp[2]})
            return cb.evaluate_task(Path(root), row, items)

        g = cb.Gold({"init_ctx": json.loads(row["gold_context"]) if isinstance(row["gold_context"], str)
                     else row["gold_context"], "repo_url": row["repo_url"], "commit": row["base_commit"]})
        glines = defaultdict(set)
        for it in g.init + g.add:
            if it.get("file"):
                glines[cb.normalize_gold_path(it["file"])].update(range(it.get("start_line", 1), it.get("end_line", 1) + 1))

        def gold_hit(u):
            sp = delivered(u)
            return len(glines.get(sp[0], set()) & span_lines(sp)) if sp else 0

        for rep in REPS:
            e = cbeval(R[rep][:10])
            m[f"{rep} @10 file"] = e["file"]["coverage"]
            m[f"{rep} @10 sym"] = e["symbol"]["coverage"]
            m[f"{rep} @10 line"] = e["line"]["coverage"]
            items = P[rep]
            e = cbeval([u for u, *_ in items], pack=True)
            m[f"{rep} pack file"] = e["file"]["coverage"]
            m[f"{rep} pack line"] = e["line"]["coverage"]
            m[f"{rep} pack _rel"] = sum(gold_hit(u) for u, *_ in items)
            m[f"{rep} pack _tok"] = sum(tk for _, tk, _, _ in items)
            m[f"{rep} pack _instok"] = sum(tk for _, tk, ins, _ in items if ins)
            m[f"{rep} pack _inshit"] = sum(tk for u, tk, ins, _ in items if ins and gold_hit(u))
        strata[rec["id"]] = "all"
    else:
        gs = gold_spans(root, t["gold"])
        gfiles = {v[0] for v in gs.values()}
        A200 = set(R["A"][:200])
        q = set(re.findall(r"[a-z0-9_]+", t["query"].lower()))
        strata[rec["id"]] = "ident" if any(g.split("#", 1)[1].split(".")[-1].lower() in q for g in gs) else "desc"
        # desc-strict: not even one sub-token (tokenizer-split) of a gold name in the query
        qt = set(subtokens(t["query"]))
        strict[rec["id"]] = strata[rec["id"]] == "desc" and not any(
            set(subtokens(g.split("#", 1)[1].split(".")[-1])) & qt for g in gs)

        def hit(u):
            return any(locates(u, g, sp, evs) for g, sp in gs.items())

        for rep in REPS:
            for k in (5, 10):
                top = R[rep][:k]
                m[f"{rep} @{k} sym"] = statistics.fmean(
                    any(locates(u, g, sp, evs) for u in top) for g, sp in gs.items()) if gs else 0.0
                m[f"{rep} @{k} file"] = statistics.fmean(
                    any((unit_span(u, rec, evs) or ("",))[0] == f for u in top) for f in gfiles) if gfiles else 0.0
            m[f"{rep} route_rec"] = sum(1 for g, sp in gs.items() if g not in A200
                                        and any(locates(u, g, sp, evs) for u in R[rep][:10]))
            m[f"{rep} displaced@10"] = sum(1 for g, sp in gs.items() if g in R["A"][:10]
                                           and not any(locates(u, g, sp, evs) for u in R[rep][:10]))
            items = P[rep]
            m[f"{rep} pack sym"] = statistics.fmean(
                any(locates(u, g, sp, evs) for u, *_ in items) for g, sp in gs.items()) if gs else 0.0
            m[f"{rep} pack _rel"] = sum(tk for u, tk, _, _ in items if hit(u))
            m[f"{rep} pack _tok"] = sum(tk for _, tk, _, _ in items)
            m[f"{rep} pack _instok"] = sum(tk for _, tk, ins, _ in items if ins)
            m[f"{rep} pack _inshit"] = sum(tk for u, tk, ins, _ in items if ins and hit(u))
            m[f"{rep} pack displaced"] = sum(1 for g in gs if any(u == g for u, *_ in P["A"])
                                             and not any(locates(u, g, gs[g], evs) for u, *_ in items))
        m["_route_loss"] = sum(1 for g in gs if g not in A200)
    for rep, v in dups.items():
        m[f"{rep} pack _dups"] = v
    per[rec["id"]] = m

ids = sorted(per)
print(f"tasks scored: {len(ids)} / {len(tasks)}  ({Path(sys.argv[2]).name})")
groups = {"all": ids}
if not IS_CB:
    groups["desc"] = [i for i in ids if strata[i] == "desc"]
    groups["ident"] = [i for i in ids if strata[i] == "ident"]
    groups["desc-strict"] = [i for i in ids if strict.get(i)]
metric_keys = ["@10 file", "@10 sym", "@10 line", "pack file", "pack line"] if IS_CB else \
    ["@5 sym", "@10 sym", "@10 file", "pack sym"]
for gname, gid in groups.items():
    if len(gid) < 5:
        continue
    print(f"\n### {gname} (n={len(gid)})\n")
    extra = "gold lines/1k pack tok (delivered snippet lines)" if IS_CB else "unit-hit tok/1k pack tok"
    hdr = "| rep | " + " | ".join(metric_keys) + f" | {extra} | inserted tok/task | inserted unit-hit tok share |"
    if not IS_CB:
        hdr += " route recovered | displaced@10 | pack displaced |"
    print(hdr)
    print("|---|" + "---:|" * (len(metric_keys) + 3 + (0 if IS_CB else 3)))
    for rep in REPS:
        row = [statistics.fmean(per[i][f"{rep} {k}"] for i in gid) for k in metric_keys]
        tok = sum(per[i][f"{rep} pack _tok"] for i in gid)
        rel = 1000 * sum(per[i][f"{rep} pack _rel"] for i in gid) / max(1, tok)
        it = sum(per[i][f"{rep} pack _instok"] for i in gid)
        ih = sum(per[i][f"{rep} pack _inshit"] for i in gid)
        s = f"| {rep} | " + " | ".join(f"{x:.3f}" for x in row) + f" | {rel:.1f} | {it/len(gid):.0f} | " \
            + (f"{ih/it:.2f}" if it else "–") + " |"
        if not IS_CB:
            s += f" {sum(per[i][f'{rep} route_rec'] for i in gid)}/{sum(per[i]['_route_loss'] for i in gid)} |" \
                 f" {sum(per[i][f'{rep} displaced@10'] for i in gid)} | {sum(per[i][f'{rep} pack displaced'] for i in gid)} |"
        print(s)
    print(f"\npaired Δ vs A, 95% bootstrap CI (n={len(gid)}):")
    for rep in REPS[1:]:
        for k in (["@10 file", "@10 line", "pack line"] if IS_CB else ["@10 sym", "pack sym"]):
            d = [per[i][f"{rep} {k}"] - per[i][f"A {k}"] for i in gid]
            lo, hi = ci(d)
            print(f"  {rep:7s} {k:10s} {statistics.fmean(d):+.3f} [{lo:+.3f}, {hi:+.3f}]  "
                  f"w/l {sum(x > 0 for x in d)}/{sum(x < 0 for x in d)}")
dsum = defaultdict(int)
for i in ids:
    for k, v in per[i].items():
        if k.endswith("_dups"):
            dsum[k.split()[0]] += v
print("\nduplicate evidence skipped before pack insertion: " + ", ".join(f"{k}={v}" for k, v in dsum.items()))
json.dump({"per_task": per, "strata": strata, "desc_strict": sorted(i for i in ids if strict.get(i))}, open(sys.argv[2] + ".scores.json", "w"))
