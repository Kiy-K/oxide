"""Cross-check: taxonomy rows.json.gz per-unit fused/lex/sem ranks vs the committed dumps."""
import json,gzip,collections,os
D0=os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)),'..','..'))+'/'; R=D0+'ranking-fusion-eval/results/'
rows=json.load(gzip.open(D0+'retrieval-failure-taxonomy/results/rows.json.gz','rt'))
print('rows',len(rows),collections.Counter(r['set'] for r in rows))
D={}
for s,f in [('dev','dump-plain'),('masked','dump-masked'),('heldout','dump-heldout'),('cb','dump-contextbench')]:
    for l in gzip.open(R+f+'.jsonl.gz','rt'):
        d=json.loads(l); D[(s,d['id'])]=d
def rm(lst):
    m={}
    for i,e in enumerate(lst): m.setdefault(e[0],i+1)
    return m
chk=collections.Counter(); bad=[]
for r in rows:
    d=D.get((r['set'],r['id']))
    if d is None: chk['missing_task',r['set']]+=1; continue
    chk['task',r['set']]+=1
    F,L,S=rm(d['fused']),rm(d['lexical']),rm(d['semantic'])
    for st,sub,w,info in r['subs']:
        if not isinstance(info,dict) or 'key' not in info: continue
        for name,M in (('fused',F),('lex',L),('sem',S)):
            if name not in info: continue
            ok=M.get(info['key'])==info[name]; chk[name,r['set'],ok]+=1
            if not ok: bad.append((r['set'],r['id'],info['key'],name))
for k,v in sorted(chk.items(),key=str): print(k,v)
print('total checks',sum(v for k,v in chk.items() if k[0]!='task'),'mismatches',len(bad))
