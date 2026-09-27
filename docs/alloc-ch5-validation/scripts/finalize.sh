#!/usr/bin/env bash
# After gen.sh: complete parent corpora, build final tasks + gold, hash, THEN run arms A/B.
set -u
F=~/.cache/oxide-ch5-fresh; W=/tmp/oxide-ch5-6ec73b6
until grep -q GEN-DONE $F/logs/gen.out; do sleep 15; done
until grep -q PARENT-DONE $F/logs/parent.out; do sleep 15; done
$F/parent.sh >> $F/logs/parent.out 2>&1          # pick up tasks generated after the first pass
python3 - <<'PY'
import json,sqlite3,os,re
F=os.path.expanduser('~/.cache/oxide-ch5-fresh')
out=[];seen=[]
for l in open(f'{F}/tasks/raw.jsonl'):
    t=json.loads(l); wt=f"{F}/wt-parent/{t['id']}"
    if not os.path.exists(f'{wt}/.oxide/.done'): print('SKIP no parent index',t['id']); continue
    db=sqlite3.connect(f'file:{wt}/.oxide/index.db?mode=ro',uri=True)
    have={f+'#'+q for f,q in db.execute('select file, qualified_name from symbols')}
    g=[x for x in t['gold'] if x in have]
    if not g: print('DROP no gold at parent',t['id']); continue
    q=set(re.findall(r'\w+',t['query'].lower()))
    dup=[s for s in seen if s['repo']==t['repo'] and sorted(s['gold'])==sorted(g) and len(q&s['_q'])/max(1,len(q|s['_q']))>=0.6]
    if dup: print('DROP near-dup',t['id'],'of',dup[0]['id']); continue
    t['gold']=g; t['path']=wt; t['_q']=q; seen.append(t); out.append(t)
with open(f'{F}/tasks/fresh-parent.jsonl','w') as fh:
    for t in out: t.pop('_q'); fh.write(json.dumps(t)+'\n')
print('tasks at parent',len(out))
PY
python3 $W/docs/alloc-utilization-eval/scripts/heldout_edit_gold.py $F/tasks/fresh-parent.jsonl > $F/tasks/fresh_gold.json
python3 - <<'PY'
# primary set: drop tasks whose edit-locus gold used the fallback (no touched line inside a gold symbol)
import json,sqlite3,os
F=os.path.expanduser('~/.cache/oxide-ch5-fresh')
gold=json.load(open(f'{F}/tasks/fresh_gold.json')); prim=[];fb=[]
for l in open(f'{F}/tasks/fresh-parent.jsonl'):
    t=json.loads(l); db=sqlite3.connect(f"file:{t['path']}/.oxide/index.db?mode=ro",uri=True)
    inside=False
    for g in t['gold']:
        f,qn=g.split('#',1); r=db.execute('select start_line,end_line from symbols where file=? and qualified_name=?',(f,qn)).fetchone()
        if r and set(gold[t['id']]['lines'].get(f,[]))&set(range(r[0],r[1]+1)): inside=True
    (prim if inside and gold[t['id']]['lines'] else fb).append(l)
open(f'{F}/tasks/fresh-primary.jsonl','w').writelines(prim); open(f'{F}/tasks/fresh-fallback.jsonl','w').writelines(fb)
print('primary',len(prim),'fallback/empty',len(fb))
PY
( cd $F/tasks && sha256sum fresh-parent.jsonl fresh-primary.jsonl fresh-fallback.jsonl fresh_gold.json && date -Is ) >> $F/prereg.sha256
echo TASKS-FROZEN
# ---- arms A (ch0) / B (ch5), one process per corpus ----
: > $F/dump.jsonl
unset OXIDE_EMBED_NATIVE OXIDE_EMBED_URL OXIDE_CONTEXT_MAX_PRIMARIES OXIDE_RETRIEVAL_MODE OXIDE_TERM_COVERAGE_ALPHA
while read -r l; do
  id=$(python3 -c "import json,sys;print(json.loads(sys.argv[1])['id'])" "$l"); p=$F/wt-parent/$id
  echo "$l" > $F/tasks/one.jsonl
  $F/bin/alloc_dump $p $F/tasks/one.jsonl --modes balanced --challengers 0,5 --timing-reps 2 >> $F/dump.jsonl 2>> $F/logs/dump.stats.jsonl || echo "FAIL $id"
done < $F/tasks/fresh-parent.jsonl
echo ARMS-DONE
