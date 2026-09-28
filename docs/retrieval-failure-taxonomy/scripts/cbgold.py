import pandas as pd, json, re
_P=re.compile(r"^(?:/workspace/[^/]+/|/testbed/)")
def norm(p): return _P.sub("",p)
def cb_gold():
    d=pd.read_parquet('/tmp/claude-0/work/cbrepo/data/full.parquet')
    out={}
    for _,r in d.iterrows():
        g=json.loads(r.gold_context) if isinstance(r.gold_context,str) else list(r.gold_context)
        gl={}
        for it in g:
            f=norm(it.get('file') or '')
            if not f: continue
            gl.setdefault(f,[]).append([int(it.get('start_line',1)),int(it.get('end_line',1))])
        out[r.instance_id]={'gold_lines':gl,'base_commit':r.base_commit,'repo':r.repo,'problem_statement':r.problem_statement}
    return out
if __name__=='__main__':
    G=cb_gold()
    seen=set()
    for l in open('/home/user/oxide/docs/contextbench-scorer-fix/raw/pin21_rescore.jsonl'):
        r=json.loads(l)
        if r['instance_id'] in seen: continue
        seen.add(r['instance_id'])
        gl=G[r['instance_id']]['gold_lines']
        n=sum(len(set(x for a,b in v for x in range(a,b+1))) for v in gl.values())
        print(r['instance_id'][-30:], 'mine',n,'scorer',r['new']['line']['gold_size'], 'OK' if n==r['new']['line']['gold_size'] else 'DIFF')
