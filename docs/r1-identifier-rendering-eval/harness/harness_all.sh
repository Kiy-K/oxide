cd /tmp/claude-0/-home-user-oxide/addc97ee-9d65-5c7d-8bef-c04e3113af55/scratchpad/r1/work
until grep -q INDEX_ALL_DONE logs/index_all.log; do sleep 30; done
python3 - > tmp/plan.txt <<'PY'
import json,glob,os
c=json.load(open('corpora.json')); seen=set(); out=[]
for n in c:
    if n.startswith('head-') and os.path.exists(f'tasks/dev__{n}.jsonl'): out.append(f'{n} full parb')
for n in c:
    if n.startswith('head-') and not os.path.exists(f'tasks/dev__{n}.jsonl'): out.append(f'{n} d0only parb')
for n in c:
    if n.startswith('par-'):
        r=n.split('@')[0]; out.append(f"{n} full {'parb' if r not in seen else ''}"); seen.add(r)
for n in c:
    if n.startswith('cb-'): out.append(f'{n} full')
print('\n'.join(out))
PY
while read c m p; do ./run_corpus.sh $c $m $p >> logs/harness_all.log 2>&1; done < tmp/plan.txt
echo HARNESS_ALL_DONE >> logs/harness_all.log
