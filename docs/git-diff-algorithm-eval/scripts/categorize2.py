import gzip, json, glob, sys
from collections import Counter
def L(d,a): return json.load(gzip.open(f"{d}/{a}.json.gz","rt"))
c=Counter(); ex=[]
root = sys.argv[1]  # e.g. b2/out
states = sorted(glob.glob(f"{root}/*/*"))
assert states, f"no states under {root}"
for d in states:
    F={a:L(d,a) for a in ("myers","ihc3","myers_noindent")}
    R={a:json.loads(F[a]["review_json"]) for a in F}
    S={a:{(x["file"],x["qualified_name"]) for x in R[a]["changed_symbols"]} for a in F}
    I={a:[(i["file"],i["qualified_name"],i["role"]) for i in json.loads(F[a]["ctx0_bal_json"])["items"]] for a in F}
    c["states"]+=1
    for k in ("ihc3","myers_noindent"):
        c[k+":review_differs"]+=F[k]["review_json"]!=F["myers"]["review_json"]
        c[k+":symbol_set_differs"]+=S[k]!=S["myers"]
        c[k+":extra_symbols"]+=len(S[k]-S["myers"]); c[k+":missing_symbols"]+=len(S["myers"]-S[k])
        c[k+":related_differs"]+=R[k]["related"]!=R["myers"]["related"]
        c[k+":ctx_json_differs"]+=F[k]["ctx0_bal_json"]!=F["myers"]["ctx0_bal_json"]
        c[k+":ctx_items_differ"]+=I[k]!=I["myers"]
        if k=="ihc3" and S[k]!=S["myers"]: ex.append((d, "extra:", sorted(S[k]-S["myers"]), "missing:", sorted(S["myers"]-S[k])))
for k in sorted(c): print(k, c[k])
print("ihc3 symbol-set-differing states (all):")
for e in ex: print(e)
