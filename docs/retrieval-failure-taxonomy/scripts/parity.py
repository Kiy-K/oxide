import json,gzip,glob,os,sys
R='/home/user/oxide/docs/ranking-fusion-eval/results/'
DUMP={'dev':'dump-plain.jsonl.gz','masked':'dump-masked.jsonl.gz','heldout':'dump-heldout.jsonl.gz','cb':'dump-contextbench.jsonl.gz'}
D={s:{json.loads(l)['id']:json.loads(l) for l in gzip.open(R+f,'rt')} for s,f in DUMP.items()}
res=[]
for f in sorted(glob.glob('/tmp/claude-0/work/out/*.jsonl')):
    s=os.path.basename(f).split('__')[0]
    for l in open(f):
        r=json.loads(l); d=D[s][r['id']]
        lk=[k for k,_ in r['lexical']]; dk=[k for k,_ in d['lexical']]
        lex_keys=lk==dk
        lex_sc=lex_keys and all(abs(a[1]-b[1])<=1e-4*max(1,abs(b[1])) for a,b in zip(r['lexical'],d['lexical']))
        fk=[k for k,_ in r['fused']]; dfk=[x[0] for x in d['fused']]
        fused_ok=fk==dfk
        fused_top16=fk[:16]==dfk[:16]
        pk=[(i['key'],i['role'].capitalize() if isinstance(i['role'],str) else i['role'],i['est']) for i in r['base']['packed']]
        dp=[(i['id'],i['role'],i['est_tokens']) for i in d['pack']['items']]
        pack_ok=[(a,b.lower(),c) for a,b,c in pk]==[(a,b.lower(),c) for a,b,c in dp] and r['used']==d['pack']['used_tokens']
        om_ok=sorted(map(tuple,r['base']['omitted']))==sorted(map(tuple,d['pack']['omitted']))
        res.append((s,r['id'],len(r['sem_missing']),lex_keys,lex_sc,fused_ok,fused_top16,pack_ok,om_ok))
import collections
c=collections.defaultdict(collections.Counter)
for s,i,sm,*fl in res:
    c[s]['n']+=1; c[s]['sem_missing_tasks']+=sm>0
    for name,v in zip(['lex_keys','lex_scores','fused_full','fused_top16','pack','omitted'],fl): c[s][name]+=v
for s in c: print(s,dict(c[s]))
if '-v' in sys.argv:
    for x in res:
        if not all(x[3:]) or x[2]: print(x)
