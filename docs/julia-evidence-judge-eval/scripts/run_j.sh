#!/usr/bin/env bash
# run_j.sh <tasks.jsonl> <tag> [alloc_dump args...] — one process per corpus (as alloc-eval run.sh)
set -u
J=~/.cache/oxide-julia-eval; tasks=$1; tag=$2; shift 2
unset OXIDE_EMBED_NATIVE OXIDE_EMBED_URL OXIDE_CONTEXT_MAX_PRIMARIES OXIDE_RETRIEVAL_MODE OXIDE_TERM_COVERAGE_ALPHA
: > $J/dumps/$tag.jsonl; : > $J/logs/$tag.stats.jsonl
python3 - "$tasks" <<'PY' > $J/tmp/$tag.corpora
import json,sys,collections
g=collections.OrderedDict()
for l in open(sys.argv[1]):
    t=json.loads(l); g.setdefault(t['path'],[]).append(l.rstrip('\n'))
for k,v in g.items(): print(k+'\t'+json.dumps(v))
PY
while IFS=$'\t' read -r wt lines; do
  c=$(basename $wt)
  python3 -c "import json,sys; print('\n'.join(json.loads(sys.argv[1])))" "$lines" > $J/tmp/$tag.$c.tasks
  $J/bin/alloc_dump_j $wt $J/tmp/$tag.$c.tasks "$@" >> $J/dumps/$tag.jsonl 2>> $J/logs/$tag.stats.jsonl || echo "FAIL $c"
done < $J/tmp/$tag.corpora
echo "DONE $tag $(wc -l < $J/dumps/$tag.jsonl)"
