#!/usr/bin/env bash
# Parent-commit corpora for every task in tasks/raw.jsonl (same as #30 parent.sh), 4 in parallel. Idempotent.
F=~/.cache/oxide-ch5-fresh
python3 -c "
import json
for l in open('$F/tasks/raw.jsonl'):
    t=json.loads(l); print(t['path']+' '+t['commit']+' '+t['id'])" | xargs -P4 -L1 bash -c '
  src=$0; sha=$1; id=$2; wt='$F'/wt-parent/$id
  [ -f $wt/.oxide/.done ] && exit 0
  unset OXIDE_EMBED_NATIVE OXIDE_EMBED_URL OXIDE_EMBED_MODEL
  [ -d $wt ] || git -C $src worktree add -q --detach $wt $sha~1 || { echo "FAIL wt $id"; exit 0; }
  s=$(date +%s); ( cd $wt && ~/.cache/oxide-alloc-eval/bin/oxide-pristine index . >/dev/null 2>'$F'/logs/pindex-$id.err ) && touch $wt/.oxide/.done && echo "parent $id $(( $(date +%s)-s ))s"'
echo PARENT-DONE
