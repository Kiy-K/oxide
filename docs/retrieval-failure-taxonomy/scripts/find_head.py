import json,gzip,subprocess,sys,collections
R='/home/user/oxide/docs/ranking-fusion-eval/results/'
fp=collections.defaultdict(dict)  # (set,repo)->{file:nlines}
def load(dump,tasks,setname):
    t={}
    for l in open(R+tasks):
        r=json.loads(l); t[r['id']]=r
    for l in gzip.open(R+dump,'rt'):
        d=json.loads(l); r=t[d['id']]
        if setname=='heldout' and '/wt/' in r['path']: continue
        for k,sp in d['spans'].items():
            if k.endswith(':__module__'):
                f=k.split('#')[0]; fp[(setname,r['repo'])][f]=sp[1]
load('dump-plain.jsonl.gz','tasks.jsonl','dev')
load('dump-masked.jsonl.gz','tasks-masked.jsonl','dev')
load('dump-heldout.jsonl.gz','heldout-clean.jsonl','heldout')
S='/tmp/claude-0/work/src/'
def nlines(repo,c,f):
    p=subprocess.run(['git','show',f'{c}:{f}'],cwd=S+repo,capture_output=True)
    if p.returncode: return None
    t=p.stdout.decode('utf8','replace')
    return len(t.splitlines())
out={}
for (s,repo),files in sorted(fp.items()):
    br=subprocess.run(['git','rev-parse','origin/HEAD'],cwd=S+repo,capture_output=True,text=True).stdout.strip()
    commits=subprocess.run(['git','rev-list','--first-parent','--before=2026-09-20T14:30:00Z','-n','60',br],cwd=S+repo,capture_output=True,text=True).stdout.split()
    sample=sorted(files)[:40]
    found=None
    for c in commits:
        ok=all(nlines(repo,c,f)==files[f] for f in sample)
        if ok:
            bad=[f for f in files if nlines(repo,c,f)!=files[f]]
            print(s,repo,c[:12],'full-check mismatches',len(bad),'of',len(files),flush=True)
            if not bad: found=c;break
    out[f'{s}:{repo}']=found
    print(s,repo,'->',found,flush=True)
json.dump(out,open('/tmp/claude-0/work/an/heads.json','w'),indent=1)
