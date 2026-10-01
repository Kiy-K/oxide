"""Stage A: raw `git diff --unified=0` (exact gitutil::diff_text argv) per algorithm,
per non-merge commit C (range C^..C), isolated from host git config."""
import json, os, subprocess, sys, time, hashlib
sys.path.insert(0, os.path.dirname(__file__))
from gitenv import ALGOS, env_for, diff_argv, parse_unified
CODE_EXT = {"py","pyi","ts","tsx","js","jsx","mjs","cjs","rs","go","java","rb","rake","gemspec","php","phtml","c","h","cc","cpp","cxx","hh","hpp","hxx","md"}
def code(path):
    n = path.rsplit("/",1)[-1]
    if n.endswith(".d.ts") or n.endswith(".min.js"): return False
    return n.rsplit(".",1)[-1] in CODE_EXT or n in ("Rakefile","Gemfile")
repo, n = sys.argv[1], int(sys.argv[2])
E = {a: env_for(a) for a in ALGOS}
commits = subprocess.run(["git","rev-list","--no-merges",f"--max-count={n+50}","HEAD"],cwd=repo,env=E["default"],capture_output=True,text=True,check=True).stdout.split()
out = []
for c in commits:
    if len(out) >= n: break
    p = subprocess.run(["git","rev-parse","--verify","-q",c+"^"],cwd=repo,env=E["default"],capture_output=True,text=True)
    if p.returncode: continue  # shallow boundary
    rec = {"commit": c, "algos": {}}
    for a in ALGOS:
        t0 = time.perf_counter()
        r = subprocess.run(diff_argv(f"{c}^..{c}"),cwd=repo,env=E[a],capture_output=True)
        dt = time.perf_counter() - t0
        assert r.returncode == 0, r.stderr
        txt = r.stdout.decode("utf-8","replace")
        parsed = parse_unified(txt)
        rec["algos"][a] = {"sha": hashlib.sha1(r.stdout).hexdigest(), "bytes": len(r.stdout), "secs": dt,
                           "parsed": parsed, "code_parsed": {k:v for k,v in parsed.items() if code(k)}}
    out.append(rec)
json.dump(out, open(sys.argv[3],"w"))
print(repo, len(out))
