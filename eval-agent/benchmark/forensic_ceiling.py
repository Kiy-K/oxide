#!/usr/bin/env python3
"""Forensic classification of 18 universal misses (no condition finds file)."""
import json, os, sys, re, sqlite3
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts/agent_eval"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "third_party/ContextBench"))
import contextbench_run as cb

ROOT = cb.ROOT
PIN = ROOT / "eval-agent/results/tier_a_instances.txt"
ALLOW = {l.strip() for l in PIN.read_text().splitlines() if l.strip()}
OX = str(ROOT / "target/release/oxide")
ENV = {"OXIDE_EMBED_URL": os.environ.get("OXIDE_EMBED_URL",""), "OXIDE_EMBED_MODEL": os.environ.get("OXIDE_EMBED_MODEL","")}
assert ENV["OXIDE_EMBED_URL"] and ENV["OXIDE_EMBED_MODEL"]=="qwen3-Q8_0"

def retrieve_limit(repo, cond, problem, limit):
    lim = str(limit)
    if cond=="budgeted":
        cmd = [OX, "context", "--task", problem, "--budget-tokens", "4096", "--json"]
        r = cb.sh(cmd, cwd=repo, env={**os.environ, **ENV})
        j = json.loads(r.stdout)
        items = j.get("items", j)
        return items, 0
    mode = {"lexical":"lexical","vec":"semantic","hybrid":"hybrid"}[cond]
    cmd = [OX, "search", problem, "--mode", mode, "--limit", lim, "--json"]
    r = cb.sh(cmd, cwd=repo, env={**os.environ, **ENV})
    j = json.loads(r.stdout)
    items = j if isinstance(j, list) else j.get("hits", j.get("items", j))
    return items, 0

def tokenize_query(q):
    return [w.lower() for w in re.split(r'[^a-zA-Z0-9]+', q.lower()) if len(w)>=3]

def file_symbols(repo, gold_file):
    db = repo / ".oxide/index.db"
    if not db.exists():
        return []
    try:
        con = sqlite3.connect(str(db))
        cur = con.execute("SELECT qualified_name FROM symbols WHERE file=?", (gold_file,))
        rows = cur.fetchall()
        con.close()
        return rows
    except Exception as e:
        return [("err:"+str(e),)]

def classify(repo, problem, gold_file, lex_map, vec_map, query_terms):
    is_test = "test" in gold_file.lower() or gold_file.startswith("tests/") or "/tests/" in gold_file or gold_file.endswith("_test.py") or gold_file.endswith(".test.ts") or gold_file.endswith(".spec.ts")
    terms = set(query_terms)
    file_tokens = set(re.split(r'[^a-z0-9]+', Path(gold_file).stem.lower())) | set(re.split(r'[^a-z0-9]+', gold_file.lower().replace('/',' ').replace('.',' ')))
    file_tokens = {t for t in file_tokens if len(t)>=3}
    overlap = terms & file_tokens
    syms = file_symbols(repo, gold_file)
    indexed = len(syms)>0
    lex_rank = lex_map.get(gold_file)
    vec_rank = vec_map.get(gold_file)
    lex_hit10 = lex_rank is not None and lex_rank < 10
    vec_hit10 = vec_rank is not None and vec_rank < 10
    lex_hit200 = lex_rank is not None
    vec_hit200 = vec_rank is not None
    if not indexed:
        cls = "indexing/chunking omission"
        why = f"0 symbols indexed for {gold_file}"
    elif not lex_hit200 and not vec_hit200:
        if overlap:
            cls = "lexical/query mismatch (file tokens in query but both misses beyond 200)"
            why = f"overlap {sorted(overlap)} but lex/vec >200"
        else:
            cls = "lexical/query mismatch"
            why = f"query {sorted(list(terms))[:10]} vs file_tokens {sorted(list(file_tokens))[:8]} no overlap"
    elif lex_hit200 and not vec_hit200:
        cls = "semantic miss"
        why = f"lex @{lex_rank} but vec >200"
    elif vec_hit200 and not lex_hit200:
        cls = "lexical/query mismatch"
        why = f"vec @{vec_rank} but lex >200"
    elif lex_hit200 and vec_hit200 and not (lex_hit10 or vec_hit10):
        cls = "hybrid fusion gap (both rank beyond 10, fusion not rescuing)"
        why = f"lex @{lex_rank} vec @{vec_rank} but hybrid top10 missed"
    elif lex_hit10 or vec_hit10:
        cls = "other (should not be universal)"
        why = f"lex {lex_rank} vec {vec_rank}"
    else:
        cls = "other"
        why = f"lex {lex_rank} vec {vec_rank} indexed={indexed}"
    meta = dict(sym_count=len(syms), is_test=is_test, overlap=sorted(overlap), lex_rank=lex_rank, vec_rank=vec_rank, file_tokens=sorted(list(file_tokens))[:8])
    return cls, why, meta

tasks = [t for t in cb.load_tasks() if t["instance_id"] in ALLOW]
from collections import Counter
counts = Counter()
details = []
for row in tasks:
    repo = cb.ensure_repo_checkout(row["repo_url"], row["base_commit"])
    problem = row["problem_statement"]
    cb.index_repo(repo, ENV["OXIDE_EMBED_URL"])
    qterms = tokenize_query(problem)
    gold = set(cb.Gold({"init_ctx": json.loads(row["gold_context"]), "repo_url": row["repo_url"], "commit": row["base_commit"]}).files())
    lex10,_ = retrieve_limit(repo,"lexical",problem,10)
    vec10,_ = retrieve_limit(repo,"vec",problem,10)
    hyb10,_ = retrieve_limit(repo,"hybrid",problem,10)
    bud,_ = retrieve_limit(repo,"budgeted",problem,10)
    lex10_files = {i["file"] for i in lex10}
    vec10_files = {i["file"] for i in vec10}
    hyb10_files = {i["file"] for i in hyb10}
    bud_files = {i["file"] for i in bud}
    lex200,_ = retrieve_limit(repo,"lexical",problem,200)
    vec200,_ = retrieve_limit(repo,"vec",problem,200)
    lex200_map = {i["file"]: idx for idx,i in enumerate(lex200)}
    vec200_map = {i["file"]: idx for idx,i in enumerate(vec200)}
    for g in sorted(gold):
        if (g in lex10_files) or (g in vec10_files) or (g in hyb10_files) or (g in bud_files):
            continue
        cls, why, meta = classify(repo, problem, g, lex200_map, vec200_map, qterms)
        counts[cls]+=1
        details.append((row["instance_id"], g, cls, why, meta))

print(f"universal_misses={len(details)} (NONE across lexical/vec/hybrid/budgeted @10)")
for k,v in counts.most_common():
    print(f"{k}: {v}")
print("\n--- per-file evidence ---")
for iid, g, cls, why, meta in details:
    print(f"\n{iid}\n  gold={g}\n  class={cls}\n  why={why}\n  meta={meta}")
out = ROOT / "eval-agent/benchmark/results/universal_miss_forensics.txt"
out.parent.mkdir(parents=True, exist_ok=True)
with open(out,"w") as f:
    f.write(f"universal_misses={len(details)}\n")
    for k,v in counts.most_common():
        f.write(f"{k}: {v}\n")
    f.write("\n")
    for iid, g, cls, why, meta in details:
        f.write(f"{iid} | {g} | {cls} | {why} | {meta}\n")
print(f"\nwrote {out}")
