#!/usr/bin/env bash
# run.sh <tasks.jsonl> <tag> [extra alloc_dump args...]  — one process per corpus
set -u
E=~/.cache/oxide-alloc-eval; tasks=$1; tag=$2; shift 2
unset OXIDE_EMBED_NATIVE OXIDE_EMBED_URL OXIDE_CONTEXT_MAX_PRIMARIES OXIDE_RETRIEVAL_MODE OXIDE_TERM_COVERAGE_ALPHA
: > $E/dumps/$tag.jsonl; : > $E/logs/$tag.stats.jsonl
python3 - "$tasks" <<'PY' > $E/tmp/$tag.corpora
import json,sys,collections
g=collections.OrderedDict()
for l in open(sys.argv[1]):
    t=json.loads(l); g.setdefault(t['path'],[]).append(l.rstrip('\n'))
for k,v in g.items(): print(k+'\t'+json.dumps(v))
PY
while IFS=$'\t' read -r wt lines; do
  c=$(basename $wt)
  python3 -c "import json,sys; print('\n'.join(json.loads(sys.argv[1])))" "$lines" > $E/tmp/$tag.$c.tasks
  $E/bin/alloc_dump $wt $E/tmp/$tag.$c.tasks "$@" >> $E/dumps/$tag.jsonl 2>> $E/logs/$tag.stats.jsonl || echo "FAIL $c"
done < $E/tmp/$tag.corpora
echo "DONE $tag"
