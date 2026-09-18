"""Quantify Codex's derive.py concern: how often is line-granularity attribution
actually ambiguous, i.e. two definition enclosing_ranges share a boundary line?"""
import sys, scip_pb2
idx=scip_pb2.Index(); idx.ParseFromString(open(sys.argv[1],'rb').read())
def encl(o):
    if o.HasField('multi_line_enclosing_range'):
        r=o.multi_line_enclosing_range; return (r.start.line,r.end.line)
    if o.HasField('single_line_enclosing_range'):
        r=o.single_line_enclosing_range; return (r.start.line,r.start.line)
    if o.enclosing_range:
        e=list(o.enclosing_range); return (e[0], e[2] if len(e)==4 else e[0])
    return None
tot=risky=0
for d in idx.documents:
    scopes=[]
    for o in d.occurrences:
        if (o.symbol_roles&1) and not o.symbol.startswith('local '):
            e=encl(o)
            if e: scopes.append(e)
    for o in d.occurrences:
        if o.symbol_roles&1 or o.symbol.startswith('local '): continue
        line=(o.multi_line_range.start.line if o.HasField('multi_line_range')
              else o.single_line_range.start.line if o.HasField('single_line_range')
              else (o.range[0] if o.range else -1))
        cands=[s for s in scopes if s[0]<=line<=s[1]]
        if not cands: continue
        tot+=1
        w=min(c[1]-c[0] for c in cands)
        if sum(1 for c in cands if c[1]-c[0]==w)>1: risky+=1
print(f"{sys.argv[2]}: attributed edges={tot}  with a same-width tie (line granularity could matter)={risky}")
