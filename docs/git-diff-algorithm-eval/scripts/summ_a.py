import json, sys, statistics as st
A = ["default","myers","minimal","histogram","patience","myers_noindent"]
for r in ["flask","pylint","cobra","zod","oxide"]:
    d = json.load(open(f"stage_a_{r}.json"))
    n = len(d)
    def cnt(a, b, key):
        return sum(x["algos"][a][key] != x["algos"][b][key] for x in d)
    def files_diff(a,b):
        tot=0; diff=0
        for x in d:
            pa, pb = x["algos"][a]["code_parsed"], x["algos"][b]["code_parsed"]
            for f in set(pa)|set(pb):
                tot+=1; diff += pa.get(f)!=pb.get(f)
        return diff, tot
    print(f"== {r}: {n} commits")
    print("  default==myers raw:", n-cnt("default","myers","sha"))
    for a,b in [("myers","histogram"),("myers","patience"),("histogram","patience"),("myers","minimal"),("myers","myers_noindent")]:
        fd = files_diff(a,b)
        print(f"  {a:>9} vs {b:<14} raw-differs {cnt(a,b,'sha'):3}  parsed-differs {cnt(a,b,'parsed'):3}  code-parsed-differs {cnt(a,b,'code_parsed'):3}  (code files {fd[0]}/{fd[1]})")
    for a in ["myers","histogram","patience"]:
        ts=[x["algos"][a]["secs"]*1000 for x in d]
        print(f"  time {a:>9}: median {st.median(ts):.2f} ms  p95 {sorted(ts)[int(.95*n)-1]:.2f}  max {max(ts):.1f}  total {sum(ts):.0f}")
