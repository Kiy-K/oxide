exec(open('find_head.py').read().split("out={}")[0])
import sys
tgt=sys.argv[1:]
for key in tgt:
    s,repo=key.split(':'); files=fp[(s,repo)]
    commits=subprocess.run(['git','rev-list','--first-parent','--since=2026-08-01','origin/HEAD'],cwd=S+repo,capture_output=True,text=True).stdout.split()
    best=None
    # coarse: check every 5th commit on a 30-file sample
    sample=sorted(files)[::max(1,len(files)//30)]
    res=[]
    for i,c in enumerate(commits):
        m=sum(nlines(repo,c,f)!=files[f] for f in sample)
        res.append((m,i,c))
        if m==0: break
    res.sort(); m,i,c=res[0]
    bad=[(f,files[f],nlines(repo,c,f)) for f in files if nlines(repo,c,f)!=files[f]]
    print(key,'best',c[:12],'idx',i,'of',len(commits),'sample-mism',m,'full-mism',len(bad),bad[:5],flush=True)
