import json,gzip,re,os,sys
sys.path.insert(0,'/tmp/claude-0/work/an')
R='/home/user/oxide/docs/ranking-fusion-eval/results/'
OUT='/tmp/claude-0/work/inputs'; os.makedirs(OUT,exist_ok=True)
HEADS={'dev:pylint':'ba5c0c79bc9c5f752404de96e4cde7e3aa684c82','dev:pytest':'79833c82b92409dd74c9236c59b8b18970d026e0',
 'dev:zod':'2bf7b0630d53','dev:requests':'611c6162cbc4ac2020a2f91c7cfa4f3abf9bbb60','dev:flask':'d73fa1cdcbd8',
 'heldout:ripgrep':'3fce3b5bb0236da2df6d99672afb8a719642eca7','heldout:httpx':'b5addb64f0161ff6bfe94c124ef76f6a1fba5254',
 'heldout:pylint':'ba5c0c79bc9c5f752404de96e4cde7e3aa684c82'}
IDENT=re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+){2,}|[A-Za-z_][A-Za-z0-9_]*(?:(?:\.|::)[A-Za-z_][A-Za-z0-9_]*)*(?:\(\))?")
QUOTE=re.compile(r"`{1,2}([^`\n]{3,80})`{1,2}|\"([^\"\n]{3,80})\"|'([^'\n]{3,80})'")
def is_identlike(t):
    b=t.rstrip('()')
    if b.count('-')>=2: return True
    return ('_' in b.strip('_') ) or ('.' in b and not b.replace('.','').isalpha() or '.' in b and any(c.isupper() for c in b)) or '::' in b \
        or bool(re.search(r"[a-z][A-Z]",b)) or bool(re.search(r"[A-Z].*[A-Z]",b) and not b.isupper()) or t.endswith('()')
def literal_patterns(q):
    pats=[]
    for m in QUOTE.finditer(q):
        s=next(g for g in m.groups() if g); pats.append(s.strip())
    for m in IDENT.finditer(q):
        t=m.group(0)
        if is_identlike(t):
            b=t.rstrip('()')
            pats.append(b)
            for sep in ('.','::'):
                if sep in b: pats.append(b.split(sep)[-1])
    out=[]
    for p in pats:
        if len(p)>=3 and p not in out: out.append(p)
    return out[:8]
def dumps(f):
    return {json.loads(l)['id']:json.loads(l) for l in gzip.open(R+f,'rt')}
corpora={}  # corpus name -> (repo, commit)
def add(setname,tasksf,dumpf):
    D=dumps(dumpf); rows=[json.loads(l) for l in open(R+tasksf)]
    by={}
    for r in rows:
        d=D[r['id']]
        if setname=='cb':
            import cbgold
            corp=f"cb-{r['repo'].split('/')[1]}@{r['base_commit'][:12]}"; corpora[corp]=(r['repo'].split('/')[1],r['base_commit'])
        elif '/wt/' in r.get('path',''):
            corp=f"wt-{r['repo']}-{r['commit'][:8]}"; corpora[corp]=(r['repo'],r['commit'])
        else:
            hk=f"{'heldout' if setname=='heldout' else 'dev'}:{r['repo']}"
            corp=f"head-{r['repo']}@{HEADS[hk][:12]}"; corpora[corp]=(r['repo'],HEADS[hk])
        t={'id':r['id'],'set':setname,'query':r['query'],'corpus':corp,
           'sem':[[k,d['ids'][k],s] for k,s in d['semantic']],
           'literal_patterns':literal_patterns(r['query'])}
        if setname=='cb': t['gold_lines']=CBG[r['id']]['gold_lines']
        else: t['gold_keys']=r['gold']
        by.setdefault(corp,[]).append(t)
    for corp,ts in by.items():
        with open(f'{OUT}/{setname}__{corp}.jsonl','w') as f:
            for t in ts: f.write(json.dumps(t)+'\n')
import cbgold
CBG=cbgold.cb_gold()
add('dev','tasks.jsonl','dump-plain.jsonl.gz')
add('masked','tasks-masked.jsonl','dump-masked.jsonl.gz')
add('heldout','heldout-clean.jsonl','dump-heldout.jsonl.gz')
add('cb','cb-tasks.jsonl','dump-contextbench.jsonl.gz')
json.dump(corpora,open(f'{OUT}/corpora.json','w'),indent=1)
print(len(corpora),'corpora', len(os.listdir(OUT))-1,'input files')
