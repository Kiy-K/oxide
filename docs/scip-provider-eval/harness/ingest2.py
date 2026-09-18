"""Corrected after Codex review: report duplicate-key collisions explicitly
instead of letting dict overwrite hide them."""
import sqlite3, sys, json
from collections import Counter
sys.path.insert(0, sys.argv[4]); from scipsym import descriptors, qname
db, scipf, label = sys.argv[1], sys.argv[2], sys.argv[3]
c=sqlite3.connect(db); c.row_factory=sqlite3.Row
oxrows=list(c.execute("select file,qualified_name,kind,start_line from symbols"))
oxkeys=Counter((r['file'],r['qualified_name']) for r in oxrows)
ox={(r['file'],r['qualified_name']):r for r in oxrows}
d=json.load(open(scipf))
SKIP={'param','typaram','meta'}
sckeys=Counter(); sc={}
for x in d['defs']:
    dd=descriptors(x['sym'])
    if not dd or dd[-1][1] in SKIP: continue
    q=qname(x['sym'])
    if not q: continue
    sckeys[(x['file'],q)]+=1; sc[(x['file'],q)]=dd[-1][1]
both=set(ox)&set(sc)
print(f"== {label}")
print(f"  OXIDE symbol rows={len(oxrows)}  distinct (file,qname) keys={len(oxkeys)}  "
      f"collisions={sum(v-1 for v in oxkeys.values())}")
print(f"  SCIP eligible def occurrences={sum(sckeys.values())}  distinct keys={len(sckeys)}  "
      f"collisions={sum(v-1 for v in sckeys.values())}")
print(f"  matched keys={len(both)}  [{len(both)/len(oxkeys):.0%} of OXIDE keys, {len(both)/len(sckeys):.0%} of SCIP keys]")
print(f"  OXIDE-only={len(set(ox)-set(sc))}  SCIP-only={len(set(sc)-set(ox))}")
m=Counter((ox[k]['kind'], sc[k]) for k in both)
print("  OXIDE kind <- SCIP descriptor suffix (the only kind signal SCIP gives):")
for (ok,ss),n in sorted(m.items()): print(f"     {ok:<10s} <- {ss:<7s} x{n}")
collapsed={}
for (ok,ss),n in m.items(): collapsed.setdefault(ss,set()).add(ok)
for ss,oks in sorted(collapsed.items()):
    if len(oks)>1: print(f"  !! SCIP suffix '{ss}' collapses OXIDE kinds {sorted(oks)}")
