#!/usr/bin/env bash
# Fresh ch5 validation tasks (PREREGISTRATION.md §2). make_tasks.py unmodified.
set -u
F=~/.cache/oxide-ch5-fresh; W=/tmp/oxide-ch5-6ec73b6
OX=~/.cache/oxide-alloc-eval/bin/oxide-pristine
unset OXIDE_EMBED_NATIVE OXIDE_EMBED_URL OXIDE_EMBED_MODEL
declare -A R=( [httpx]=~/.cache/oxide-semantic-eval/repos/httpx [requests]=~/.cache/oxide-contextbench/repos/requests [flask]=~/.cache/oxide-contextbench/repos/flask [ripgrep]=~/.cache/oxide-semantic-eval/repos/ripgrep [clap]=~/.cache/oxide-contextbench/repos/clap [rayon]=~/.cache/oxide-contextbench/repos/rayon [zod]=~/.cache/oxide-semantic-eval/repos/zod [axios]=~/.cache/oxide-contextbench/repos/axios )
for repo in httpx requests flask ripgrep clap rayon zod axios; do
  r=${R[$repo]}
  # exclusion: c or c~1 in S = used ∪ parents(used), 12-char prefix match
  python3 - "$r" > $F/tasks/exclude-$repo.jsonl <<'PY'
import json,subprocess,sys,os
r=sys.argv[1]
used=json.load(open(os.path.expanduser('~/.cache/oxide-ch5-fresh/used_shas.json')))
g=lambda *a: subprocess.run(['git','-C',r,*a],capture_output=True,text=True).stdout.strip()
S={u[:12] for u in used}
for u in used:
    p=g('rev-parse','--verify','-q',u+'~1') if len(u)==40 else ''
    if p: S.add(p[:12])
log=g('log','--no-merges','--format=%H %P','-n','900').splitlines()
n=0
for line in log:
    parts=line.split(); c=parts[0]; ps=parts[1:]
    if c[:12] in S or any(p[:12] in S for p in ps):
        print(json.dumps({'commit':c})); n+=1
print(f'{os.path.basename(r)} excluded {n}',file=sys.stderr)
PY
  python3 $W/docs/ranking-fusion-eval/scripts/make_tasks.py $OX $r $repo 8 --exclude $F/tasks/exclude-$repo.jsonl --worktree $F/wt >> $F/tasks/raw.jsonl 2>> $F/logs/gen.log
  echo "$repo done $(grep -c "\"repo\": \"$repo\"" $F/tasks/raw.jsonl)"
done
echo GEN-DONE
