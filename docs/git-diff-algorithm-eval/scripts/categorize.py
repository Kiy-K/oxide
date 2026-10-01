import gzip, json, glob, os, sys
from collections import Counter, defaultdict
def L(d,a): return json.load(gzip.open(f"{d}/{a}.json.gz","rt"))
PAIRS=[("myers","histogram"),("myers","patience"),("histogram","patience")]
root=sys.argv[1]
tot=defaultdict(Counter); setcases=[]
for d in sorted(glob.glob(f"{root}/*/*")):
    repo=d.split("/")[-2]
    F={a:L(d,a) for a in ("myers","histogram","patience")}
    for a,b in PAIRS:
        ra,rb=json.loads(F[a]["review_json"]),json.loads(F[b]["review_json"])
        ca=[(c["file"],c["qualified_name"],c["added_lines"]) for c in ra["changed_symbols"]]
        cb=[(c["file"],c["qualified_name"],c["added_lines"]) for c in rb["changed_symbols"]]
        k=tot[(repo,a,b)]; k["states"]+=1
        if F[a]["review_json"]==F[b]["review_json"]: k["review_identical"]+=1
        elif {x[:2] for x in ca}!={x[:2] for x in cb}:
            k["review_symbol_set"]+=1; setcases.append((d,a,b,sorted({x[:2] for x in ca}^{x[:2] for x in cb})))
        elif ca==cb: k["review_symbols_identical_other_fields_differ"]+=1
        elif sorted(ca)==sorted(cb): k["review_order_only"]+=1
        elif [x[:2] for x in ca]==[x[:2] for x in cb]: k["review_added_lines_only"]+=1
        else: k["review_order+added_lines"]+=1
        if ra["related"]!=rb["related"]: k["review_related_differs"]+=1
        for key in F[a]:
            if key.startswith("ctx") and key.endswith("_json"):
                xa,xb=json.loads(F[a][key]),json.loads(F[b][key])
                if xa==xb: k["ctx_identical"]+=1; continue
                fields=set()
                for i,j in zip(xa["items"],xb["items"]):
                    fields|={f for f in set(i)|set(j) if f not in i or f not in j or i[f]!=j[f]}
                if len(xa["items"])!=len(xb["items"]): fields.add("n_items")
                fields|={f"top.{f}" for f in set(xa)|set(xb) if f!="items" and xa.get(f, KeyError)!=xb.get(f, KeyError)}
                k["ctx_differs:"+",".join(sorted(fields))]+=1
for key in sorted(tot): print(key, dict(tot[key]))
print("\nSYMBOL-SET CASES:")
for s in setcases: print(s)
