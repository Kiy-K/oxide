import gzip, json, os, sys, glob, statistics as st
from collections import defaultdict
ALG = ["default","myers","histogram","patience","myers_2"]
PAIRS = [("default","myers"),("myers","myers_2"),("myers","histogram"),("myers","patience"),("histogram","patience")]
def feats(o):
    r = json.loads(o["review_json"])
    f = {
     "changed_files": tuple(r["changed_files"]),
     "cs_set": frozenset((c["file"], c["qualified_name"]) for c in r["changed_symbols"]),
     "cs_lines": tuple((c["file"], c["qualified_name"], c["added_lines"]) for c in r["changed_symbols"]),
     "review_related": tuple((c["file"], c["qualified_name"]) for c in r["related"]),
     "review_json": o["review_json"], "review_human": o["review_human"],
    }
    for k in sorted(o):
        if k.startswith("ctx") and k.endswith("_json"):
            b = k[:-5]; c = json.loads(o[k])
            f[b+"_items"] = tuple((i["file"], i["qualified_name"], i["role"]) for i in c["items"])
            f[b+"_json"] = o[k]; f[b+"_human"] = o[b+"_human"]
    return f
def load(d):
    return {a: feats(json.load(gzip.open(os.path.join(d, a+".json.gz"), "rt"))) for a in ALG if os.path.exists(os.path.join(d, a+".json.gz"))}
def main(root, label):
    states = sorted(glob.glob(os.path.join(root, "*")))
    agg = defaultdict(lambda: defaultdict(int)); details = []
    times = defaultdict(list)
    for d in states:
        F = load(d)
        o = json.load(gzip.open(os.path.join(d, "myers.json.gz"), "rt"))
        for a in ALG:
            oo = json.load(gzip.open(os.path.join(d, a+".json.gz"), "rt"))
            times[a].append(oo["t_review"])
        keys = F["myers"].keys()
        for a, b in PAIRS:
            for k in keys:
                agg[(a,b)][k] += F[a][k] != F[b][k]
        rec = {"state": os.path.basename(d)}
        for a, b in [("myers","histogram"),("myers","patience"),("histogram","patience")]:
            sa, sb = F[a]["cs_set"], F[b]["cs_set"]
            rec[f"{a}-{b}"] = {"only_"+a: sorted(sa-sb), "only_"+b: sorted(sb-sa),
                               "lines_differ": F[a]["cs_lines"] != F[b]["cs_lines"],
                               "ctx_differ": [k for k in keys if k.startswith("ctx") and k.endswith("_items") and F[a][k] != F[b][k]],
                               "related_differ": F[a]["review_related"] != F[b]["review_related"],
                               "n_cs": (len(sa), len(sb))}
        details.append(rec)
    print(f"#### {label}: {len(states)} states")
    keys = list(F["myers"].keys())
    print("pair".ljust(22) + " ".join(k[:14].rjust(14) for k in keys))
    for p in PAIRS:
        print(f"{p[0]}~{p[1]}".ljust(22) + " ".join(str(agg[p][k]).rjust(14) for k in keys))
    print("review wall ms median:", {a: round(st.median(times[a])*1000,1) for a in ALG})
    return details
if __name__ == "__main__":
    det = main(sys.argv[1], sys.argv[2])
    json.dump(det, open(sys.argv[3], "w"), indent=1)
