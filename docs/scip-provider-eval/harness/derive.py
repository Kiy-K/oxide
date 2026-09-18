"""Derive (caller_symbol -> resolved callee) edges from a SCIP index.

Attribution mirrors OXIDE's structural_relations::enclosing: innermost
DEFINITION occurrence whose enclosing_range contains the reference line.
"""
import sys, json, scip_pb2

def rng(o):
    if o.HasField('multi_line_range'):
        r=o.multi_line_range; return (r.start.line, r.start.character, r.end.line, r.end.character)
    if o.HasField('single_line_range'):
        r=o.single_line_range; return (r.start.line, r.start.character, r.start.line, r.end_character)
    e=list(o.range)
    return (e[0],e[1],e[0],e[2]) if len(e)==3 else tuple(e)

def encl(o):
    if o.HasField('multi_line_enclosing_range'):
        r=o.multi_line_enclosing_range; return (r.start.line, r.end.line)
    if o.HasField('single_line_enclosing_range'):
        r=o.single_line_enclosing_range; return (r.start.line, r.start.line)
    if o.enclosing_range:
        e=list(o.enclosing_range); return (e[0], e[2] if len(e)==4 else e[0])
    return None

idx = scip_pb2.Index(); idx.ParseFromString(open(sys.argv[1],'rb').read())
edges=[]; bases=[]; defs_out=[]
for d in idx.documents:
    # definition scopes that carry an enclosing_range
    scopes=[]
    for o in d.occurrences:
        if (o.symbol_roles & 1) and not o.symbol.startswith('local '):
            e=encl(o)
            defs_out.append({'file':d.relative_path,'sym':o.symbol,'line':rng(o)[0],'encl':e})
            if e: scopes.append((e[0], e[1], o.symbol))
    for o in d.occurrences:
        if o.symbol_roles & 1: continue          # definitions aren't calls
        if o.symbol.startswith('local '): continue
        line = rng(o)[0]
        cands=[s for s in scopes if s[0] <= line <= s[1]]
        if not cands: continue
        caller = min(cands, key=lambda s: (s[1]-s[0], -len(s[2])))[2]
        if caller == o.symbol: continue
        edges.append({'file':d.relative_path,'caller':caller,'callee':o.symbol,'line':line})
    for s in d.symbols:
        for r in s.relationships:
            if r.is_implementation:
                bases.append({'file':d.relative_path,'sub':s.symbol,'base':r.symbol})
json.dump({'edges':edges,'bases':bases,'defs':defs_out}, open(sys.argv[2],'w'))
print(f"defs={len(defs_out)} edges={len(edges)} impl_rels={len(bases)}")
