#!/usr/bin/env bash
# run_dump.sh <tasks.jsonl> <tag>
# For every corpus (task `path`) in the task file: extract intent evidence
# (once per corpus, cached under ev/), then run the pinned intent_dump binary
# against the corpus's own production index (read-only). Appends to
# dumps/<tag>.jsonl; skips corpora already present. Harness stats per corpus
# go to logs/<tag>.stats.jsonl.
set -u
E=~/.cache/oxide-intent-eval
S=$(cd "$(dirname "$0")" && pwd)
BIN=$E/bin/intent_dump
tasks=$1; tag=$2
mkdir -p $E/dumps $E/ev $E/tmp $E/logs
dump=$E/dumps/$tag.jsonl; touch $dump
unset OXIDE_EMBED_NATIVE OXIDE_EMBED_URL
python3 - "$tasks" <<'PY' > $E/tmp/$tag.corpora
import json,sys,collections
g=collections.OrderedDict()
for l in open(sys.argv[1]):
    t=json.loads(l); g.setdefault(t['path'],[]).append(l.rstrip('\n'))
for k,v in g.items(): print(k+'\t'+json.dumps(v))
PY
while IFS=$'\t' read -r wt lines; do
  corpus=$(basename $wt)
  [ -f $wt/.oxide/index.db ] || { echo "SKIP $corpus (no index)"; continue; }
  first=$(python3 -c "import json,sys; print(json.loads(json.loads(sys.argv[1])[0])['id'])" "$lines")
  grep -q "\"id\":\"$first\"" $dump && continue
  python3 -c "import json,sys; print('\n'.join(json.loads(sys.argv[1])))" "$lines" > $E/tmp/$tag.$corpus.tasks
  ev=$E/ev/$corpus.jsonl
  if [ ! -s $ev ]; then
    s=$(date +%s%N)
    python3 $S/extract_intent.py $wt $ev 2>> $E/logs/extract.log
    echo "{\"corpus\":\"$corpus\",\"extract_ms\":$(( ($(date +%s%N) - s) / 1000000 ))}" >> $E/logs/extract_time.jsonl
  fi
  $BIN $wt $E/tmp/$tag.$corpus.tasks --evidence $ev --cache $E/emb-cache.db >> $dump 2>> $E/logs/$tag.stats.jsonl \
    || echo "FAIL $corpus"
  echo "$corpus $(tail -n1 $E/logs/$tag.stats.jsonl | cut -c1-200)"
done < $E/tmp/$tag.corpora
echo "DONE $tag"
