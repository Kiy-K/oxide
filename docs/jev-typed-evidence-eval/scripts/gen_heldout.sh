#!/usr/bin/env bash
# #40 held-out generation (PROTOCOL §1). Unmodified make_tasks.py, heldout_edit_gold.py,
# and ch5 parent.sh/finalize.sh logic; changed only: binary, clone/worktree paths,
# max_tasks=20, and the exclusion files (scripts/excl.py). Every heavy step runs
# under a systemd memory/CPU cap. Idempotent per stage.
set -u
J=~/.cache/oxide-jev-eval; R=$(cd "$(dirname "$0")/../../.." && pwd)
OX=$J/target-frozen/release/oxide
CAP="systemd-run --user --scope -q -p MemoryMax=4G -p MemorySwapMax=512M -p CPUQuota=400% nice -n 10"
unset OXIDE_EMBED_NATIVE OXIDE_EMBED_URL OXIDE_EMBED_MODEL OXIDE_RETRIEVAL_MODE OXIDE_CONTEXT_MAX_PRIMARIES OXIDE_TERM_COVERAGE_ALPHA
mkdir -p $J/tasks $J/logs $J/wt $J/wt-parent

# 1. make_tasks.py, unmodified, max_tasks=20, newest first (its git-log order)
for repo in httpx requests flask ripgrep clap rayon zod axios; do
  [ -f $J/tasks/raw-$repo.jsonl.done ] && continue
  : > $J/tasks/raw-$repo.jsonl
  $CAP python3 $R/docs/ranking-fusion-eval/scripts/make_tasks.py $OX $J/repos/$repo $repo 20 \
    --exclude $J/excl/exclude-$repo.jsonl --worktree $J/wt > $J/tasks/raw-$repo.jsonl 2>> $J/logs/gen-$repo.log \
    && touch $J/tasks/raw-$repo.jsonl.done
  echo "$repo generated $(wc -l < $J/tasks/raw-$repo.jsonl)"
done
cat $J/tasks/raw-{httpx,requests,flask,ripgrep,clap,rayon,zod,axios}.jsonl > $J/tasks/raw.jsonl

# 2. parent-commit corpora (ch5 parent.sh)
python3 -c "
import json
for l in open('$J/tasks/raw.jsonl'):
    t=json.loads(l); print(t['path']+' '+t['commit']+' '+t['id'])" | while read -r src sha id; do
  wt=$J/wt-parent/$id
  [ -f $wt/.oxide/.done ] && continue
  [ -d $wt ] || git -C $src worktree add -q --detach $wt $sha~1 || { echo "FAIL wt $id"; continue; }
  ( cd $wt && $CAP $OX index . >/dev/null 2>$J/logs/pindex-$id.err ) && touch $wt/.oxide/.done && echo "parent $id"
done

# 3. ch5 finalize.sh filters: gold at parent, near-duplicates, edit-locus gold, fallback exclusion
python3 - <<'PY'
import json,sqlite3,os,re
J=os.path.expanduser('~/.cache/oxide-jev-eval')
out=[];seen=[]
for l in open(f'{J}/tasks/raw.jsonl'):
    t=json.loads(l); wt=f"{J}/wt-parent/{t['id']}"
    if not os.path.exists(f'{wt}/.oxide/.done'): print('SKIP no parent index',t['id']); continue
    db=sqlite3.connect(f'file:{wt}/.oxide/index.db?mode=ro',uri=True)
    have={f+'#'+q for f,q in db.execute('select file, qualified_name from symbols')}
    g=[x for x in t['gold'] if x in have]
    if not g: print('DROP no gold at parent',t['id']); continue
    q=set(re.findall(r'\w+',t['query'].lower()))
    dup=[s for s in seen if s['repo']==t['repo'] and sorted(s['gold'])==sorted(g) and len(q&s['_q'])/max(1,len(q|s['_q']))>=0.6]
    if dup: print('DROP near-dup',t['id'],'of',dup[0]['id']); continue
    t['gold']=g; t['path']=wt; t['_q']=q; seen.append(t); out.append(t)
with open(f'{J}/tasks/heldout-parent.jsonl','w') as fh:
    for t in out: t.pop('_q'); fh.write(json.dumps(t)+'\n')
print('tasks at parent',len(out))
PY
python3 $R/docs/alloc-utilization-eval/scripts/heldout_edit_gold.py $J/tasks/heldout-parent.jsonl > $J/tasks/heldout_gold.json
python3 - <<'PY'
import json,sqlite3,os
J=os.path.expanduser('~/.cache/oxide-jev-eval')
gold=json.load(open(f'{J}/tasks/heldout_gold.json')); prim=[];fb=[]
for l in open(f'{J}/tasks/heldout-parent.jsonl'):
    t=json.loads(l); db=sqlite3.connect(f"file:{t['path']}/.oxide/index.db?mode=ro",uri=True)
    inside=False
    for g in t['gold']:
        f,qn=g.split('#',1); r=db.execute('select start_line,end_line from symbols where file=? and qualified_name=?',(f,qn)).fetchone()
        if r and set(gold[t['id']]['lines'].get(f,[]))&set(range(r[0],r[1]+1)): inside=True
    (prim if inside and gold[t['id']]['lines'] else fb).append(l)
open(f'{J}/tasks/heldout-primary.jsonl','w').writelines(prim); open(f'{J}/tasks/heldout-fallback.jsonl','w').writelines(fb)
print('primary',len(prim),'fallback/empty',len(fb))
PY
echo HELDOUT-GEN-DONE
