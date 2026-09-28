import json,gzip,subprocess,sys,collections,os
R='/home/user/oxide/docs/ranking-fusion-eval/results/'
S='/tmp/claude-0/work/src/'
repo=sys.argv[1]; dumpf=sys.argv[2]; tasksf=sys.argv[3]
t={json.loads(l)['id']:json.loads(l) for l in open(R+tasksf)}
per={}
for l in gzip.open(R+dumpf,'rt'):
    d=json.loads(l); r=t[d['id']]
    if r['repo']!=repo or '/wt/' in r.get('path',''): continue
    per[d['id']]={k.split('#')[0]:sp[1] for k,sp in d['spans'].items() if k.endswith(':__module__')}
commits=subprocess.run(['git','rev-list','--all','--since=2026-06-01'],cwd=S+repo,capture_output=True,text=True).stdout.split()
print(len(commits),'commits',len(per),'tasks',flush=True)
blobcache={}
def cnt(b):
    if b not in blobcache:
        data=subprocess.run(['git','cat-file','blob',b],cwd=S+repo,capture_output=True).stdout
        n=len(data.decode('utf8','replace').splitlines()); blobcache[b]=max(n,1)
    return blobcache[b]
allfiles=set().union(*per.values())
res={tid:[] for tid in per}
for c in commits:
    out=subprocess.run(['git','ls-tree','-r',c],cwd=S+repo,capture_output=True,text=True).stdout
    tree={}
    for line in out.splitlines():
        meta,p=line.split('\t',1)
        if p in allfiles:
            mode,typ,b=meta.split(); tree[p]=(mode,b)
    for tid,files in per.items():
        m=0
        for f,n in files.items():
            if f not in tree: m+=1; continue
            mode,b=tree[f]
            if mode=='120000': continue
            if cnt(b)!=n: m+=1
        res[tid].append((m,c))
summary=collections.Counter()
best={}
for tid,l in res.items():
    l.sort(); best[tid]=l[0]; summary[l[0][0]]+=1
print('min-mismatch distribution',sorted(summary.items()))
# which commits are exact for all tasks?
exact=[c for c in commits if all(dict((cc,m) for m,cc in res[t_])[c]==0 for t_ in per)]
print('commits exact for all tasks:',exact[:5])
json.dump({tid:[b[0],b[1]] for tid,b in best.items()},open(f'best-{repo}-{dumpf[:12]}.json','w'))
cc=collections.Counter()
for tid,l in res.items():
    for m,c in l:
        if m==0: cc[c]+=1
top=cc.most_common(5)
for c,n in top:
    d=subprocess.run(['git','log','-1','--format=%ci %s',c],cwd=S+repo,capture_output=True,text=True).stdout.strip()
    print(n,c[:12],d)
bad=[tid for tid in per if dict((c_,m) for m,c_ in res[tid]).get(top[0][0])!=0]
print('not exact at top:',bad, [best[b] for b in bad])
