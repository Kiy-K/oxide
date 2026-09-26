#!/usr/bin/env python3
"""SCIP-exact oracle: the pre-registered "every edge exact" measure, using
rust-analyzer's resolution instead of knowledge of gold.

On a benchmark copy (same protocol as admission_oracle.py), for every
direct seed S of the baseline pack that has a SCIP document:

  T(S)  = OXIDE symbols that SCIP references inside S's span resolve to
          (definition joined by file + containing span + display name);
  C(S)  = OXIDE symbols whose span holds a SCIP reference resolving to S.

then
  - `uses`: every same-named definition of a name S references that is in
    no T(S) and is not a direct item is renamed away (so name-tier `uses`
    lands only on what rust-analyzer resolved). Global rename: it also
    removes those definitions from Markdown seeds' fan-out, which an
    overlay could not do — generous to SCIP;
  - callers: `calls` rows targeting S's name from symbols not in C(S) are
    deleted;
  - containment is left alone (Rust impls legitimately span files).

Gold gets no protection. Also answers, per instance, whether each missed
gold symbol is in T(S) or C(S) for some direct seed (a real edge), and
`--link-check` reports only that (used for instance 0e4346f7).

`--fixpoint N` (added after the advisor review) removes the two leaks of the
one-shot run — a global keep set (a definition true for *any* seed
survived for every seed) and untouched cross-file containment — by
iterating: every structural pack item is judged against the seed named in
its own reason tag (`rel←seed`) and evicted unless SCIP confirms it for
that seed (`uses`/`imported-definition`: in T(seed); callers and `test`:
in C(seed); `parent`/`child`/`sibling`: same file as the seed). Items from
seeds with no SCIP document (Markdown) are kept — an overlay cannot judge
them — unless `--evict-nonscip`. Stops when every structural item is
SCIP-confirmed or non-SCIP; the final slot holders are logged by class.

Usage: scip_exact_oracle.py <instance tag> <scipread JSON> <work dir> <out.jsonl>
           [--link-check | --fixpoint N [--evict-nonscip]]
"""
import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

import admission_oracle as ao
import gap_screen as gs


def innermost_factory(syms):
    by_file = defaultdict(list)
    for s in syms.values():
        by_file[s["file"]].append(s)

    def innermost(f, line, name=None, allow_module=False):
        c = [s for s in by_file.get(f, ()) if s["s"] <= line <= s["e"]
             and (name is None or s["name"] == name)]
        nonmod = [s for s in c if s["kind"] != "module"]
        c = nonmod or (c if allow_module else [])
        return min(c, key=lambda s: (s["e"] - s["s"], -len(s["qn"]))) if c else None
    return innermost


def scip_refs(idx, innermost):
    """file -> [(line, joined OXIDE target id)], plus the set of documents."""
    info = idx["symbols"]
    target = {}
    for d in idx["documents"]:
        for sym, sl, _, _, _, is_def, _ in d["occ"]:
            if is_def and sym not in target and sym in info:
                t = innermost(d["path"], sl, info[sym][1], allow_module=True)
                if t:
                    target[sym] = t["id"]
    refs = defaultdict(list)
    for d in idx["documents"]:
        for sym, sl, _, _, _, is_def, _ in d["occ"]:
            if not is_def and sym in target:
                refs[d["path"]].append((sl, target[sym]))
    return refs, {d["path"] for d in idx["documents"]}


CONTAINMENT = ("parent", "child", "sibling")


def judge(item, syms, by_key, seed_ids, refs, docs, innermost, evict_nonscip):
    """Class of a structural pack item under per-seed SCIP resolution."""
    x = by_key.get((item["file"], item["qualified_name"]))
    classes = []
    for r in item["reasons"]:
        if "←" not in r or r.startswith(gs.DIRECT):
            continue
        rel, seed_qn = r.split("←", 1)
        rel = rel.split(":")[-1]
        cands = [s for s in syms.values() if s["qn"] == seed_qn]
        cands = [s for s in cands if s["id"] in seed_ids] or cands
        for seed in cands:
            if seed["file"] not in docs:
                classes.append("non-scip-seed")
                continue
            if x is None:
                continue
            if rel in CONTAINMENT:
                ok = x["file"] == seed["file"]
            elif rel in ("uses", "imported-definition"):
                ok = any(t == x["id"] for (l, t) in refs.get(seed["file"], ())
                         if seed["s"] <= l <= seed["e"])
            else:  # ast-grep-caller, caller, test: x must reference the seed
                ok = any(t == seed["id"] and x["s"] <= l <= x["e"]
                         for (l, t) in refs.get(x["file"], ()))
            classes.append(f"scip-confirmed:{rel}" if ok else f"wrong:{rel}")
    confirmed = [c for c in classes if c.startswith("scip-confirmed")]
    if confirmed:
        return confirmed[0], x
    if "non-scip-seed" in classes and not evict_nonscip:
        return "non-scip-seed", x
    return (classes[0] if classes else "unjudged"), x


def fixpoint(res, dst, db, row, syms, gsyms, seed_ids, refs, docs, innermost, after, rounds,
             evict_nonscip):
    by_key = {(s["file"], s["qn"]): s for s in syms.values()}
    res.update(fixpoint_rounds=0, fixpoint_evicted=0, evict_nonscip=evict_nonscip)
    while res["fixpoint_rounds"] < rounds:
        victims = []
        for it in after["items"]:
            if any(r.startswith(gs.DIRECT) for r in it["reasons"]):
                continue
            cls, x = judge(it, syms, by_key, seed_ids, refs, docs, innermost, evict_nonscip)
            kept = cls.startswith("scip-confirmed") or (cls == "non-scip-seed" and not evict_nonscip)
            if not kept and x and x["id"] not in gsyms:
                victims.append(x["id"])
        if not victims:
            break
        con = sqlite3.connect(db)
        for sid in victims:
            ao.suffix_symbol(con, sid)
            con.execute("DELETE FROM symbol_relations WHERE symbol_id=?", (sid,))
        con.commit()
        con.close()
        res["fixpoint_evicted"] += len(victims)
        res["fixpoint_rounds"] += 1
        after = gs.run_context(dst, row["problem_statement"])
    res["final_slot_holders"] = [
        (it["id"], it["reasons"], judge(it, syms, by_key, seed_ids, refs, docs, innermost,
                                        evict_nonscip)[0],
         (it["file"], it["qualified_name"]) in {(g["file"], g["qn"]) for g in gsyms.values()})
        for it in after["items"] if not any(r.startswith(gs.DIRECT) for r in it["reasons"])]
    return after


def main():
    tag, scip_json, work, out = sys.argv[1:5]
    link_check = "--link-check" in sys.argv
    row = gs.normalize_gold({r["instance_id"][-8:]: r for r in gs.load_rust_instances()}[tag])
    src = gs.cb.ensure_repo_checkout(row["repo_url"], row["base_commit"])
    dst = Path(work) / f"{tag}-exact"
    ao.copy_repo(src, dst)
    db = dst / ".oxide" / "index.db"
    syms = gs.load_symbols(db)
    gsyms = gs.gold_symbols(gs.gold_spans(row), syms)
    base = gs.run_context(dst, row["problem_statement"])
    by_key = {(s["file"], s["qn"]): s for s in syms.values()}
    direct = [by_key[(i["file"], i["qualified_name"])] for i in base["items"]
              if any(r.startswith(gs.DIRECT) for r in i["reasons"])
              and (i["file"], i["qualified_name"]) in by_key]
    # Search-expansion seeds can be subsumed or capped out of the final pack
    # (`omitted`) yet still have expanded; treat every omitted candidate as
    # a seed too, so their edges are made exact as well.
    by_id = {f'{s["file"]}#{s["qn"]}': s for s in syms.values()}
    seen = {s["id"] for s in direct}
    for o in base["omitted"]:
        s = by_id.get(o["id"])
        if s and s["id"] not in seen and s["id"] not in gsyms:
            direct.append(s)
            seen.add(s["id"])
    innermost = innermost_factory(syms)
    refs, docs = scip_refs(json.load(open(scip_json)), innermost)

    T, C = {}, {}
    for s in direct:
        if s["file"] not in docs:
            continue
        T[s["id"]] = {t for (l, t) in refs[s["file"]] if s["s"] <= l <= s["e"] and t != s["id"]}
        C[s["id"]] = {x["id"] for f, rs in refs.items() for (l, t) in rs if t == s["id"]
                      for x in [innermost(f, l)] if x and x["id"] != s["id"]}
    seed_ids = {x["id"] for x in direct}
    res_missed = [gid for gid in gsyms if gid not in ao.covered_nonmodule(base, gsyms)]

    def encloses(k, g):
        return syms[k]["file"] == g["file"] and syms[k]["s"] <= g["s"] and g["e"] <= syms[k]["e"]
    res = dict(instance=tag, direct=[f'{s["file"]}#{s["qn"]}' for s in direct],
               seeds_with_scip_doc=len(T),
               true_targets_per_seed={f'{syms[k]["file"]}#{syms[k]["qn"]}': len(v) for k, v in T.items()},
               gold_real_edge=sorted(f'{g["file"]}#{g["qn"]}' for gid, g in gsyms.items()
                                     if any(gid in T[k] or gid in C.get(k, ()) for k in T)),
               # missed gold only, and not merely inside a module seed's own span
               gold_real_edge_missed=sorted(
                   f'{gsyms[gid]["file"]}#{gsyms[gid]["qn"]}' for gid in res_missed
                   if any((gid in T[k] or gid in C.get(k, ())) and not encloses(k, gsyms[gid])
                          for k in T)))
    if not link_check:
        keep = set().union(*T.values()) | {s["id"] for s in direct} if T else {s["id"] for s in direct}
        names = {n for s in direct if s["id"] in T for n in s["refs"]}
        con = sqlite3.connect(db)
        renamed = deleted = 0
        for n in sorted(names):
            for (sid,) in con.execute("SELECT id FROM symbols WHERE name=? AND kind<>'module'",
                                      (n,)).fetchall():
                if sid not in keep:
                    con.execute("UPDATE symbols SET name=name||? WHERE id=?", (ao.SUFFIX, sid))
                    renamed += 1
        for s in direct:
            if s["id"] not in C:
                continue
            for (rid, sid) in con.execute("SELECT rowid, symbol_id FROM symbol_relations "
                                          "WHERE kind='calls' AND target=?", (s["name"],)).fetchall():
                if sid not in C[s["id"]]:
                    con.execute("DELETE FROM symbol_relations WHERE rowid=?", (rid,))
                    deleted += 1
        con.commit()
        con.close()
        after = gs.run_context(dst, row["problem_statement"])
        m0, m1 = ao.covered_nonmodule(base, gsyms), ao.covered_nonmodule(after, gsyms)
        b = [i["id"] for i in base["items"]]
        a = {j["id"] for j in after["items"]}
        if "--fixpoint" in sys.argv:
            after = fixpoint(res, dst, db, row, syms, gsyms, seed_ids, refs, docs, innermost,
                             after, int(sys.argv[sys.argv.index("--fixpoint") + 1]),
                             "--evict-nonscip" in sys.argv)
            m1 = ao.covered_nonmodule(after, gsyms)
            a = {j["id"] for j in after["items"]}
        res.update(renamed=renamed, calls_deleted=deleted,
                   direct_identical=ao.pack_direct(after) == ao.pack_direct(base),
                   gold_nonmodule_before=len(m0), gold_nonmodule_after=len(m1),
                   newly_admitted_nonmodule=sorted(f'{gsyms[g]["file"]}#{gsyms[g]["qn"]}'
                                                   for g in m1 - m0),
                   added=[(i["id"], i["reasons"]) for i in after["items"] if i["id"] not in b],
                   dropped=[i for i in b if i not in a])
    with open(out, "a") as fh:
        fh.write(json.dumps(res) + "\n")
    print(json.dumps({k: v for k, v in res.items() if k != "direct"}, indent=1))


if __name__ == "__main__":
    main()
