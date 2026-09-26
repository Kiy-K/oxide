#!/usr/bin/env python3
"""Edge-level agreement between OXIDE's name-tier relations and
rust-analyzer's resolved SCIP references on one checkout.

rust-analyzer is the *reference* here, not ground truth (scoring SCIP
against itself would be circular; no hand-labeled Rust edge set exists).
In-project edges only.

  calls   OXIDE (X, n) for n in X.calls — X's body has a call expression
          whose last path segment is n. Name-tier candidates are every
          non-module symbol named n. SCIP: non-definition occurrences whose
          line falls in X's span (innermost symbol, line-granular) and whose
          symbol has an in-project definition of a callable kind, joined to
          the OXIDE symbol by (file, span contains the def line, name).
  bases   OXIDE (T, B) for B in T.bases. SCIP: `impl#[T][B]` descriptors on
          in-project trait-impl members (display text — bare names on
          both sides, so this measures coverage, not resolution). Only
          impls defined in files OXIDE indexed are compared.

Usage: edge_agreement.py <repo with .oxide/index.db> <scipread JSON> <out.json>
"""
import json
import re
import sys
from collections import defaultdict

import gap_screen as gs

CALLABLE = {"Function", "Method", "StaticMethod", "TraitMethod", "Macro"}
IMPL = re.compile(r"impl#\[([^\]]+)\]\[`?([^\]`]+)`?\]")


def main():
    repo, scip_json, out = sys.argv[1:4]
    syms = gs.load_symbols(f"{repo}/.oxide/index.db")
    by_file = defaultdict(list)
    by_name = defaultdict(list)
    for s in syms.values():
        by_file[s["file"]].append(s)
        if s["kind"] != "module":
            by_name[s["name"]].append(s)

    def innermost(f, line, name=None):
        c = [s for s in by_file.get(f, ()) if s["s"] <= line <= s["e"] and s["kind"] != "module"
             and (name is None or s["name"] == name)]
        return min(c, key=lambda s: (s["e"] - s["s"], -len(s["qn"]))) if c else None

    idx = json.load(open(scip_json))
    info = idx["symbols"]
    defs = {}
    for d in idx["documents"]:
        for sym, sl, _, _, _, is_def, _ in d["occ"]:
            if is_def:
                defs.setdefault(sym, (d["path"], sl))

    scip_calls = defaultdict(set)      # caller id -> {target id}
    outside = defaultdict(set)         # caller id -> {target name} defined in files OXIDE never indexed
    unjoined_targets = 0
    for d in idx["documents"]:
        for sym, sl, _, _, _, is_def, _ in d["occ"]:
            if is_def or sym not in defs or info.get(sym, ["", ""])[0] not in CALLABLE:
                continue
            caller = innermost(d["path"], sl)
            tf, tl = defs[sym]
            target = innermost(tf, tl, info[sym][1])
            if caller is None:
                continue
            if target is None:
                if tf not in by_file:
                    outside[caller["id"]].add(info[sym][1])
                else:
                    unjoined_targets += 1
                continue
            if target["id"] != caller["id"]:
                scip_calls[caller["id"]].add(target["id"])

    ox_edges = [(s["id"], n) for s in syms.values() for n in sorted(s["calls"])]
    in_project = [(x, n) for x, n in ox_edges if by_name.get(n)]
    confirmed = [(x, n) for x, n in in_project
                 if any(syms[t]["name"] == n for t in scip_calls.get(x, ()))]
    confirmed_set = set(confirmed)
    to_unindexed = [(x, n) for x, n in in_project if (x, n) not in confirmed_set and n in outside.get(x, ())]
    fan = [len(by_name[n]) for _, n in in_project]
    wrong_candidates = sum(len(by_name[n]) - sum(1 for t in scip_calls.get(x, ()) if syms[t]["name"] == n)
                           for x, n in confirmed)
    ambiguous = [(x, n) for x, n in in_project if len(by_name[n]) > 1]
    scip_edges = [(x, t) for x, ts in scip_calls.items() for t in ts]
    missing = [(x, t) for x, t in scip_edges if syms[t]["name"] not in syms[x]["calls"]]

    impls = set()
    for sym in info:
        m = IMPL.search(sym)
        if m and sym in defs and defs[sym][0] in by_file:
            # generic types are backtick-escaped: impl#[`Box<T>`][Args]
            impls.add(tuple(g.strip("`").split("<")[0].strip() for g in m.groups()))
    ox_bases = {(s["name"], b) for s in syms.values() for b in s["bases"]}
    res = dict(
        calls=dict(
            oxide_edges=len(ox_edges),
            oxide_edges_with_in_project_name=len(in_project),
            confirmed_by_scip=len(confirmed),
            unconfirmed_in_project=len(in_project) - len(confirmed),
            unconfirmed_but_scip_resolves_into_unindexed_file=len(to_unindexed),
            oxide_indexed_files=len(by_file),
            scip_documents=len(idx["documents"]),
            scip_documents_not_indexed_by_oxide=sorted(d["path"] for d in idx["documents"] if d["path"] not in by_file),
            ambiguous_name_edges=len(ambiguous),
            mean_name_candidates=round(sum(fan) / len(fan), 2) if fan else 0,
            wrong_candidates_on_confirmed_edges=wrong_candidates,
            scip_callable_edges=len(scip_edges),
            scip_edges_missing_from_oxide_calls=len(missing),
            scip_refs_into_unindexed_files=sum(len(v) for v in outside.values()),
            scip_targets_not_joined_to_oxide_symbol=unjoined_targets,
            examples_unconfirmed=[f'{syms[x]["file"]}#{syms[x]["qn"]} -> {n}'
                                  for x, n in in_project if (x, n) not in confirmed_set][:15],
            examples_missing=[f'{syms[x]["file"]}#{syms[x]["qn"]} -> {syms[t]["file"]}#{syms[t]["qn"]}'
                              for x, t in missing[:15]],
        ),
        bases=dict(
            oxide_pairs=len(ox_bases), scip_impl_pairs=len(impls),
            both=len(ox_bases & impls), oxide_only=sorted(ox_bases - impls)[:20],
            scip_only=sorted(impls - ox_bases)[:20], scip_only_count=len(impls - ox_bases),
            oxide_only_count=len(ox_bases - impls),
        ),
    )
    json.dump(res, open(out, "w"), indent=1)
    print(json.dumps({k: {kk: vv for kk, vv in v.items() if not isinstance(vv, list)}
                      for k, v in res.items()}, indent=1))


if __name__ == "__main__":
    main()
