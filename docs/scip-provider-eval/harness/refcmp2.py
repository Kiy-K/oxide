"""Corrected after Codex review:
 - parameter / type-parameter / meta definitions are NOT in defmap (a reference
   to a parameter was previously credited to its owning method);
 - denominator is len(sc_exact), the exact-target edge set;
 - bare-name collapse is reported separately instead of silently shrinking it."""
import sqlite3, sys, json
sys.path.insert(0, sys.argv[4]); from scipsym import descriptors, qname
db, scipf, label = sys.argv[1], sys.argv[2], sys.argv[3]
c=sqlite3.connect(db); c.row_factory=sqlite3.Row
syms=list(c.execute("select id,file,qualified_name,name,kind from symbols"))
byid={r['id']:r for r in syms}
oxnames={}
for s in syms:
    if s['kind']!='module': oxnames.setdefault(s['name'],[]).append((s['file'],s['qualified_name']))
ox=set()
for r in c.execute("select symbol_id,kind,target from symbol_relations where kind in ('calls','bases')"):
    s=byid[r['symbol_id']]; ox.add((s['file'],s['qualified_name'],r['target']))
for r in c.execute("select id,references_json from symbols"):
    s=byid[r['id']]
    for t in json.loads(r['references_json'] or '[]'): ox.add((s['file'],s['qualified_name'],t))

d=json.load(open(scipf))
SKIP={'param','typaram','meta'}
defmap={}; dropped=0
for x in d['defs']:
    dd=descriptors(x['sym'])
    if not dd: continue
    if dd[-1][1] in SKIP: dropped+=1; continue          # <-- the fix
    q=qname(x['sym'])
    if q: defmap[x['sym']]=(x['file'],q)
sc_exact=set(); param_edges=0
for e in d['edges']:
    cd=descriptors(e['caller'])
    if not cd or cd[-1][1] in SKIP: continue
    cq=qname(e['caller'])
    td=descriptors(e['callee'])
    if td and td[-1][1] in SKIP: param_edges+=1; continue
    tgt=defmap.get(e['callee'])
    if not cq or not tgt or tgt==(e['file'],cq): continue
    sc_exact.add((e['file'],cq,tgt[0],tgt[1]))
xf={e for e in sc_exact if e[2]!=e[0]}

covered=0; amb=0; fan=[]
for f,cq,tf,tq in sc_exact:
    bare=tq.split('.')[-1]
    if (f,cq,bare) in ox: covered+=1
    n=len(oxnames.get(bare,[]))
    if n>1: amb+=1; fan.append(n)
bare_keys={(f,cq,tq.split('.')[-1]) for f,cq,tf,tq in sc_exact}
print(f"== {label}")
print(f"  SCIP definitions excluded as param/typaram/meta : {dropped}")
print(f"  SCIP reference edges whose TARGET was a param   : {param_edges}  (previously miscounted)")
print(f"  SCIP in-project resolved reference edges        : {len(sc_exact)}   (cross-file: {len(xf)})")
print(f"    distinct after collapsing target to a bare name: {len(bare_keys)}")
print(f"  OXIDE has a matching bare-name edge             : {covered}/{len(sc_exact)}  ({covered/len(sc_exact):.0%})")
print(f"  of those SCIP edges, bare name is ambiguous in OXIDE: {amb}/{len(sc_exact)}"
      + (f"  (mean fan-out {sum(fan)/len(fan):.1f})" if fan else ""))
print(f"  OXIDE bare-name edge set (calls+bases+references): {len(ox)}")
print("  -- cross-file exact edges OXIDE cannot disambiguate --")
shown=0
for f,cq,tf,tq in sorted(xf):
    n=len(oxnames.get(tq.split('.')[-1],[]))
    if n>1 and shown<6: print(f"     {f}#{cq} -> {tf}#{tq}  (bare '{tq.split('.')[-1]}' matches {n})"); shown+=1
