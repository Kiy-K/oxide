#!/usr/bin/env python3
"""External repository-context benchmark vs OXIDE (frozen).
Compares 5 signals under same 21-task pinned conditions:
  A grep (lexical file hits via git grep)
  B repo-map (symbol-name structure)
  C dependency-aware (lexical seeds + 1-hop imports)
  D file-level dense (mean symbol vectors per file)
  E OXIDE lexical / vec / hybrid / budgeted (frozen baseline)
Metrics: R@1/5/10/20, MRR, nDCG@10, tokens, latency.
Most important: failure overlap per gold file, especially OXIDE miss + competitor hit.
"""
import json, math, os, re, sqlite3, subprocess, sys, time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts/agent_eval"))
import contextbench_run as cb

ROOT = cb.ROOT
PIN = ROOT / "eval-agent/results/tier_a_instances.txt"
ALLOW = {l.strip() for l in PIN.read_text().splitlines() if l.strip()}
OX = str(ROOT / "target/release/oxide")
CHARS_PER_TOKEN = 4.0
STOP = set("the this that with from into when what then they them their there these those have been will would could should your you're about which where while whose also more most some such only over under between because however therefore thus hence other another each every any all can cannot just like even ever never always often once twice here there does done doing being been was were has had having its it's don didn won isn aren were wasn weren".split())

ENV = {"OXIDE_EMBED_URL": os.environ.get("OXIDE_EMBED_URL",""), "OXIDE_EMBED_MODEL": os.environ.get("OXIDE_EMBED_MODEL","")}
assert ENV["OXIDE_EMBED_URL"], "set OXIDE_EMBED_URL"
assert ENV["OXIDE_EMBED_MODEL"]=="qwen3-Q8_0"

def terms(problem):
    ws=[w.lower() for w in re.split(r"[^a-zA-Z0-9]+", problem)]
    return [w for w in dict.fromkeys(ws) if len(w)>=4 and w not in STOP][:24]

def est_tokens(text): return int(len(text)/CHARS_PER_TOKEN)

def sh(cmd,cwd=None,env=None,timeout=120):
    return subprocess.run(cmd,cwd=cwd,capture_output=True,text=True,timeout=timeout,env={**os.environ,**(env or {})})

def retrieve_oxide(repo, mode, problem, limit=10):
    ox=str(ROOT/"target/release/oxide")
    env={"OXIDE_EMBED_URL": ENV["OXIDE_EMBED_URL"]}
    if mode=="budgeted":
        r=sh([ox,"context","--task",problem,"--budget-tokens","4096","--json"],cwd=repo,env=env)
        pack=json.loads(r.stdout)
        items=[{"file":it["file"],"start_line":it["start_line"],"end_line":it["end_line"]} for it in pack["items"]]
        files=list(dict.fromkeys(it["file"] for it in pack["items"]))
        return files, pack["used_tokens"], 0
    m={"lexical":"lexical","vec":"semantic","hybrid":"hybrid"}[mode]
    t0=time.perf_counter()
    r=sh([ox,"search",problem,"--mode",m,"--limit",str(limit),"--json"],cwd=repo,env=env)
    dt=time.perf_counter()-t0
    hits=json.loads(r.stdout)
    files=list(dict.fromkeys(h["file"] for h in hits))
    tok=est_tokens("\n".join(h.get("snippet","") for h in hits))
    return files, tok, dt

def grep_rank(repo, problem, limit=10):
    t0=time.perf_counter()
    ts=terms(problem)
    counts=defaultdict(int)
    for t in ts:
        r=sh(["git","grep","-c","-i","-F",t,"--","*.py","*.ts","*.tsx"],cwd=repo)
        for line in r.stdout.splitlines():
            fname,_,cnt=line.rpartition(":")
            counts[fname]+=int(cnt) if cnt.isdigit() else 0
    ranked=[f for f,_ in sorted(counts.items(), key=lambda kv:-kv[1])[:limit]]
    dt=time.perf_counter()-t0
    # tokens whole-file estimate
    tok=0
    for f in ranked:
        p=repo/f
        if p.exists():
            try: tok+=len(p.read_text(errors="ignore"))//4
            except: pass
    return ranked, tok, dt

def repomap_rank(repo, problem, limit=10):
    t0=time.perf_counter()
    ts=set(terms(problem))
    # symbol-name lexical: count term hits in qualified_name+signature
    con=sqlite3.connect(str(repo/".oxide/index.db"))
    rows=con.execute("SELECT file, qualified_name, signature FROM symbols").fetchall()
    con.close()
    scores=defaultdict(int)
    for f,qn,sig in rows:
        text=(qn+" "+(sig or "")).lower()
        for t in ts:
            if t in text:
                scores[f]+=1
    # also file path token hits
    for f in list(scores.keys()):
        # bonus if term in path already counted via qn? skip
        pass
    ranked=[f for f,_ in sorted(scores.items(), key=lambda kv:-kv[1])[:limit]]
    dt=time.perf_counter()-t0
    tok=0
    for f in ranked:
        p=repo/f
        if p.exists():
            try: tok+=len(p.read_text(errors="ignore"))//4
            except: pass
    return ranked, tok, dt

def dep_rank(repo, problem, limit=10):
    # lexical seeds top-5 + 1-hop imports via RelationGraph if available, else file co-occurrence
    t0=time.perf_counter()
    # get lexical top-5 via oxide lexical
    lex_files,_,_=retrieve_oxide(repo,"lexical",problem,limit=5)
    # build graph: for each seed file, find files that import it or it imports (via symbols imports column)
    con=sqlite3.connect(str(repo/".oxide/index.db"))
    # symbols imports is JSON list of imported modules; we approximate by file-level adjacency
    rows=con.execute("SELECT file, imports FROM symbols").fetchall()
    con.close()
    # map module name -> file (simple heuristic: last path component)
    file_modules={}
    for f,_ in rows:
        mod=f.replace("/",".").removesuffix(".py").removesuffix(".ts")
        file_modules[mod]=f
    adj=defaultdict(set)
    for f,imp_json in rows:
        try:
            imps=json.loads(imp_json) if isinstance(imp_json,str) else (imp_json or [])
        except: imps=[]
        for m in imps or []:
            if m in file_modules:
                adj[f].add(file_modules[m])
                adj[file_modules[m]].add(f)
    expanded=list(lex_files)
    seen=set(expanded)
    for f in lex_files:
        for nb in adj.get(f,[]):
            if nb not in seen:
                expanded.append(nb)
                seen.add(nb)
            if len(expanded)>=limit: break
        if len(expanded)>=limit: break
    dt=time.perf_counter()-t0
    tok=0
    for f in expanded[:limit]:
        p=repo/f
        if p.exists():
            try: tok+=len(p.read_text(errors="ignore"))//4
            except: pass
    return expanded[:limit], tok, dt

def file_dense_rank(repo, problem, limit=10):
    t0=time.perf_counter()
    # mean vector per file from symbol embeddings, cosine vs query embedding
    # fetch query embedding via HTTP
    import urllib.request, json as _json
    url=ENV["OXIDE_EMBED_URL"]
    data=_json.dumps({"input": problem}).encode()
    req=urllib.request.Request(url, data=data, headers={"Content-Type":"application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            qv=_json.loads(resp.read())["data"][0]["embedding"]
    except Exception as e:
        return [],0,time.perf_counter()-t0
    con=sqlite3.connect(str(repo/".oxide/index.db"))
    # embeddings table: id (u64 as i64), vec blob? check schema
    try:
        rows=con.execute("SELECT s.file, e.embedding FROM symbols s JOIN embeddings e ON s.id=e.id").fetchall()
    except Exception:
        rows=[]
    con.close()
    if not rows:
        return [],0,time.perf_counter()-t0
    # per file mean
    import math
    file_vecs=defaultdict(list)
    for f,blob in rows:
        # blob is stored as bytes of float32 array? OXIDE uses hashed? Actually HashedEmbedder stores vectors as blob of f32
        # try to decode
        if blob is None: continue
        try:
            # blob may be json or bytes
            if isinstance(blob, bytes):
                import struct
                n=len(blob)//4
                vec=list(struct.unpack(f"<{n}f", blob[:n*4]))
            else:
                vec=json.loads(blob) if isinstance(blob,str) else list(blob)
        except:
            continue
        file_vecs[f].append(vec)
    # mean per file
    file_mean={}
    for f,vecs in file_vecs.items():
        dim=len(vecs[0])
        mean=[0.0]*dim
        for v in vecs:
            for i,x in enumerate(v): mean[i]+=x
        mean=[x/len(vecs) for x in mean]
        file_mean[f]=mean
    # cosine
    def cosine(a,b):
        dot=sum(x*y for x,y in zip(a,b))
        na=math.sqrt(sum(x*x for x in a))
        nb=math.sqrt(sum(x*x for x in b))
        return dot/(na*nb) if na and nb else 0.0
    scored=[(f,cosine(qv,mv)) for f,mv in file_mean.items()]
    scored.sort(key=lambda kv:-kv[1])
    ranked=[f for f,_ in scored[:limit]]
    dt=time.perf_counter()-t0
    tok=0
    for f in ranked:
        p=repo/f
        if p.exists():
            try: tok+=len(p.read_text(errors="ignore"))//4
            except: pass
    return ranked, tok, dt

def ndcg(ranked, gold, k=10):
    dcg=0.0
    for i,f in enumerate(ranked[:k]):
        if f in gold: dcg+=1.0/math.log2(i+2)
    # ideal: all gold at top
    ideal=min(len(gold),k)
    idcg=sum(1.0/math.log2(i+2) for i in range(ideal))
    return dcg/idcg if idcg else 0.0

def main():
    tasks=[t for t in cb.load_tasks() if t["instance_id"] in ALLOW]
    print(f"tasks={len(tasks)} model={ENV['OXIDE_EMBED_MODEL']}")
    # also measure index time per repo once (deduped) — skip if already indexed (frozen stack)
    # indexes are cached at ~/.cache/oxide-contextbench/repos/*/.oxide/index.db and already built with qwen3-Q8_0
    # re-index is incremental/no-op but can be slow; we just ensure checkout and that a DB exists
    seen_repos=set()
    for row in tasks:
        repo=cb.ensure_repo_checkout(row["repo_url"], row["base_commit"])
        key=str(repo)
        if key in seen_repos: continue
        seen_repos.add(key)
        db=repo/".oxide/index.db"
        if db.exists():
            print(f"skip indexing {repo.name} (db exists {db.stat().st_size//1024}KB)",flush=True)
        else:
            print(f"indexing {repo.name}...",flush=True)
            cb.index_repo(repo, ENV["OXIDE_EMBED_URL"])
            print(f"  indexed {repo.name}",flush=True)
        # ensure vectors are built (index is incremental)
    agg=defaultdict(lambda: defaultdict(float))
    counts=defaultdict(int)
    # per gold-file failure matrix
    rows=[]
    for row in tasks:
        repo=cb.ensure_repo_checkout(row["repo_url"], row["base_commit"])
        gold=set(cb.Gold({"init_ctx": json.loads(row["gold_context"]), "repo_url": row["repo_url"], "commit": row["base_commit"]}).files())
        # retrieve all conditions
        retrievers={
            "grep": lambda: grep_rank(repo, row["problem_statement"], 10),
            "repomap": lambda: repomap_rank(repo, row["problem_statement"], 10),
            "dep": lambda: dep_rank(repo, row["problem_statement"], 10),
            "file_dense": lambda: file_dense_rank(repo, row["problem_statement"], 10),
            "oxide_lex": lambda: retrieve_oxide(repo,"lexical",row["problem_statement"],10),
            "oxide_vec": lambda: retrieve_oxide(repo,"vec",row["problem_statement"],10),
            "oxide_hybrid": lambda: retrieve_oxide(repo,"hybrid",row["problem_statement"],10),
            "oxide_budgeted": lambda: retrieve_oxide(repo,"budgeted",row["problem_statement"],10),
        }
        results={}
        for name,fn in retrievers.items():
            try:
                files,tok,lat=fn()
            except Exception as e:
                files, tok, lat = [],0,0
                print(f"  {name} error {e}")
            results[name]=(files,tok,lat)
            # aggregate R@k/MRR/nDCG
            a=agg[name]
            for k in [1,5,10,20]:
                hit=len(gold & set(files[:k]))/max(1,len(gold)) if k<=10 else len(gold & set(files[:10]))/max(1,len(gold))
                a[f"R@{k}"]+=hit
                if k==10:
                    a["hit@10"]+=float(any(f in gold for f in files[:10]))
            rr=0.0
            for i,f in enumerate(files):
                if f in gold:
                    rr=1.0/(i+1); break
            a["MRR"]+=rr
            a["nDCG@10"]+=ndcg(files,gold,10)
            a["tok"]+=tok
            a["lat"]+=lat
            counts[name]+=1
        # per gold file overlap
        for g in sorted(gold):
            oxide_hit= g in results["oxide_budgeted"][0][:10]
            comp_hits={k: g in v[0][:10] for k,v in results.items()}
            rows.append((row["instance_id"], g, oxide_hit, comp_hits, {k: (v[0].index(g)+1 if g in v[0] else None) for k,v in results.items()}))
        print(f"[{row['instance_id'][:36]}] gold={sorted(gold)} oxide_budgeted={results['oxide_budgeted'][0][:3]} grep={results['grep'][0][:2]} repomap={results['repomap'][0][:2]}")
    # print aggregates
    print("\n=== aggregate (mean over tasks) ===")
    for name in ["grep","repomap","dep","file_dense","oxide_lex","oxide_vec","oxide_hybrid","oxide_budgeted"]:
        a=agg[name]; n=counts[name]
        if n==0: continue
        print(f"{name:15} R@1={a['R@1']/n:.3f} R@5={a['R@5']/n:.3f} R@10={a['R@10']/n:.3f} MRR={a['MRR']/n:.3f} nDCG@10={a['nDCG@10']/n:.3f} tok={a['tok']/n:.0f} lat={a['lat']/n:.3f}s hit@10={a['hit@10']/n:.3f}")
    # failure overlap summary
    total_gold=len(rows)
    oxide_miss_comp_hit=defaultdict(int)
    for inst,g,oxide_hit,comp_hits,ranks in rows:
        if not oxide_hit:
            for k,hit in comp_hits.items():
                if hit and k.startswith("oxide")==False:
                    oxide_miss_comp_hit[k]+=1
    print("\n=== failure overlap: OXIDE miss + competitor hit (gold-file instances) ===")
    print(f"total gold files={total_gold} oxide_budgeted misses={sum(1 for _,_,h,_,_ in rows if not h)}")
    for k,v in sorted(oxide_miss_comp_hit.items(), key=lambda kv:-kv[1]):
        print(f"  {k}: {v}")
    # per case where competitor rescues
    print("\n=== rescue cases (OXIDE miss + competitor hit) ===")
    for inst,g,oxide_hit,comp_hits,ranks in rows:
        if not oxide_hit and any(comp_hits[k] for k in ["grep","repomap","dep","file_dense"]):
            rescuers=[k for k in ["grep","repomap","dep","file_dense"] if comp_hits[k]]
            print(f"{inst}|{g}|rescued_by={','.join(rescuers)} ranks={ {k:ranks[k] for k in rescuers} } oxide_rank={ranks['oxide_hybrid']}")
    # save raw
    out=ROOT/"eval-agent/benchmark/results/external_benchmark.jsonl"
    with open(out,"w") as f:
        for inst,g,oxide_hit,comp_hits,ranks in rows:
            f.write(json.dumps({"instance":inst,"gold":g,"oxide_hit":oxide_hit,"comp_hits":comp_hits,"ranks":ranks})+"\n")
    print(f"wrote {out}")

if __name__=="__main__": main()
