#!/usr/bin/env python3
"""Forensic for file->span localization. No OXIDE code change."""
import json, os, re, sqlite3, subprocess, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/agent_eval"))
import contextbench_run as cb
REPO_CACHE = Path.home() / ".cache/oxide-contextbench/repos"
PIN = ROOT / "eval-agent/results/tier_a_instances.txt"
ALLOW = {l.strip() for l in PIN.read_text().splitlines() if l.strip()}
# 6 rescue cases from prior negative (grep rank <=10 but hybrid miss) + 3 displaced budgeted hits
RESCUES = [
    # (instance_id, gold_file, note)
    ("SWE-Bench-Verified__python__maintenance__bugfix__36989b6d", "seaborn/_core/plot.py", "grep3 hybrid miss prior"),
    ("SWE-Bench-Verified__python__maintenance__bugfix__36989b6d", "seaborn/distributions.py", "route loss grep8 rank3 after file lex"),
    ("SWE-Bench-Verified__python__maintenance__bugfix__1fdd9275", "src/requests/utils.py", "fusion loss grep6"),
    ("SWE-Bench-Verified__python__maintenance__bugfix__10750f29", "pylint/checkers/variables.py", "route loss grep4"),
    ("SWE-Bench-Verified__python__maintenance__bugfix__da598baa", "pylint/utils/utils.py", "route loss repomap9 grep?"),
    ("SWE-Bench-Verified__python__maintenance__bugfix__60068eb0", "tests/test_skipping.py", "allocation loss grep4"),
]
DISPLACED = [
    ("SWE-Bench-Verified__python__maintenance__bugfix__1fdd9275", "pylint/checkers/similar.py"),  # placeholder from report; need actual displaced set
    ("SWE-Bench-Verified__python__maintenance__bugfix__0eecae1e", "flask/blueprints.py"),
    ("SWE-Bench-Verified__python__maintenance__bugfix__049a7048", "ansible/modules/some.py"),
]

STOP = set("the this that with from into when what then they them their there these those have been will would could should your you're about which where while whose also more most some such only over under between because however therefore thus hence other another each every any all can cannot just like even ever never always often once twice here there does done doing being been was were has had having its it's don didn won isn aren were wasn weren".split())

def terms(problem):
    ws = [w.lower() for w in re.split(r"[^a-zA-Z0-9]+", problem or "")]
    return [w for w in dict.fromkeys(ws) if len(w)>=4 and w not in STOP][:24]

def sh(cmd, cwd, env=None):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, env={**os.environ, **(env or {})})

def load_symbols(repo):
    db = repo / ".oxide/index.db"
    if not db.exists():
        return []
    con = sqlite3.connect(str(db))
    cur = con.execute("SELECT qualified_name, file, start_line, end_line, kind FROM symbols")
    rows = cur.fetchall()
    con.close()
    return rows

def classify_line(line, in_symbol_kind):
    s=line.strip()
    if not s: return "blank"
    if s.startswith("import ") or s.startswith("from "):
        return "import"
    if s.startswith("#"):
        return "comment"
    if '"""' in s or "'''" in s or s.startswith('"') or s.startswith("'"):
        # heuristic for docstring/string/data
        if len(s)>60 and s.count(",")>3:
            return "string/config/data"
        return "comment/docstring" if s.startswith(('"',"'")) else "string/config/data" if in_symbol_kind else "comment/docstring"
    if in_symbol_kind in ("Function","Method","Class"):
        return f"inside {in_symbol_kind.lower()}"
    return "module-level code"

tasks = {r["instance_id"]: r for r in cb.load_tasks() if r["instance_id"] in ALLOW}
out_lines=[]
out_lines.append("# File->span forensic (phase1) — no code change")
out_lines.append("Date: 2026-08-27  model qwen3-Q8_0  rev d1076f5 reverted")
for inst, gold_file, note in RESCUES:
    row = tasks.get(inst)
    if not row:
        out_lines.append(f"\n## {inst} -> {gold_file} MISSING in tier_a")
        continue
    repo = cb.ensure_repo_checkout(row["repo_url"], row["base_commit"])
    # ensure indexed
    r = sh([str(ROOT/"target/release/oxide"), "index", "."], cwd=repo, env={"OXIDE_EMBED_URL": os.environ["OXIDE_EMBED_URL"]})
    problem = row["problem_statement"]
    ts = terms(problem)
    out_lines.append(f"\n## {inst}")
    out_lines.append(f"repo {row['repo']}  file {gold_file}  note {note}")
    out_lines.append(f"terms[{len(ts)}]: {ts[:12]}")
    fpath = repo / gold_file
    if not fpath.exists():
        # try without prefix src/
        alt = gold_file
        out_lines.append(f"  FILE NOT FOUND at {gold_file} (repo {repo})")
        continue
    lines = fpath.read_text(errors="ignore").splitlines()
    # find occurrences
    occ=[]
    for i,ln in enumerate(lines, start=1):
        ll=ln.lower()
        hits=[t for t in ts if t in ll]
        if hits:
            occ.append((i, ln.strip()[:120], hits))
    out_lines.append(f"  file lines {len(lines)}  term-hit lines {len(occ)}")
    for ln, txt, hs in occ[:12]:
        out_lines.append(f"    L{ln:4d} [{','.join(hs[:3])}] {txt}")
    if len(occ)>12:
        out_lines.append(f"    ... and {len(occ)-12} more")
    # symbols enclosing
    syms = [s for s in load_symbols(repo) if s[1]==gold_file or s[1]==gold_file.lstrip("src/")]
    # also try exact
    if not syms:
        # maybe stored without leading? try suffix match
        all_syms = load_symbols(repo)
        syms = [s for s in all_syms if Path(s[1]).name == Path(gold_file).name]
    syms_sorted = sorted(syms, key=lambda x: x[2])
    out_lines.append(f"  indexed symbols in file: {len(syms_sorted)}")
    for qn, f, sl, el, kind in syms_sorted[:8]:
        out_lines.append(f"    {kind:10s} {qn}  L{sl}-{el}")
    if len(syms_sorted)>8:
        out_lines.append(f"    ... and {len(syms_sorted)-8} more")
    # nearest symbol per occurrence
    def nearest(line):
        cand=[s for s in syms_sorted if s[2]<=line<=s[3]]
        if not cand: return None
        # smallest span
        cand.sort(key=lambda s: s[3]-s[2])
        return cand[0]
    # classify
    from collections import Counter
    buckets=Counter()
    rep_candidates=[]
    for ln, txt, hs in occ:
        ns=nearest(ln)
        kind = ns[4] if ns else "module-level"
        # line classification heuristic
        cls = classify_line(lines[ln-1], kind if ns else None)
        buckets[cls]+=1
        if ns:
            rep_candidates.append(ns)
    out_lines.append(f"  occurrence bucket: {dict(buckets)}")
    # symbol-level BM25 scores via oxide search lexical
    env={"OXIDE_EMBED_URL": os.environ.get("OXIDE_EMBED_URL","")}
    r = sh([str(ROOT/"target/release/oxide"), "search", "--task", problem, "--mode", "lexical", "--limit", "20", "--json"], cwd=repo, env=env)
    try:
        hits=json.loads(r.stdout)
        # filter to file
        file_hits=[h for h in hits if h["file"]==gold_file or h["file"]==gold_file.lstrip("src/") or Path(h["file"]).name==Path(gold_file).name]
        out_lines.append(f"  lexical search: {len(hits)} hits, {len(file_hits)} in file")
        for h in file_hits[:5]:
            out_lines.append(f"    {h['qualified_name']} lexical={h['reasons']} ")
        # best symbol by lexical score would be file_lex rep
        if file_hits:
            out_lines.append(f"  file-lex rep would be: {file_hits[0]['qualified_name']} L{hits[0].get('start_line','?')}? actually {file_hits[0]['file']}#{file_hits[0]['qualified_name']}")
        else:
            # no lexical hit in file => symbol BM25 zero, file lexical would rescue via whole-file but no symbol carries term
            out_lines.append(f"  NO symbol in file has lexical hit in top20 — whole-file term is outside indexed symbols (imports/comments/data) or body not in lexical index weight")
    except Exception as e:
        out_lines.append(f"  search failed: {e} {r.stderr[:200]}")
    # hybrid baseline rank
    r = sh([str(ROOT/"target/release/oxide"), "search", "--task", problem, "--mode", "hybrid", "--limit", "20", "--json"], cwd=repo, env=env)
    try:
        hits=json.loads(r.stdout)
        rank = next((i+1 for i,h in enumerate(hits) if h["file"]==gold_file), None)
        out_lines.append(f"  baseline hybrid rank for file: {rank if rank else 'outside top20'}  top5 files {[h['file'] for h in hits[:5]]}")
    except Exception as e:
        out_lines.append(f"  hybrid search failed {e}")
    # bounded window would contain gold?
    if occ:
        # cluster
        occ_lines=[o[0] for o in occ]
        # smallest window covering >=50% of hits within 60 lines?
        occ_lines_sorted=sorted(occ_lines)
        best_win=None
        best_cov=0
        for w in [30,60,100]:
            for start in occ_lines_sorted:
                cov=sum(1 for l in occ_lines if start<=l<=start+w)
                if cov>best_cov:
                    best_cov=cov
                    best_win=(start,start+w,cov)
        out_lines.append(f"  window coverage: best {best_win} of {len(occ)} hits within window")
        # gold evidence check: assume fix is near term clusters; window would contain if near
        out_lines.append(f"  bounded query-centered window (60 lines) would contain {best_cov}/{len(occ)} term hits -> {'useful' if best_cov>=2 else 'sparse'}")

# also check displaced
out_lines.append("\n# Displaced budgeted files check (why collapse)")
for inst, f in DISPLACED:
    row=tasks.get(inst)
    if not row: continue
    repo = cb.ensure_repo_checkout(row["repo_url"], row["base_commit"])
    problem=row["problem_statement"]
    env={"OXIDE_EMBED_URL": os.environ.get("OXIDE_EMBED_URL","")}
    r = sh([str(ROOT/"target/release/oxide"), "search", "--task", problem, "--mode", "hybrid", "--limit", "20", "--json"], cwd=repo, env=env)
    try:
        hits=json.loads(r.stdout)
        rank = next((i+1 for i,h in enumerate(hits) if h["file"]==f), None)
        out_lines.append(f"  {inst[:8]} {f} hybrid rank {rank} top3 {[h['file'] for h in hits[:3]]}")
    except: pass

Path("eval-agent/benchmark/results/file_span_forensic.txt").write_text("\n".join(out_lines))
print("\n".join(out_lines[:200]))
print("wrote forensic")
