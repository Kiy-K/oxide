#!/usr/bin/env python3
"""Admission oracle: an upper bound on what exact structural resolution
could add to OXIDE's *final* bounded context, run through the real binary
on benchmark database copies.

For every instance where `gap_screen.py` found gold linked to a direct seed
only through an ambiguous name, this makes the name tier behave as if every
ambiguous link resolved to gold (deliberately favorable to SCIP):

  - every definition carrying that name that is neither gold nor already in
    the pack is renamed (`symbols.name` only — ids, qualified names,
    embeddings and persisted BM25 postings are untouched), so `uses` /
    `imported-definition` / blast-radius lookups can only land on gold;
  - every non-gold, non-pack `calls`/`bases` row targeting that name is
    deleted, so `callers_of`/`implementors_of` can only return gold.

Protocol per instance:
  1. `cp -a` the commit worktree (the literal walk needs the files); the
     index is copied with `VACUUM INTO` (consistent snapshot of a WAL db),
     and the copy's `meta.root` repointed to the copy (`validate_index`).
  2. A/A: the unmodified copy must reproduce the original pack exactly
     (items with scores and reasons, and `omitted`), and
     `meta.index_generation` must not move (no auto-reindex).
  3. `meta.lexical_index_version` must be present, so ranking reads the
     persisted postings and a rename cannot reach BM25.
  4. Intervention, rerun, then a validity check: the direct items (id,
     order, lexical/semantic reason strings) must be identical to baseline.
     Any difference invalidates the instance.

`--full` (added after independent review) widens the upper bound to every
structural path, on all 20 instances, not only ambiguous-name links:
  - every non-gold structural pack item is evicted (name, qualified name
    and parent suffixed, all its `symbol_relations` rows deleted), freeing
    the 2 expansion slots (`CONTEXT_EXPANSION_TOTAL`) and the caller slots;
  - cross-file containment is made exact: for each direct seed, non-gold
    symbols in *other* files whose qualified name equals the seed's parent
    or its own qualified name, or whose parent equals either, are
    suffixed (`RelationGraph::by_qualified` / sibling and child lookups
    match those strings across files);
  - plus the ambiguous-name intervention above.
  - then iterate to the fixpoint (`--rounds`): rerun, evict whatever
    non-gold structural items refilled the freed slots, until gold appears
    or no non-gold structural item remains — the true upper bound for any
    resolution that only ever *removes* wrong edges.
  `direct_identical` is recorded, not used to discard: evicting a structural
  item can legitimately change module subsumption (positive control 3).

Pre-registered stop rule (docs/scip-rust-eval/README.md §3): fewer than 2
instances with at least one gold symbol newly admitted ⇒ stop.

Usage:
    eval-agent/.venv/bin/python docs/scip-rust-eval/scripts/admission_oracle.py \
        --gap docs/scip-rust-eval/raw/gap_screen.jsonl \
        --out docs/scip-rust-eval/raw/admission_oracle.jsonl --work <scratch dir>
"""
import argparse
import json
import os
import shutil
import sqlite3
import subprocess
from collections import defaultdict
from pathlib import Path

import gap_screen as gs

SUFFIX = "__oracle_renamed"


def pack_direct(pack):
    out = []
    for it in pack["items"]:
        d = [r for r in it["reasons"] if r.startswith(gs.DIRECT)]
        if d:
            out.append((it["id"], tuple(d)))
    return out


def meta(db, key):
    con = sqlite3.connect(db)
    try:
        row = con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    finally:
        con.close()
    return row[0] if row else None


def copy_repo(src: Path, dst: Path):
    if dst.exists():
        shutil.rmtree(dst)
    subprocess.run(["cp", "-a", str(src), str(dst)], check=True)
    for f in (dst / ".oxide").glob("index.db*"):
        f.unlink()
    con = sqlite3.connect(src / ".oxide" / "index.db")
    con.execute("VACUUM INTO ?", (str(dst / ".oxide" / "index.db"),))
    con.close()
    con = sqlite3.connect(dst / ".oxide" / "index.db")
    con.execute("UPDATE meta SET value=? WHERE key='root'", (str(dst),))
    con.commit()
    con.close()


def covered(pack, gsyms):
    return {g for g, s in gsyms.items() for it in pack["items"]
            if it["file"] == s["file"]
            and gs.overlaps((it["start_line"], it["end_line"]), (s["s"], s["e"]))}


def covered_nonmodule(pack, gsyms):
    """Coverage by a non-module pack item only: a module item's span covers
    the whole file but its snippet is capped, so it overstates coverage."""
    return {g for g, s in gsyms.items() for it in pack["items"]
            if it["kind"] != "module" and it["file"] == s["file"]
            and gs.overlaps((it["start_line"], it["end_line"]), (s["s"], s["e"]))}


def intervene(db, gsyms, pack, names):
    con = sqlite3.connect(db)
    by_key = {(f, qn): sid for sid, f, qn in con.execute("SELECT id,file,qualified_name FROM symbols")}
    keep = set(gsyms) | {by_key.get((it["file"], it["qualified_name"])) for it in pack["items"]}
    renamed = deleted = 0
    for n in sorted(names):
        for (sid,) in con.execute("SELECT id FROM symbols WHERE name=?", (n,)).fetchall():
            if sid not in keep:
                con.execute("UPDATE symbols SET name=? WHERE id=?", (n + SUFFIX, sid))
                renamed += 1
        for (rid, sid) in con.execute(
                "SELECT rowid, symbol_id FROM symbol_relations WHERE target=?", (n,)).fetchall():
            if sid not in keep:
                con.execute("DELETE FROM symbol_relations WHERE rowid=?", (rid,))
                deleted += 1
    con.commit()
    con.close()
    return renamed, deleted


def suffix_symbol(con, sid):
    """Make a symbol unreachable by every name-keyed relation. A module
    symbol keeps its qualified name (`<file>:__module__` is what symbol
    completion resolves it by); its name and relations are enough."""
    # OXIDE recomputes an id from file + qualified name when it completes a
    # candidate, so a qn-renamed symbol that re-enters the pool fails loudly
    # ("no such row"); ORACLE_KEEP_QN=1 renames name/parent only.
    keep_qn = os.environ.get("ORACLE_KEEP_QN") == "1"
    con.execute("UPDATE symbols SET name=name||?, "
                "qualified_name=CASE WHEN kind='module' OR ? THEN qualified_name ELSE qualified_name||? END, "
                "parent=CASE WHEN parent IS NULL THEN NULL ELSE parent||? END WHERE id=?",
                (SUFFIX, keep_qn, SUFFIX, SUFFIX, sid))


def evict_structural(db, gsyms, pack):
    """Suffix and unlink every non-gold, non-direct item in `pack`."""
    con = sqlite3.connect(db)
    n = 0
    for it in pack["items"]:
        if any(r.startswith(gs.DIRECT) for r in it["reasons"]):
            continue
        row = con.execute("SELECT id FROM symbols WHERE file=? AND qualified_name=?",
                          (it["file"], it["qualified_name"])).fetchone()
        if row and row[0] not in gsyms:
            suffix_symbol(con, row[0])
            con.execute("DELETE FROM symbol_relations WHERE symbol_id=?", (row[0],))
            n += 1
    con.commit()
    con.close()
    return n


def intervene_full(db, gsyms, pack, names):
    renamed, deleted = intervene(db, gsyms, pack, names)
    con = sqlite3.connect(db)
    rows = {(f, qn): (sid, parent) for sid, f, qn, parent in
            con.execute("SELECT id,file,qualified_name,parent FROM symbols")}
    direct = [(it["file"], it["qualified_name"]) for it in pack["items"]
              if any(r.startswith(gs.DIRECT) for r in it["reasons"])]
    direct_ids = {rows[k][0] for k in direct if k in rows}
    keep = set(gsyms) | direct_ids

    def suffix(sid):
        nonlocal renamed
        suffix_symbol(con, sid)
        renamed += 1

    evicted = 0
    for it in pack["items"]:
        k = (it["file"], it["qualified_name"])
        if k in rows and rows[k][0] not in keep:
            suffix(rows[k][0])
            deleted += con.execute("DELETE FROM symbol_relations WHERE symbol_id=?",
                                   (rows[k][0],)).rowcount
            evicted += 1
    containment = 0
    for f, qn in direct:
        if (f, qn) not in rows:
            continue
        parent = rows[(f, qn)][1]
        keys = {qn} | ({parent} if parent else set())
        for sid, sf in con.execute(
                "SELECT id,file FROM symbols WHERE file<>? AND (qualified_name IN (%s) OR parent IN (%s))"
                % (",".join("?" * len(keys)), ",".join("?" * len(keys))),
                (f, *keys, *keys)).fetchall():
            if sid not in keep:
                suffix(sid)
                containment += 1
    con.commit()
    con.close()
    return renamed, deleted, evicted, containment


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gap", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--full", action="store_true")
    ap.add_argument("--rounds", type=int, default=1)
    a = ap.parse_args()
    rows = {r["instance_id"]: r for r in gs.load_rust_instances()}
    work = Path(a.work)
    work.mkdir(parents=True, exist_ok=True)
    out = Path(a.out)
    for rec in map(json.loads, open(a.gap)):
        amb = [m for m in rec["default"]["missed"] if m["cls"] == "linked-ambiguous"]
        if not amb and not a.full:
            continue
        iid = rec["instance_id"]
        row = gs.normalize_gold(rows[iid])
        res = dict(instance_id=iid, repo=rec["repo"])
        try:
            src = gs.cb.ensure_repo_checkout(row["repo_url"], row["base_commit"])
            dst = work / iid[-8:]
            copy_repo(src, dst)
            db = dst / ".oxide" / "index.db"
            syms = gs.load_symbols(db)
            gsyms = gs.gold_symbols(gs.gold_spans(row), syms)
            defs = defaultdict(list)
            for s in syms.values():
                if s["kind"] != "module":
                    defs[s["name"]].append(s["id"])
            base = gs.run_context(src, row["problem_statement"])
            gen0 = meta(db, "index_generation")
            aa = gs.run_context(dst, row["problem_statement"])
            res["aa_identical"] = (json.dumps(aa["items"], sort_keys=True) == json.dumps(base["items"], sort_keys=True)
                                   and aa["omitted"] == base["omitted"])
            res["generation_unchanged"] = meta(db, "index_generation") == gen0
            res["lexical_persisted"] = meta(db, "lexical_index_version") is not None
            gold_ids = {f'{s["file"]}#{s["qn"]}' for s in gsyms.values()}
            res["gold_omitted_baseline"] = [o for o in base["omitted"] if o["id"] in gold_ids]
            if not (res["aa_identical"] and res["generation_unchanged"] and res["lexical_persisted"]):
                raise RuntimeError("A/A or precondition failed; intervention not run")
            names = {v for m in amb for v in m["via"]
                     if v != "<containment>" and len(defs.get(v, ())) > 1}
            res["names"] = sorted(names)
            if a.full:
                (res["renamed"], res["relations_deleted"], res["evicted_structural"],
                 res["containment_renamed"]) = intervene_full(db, gsyms, base, names)
            else:
                res["renamed"], res["relations_deleted"] = intervene(db, gsyms, base, names)
            after = gs.run_context(dst, row["problem_statement"])
            res["rounds"] = 1
            while a.full and res["rounds"] < a.rounds:
                evicted = evict_structural(db, gsyms, after)
                if not evicted:
                    break
                res["evicted_structural"] += evicted
                after = gs.run_context(dst, row["problem_statement"])
                res["rounds"] += 1
            res["structural_left"] = sum(1 for it in after["items"]
                                         if not any(r.startswith(gs.DIRECT) for r in it["reasons"]))
            res["direct_identical"] = pack_direct(after) == pack_direct(base)
            c0, c1 = covered(base, gsyms), covered(after, gsyms)
            res["gold_covered_before"], res["gold_covered_after"] = len(c0), len(c1)
            nm = {g: s for g, s in gsyms.items()}
            m0, m1 = covered_nonmodule(base, nm), covered_nonmodule(after, nm)
            res["gold_covered_nonmodule_before"], res["gold_covered_nonmodule_after"] = len(m0), len(m1)
            res["newly_admitted_nonmodule"] = sorted(f'{gsyms[g]["file"]}#{gsyms[g]["qn"]}' for g in m1 - m0)
            res["newly_admitted"] = sorted(f'{gsyms[g]["file"]}#{gsyms[g]["qn"]}' for g in c1 - c0)
            res["lost"] = sorted(f'{gsyms[g]["file"]}#{gsyms[g]["qn"]}' for g in c0 - c1)
            res["items_before"] = [(i["id"], i["reasons"]) for i in base["items"]]
            res["items_after"] = [(i["id"], i["reasons"]) for i in after["items"]]
            res["gold_omitted_after"] = [o for o in after["omitted"] if o["id"] in gold_ids]
        except Exception as e:
            res["error"] = f"{type(e).__name__}: {e}"[:800]
        with out.open("a") as fh:
            fh.write(json.dumps(res) + "\n")
        print(iid[-8:], res.get("error") or {k: res[k] for k in (
            "aa_identical", "direct_identical", "renamed", "relations_deleted",
            "gold_covered_before", "gold_covered_after", "newly_admitted",
            "newly_admitted_nonmodule") if k in res}, flush=True)


if __name__ == "__main__":
    main()
