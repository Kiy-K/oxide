#!/usr/bin/env python3
"""Gap screen (OXIDE only): could exact (SCIP-grade) structural edges have
changed OXIDE's bounded context on ContextBench's verified Rust instances?

No SCIP here — this measures the *ceiling* for any resolution improvement,
using OXIDE's own name-tier relations as a superset of the true ones.

Per instance: index the base commit with the shipped default embedder, run
`oxide context --json` (default 4096-token budget, balanced) and, as a
secondary condition, `--blast-radius`; map human gold spans to OXIDE
symbols; then classify every gold symbol the pack missed:

  unlinked            no name-tier relation to any direct seed in the pack
  linked-unambiguous  linked, and the linking name has exactly one
                      definition in the repo — OXIDE's edge already equals
                      the exact one; resolution cannot change it
  linked-ambiguous    linked through a name with >1 definition — the only
                      class exact resolution could promote

and every structural pack item (non-direct reason) by whether its linking
name is ambiguous (noise exact resolution could remove, freeing budget).
Gold sitting in `omitted` as "over token budget" is recorded so a freed-
budget effect can be bounded too.

Usage:
    eval-agent/.venv/bin/python docs/scip-rust-eval/scripts/gap_screen.py \
        --out docs/scip-rust-eval/raw/gap_screen.jsonl [--only clap]
"""
import argparse
import json
import re
import sqlite3
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts" / "agent_eval"))
import contextbench_run as cb  # noqa: E402  (ensures ContextBench is cloned)
from contextbench.parsers.gold import Gold  # noqa: E402

OX = str(ROOT / "target/release/oxide")
PARQUET = cb.CB_DIR / "data" / "contextbench_verified.parquet"
DIRECT = ("lexical=", "semantic=", "literal")


def load_rust_instances(only=None):
    import pandas as pd
    d = pd.read_parquet(PARQUET)
    d = d[d.language == "rust"].sort_values("instance_id")
    rows = d.to_dict("records")
    if only:
        rows = [r for r in rows if only in r["repo"]]
    return rows


WORKSPACE_PREFIX = re.compile(r"^/workspace/[^/]+/")


def normalize_gold(row):
    """Some instances record gold paths container-absolute
    (`/workspace/clap-rs__clap__0.1/src/x.rs`); strip that prefix so they
    match repo-relative paths. Without this, gold maps to no symbol and
    ContextBench's line/span metrics read 0 while file coverage (normalized
    inside `Gold.files()`) does not."""
    ctx = row["gold_context"]
    items = json.loads(ctx) if isinstance(ctx, str) else list(ctx)
    for it in items:
        if isinstance(it, dict) and it.get("file"):
            it["file"] = WORKSPACE_PREFIX.sub("", it["file"])
    return {**row, "gold_context": json.dumps(items)}


def gold_spans(row):
    ctx = row["gold_context"]
    g = Gold({"init_ctx": json.loads(ctx) if isinstance(ctx, str) else ctx,
              "repo_url": row["repo_url"], "commit": row["base_commit"]})
    out = defaultdict(list)
    for it in g.init + g.add:
        if it.get("file"):
            out[it["file"]].append((it.get("start_line", 1), it.get("end_line", 1)))
    return dict(out)


def load_symbols(db):
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    syms = {}
    for (sid, f, qn, name, kind, s, e, parent, refs) in con.execute(
            "SELECT id,file,qualified_name,name,kind,start_line,end_line,parent,"
            "references_json FROM symbols"):
        syms[sid] = dict(id=sid, file=f, qn=qn, name=name, kind=kind, s=s, e=e,
                         parent=parent, refs=set(json.loads(refs)),
                         calls=set(), bases=set())
    for (sid, kind, target) in con.execute(
            "SELECT symbol_id,kind,target FROM symbol_relations"):
        if sid in syms:
            syms[sid]["calls" if kind == "calls" else "bases"].add(target)
    con.close()
    return syms


def overlaps(a, b):
    return a[0] <= b[1] and b[0] <= a[1]


def gold_symbols(gspans, syms):
    """Innermost non-module OXIDE symbols overlapping each gold span."""
    by_file = defaultdict(list)
    for s in syms.values():
        if s["kind"] != "module":
            by_file[s["file"]].append(s)
    out = {}
    for f, spans in gspans.items():
        for sp in spans:
            hit = [s for s in by_file.get(f, []) if overlaps((s["s"], s["e"]), sp)]
            inner = [s for s in hit if not any(
                o is not s and o["s"] >= s["s"] and o["e"] <= s["e"]
                and (o["s"], o["e"]) != (s["s"], s["e"]) for o in hit)]
            for s in inner:
                out[s["id"]] = s
    return out


def links(a, b):
    """Name-tier relation names connecting a and b, either direction."""
    names = set()
    for x, y in ((a, b), (b, a)):
        if y["name"] in x["refs"] or y["name"] in x["calls"] or y["name"] in x["bases"]:
            names.add(y["name"])
    if a["file"] == b["file"] and (a["parent"] == b["qn"] or b["parent"] == a["qn"]
                                   or (a["parent"] and a["parent"] == b["parent"])):
        names.add("<containment>")
    return names


def run_context(repo, problem, extra=()):
    r = cb.sh([OX, "context", "--task", problem, "--json", *extra], cwd=repo,
              env={"OXIDE_EMBED_URL": ""}, timeout=900)
    if r.returncode != 0:
        raise RuntimeError(r.stderr[-500:])
    return json.loads(r.stdout)


def analyse(pack, syms, gsyms, defs_by_name):
    by_key = {(s["file"], s["qn"]): s for s in syms.values()}
    items = [(it, by_key.get((it["file"], it["qualified_name"]))) for it in pack["items"]]
    direct = [s for it, s in items if s and any(r.startswith(DIRECT) for r in it["reasons"])]
    covered = {g for g, gs in gsyms.items() for it, _ in items
               if it["file"] == gs["file"]
               and overlaps((it["start_line"], it["end_line"]), (gs["s"], gs["e"]))}

    def ambiguous(name):
        return name != "<containment>" and len(defs_by_name.get(name, ())) > 1

    missed = []
    for g, gs in gsyms.items():
        if g in covered:
            continue
        ln = set().union(*(links(gs, s) for s in direct)) if direct else set()
        cls = ("unlinked" if not ln else
               "linked-ambiguous" if any(ambiguous(n) for n in ln) else
               "linked-unambiguous")
        missed.append(dict(id=f'{gs["file"]}#{gs["qn"]}', cls=cls, via=sorted(ln)[:6]))

    structural = []
    for it, s in items:
        rs = [r for r in it["reasons"] if not r.startswith(DIRECT)]
        if not rs or any(r.startswith(DIRECT) for r in it["reasons"]):
            continue
        gold = s is not None and s["id"] in gsyms
        ln = set().union(*(links(s, d) for d in direct)) if s else set()
        structural.append(dict(id=it["id"], reasons=rs, gold=gold,
                               ambiguous=any(ambiguous(n) for n in ln),
                               tokens=it.get("est_tokens", 0)))
    gold_ids = {f'{gs["file"]}#{gs["qn"]}' for gs in gsyms.values()}
    over_budget_gold = [o["id"] for o in pack.get("omitted", [])
                        if o["id"] in gold_ids and "budget" in o["why"]]
    return dict(n_items=len(items), n_direct=len(direct), used_tokens=pack["used_tokens"],
                gold_covered=len(covered), missed=missed, structural=structural,
                over_budget_gold=over_budget_gold,
                gold_via_structural=sum(1 for x in structural if x["gold"]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--only")
    a = ap.parse_args()
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    done = {json.loads(l)["instance_id"] for l in out.open()} if out.exists() else set()
    for row in load_rust_instances(a.only):
        iid = row["instance_id"]
        if iid in done:
            continue
        t0 = time.time()
        row = normalize_gold(row)
        rec = dict(instance_id=iid, repo=row["repo"], base_commit=row["base_commit"])
        try:
            repo = cb.ensure_repo_checkout(row["repo_url"], row["base_commit"])
            ti = time.time()
            r = cb.sh([OX, "index", "."], cwd=repo, env={"OXIDE_EMBED_URL": ""}, timeout=5400)
            if r.returncode != 0:
                raise RuntimeError(f"index: {r.stderr[-400:]}")
            rec["index_s"] = round(time.time() - ti, 1)
            db = repo / ".oxide" / "index.db"
            rec["embedder"] = sqlite3.connect(db).execute(
                "SELECT value FROM meta WHERE key='embedder'").fetchone()[0]
            syms = load_symbols(db)
            defs_by_name = defaultdict(list)
            for s in syms.values():
                if s["kind"] != "module":
                    defs_by_name[s["name"]].append(s["id"])
            gsp = gold_spans(row)
            gsyms = gold_symbols(gsp, syms)
            rec.update(n_symbols=len(syms), gold_files=sorted(gsp), n_gold_symbols=len(gsyms))
            for cond, extra in (("default", ()), ("blast", ("--blast-radius",))):
                pack = run_context(repo, row["problem_statement"], extra)
                rec[cond] = analyse(pack, syms, gsyms, defs_by_name)
                items = [{"file": i["file"], "start_line": i["start_line"],
                          "end_line": i["end_line"]} for i in pack["items"]]
                m = cb.evaluate_task(repo, row, items)
                rec[cond]["cb_metrics"] = {k: m[k] for k in m if k in ("file", "symbol", "span", "line")}
        except Exception as e:  # recorded, never silently skipped
            rec["error"] = f"{type(e).__name__}: {e}"[:800]
        rec["wall_s"] = round(time.time() - t0, 1)
        with out.open("a") as fh:
            fh.write(json.dumps(rec) + "\n")
        print(iid, rec.get("error") or {c: (rec[c]["gold_covered"], rec["n_gold_symbols"])
                                        for c in ("default", "blast")}, flush=True)


if __name__ == "__main__":
    main()
